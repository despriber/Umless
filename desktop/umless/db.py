"""本地 SQLite 数据层。常驻进程与主窗口进程共享同一个库（WAL 模式支持并发读写）。

原始逐字稿（raw_text）是表达诊断的数据基础，必须保留。
"""

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS utterances (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,          -- 本地时间 ISO 格式
    duration_s    REAL    NOT NULL,          -- 录音时长
    app           TEXT,                      -- 前台应用进程名
    asr_engine    TEXT    NOT NULL,
    raw_text      TEXT    NOT NULL,          -- ASR 逐字稿（保留口癖）
    llm_model     TEXT,
    polished_text TEXT,                      -- 润色结果；LLM 关闭或失败时为空
    output_text   TEXT    NOT NULL,          -- 实际输出给用户的文字
    asr_ms        INTEGER NOT NULL,
    llm_ms        INTEGER,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_utterances_created ON utterances(created_at);
"""

SCHEMA_V2 = """
-- 场景：chat / document / code / email / default；润色强度：faithful / moderate / restructure
ALTER TABLE utterances ADD COLUMN scene TEXT;
ALTER TABLE utterances ADD COLUMN intensity TEXT;

CREATE TABLE IF NOT EXISTS metrics (
    utterance_id   INTEGER PRIMARY KEY REFERENCES utterances(id) ON DELETE CASCADE,
    chars          INTEGER NOT NULL,      -- 原文有效字数（不含标点空白）
    fillers        TEXT    NOT NULL,      -- {"就是": 2, "嗯": 1}
    filler_count   INTEGER NOT NULL,
    repeat_count   INTEGER NOT NULL,
    correction_count INTEGER NOT NULL,
    rewrite_ratio  REAL,                  -- 改写幅度 0~1，无润色稿时为空
    cpm            REAL,                  -- 语速：字 / 分钟
    fluency        REAL,                  -- 0~100
    concision      REAL,                  -- 0~100
    scoreable      INTEGER NOT NULL,      -- 是否参与评分（足够长）
    highlights     TEXT    NOT NULL,      -- 原文分段标注 [[文本, 类型], ...]
    version        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS lexicon (
    word       TEXT PRIMARY KEY,
    source     TEXT NOT NULL,             -- builtin / llm / user
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnoses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL,             -- 诊断覆盖的日期 YYYY-MM-DD
    created_at TEXT NOT NULL,
    model      TEXT,
    n          INTEGER NOT NULL,          -- 样本条数
    logic      REAL,                      -- LLM 1~5 分 × 20，即 0~100
    clarity    REAL,
    fluency    REAL,                      -- 当天规则均值 0~100
    concision  REAL,
    overall    REAL,
    data       TEXT NOT NULL              -- LLM 返回的完整 JSON
);
CREATE INDEX IF NOT EXISTS idx_diagnoses_date ON diagnoses(date);

CREATE TABLE IF NOT EXISTS habits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL UNIQUE,     -- 例如 word:就是 / repeat / logic
    title       TEXT NOT NULL,
    kind        TEXT NOT NULL,            -- word / repeat / correction / logic / clarity
    word        TEXT,
    advice      TEXT,
    evidence    TEXT,                     -- JSON 列表
    baseline    REAL,
    target      REAL,
    current     REAL,
    status      TEXT NOT NULL,            -- active / achieved / archived
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    achieved_at TEXT
);

CREATE TABLE IF NOT EXISTS practice (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    topic      TEXT NOT NULL,
    category   TEXT,
    duration_s REAL NOT NULL,
    raw_text   TEXT NOT NULL,
    overall    REAL,
    data       TEXT NOT NULL              -- 评分与反馈 JSON
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Utterance:
    duration_s: float
    app: str | None
    asr_engine: str
    raw_text: str
    asr_ms: int
    output_text: str
    llm_model: str | None = None
    polished_text: str | None = None
    llm_ms: int | None = None
    error: str | None = None
    scene: str | None = None
    intensity: str | None = None


class DB:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._migrate()

    # ---- 基础 ----

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            self._conn.executescript(SCHEMA_V1)
        if version < 2:
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(utterances)")}
            for stmt in SCHEMA_V2.split(";"):
                # 去掉整行注释（上一条语句的行尾注释会落到下一段开头）
                stmt = "\n".join(
                    line for line in stmt.splitlines() if not line.strip().startswith("--")
                ).strip()
                if not stmt:
                    continue
                if m := re.match(r"ALTER TABLE utterances ADD COLUMN (\w+)", stmt):
                    if m.group(1) in cols:
                        continue
                self._conn.execute(stmt)
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.lastrowid

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- 键值（调度状态、缓存等） ----

    def get_kv(self, key: str, default=None):
        rows = self.query("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_kv(self, key: str, value) -> None:
        self.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    # ---- 语音记录 ----

    def add_utterance(self, u: Utterance) -> int:
        return self.execute(
            "INSERT INTO utterances (created_at, duration_s, app, asr_engine, raw_text, llm_model, "
            "polished_text, output_text, asr_ms, llm_ms, error, scene, intensity) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now_iso(), round(u.duration_s, 2), u.app, u.asr_engine, u.raw_text, u.llm_model,
                u.polished_text, u.output_text, u.asr_ms, u.llm_ms, u.error, u.scene, u.intensity,
            ),
        )

    def recent(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM utterances ORDER BY id DESC LIMIT ?", (limit,))

    def utterances_between(self, start: str, end: str) -> list[sqlite3.Row]:
        """[start, end) 区间内的记录及其指标，按时间正序。"""
        return self.query(
            "SELECT u.*, m.chars, m.fillers, m.filler_count, m.repeat_count, m.correction_count, "
            "m.rewrite_ratio, m.cpm, m.fluency, m.concision, m.scoreable, m.highlights "
            "FROM utterances u LEFT JOIN metrics m ON m.utterance_id = u.id "
            "WHERE u.created_at >= ? AND u.created_at < ? ORDER BY u.id",
            (start, end),
        )

    def delete_utterance(self, uid: int) -> None:
        self.execute("DELETE FROM utterances WHERE id = ?", (uid,))

    def purge_older_than(self, days: int) -> int:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        with self._lock:
            n = self._conn.execute("DELETE FROM utterances WHERE created_at < ?", (cutoff,)).rowcount
            self._conn.commit()
        return n

    # ---- 指标 ----

    def save_metrics(self, uid: int, m: dict) -> None:
        self.execute(
            "INSERT OR REPLACE INTO metrics (utterance_id, chars, fillers, filler_count, repeat_count, "
            "correction_count, rewrite_ratio, cpm, fluency, concision, scoreable, highlights, version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uid, m["chars"], json.dumps(m["fillers"], ensure_ascii=False), m["filler_count"],
                m["repeat_count"], m["correction_count"], m["rewrite_ratio"], m["cpm"], m["fluency"],
                m["concision"], int(m["scoreable"]),
                json.dumps(m["highlights"], ensure_ascii=False), m["version"],
            ),
        )

    def utterances_missing_metrics(self, version: int) -> list[sqlite3.Row]:
        return self.query(
            "SELECT u.* FROM utterances u LEFT JOIN metrics m ON m.utterance_id = u.id "
            "WHERE m.utterance_id IS NULL OR m.version < ?",
            (version,),
        )

    # ---- 口癖词表 ----

    def lexicon(self, enabled_only: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM lexicon" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY source, word"
        return self.query(sql)

    def add_words(self, words: list[str], source: str) -> list[str]:
        """加入新词，返回真正新增的词（已存在的不覆盖来源与开关）。"""
        added = []
        # 整批一个事务：其他线程不会读到只插入了一半的词表（例如新库首次补入内置口癖词时）
        with self._lock:
            for w in words:
                w = w.strip()
                if not w or len(w) > 8:
                    continue
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO lexicon (word, source, enabled, created_at) VALUES (?, ?, 1, ?)",
                    (w, source, now_iso()),
                )
                if cur.rowcount:
                    added.append(w)
            self._conn.commit()
        return added

    def set_word_enabled(self, word: str, enabled: bool) -> None:
        self.execute("UPDATE lexicon SET enabled = ? WHERE word = ?", (int(enabled), word))

    def remove_word(self, word: str) -> None:
        self.execute("DELETE FROM lexicon WHERE word = ?", (word,))

    # ---- 导出 / 清空 ----

    def export_all(self) -> dict:
        out = {}
        for table in ("utterances", "metrics", "lexicon", "diagnoses", "habits", "practice"):
            out[table] = [dict(r) for r in self.query(f"SELECT * FROM {table}")]
        return out

    def clear_all(self) -> None:
        with self._lock:
            for table in ("metrics", "utterances", "diagnoses", "habits", "practice", "kv"):
                self._conn.execute(f"DELETE FROM {table}")
            self._conn.execute("DELETE FROM lexicon WHERE source != 'builtin'")
            self._conn.commit()
            self._conn.execute("VACUUM")
