// 通用 UI：toast、确认弹窗、格式化（随界面语言）、枚举文案、数字动画、空状态插画、悬浮提示。
import { html, raw, el, esc, render } from "./dom.js";
import { icon } from "./icons.js";
import { t, th, isZh } from "./i18n.js";

/* ---------------------------------------------------------------- 枚举文案 */

export const SCENE_KEYS = ["chat", "document", "code", "email", "default"];
export const SCENE_ICONS = { chat: "chat", document: "doc", code: "code", email: "mail", default: "layers" };
export const INTENSITY_KEYS = ["faithful", "moderate", "restructure"];

export const sceneName = (k) => t(`scene.${SCENE_ICONS[k] ? k : "default"}`);
export const intensityName = (k) => (INTENSITY_KEYS.includes(k) ? t(`intensity.${k}`) : "—");
export const hlName = (k) => t(`hl.${k}`);
export const issueType = (k) => {
  const cls = { filler: "is-warn", repeat: "is-violet", correction: "is-blue", structure: "is-accent" }[k] ?? "";
  return [t(`issue.${["filler", "repeat", "correction", "structure", "redundancy", "wording"].includes(k) ? k : "other"}`), cls];
};
export const lexSource = (k) => (["builtin", "llm", "user"].includes(k) ? t(`lex.src.${k}`) : k);

/** 进程名（桌面）或包名（Android）→ 友好名称（[中文, 英文]） */
const APP_NAMES = {
  "wechat.exe": ["微信", "WeChat"], "weixin.exe": ["微信", "WeChat"], "qq.exe": ["QQ", "QQ"],
  "dingtalk.exe": ["钉钉", "DingTalk"], "feishu.exe": ["飞书", "Feishu"], "lark.exe": ["飞书", "Lark"],
  "telegram.exe": "Telegram", "slack.exe": "Slack", "typora.exe": "Typora", "winword.exe": "Word",
  "obsidian.exe": "Obsidian", "notion.exe": "Notion", "wps.exe": "WPS", "code.exe": "VS Code", "cursor.exe": "Cursor",
  "windowsterminal.exe": ["终端", "Terminal"], "wezterm-gui.exe": "WezTerm", "cmd.exe": ["命令提示符", "Command Prompt"],
  "powershell.exe": "PowerShell", "outlook.exe": "Outlook", "foxmail.exe": "Foxmail", "thunderbird.exe": "Thunderbird",
  "chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox", "idea64.exe": "IntelliJ IDEA",
  "pycharm64.exe": "PyCharm", "keepass.exe": "KeePass", "1password.exe": "1Password",
  "pythonw.exe": "Umless", "python.exe": "Python",
  // Android 包名
  "com.tencent.mm": ["微信", "WeChat"], "com.tencent.mobileqq": "QQ", "com.alibaba.android.rimet": ["钉钉", "DingTalk"],
  "com.ss.android.lark": ["飞书", "Lark"], "org.telegram.messenger": "Telegram", "com.slack": "Slack",
  "md.obsidian": "Obsidian", "notion.id": "Notion", "cn.wps.moffice_eng": "WPS", "com.termux": "Termux",
  "com.github.android": "GitHub", "com.microsoft.office.outlook": "Outlook", "com.google.android.gm": "Gmail",
  "com.android.chrome": "Chrome", "com.x8bit.bitwarden": "Bitwarden", "org.umless.ime": "Umless",
};
export function appName(exe) {
  if (!exe) return t("app.unknown");
  const v = APP_NAMES[String(exe).toLowerCase()];
  if (Array.isArray(v)) return isZh() ? v[0] : v[1];
  return v || String(exe).replace(/\.exe$/i, "");
}

/* ---------------------------------------------------------------- 格式化 */

const pad = (n) => String(n).padStart(2, "0");
const locale = () => (isZh() ? "zh-CN" : "en-US");
export const fmtInt = (n) => (n == null ? "—" : Math.round(n).toLocaleString(locale()));
export const fmtScore = (n) => (n == null ? "—" : String(Math.round(n)));
export const fmtPct = (r) => (r == null ? "—" : `${Math.round(r * 100)}%`);
export function fmtNum(n, digits = 1) {
  if (n == null) return "—";
  const v = Number(n);
  return Number.isInteger(v) ? String(v) : v.toFixed(digits).replace(/\.0+$/, "");
}
export function fmtMs(ms) {
  if (ms == null) return "—";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}
