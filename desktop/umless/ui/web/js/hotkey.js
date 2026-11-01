// 热键：显示名（与 Python 端 hotkey.label 一致）、键帽展示、"按下新热键"录制控件。
// 热键写法：caps_lock / right_ctrl / f8，或组合 ctrl+space、ctrl+shift+f1；见 docs/ui-api.md。
import { call } from "./api.js";
import { html, raw } from "./dom.js";
import { icon } from "./icons.js";
import { t } from "./i18n.js";

export const DEFAULT_HOTKEY = "caps_lock";
/** 一键选用的常用热键 */
export const HOTKEY_PRESETS = ["caps_lock", "right_ctrl", "f8", "ctrl+space"];

const LABELS = {
  caps_lock: "CapsLock", scroll_lock: "ScrollLock", num_lock: "NumLock", pause: "Pause", print_screen: "PrtSc",
  insert: "Insert", delete: "Delete", home: "Home", end: "End", page_up: "PgUp", page_down: "PgDn",
  up: "↑", down: "↓", left: "←", right: "→", apps: "Menu", space: "Space", tab: "Tab", enter: "Enter",
  backspace: "Backspace", esc: "Esc", backquote: "`", minus: "-", equal: "=", bracket_left: "[",
  bracket_right: "]", backslash: "\\", semicolon: ";", quote: "'", comma: ",", period: ".", slash: "/",
  ctrl: "Ctrl", alt: "Alt", shift: "Shift", win: "Win",
};

/** 每个键的显示名，如 ["Ctrl", "Space"]、["右 Ctrl"] */
export function hotkeyParts(spec) {
  const parts = String(spec || DEFAULT_HOTKEY).toLowerCase().replace(/\s+/g, "").split("+").filter(Boolean);
  return parts.map((p) => {
    const side = p.match(/^(left|right)_(ctrl|alt|shift|win)$/);
    if (side) return `${t(`hotkey.${side[1]}`)}${LABELS[side[2]]}`;
    const num = p.match(/^num(\d)$/);
    if (num) return `Num ${num[1]}`;
    if (p.startsWith("vk_")) return p.toUpperCase().replace("VK_0X", "VK 0x");
    return LABELS[p] ?? p.toUpperCase();
  });
}

export const hotkeyName = (spec) => hotkeyParts(spec).join(" + ");

/** 一组键帽：<kbd>Ctrl</kbd>+<kbd>Space</kbd> */
export const hotkeyKbds = (spec) => html`<span class="hk-kbds">${hotkeyParts(spec).map((p, i) => html`${i ? html`<span class="hk-plus">+</span>` : ""}<kbd>${p}</kbd>`)}</span>`;

/**
 * 热键控件：显示当前热键、"更改"按钮和常用热键。recording 为真时显示"请按下新热键"。
 * 事件约定：[data-hk-record] 开始录制，[data-hk-cancel] 取消，[data-hk-preset] 选用常用热键。
 */
export function hotkeyPicker(spec, { recording = false, presets = true } = {}) {
  const norm = String(spec || DEFAULT_HOTKEY);
  return html`<div class="hk-picker ${recording ? "is-recording" : ""}">
    <div class="hk-current" aria-live="polite">
      ${recording
        ? html`<span class="hk-listening"><span class="rec-dot"></span>${t("hotkey.pressNew")}</span>`
        : hotkeyKbds(norm)}
      ${recording
        ? html`<button type="button" class="btn btn-ghost btn-sm" data-hk-cancel>${t("common.cancel")}</button>`
        : html`<button type="button" class="btn btn-secondary btn-sm" data-hk-record>${icon("keyboard", 14)}${t("hotkey.change")}</button>`}
    </div>
    ${presets && !recording ? html`<div class="hk-presets"><span class="muted small">${t("hotkey.presets")}</span>
      ${HOTKEY_PRESETS.map((p) => html`<button type="button" class="chip no-x ${p === norm ? "is-sel" : ""}" data-hk-preset="${p}" ${p === norm ? raw('aria-pressed="true"') : ""}>${hotkeyName(p)}</button>`)}
    </div>` : ""}
  </div>`;
}

/** 录制下一个热键：返回规范写法；Esc / 超时 / 取消返回 null；不合法时抛出（message 为后端提示） */
export async function captureHotkey() {
  return (await call("capture_hotkey")) || null;
}

export const cancelCapture = () => call("cancel_hotkey_capture").catch(() => {});
