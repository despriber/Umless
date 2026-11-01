// DOM 工具：安全的 HTML 模板（插值默认转义）与常用选择器。
// 约定：innerHTML 只接受 html`` 生成的 SafeHtml；任何用户文本都经过 esc() 转义。

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** 转义任意值为 HTML 文本 */
export function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
}

/** 已确认安全的 HTML 片段（只能由 html`` 或 raw() 生成） */
export class SafeHtml {
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}

/** 标记一段可信的 HTML（仅用于本地常量，如图标 SVG） */
export const raw = (s) => new SafeHtml(String(s));

function interp(v) {
  if (v instanceof SafeHtml) return v.s;
  if (Array.isArray(v)) return v.map(interp).join("");
  if (v === null || v === undefined || v === false || v === true) return "";
  return esc(v);
}

/** 标签模板：html`<b>${userText}</b>`，插值自动转义；数组会被拼接；false/null 忽略 */
export function html(strings, ...vals) {
  let out = strings[0];
  for (let i = 0; i < vals.length; i++) out += interp(vals[i]) + strings[i + 1];
  return new SafeHtml(out);
}

/** 把 SafeHtml 渲染进元素 */
export function render(el, content) {
  if (!(content instanceof SafeHtml)) throw new TypeError("render() 只接受 html`` 生成的内容");
  el.innerHTML = content.s;
  return el;
}

/** 由 SafeHtml 创建单个元素 */
export function el(content) {
  const t = document.createElement("template");
  render(t, content);
  return t.content.firstElementChild;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/** 事件委托：on(root, "click", "[data-x]", (e, target) => …) */
export function on(root, type, selector, handler, opts) {
  const fn = (e) => {
    const t = e.target.closest(selector);
    if (t && root.contains(t)) handler(e, t);
  };
  root.addEventListener(type, fn, opts);
  return () => root.removeEventListener(type, fn, opts);
}

/** 属性值（用于 data-* 等），同 esc */
export const attr = esc;