/** 时长：58 秒 / 1 分 05 秒；58s / 1m 05s */
export function fmtDuration(s) {
  if (s == null) return "—";
  s = Math.round(s);
  return s < 60 ? t("fmt.sec", { n: s }) : t("fmt.minsec", { m: Math.floor(s / 60), s: pad(s % 60) });
}
export const fmtClock = (s) => {
  s = Math.max(0, Math.ceil(s));
  return `${Math.floor(s / 60)}:${pad(s % 60)}`;
};

export const parseDate = (s) => {
  // "2026-10-12" 或 "2026-10-12T10:57:44"，按本地时间解析
  const [d, tm = "00:00:00"] = String(s).split("T");
  const [y, m, day] = d.split("-").map(Number);
  const [hh, mm, ss] = tm.split(":").map(Number);
  return new Date(y, m - 1, day, hh || 0, mm || 0, ss || 0);
};
export const isoDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const todayIso = () => isoDate(new Date());
/** 10月12日 / Oct 12 */
export const fmtMD = (s) => {
  const d = parseDate(s);
  return isZh() ? `${d.getMonth() + 1}月${d.getDate()}日` : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
};
/** 星期一 / Monday */
export const fmtWeekday = (s) => parseDate(s).toLocaleDateString(locale(), { weekday: "long" });
export const fmtShortMD = (s) => { const d = parseDate(s); return `${d.getMonth() + 1}/${d.getDate()}`; };
export const fmtHM = (s) => { const d = parseDate(s); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };

/** 相对日期：今天 / 昨天 / 前天 / 10月8日 */
export function relDay(s) {
  const d = parseDate(String(s).slice(0, 10));
  const now = parseDate(todayIso());
  const diff = Math.round((now - d) / 86400000);
  if (diff === 0) return t("day.today");
  if (diff === 1) return t("day.yesterday");
  if (diff === 2 && isZh()) return "前天";
  if (d.getFullYear() === now.getFullYear()) return fmtMD(s);
  return isZh() ? `${d.getFullYear()}年${fmtMD(s)}` : d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}
export const isToday = (s) => String(s).slice(0, 10) === todayIso();
export const relDateTime = (s) => `${relDay(s)} ${fmtHM(s)}`;

export function greeting() {
  const h = new Date().getHours();
  return t(h < 5 ? "greet.night" : h < 11 ? "greet.morning" : h < 13 ? "greet.noon" : h < 18 ? "greet.afternoon" : "greet.evening");
}

/* ---------------------------------------------------------------- 杂项工具 */

export function debounce(fn, ms) {
  let t;
  const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  d.flush = (...a) => { clearTimeout(t); return fn(...a); };
  d.cancel = () => clearTimeout(t);
  return d;
}

