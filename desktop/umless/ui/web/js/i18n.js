// 界面语言：t(key, vars) 取纯文本，th(key, vars) 取带少量标记（<b>、<kbd>）的可信 HTML。
// 文案字典在 locales/zh.js、locales/en.js，两份的 key 必须一致；值可以是字符串或 (vars) => 字符串。
// 平台专属文案：Android 上若存在 `<key>.android`（见 locales/android-*.js），t(key) 自动取它，
// 例如"按住热键说话"在 Android 上变成"长按空格键说话"，页面代码无需逐处判断平台。
import { SafeHtml, esc } from "./dom.js";
import { isAndroid } from "./platform.js";
import zh from "./locales/zh.js";
import en from "./locales/en.js";

const DICTS = { zh, en };
let current = "zh";

export function setLang(lang) {
  current = DICTS[lang] ? lang : "zh";
  document.documentElement.lang = current === "zh" ? "zh-CN" : "en";
  document.documentElement.dataset.lang = current;
}

export const lang = () => current;
export const isZh = () => current === "zh";

function lookup(key) {
  if (isAndroid()) {
    const alt = DICTS[current][`${key}.android`] ?? DICTS.zh[`${key}.android`];
    if (alt !== undefined) return alt;
  }
  const v = DICTS[current][key] ?? DICTS.zh[key];
  if (v === undefined) {
    console.warn(`缺少文案：${key}`);
    return key;
  }
  return v;
}

function fill(template, vars, escape) {
  return template.replace(/\{(\w+)\}/g, (m, k) => {
    if (!(k in vars)) return m;
    const v = vars[k];
    if (v instanceof SafeHtml) return v.s;
    return escape ? esc(v) : String(v ?? "");
  });
}

/** 纯文本文案（插入 html`` 时会被转义） */
export function t(key, vars = {}) {
  const v = lookup(key);
  const s = typeof v === "function" ? v(vars) : v;
  return fill(s, vars, false);
}

/** 带标记的文案：字典里的模板可信，插值变量一律转义（SafeHtml 除外） */
export function th(key, vars = {}) {
  const v = lookup(key);
  const s = typeof v === "function" ? v(vars) : v;
  return new SafeHtml(fill(s, vars, true));
}

/** 英文复数：plural(n, "record", "records") */
export const plural = (n, one, many) => (Number(n) === 1 ? one : many);

/** 检查两份字典的 key 是否一致（预览脚本与调试用） */
export function missingKeys() {
  const a = Object.keys(zh), b = Object.keys(en);
  return { en: a.filter((k) => !(k in en)), zh: b.filter((k) => !(k in zh)) };
}
