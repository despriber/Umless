"""后台任务：耗时操作在线程里执行，前端通过 job_status 轮询进度与结果。"""

import logging
import threading
import uuid
from collections.abc import Callable

from .i18n import t

log = logging.getLogger(__name__)


class Jobs:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, fn: Callable, *args, message: str | None = None, key: str | None = None) -> dict:
        """fn(progress, *args) 的返回值作为任务结果；progress(比例或 None, 说明)。

        key 相同的任务还在运行时不再重复启动，直接返回它的 job_id：切换页面再回来重复点击
        诊断、下载时，不会再调一次 LLM 或让两个线程写同一个文件。
        """
        message = message or t("job.processing")
        job_id = uuid.uuid4().hex[:12]
        state = {"state": "running", "progress": None, "message": message, "result": None, "error": None,
                 "key": key}
        with self._lock:
            if key:
                for jid, s in self._jobs.items():
                    if s["key"] == key and s["state"] == "running":
                        return {"job_id": jid}
            self._jobs[job_id] = state

        def progress(p: float | None, msg: str | None = None) -> None:
            with self._lock:
                state["progress"] = p
                if msg:
                    state["message"] = msg

        def run() -> None:
            try:
                result = fn(progress, *args)
                with self._lock:
                    state.update(state="done", progress=1.0, result=result)
            except Exception as e:
                log.exception("任务失败")
                with self._lock:
                    state.update(state="error", error=str(e) or e.__class__.__name__)

        threading.Thread(target=run, daemon=True).start()
        return {"job_id": job_id}

    def status(self, job_id: str) -> dict:
        with self._lock:
            state = self._jobs.get(job_id)
            if state is None:
                return {"state": "error", "progress": None, "message": "", "result": None, "error": t("job.not_found")}
            snapshot = {k: v for k, v in state.items() if k != "key"}
            if state["state"] != "running":
                self._jobs.pop(job_id, None)  # 结果只取一次
            return snapshot