export const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/** 数字从当前值过渡到目标值（250ms，尊重减少动效） */
export function animateNumber(node, to, { format = (v) => String(Math.round(v)), from, duration = 250 } = {}) {
  if (!node || to == null || Number.isNaN(Number(to))) return;
  const start = from ?? Number(node.dataset.value ?? 0);
  node.dataset.value = to;
  if (reducedMotion() || start === to) { node.textContent = format(to); return; }
  const t0 = performance.now();
  const step = (t) => {
    const p = Math.min(1, (t - t0) / duration);
    const e = 1 - Math.pow(1 - p, 3);
    node.textContent = format(start + (to - start) * e);
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ---------------------------------------------------------------- toast */

export function toast(message, kind = "ok", ms = 2600) {
  const box = document.getElementById("toasts");
  const node = el(html`<div class="toast ${kind === "error" ? "is-error" : ""}" role="status">
    ${icon(kind === "error" ? "alert" : kind === "info" ? "info" : "checkCircle", 16)}<span>${message}</span></div>`);
  box.appendChild(node);
  setTimeout(() => {
    node.classList.add("is-out");
    node.addEventListener("animationend", () => node.remove(), { once: true });
    setTimeout(() => node.remove(), 400);
  }, ms);
}
export const toastError = (e) => toast(e?.message || String(e), "error", 4200);

/* ---------------------------------------------------------------- 确认弹窗 */

/** 确认弹窗。typeToConfirm 非空时，必须输入该词才能确认（用于不可撤销的操作） */
export function confirmDialog({ title, body, confirmText = t("common.ok"), cancelText = t("common.cancel"), danger = false, typeToConfirm = "" }) {
  return new Promise((resolve) => {
    const node = el(html`<div class="modal-backdrop">
      <div class="modal" role="dialog" aria-modal="true">
        <div class="modal-body">
          <h3>${title}</h3>
          <p>${body}</p>
          ${typeToConfirm ? html`<input class="input confirm-input" data-confirm-input autocomplete="off" spellcheck="false" placeholder="${typeToConfirm}">
            <div class="confirm-hint">${th("common.typeToConfirm", { word: typeToConfirm })}</div>` : ""}
        </div>
        <div class="modal-actions">
          <button class="btn btn-secondary" data-r="0">${cancelText}</button>
          <button class="btn ${danger ? "btn-danger" : "btn-primary"}" data-r="1" ${typeToConfirm ? raw("disabled") : ""}>${confirmText}</button>
        </div>
      </div></div>`);
    const ok = node.querySelector('[data-r="1"]');
    const input = node.querySelector("[data-confirm-input]");
    const close = (v) => {
      document.removeEventListener("keydown", onKey);
      node.remove();
      resolve(v);
    };
    // Enter 只触发当前聚焦的按钮（默认聚焦"取消"），避免习惯性回车直接执行删除；
    // 需要输入确认词时，在输入框里回车才算确认
    const onKey = (e) => {
      if (e.key === "Escape") close(false);
      if (e.key === "Enter" && e.target === input && !ok.disabled && !e.isComposing) close(true);
    };
    input?.addEventListener("input", () => { ok.disabled = input.value.trim() !== typeToConfirm; });
    node.addEventListener("click", (e) => {
      if (e.target === node) return close(false);
      const b = e.target.closest("[data-r]");
      if (b && !b.disabled) close(b.dataset.r === "1");
    });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(node);
    (input || node.querySelector('[data-r="0"]')).focus({ focusVisible: false });
  });
}

/* ---------------------------------------------------------------- 信息气泡 */

let openPop = null;
/** 在 anchor 旁打开说明气泡（点击其他地方或 Esc 关闭）。content 为 SafeHtml */
export function popover(anchor, content) {
  closePopover();
  const node = el(html`<div class="popover" role="dialog">${content}</div>`);
  document.body.appendChild(node);
  const r = anchor.getBoundingClientRect();
  const w = node.offsetWidth, h = node.offsetHeight;
  let left = r.left, top = r.bottom + 8, ox = "left", oy = "top";
  if (left + w > innerWidth - 12) { left = r.right - w; ox = "right"; }
  if (top + h > innerHeight - 12) { top = r.top - h - 8; oy = "bottom"; }
  node.style.left = `${Math.max(12, left)}px`;
  node.style.top = `${Math.max(12, top)}px`;
  node.style.setProperty("--origin", `${oy} ${ox}`);
  const away = (e) => { if (!node.contains(e.target) && !anchor.contains(e.target)) closePopover(); };
  const key = (e) => { if (e.key === "Escape") closePopover(); };
  setTimeout(() => document.addEventListener("pointerdown", away), 0);
  document.addEventListener("keydown", key);
  openPop = { node, off: () => { document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", key); } };
}
export function closePopover() {
  if (!openPop) return;
  openPop.off();
  openPop.node.remove();
  openPop = null;
}

/* ---------------------------------------------------------------- 悬浮提示 */

const tip = () => document.getElementById("tooltip");
/** 显示提示：content 为 SafeHtml；(x, y) 为视口坐标，自动避让边缘 */
export function showTip(content, x, y) {
  const t = tip();
  render(t, content);
  t.hidden = false;
  const r = t.getBoundingClientRect();
  let left = x + 14;
  let top = y - r.height / 2;
  if (left + r.width > innerWidth - 8) left = x - r.width - 14;
  top = Math.max(8, Math.min(innerHeight - r.height - 8, top));
  t.style.left = `${left}px`;
  t.style.top = `${top}px`;
}
export const hideTip = () => { tip().hidden = true; };

// 触屏：提示由点按触发（图表与热力图的 pointerdown），下一次点按其他地方或滚动时关闭。
// 捕获阶段先关闭，目标元素自己的 pointerdown 随后会重新显示。
let touchTip = false;
document.addEventListener("pointerdown", (e) => { touchTip = e.pointerType !== "mouse"; if (touchTip) hideTip(); }, true);
document.addEventListener("scroll", () => { if (touchTip && !tip().hidden) hideTip(); }, true);

/* ---------------------------------------------------------------- 空状态插画 */

const ART = {
  mic: `<svg width="120" height="92" viewBox="0 0 120 92" fill="none">
    <circle cx="60" cy="46" r="40" fill="var(--accent-soft)"/>
    <circle cx="60" cy="46" r="28" fill="var(--accent-soft)"/>
    <rect x="52" y="24" width="16" height="28" rx="8" fill="var(--surface)" stroke="var(--accent)" stroke-width="2"/>
    <path d="M45 44a15 15 0 0 0 30 0M60 59v8M53 67h14" stroke="var(--accent)" stroke-width="2" stroke-linecap="round"/>
    <g stroke="var(--accent)" stroke-width="2" stroke-linecap="round" opacity=".55">
      <path d="M18 42v8M24 38v16M96 38v16M102 42v8"/></g></svg>`,
  report: `<svg width="120" height="92" viewBox="0 0 120 92" fill="none">
    <circle cx="60" cy="46" r="40" fill="var(--accent-soft)"/>
    <rect x="34" y="20" width="52" height="54" rx="8" fill="var(--surface)" stroke="var(--line-strong)" stroke-width="1.5"/>
    <path d="M43 60l9-10 8 6 16-17" stroke="var(--accent)" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="76" cy="39" r="3" fill="var(--accent)"/>
    <path d="M43 31h18" stroke="var(--line-strong)" stroke-width="2" stroke-linecap="round"/></svg>`,
  search: `<svg width="120" height="92" viewBox="0 0 120 92" fill="none">
    <circle cx="60" cy="46" r="40" fill="var(--accent-soft)"/>
    <circle cx="55" cy="42" r="17" fill="var(--surface)" stroke="var(--accent)" stroke-width="2.25"/>
    <path d="M67.5 54.5 79 66" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/>
    <path d="M48 42h14" stroke="var(--line-strong)" stroke-width="2" stroke-linecap="round"/></svg>`,
  practice: `<svg width="120" height="92" viewBox="0 0 120 92" fill="none">
    <circle cx="60" cy="46" r="40" fill="var(--accent-soft)"/>
    <rect x="30" y="26" width="60" height="40" rx="10" fill="var(--surface)" stroke="var(--line-strong)" stroke-width="1.5"/>
    <g stroke="var(--accent)" stroke-width="2.5" stroke-linecap="round"><path d="M46 42v8M53 37v18M60 40v12M67 34v24M74 42v8"/></g></svg>`,
  habit: `<svg width="120" height="92" viewBox="0 0 120 92" fill="none">
    <circle cx="60" cy="46" r="40" fill="var(--accent-soft)"/>
    <circle cx="60" cy="46" r="22" fill="var(--surface)" stroke="var(--line-strong)" stroke-width="1.5"/>
    <circle cx="60" cy="46" r="12" stroke="var(--accent)" stroke-width="2.25"/>
    <circle cx="60" cy="46" r="3" fill="var(--accent)"/></svg>`,
};

export function emptyState({ art = "mic", title, desc = "", action = null, compact = false }) {
  return html`<div class="empty ${compact ? "is-compact" : ""}">
    <div class="empty-art">${raw(ART[art] || ART.mic)}</div>
    <div class="empty-title">${title}</div>
    ${desc ? html`<div class="empty-desc">${desc}</div>` : ""}
    ${action || ""}
  </div>`;
}

/** 复制文本：优先走后端（剪贴板更可靠），失败时退回浏览器 API */
export async function copyText(text, call) {
  try {
    await call("copy_text", text);
  } catch {
    await navigator.clipboard?.writeText(text);
  }
  toast(t("common.copied"));
}

export { esc };
export { hotkeyName } from "./hotkey.js";
