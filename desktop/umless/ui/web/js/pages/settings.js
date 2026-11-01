import { t, isZh } from "../i18n.js";
// 设置：分组导航；修改即时保存（防抖合并为一次 save_settings），并给出轻量“已保存”反馈。
// 手机布局：#settings 显示分组列表，#settings/<分组> 显示详情（带返回）；宽屏两栏，未指定分组时显示第一个。
// Android：隐藏桌面专属设置（热键、常开麦克风、提示条、输出方式、开机自启、打开文件、Qwen3-ASR），新增「键盘」分组。
// 关于：「更新」分组（检查 / 下载 / 安装 / 跳过），状态与顶部提示条共用 js/update.js。
import { call, runJob } from "../api.js";
import { html, render, raw, $, $$, on } from "../dom.js";
import { icon, logo } from "../icons.js";
import { toggle, segmented, jobBox } from "../components.js";
import {
  SCENE_KEYS, SCENE_ICONS, INTENSITY_KEYS, sceneName, intensityName, hotkeyName, lexSource, debounce, toast, toastError, confirmDialog, fmtMs,
  relDateTime,
} from "../ui.js";
import { upd, subscribe, checkUpdate, downloadUpdate, installUpdate, skipUpdate, fmtSize } from "../update.js";
import { hotkeyPicker, captureHotkey, cancelCapture } from "../hotkey.js";
import { isAndroid } from "../platform.js";

/* ---------------------------------------------------------------- 小工具 */

const get = (obj, path) => path.split(".").reduce((o, k) => (o == null ? o : o[k]), obj);
function setIn(obj, path, value) {
  const ks = path.split(".");
  let o = obj;
  ks.slice(0, -1).forEach((k) => { if (typeof o[k] !== "object" || o[k] === null || Array.isArray(o[k])) o[k] = {}; o = o[k]; });
  o[ks[ks.length - 1]] = value;
}

const group = (title, desc, body, { flush = true, extra = "" } = {}) => html`<section class="set-group">
  <div class="set-group-head"><div><h3>${title}</h3>${desc ? html`<p>${desc}</p>` : ""}</div>${extra}</div>
  <div class="card ${flush ? "is-flush set-card" : ""}">${body}</div>
</section>`;

const row = (label, desc, control, { id = "", cls = "" } = {}) => html`<div class="set-row ${cls}" ${id ? raw(`id="${id}"`) : ""}>
  <div class="set-text"><div class="set-label">${label}</div>${desc ? html`<div class="set-desc">${desc}</div>` : ""}</div>
  <div class="set-ctl">${control}</div>
</div>`;

const select = (path, options, value, attrs = "") => html`<select class="select set-select" data-path="${path}" ${raw(attrs)}>
  ${options.map(([v, l]) => html`<option value="${v}" ${String(v) === String(value) ? raw("selected") : ""}>${l}</option>`)}
</select>`;

/** 发布时间（UTC 的 ISO 字符串）→ 2026年11月5日 / Nov 5, 2026 */
function fmtPublished(s) {
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return String(s).slice(0, 10);
  return isZh() ? `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`
    : d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

const secret = (path, value, placeholder = "sk-…") => html`<div class="input-group">
  <input class="input is-mono" type="password" data-path="${path}" value="${value || ""}" placeholder="${placeholder}" autocomplete="off" spellcheck="false">
  <button class="btn btn-ghost btn-icon btn-sm trail" data-reveal aria-label="${t("settings.copy31")}">${icon("eye", 15)}</button>
</div>`;

/* ---------------------------------------------------------------- 页面 */

export async function mount(root, ctx) {
  const android = isAndroid();
  const SECTIONS = [
    ...(android ? [["keyboard", t("settings.keyboard"), "keyboard"]] : []),
    ["general", t("settings.copy1"), "settings"],
    // 提示音只在桌面端：Android 上开始 / 结束录音用震动反馈（键盘设置里的「震动」）
    ...(android ? [] : [["sounds", t("settings.sounds"), "volume"]]),
    ["asr", t("settings.copy2"), "wave"],
    ["polish", t("settings.copy3"), "wand"],
    ["scenes", t("settings.copy4"), "layers"],
    ["lexicon", t("settings.copy5"), "book"],
    ["coach", t("settings.copy6"), "sparkles"],
    ["privacy", t("settings.copy7"), "shield"],
    ["about", t("settings.copy8"), "info"],
  ];

  const ENGINES = [
    { id: "sensevoice", name: "SenseVoice", sub: t("settings.copy9"), desc: t("settings.copy10"), ico: "zap", local: true },
    { id: "qwen3_asr", name: "Qwen3-ASR", sub: t("settings.copy11"), desc: t("settings.copy12"), ico: "cpu", local: true },
    { id: "openai", name: t("settings.copy13"), sub: t("settings.copy14"), desc: t("settings.copy15"), ico: "cloud", local: false },
  ].filter((e) => !(android && e.id === "qwen3_asr")); // 手机上 Qwen3-ASR 太大

  const INTENSITY_INFO = {
    faithful: {
      desc: t("settings.copy16"),
      before: t("settings.copy17"),
      after: t("settings.copy18"),
    },
    moderate: {
      desc: t("settings.copy19"),
      before: t("settings.copy20"),
      after: t("settings.copy21"),
    },
    restructure: {
      desc: t("settings.copy22"),
      before: t("settings.copy23"),
      after: t("settings.copy24"),
    },
  };

  const RETENTION = [[0, t("settings.copy25")], [7, t("settings.copy26")], [30, t("settings.copy27")], [90, t("settings.copy28")], [180, t("settings.copy29")], [365, t("settings.copy30")]];


  const SCENES = Object.fromEntries(SCENE_KEYS.map(k => [k, sceneName(k)]));
  const INTENSITY = Object.fromEntries(INTENSITY_KEYS.map(k => [k, intensityName(k)]));
  const st = {
    section: "general",
    s: null, // get_settings()
    lexicon: [],
    sounds: { events: [], presets: [] },
    lexFilter: "all",
    pending: {},
    saving: false,
    rulesDirty: false,
    downloads: {},
    models: null, // 获取到的模型列表
    test: null, // test_llm 结果
    testing: false,
    hkRecording: false, // 正在录制新热键
    ime: ctx.app.ime, // Android：键盘状态 {enabled, selected}
    imeTimer: null,
    abort: new AbortController(),
  };

  try {
    [st.s, st.lexicon, st.sounds] = await Promise.all([call("get_settings"), call("get_lexicon"), call("list_sounds")]);
  } catch (e) {
    toastError(e);
    return;
  }
  if (!root.isConnected) { st.abort.abort(); return; }
  const cfg = () => st.s.config;

  render(root, html`
    <header class="page-head set-head">
      <div class="set-head-list"><h1 class="page-title">${t("settings.copy32")}</h1><p class="page-sub">${t("settings.copy33")}</p></div>
      <div class="set-head-detail"><a class="set-back" href="#settings">${icon("chevronLeft", 18)}<span>${t("settings.copy32")}</span></a><div class="set-detail-title" id="set-title"></div></div>
      <div class="page-actions"><span class="saved" id="saved" aria-live="polite"></span></div>
    </header>
    <div class="set-layout">
      <nav class="set-nav" id="set-nav"></nav>
      <div class="set-main" id="set-main" data-tour="settings-content"></div>
    </div>`);
  const navEl = $("#set-nav", root);
  const titleEl = $("#set-title", root);
  const mainEl = $("#set-main", root);
  const savedEl = $("#saved", root);

  /* ------------------------------------------------------------ 保存 */

  let savedTimer;
  function showSaved(text, ok = true) {
    clearTimeout(savedTimer);
    render(savedEl, html`${ok ? icon("check", 14) : html`<span class="spinner"></span>`}${text}`);
    savedEl.classList.add("is-on");
    if (ok) savedTimer = setTimeout(() => savedEl.classList.remove("is-on"), 1800);
  }

  let saveChain = Promise.resolve(true);
  function persistPending() {
    const patch = st.pending;
    st.pending = {};
    if (!Object.keys(patch).length) return saveChain;
    saveChain = saveChain.then(async () => {
      showSaved(t("settings.copy34"), false);
      try {
        const res = await call("save_settings", patch);
        st.s.models = res.models;
        st.s.providers = res.providers;
        st.s.autostart = res.autostart;
        if (patch.hotkey) await ctx.app.refreshInfo();
        if (!st.abort.signal.aborted) showSaved(t("settings.copy35"));
        return true;
      } catch (e) {
        savedEl.classList.remove("is-on");
        toastError(e);
        return false;
      }
    });
    return saveChain;
  }
  const flush = debounce(persistPending, 450);

  function queue(path, value) {
    setIn(cfg(), path, value);
    setIn(st.pending, path, value);
    flush();
  }

  /* ------------------------------------------------------------ 各分组 */

  function general() {
    const c = cfg();
    if (android) {
      return group(t("settings.copy49"), "", html`
        ${row(t("settings.language"), t("settings.languageHelp"), languageSelect(c))}
        ${row(t("settings.copy50"), "", themeSeg(c))}`);
    }
    return html`
      ${group(t("settings.copy36"), "", html`
        ${row(t("settings.copy37"), html`${t("settings.copy38")}${c.hotkey.key === "caps_lock" ? t("settings.copy39") : ""}<br>${t("settings.copy40")}`,
          hotkeyPicker(c.hotkey.key, { recording: st.hkRecording }), { cls: "is-hotkey" })}
        ${row(t("settings.holdMs"), t("settings.holdMsHelp"), html`<div class="stepper" data-stepper="hotkey.min_hold_ms" data-min="100" data-max="1500">
          <button data-step="-50" aria-label="${t("settings.copy68")}">${icon("minus", 14)}</button><span class="val">${c.hotkey.min_hold_ms} ms</span><button data-step="50" aria-label="${t("settings.copy69")}">${icon("plus", 14)}</button></div>`)}
        ${row(t("settings.copy42"), t("settings.copy43"), toggle("audio.mic_always_on", c.audio.mic_always_on))}
        ${row(t("settings.copy44"), t("settings.copy45"), toggle("ui.overlay", c.ui.overlay))}
        ${row(t("settings.copy46"), "", segmented("output.mode", [["paste", t("settings.copy47")], ["clipboard", t("settings.copy48")]], c.output.mode))}
        ${row(t("settings.keepClipboard"), t("settings.keepClipboardHelp"), html`<label class="switch"><input type="checkbox" data-keep-clipboard ${!c.output.restore_clipboard ? raw("checked") : ""} aria-label="${t("settings.keepClipboard")}"><span class="track"></span></label>`)}
      `)}
      ${group(t("settings.copy49"), "", html`
        ${row(t("settings.language"), t("settings.languageHelp"), languageSelect(c))}
        ${row(t("settings.copy50"), "", themeSeg(c))}
        ${row(t("settings.copy54"), t("settings.copy55"), html`<label class="switch"><input type="checkbox" data-autostart ${st.s.autostart ? raw("checked") : ""}><span class="track"></span></label>`)}
      `)}`;
  }

  function languageSelect(c) {
    return html`<select class="select set-select" data-language aria-label="${t("settings.language")}"><option value="auto" ${c.app.language === "auto" ? raw("selected") : ""}>${t("settings.autoLanguage")}</option><option value="zh" ${c.app.language === "zh" ? raw("selected") : ""}>简体中文</option><option value="en" ${c.app.language === "en" ? raw("selected") : ""}>English</option></select>`;
  }
  const themeSeg = (c) => segmented("app.theme", [["system", t("settings.copy51"), "monitor"], ["light", t("settings.copy52"), "sun"], ["dark", t("settings.copy53"), "moon"]], c.app.theme);

  /** Android：键盘是否启用、是否为当前输入法 */
  function imeRows() {
    const ime = st.ime;
    const ok = (label) => html`<span class="tag is-accent">${icon("check", 13)}${label}</span>`;
    return html`
      ${row(t("settings.kb.enabled"), t("settings.kb.enabledHelp"), ime?.enabled ? ok(t("settings.kb.on"))
        : html`<button class="btn btn-primary btn-sm" data-ime-settings>${icon("external", 14)}${t("ime.enable")}</button>`)}
      ${row(t("settings.kb.selected"), t("settings.kb.selectedHelp"), ime?.selected ? ok(t("settings.kb.current"))
        : html`<button class="btn btn-secondary btn-sm" data-ime-picker ${ime?.enabled ? "" : raw("disabled")}>${icon("keyboard", 14)}${t("ime.switch")}</button>`)}`;
  }

  /** Android「键盘」分组：状态 + 语音入口与反馈 */
  function keyboard() {
    const v = cfg().voice || {};
    return html`
      ${group(t("settings.kb.title"), t("settings.kb.help"), html`<div id="ime-rows">${imeRows()}</div>`)}
      ${group(t("settings.kb.voice"), t("settings.kb.voiceHelp"), html`
        ${row(t("settings.kb.space"), t("settings.kb.spaceHelp"), toggle("voice.space_long_press", v.space_long_press ?? true))}
        ${row(t("settings.kb.toolbar"), t("settings.kb.toolbarHelp"), toggle("voice.toolbar_button", v.toolbar_button ?? true))}
        ${row(t("settings.kb.haptic"), t("settings.kb.hapticHelp"), toggle("voice.haptic", v.haptic ?? true))}
        <div class="set-row set-note">${icon("hand", 16)}<span><b>${t("settings.kb.cancelTitle")}</b>${t("settings.kb.cancelHelp")}</span></div>
      `)}`;
  }

  function soundSettings() {
    const c = cfg().audio;
    return html`<div data-tour="settings-sounds">${group(t("settings.sounds"), t("settings.soundsHelp"), html`
      ${row(t("settings.soundEnabled"), "", toggle("audio.beep", c.beep))}
      ${row(t("settings.volume"), "", html`<input type="range" min="0" max="100" step="5" data-volume value="${c.volume ?? 60}" aria-label="${t("settings.volume")}" ${!c.beep ? raw("disabled") : ""}><output data-volume-value>${c.volume ?? 60}%</output>`)}
      ${st.sounds.events.map(event => {
        const value = c.sounds?.[event] ?? "";
        const custom = value && !st.sounds.presets.includes(value);
        return row(t(`settings.soundEvent.${event}`), custom ? value : "", html`<div class="sound-controls">
          <select class="select set-select" data-sound="${event}" aria-label="${t(`settings.soundEvent.${event}`)}" ${!c.beep ? raw("disabled") : ""}>
            <option value="" ${!value ? raw("selected") : ""}>${t("settings.silent")}</option>
            ${st.sounds.presets.map(preset => html`<option value="${preset}" ${value === preset ? raw("selected") : ""}>${t(`settings.soundPreset.${preset}`)}</option>`)}
            ${custom ? html`<option value="${value}" selected>${t("settings.customSound")}</option>` : ""}
          </select>
          <button class="btn btn-secondary btn-sm" data-sound-file="${event}" ${!c.beep ? raw("disabled") : ""}>${icon("folder", 14)}${t("settings.chooseSound")}</button>
          <button class="btn btn-ghost btn-icon btn-sm" data-sound-preview="${event}" aria-label="${t("settings.previewSound")}" title="${t("settings.previewSound")}" ${!c.beep || !value ? raw("disabled") : ""}>${icon("play", 14)}</button>
        </div>`);
      })}
    `)}</div>`;
  }

  function engineCard(e) {
    const c = cfg();
    const m = st.s.models[e.id];
    const active = c.asr.engine === e.id;
    const dl = st.downloads[e.id];
    let status;
    if (!e.local) status = html`<span class="eng-status">${icon("cloud", 13)}${t("settings.copy56")}</span>`;
    else if (dl) status = html`<div class="eng-dl">${jobBox(dl.message, dl.progress)}</div>`;
    else if (m?.downloaded) status = html`<span class="eng-status is-ok">${icon("checkCircle", 14)}${t("settings.copy57")} ${m.size_mb} MB</span>`;
    else status = html`<button class="btn btn-soft btn-sm" data-download="${e.id}">${icon("download", 14)}${t("settings.copy58")} ${m?.size_mb ?? "—"} MB</button>`;
    return html`<div class="engine ${active ? "is-active" : ""}" data-engine="${e.id}" role="radio" aria-checked="${active}" tabindex="0">
      <div class="eng-top">
        <span class="eng-ico">${icon(e.ico, 18)}</span>
        <span class="eng-radio">${active ? icon("check", 12) : ""}</span>
      </div>
      <div class="eng-name">${e.name}</div>
      <div class="eng-sub">${e.sub}</div>
      <p class="eng-desc">${e.desc}</p>
      <div class="eng-foot">${status}</div>
    </div>`;
  }

  function asr() {
    const c = cfg();
    return html`
      <section class="set-group">
        <div class="set-group-head"><div><h3>${t("settings.copy59")}</h3><p>${t("settings.copy60")}</p></div></div>
        <div class="engines ${ENGINES.length === 2 ? "is-two" : ""}" id="engines">${ENGINES.map(engineCard)}</div>
      </section>
      ${c.asr.engine === "openai" ? group(t("settings.copy61"), t("settings.copy62"), html`<div class="form">
        <label class="field"><span class="field-label">${t("settings.copy63")}</span><input class="input is-mono" data-path="asr.openai.base_url" value="${c.asr.openai.base_url}" spellcheck="false"></label>
        <label class="field"><span class="field-label">API Key</span>${secret("asr.openai.api_key", c.asr.openai.api_key)}</label>
        <label class="field"><span class="field-label">${t("settings.copy64")}</span><input class="input is-mono" data-path="asr.openai.model" value="${c.asr.openai.model}" spellcheck="false"></label>
      </div>`, { flush: false }) : ""}
      ${group(t("settings.copy65"), "", html`
        ${row(t("settings.copy66"), t("settings.copy67"), html`<div class="stepper" data-stepper="asr.num_threads" data-min="1" data-max="16">
          <button data-step="-1" aria-label="${t("settings.copy68")}">${icon("minus", 14)}</button><span class="val">${c.asr.num_threads}</span><button data-step="1" aria-label="${t("settings.copy69")}">${icon("plus", 14)}</button></div>`)}
      `)}`;
  }

  function providerOf(url) {
    const p = st.s.providers.find((x) => x.base_url.replace(/\/$/, "") === String(url || "").replace(/\/$/, ""));
    return p ? p.id : "custom";
  }

  function testResult() {
    const result = st.test;
    if (st.testing) return html`<div class="test-box is-pending"><span class="spinner"></span>${t("settings.copy70")}</div>`;
    if (!result) return html``;
    if (!result.ok) return html`<div class="test-box is-error">${icon("alert", 16)}<div><b>${t("settings.copy71")}</b><p>${result.error || t("settings.copy72")}</p></div></div>`;
    return html`<div class="test-box is-ok">
      <div class="test-head">${icon("checkCircle", 16)}<b>${t("settings.copy73")}</b><span class="tag is-sm is-accent num">${fmtMs(result.latency_ms)}</span></div>
      <div class="test-io">
        <div><span class="mini-title">${t("settings.copy74")}</span><p class="selectable">${result.input}</p></div>
        ${icon("arrowRight", 16, "test-arrow")}
        <div><span class="mini-title">${t("settings.copy75")}</span><p class="selectable is-out">${result.output}</p></div>
      </div>
    </div>`;
  }

  function polish() {
    const c = cfg();
    const pid = providerOf(c.llm.base_url);
    const intensity = c.polish.intensity;
    const info = INTENSITY_INFO[intensity];
    return html`
      ${group(t("settings.copy76"), t("settings.copy77"), html`
        ${row(t("settings.copy78"), t("settings.copy79"), toggle("llm.enabled", c.llm.enabled))}
        <div class="form set-form ${c.llm.enabled ? "" : "is-disabled"}">
          <div class="form-row">
            <label class="field"><span class="field-label">${t("settings.copy80")}</span>
              <select class="select" data-provider>
                ${st.s.providers.map((p) => html`<option value="${p.id}" ${p.id === pid ? raw("selected") : ""}>${p.name}</option>`)}
                <option value="custom" ${pid === "custom" ? raw("selected") : ""}>${t("settings.copy81")}</option>
              </select></label>
            <label class="field"><span class="field-label">${t("settings.copy64")}</span>
              <div class="input-group"><input class="input is-mono" data-path="llm.model" id="llm-model" value="${c.llm.model}" spellcheck="false" placeholder="${t("settings.copy82")}">
              <button class="btn btn-ghost btn-sm trail" data-fetch-models title="${t("settings.copy83")}">${icon("refresh", 14)}${t("settings.copy84")}</button></div></label>
          </div>
          ${st.models ? html`<div class="model-chips"><span class="muted small">${t("settings.copy85")}</span>${st.models.map((m) => html`<button class="chip no-x model-chip ${m === c.llm.model ? "is-sel" : ""}" data-model="${m}">${m}</button>`)}</div>` : ""}
          <label class="field"><span class="field-label">${t("settings.copy63")}</span><input class="input is-mono" data-path="llm.base_url" id="llm-url" value="${c.llm.base_url}" spellcheck="false" placeholder="https://…/v1"></label>
          <label class="field"><span class="field-label">API Key</span>${secret("llm.api_key", c.llm.api_key, pid === "ollama" ? t("settings.copy86") : "sk-…")}
            <span class="field-hint">${t("settings.copy87")}</span></label>
          <div class="test-row">
            <button class="btn btn-secondary" data-test ${st.testing ? raw("disabled") : ""}>${icon("zap", 15)}${t("settings.copy88")}</button>
            <span class="muted small">${t("settings.copy89")}</span>
          </div>
          <div id="test-result">${testResult()}</div>
        </div>
      `)}
      <section class="set-group">
        <div class="set-group-head"><div><h3>${t("settings.copy90")}</h3><p>${t("settings.copy91")}</p></div></div>
        <div class="card intensity-card">
          ${segmented("polish.intensity", Object.entries(INTENSITY).map(([k, v]) => [k, v]), intensity, { cls: "is-lg is-block" })}
          <p class="int-desc">${info.desc}</p>
          <div class="int-example">
            <div class="int-side"><span class="mini-title">${t("settings.copy92")}</span><p>${info.before}</p></div>
            <span class="int-arrow">${icon("arrowRight", 16)}</span>
            <div class="int-side is-after"><span class="mini-title">${icon("sparkles", 12)}${t("settings.copy93")}</span><p>${info.after}</p></div>
          </div>
          <p class="int-note">${icon("shield", 14)}${t("settings.copy94")}</p>
        </div>
      </section>`;
  }

  function scenes() {
    const c = cfg();
    const on_ = c.scenes.enabled;
    const glob = INTENSITY[c.polish.intensity];
    const styles = { chat: t("settings.copy95"), document: t("settings.copy96"), code: t("settings.copy97"), email: t("settings.copy98"), default: t("settings.copy1") };
    return html`
      ${group(t("settings.copy99"), "", row(t("settings.copy100"), t("settings.copy101"), toggle("scenes.enabled", on_)))}
      ${group(t("settings.copy102"), t("settings.copy103"), html`${Object.keys(SCENES).map((k) => row(
        html`<span class="scene-label"><span class="scene-ico scene-ico-${k}">${icon(SCENE_ICONS[k], 15)}</span>${SCENES[k]}</span>`,
        styles[k],
        select(`scenes.intensity.${k}`, [["", t("settings.globalStrength", { strength: glob })], ["faithful", t("settings.copy105")], ["moderate", t("settings.copy106")], ["restructure", t("settings.copy107")]], c.scenes.intensity[k] ?? "", on_ ? "" : "disabled"),
      ))}`)}
      ${group(t("settings.copy108"), t("settings.copy109"), html`
        <div class="rules ${on_ ? "" : "is-disabled"}" id="rules">
          <div class="rule rule-head"><span>${t("settings.copy110")}</span><span>${t("settings.copy4")}</span><span></span></div>
          ${c.scenes.rules.map((r, i) => html`<div class="rule" data-rule="${i}">
            <input class="input input-sm is-mono" data-rule-app value="${r.app}" placeholder="${t("settings.copy111")}" spellcheck="false">
            <select class="select input-sm" data-rule-scene>${Object.entries(SCENES).map(([k, v]) => html`<option value="${k}" ${k === r.scene ? raw("selected") : ""}>${v}</option>`)}</select>
            <button class="btn btn-ghost btn-icon btn-sm" data-rule-del aria-label="${t("settings.copy112")}">${icon("trash", 15)}</button>
          </div>`)}
          <div class="rule-add"><button class="btn btn-ghost btn-sm" data-rule-add>${icon("plus", 14)}${t("settings.copy113")}</button></div>
        </div>`)}`;
  }

  function lexicon() {
    const c = cfg();
    const counts = { all: st.lexicon.length };
    st.lexicon.forEach((w) => { counts[w.source] = (counts[w.source] || 0) + 1; });
    const shown = st.lexicon.filter((w) => st.lexFilter === "all" || w.source === st.lexFilter);
    return html`
      ${group(t("settings.copy114"), t("settings.copy115"), html`<div class="chips" id="terms">
        ${c.glossary.terms.map((term) => html`<span class="chip"><span>${term}</span><button class="chip-x" data-term-del="${term}" aria-label="${t("settings.copy112")}">${icon("x", 12)}</button></span>`)}
        <span class="chip-adder"><input class="input" data-term-add placeholder="${t("settings.copy116")}" spellcheck="false"></span>
      </div>`, { flush: false })}
      ${group(t("settings.copy117"), t("settings.copy118"), html`
        <div class="lex-tools">${segmented("lexFilter", [["all", `${t("settings.copy119")} ${counts.all}`], ["builtin", `${t("settings.copy120")} ${counts.builtin || 0}`], ["llm", `${t("settings.copy121")} ${counts.llm || 0}`], ["user", `${t("settings.copy81")} ${counts.user || 0}`]], st.lexFilter)}</div>
        <div class="chips">
          ${shown.map((w) => html`<span class="chip ${w.enabled ? "" : "is-off"} ${w.source === "builtin" ? "no-x" : ""}" title="${w.enabled ? t("settings.copy122") : t("settings.copy123")}">
            <span class="chip-toggle" data-lex-toggle="${w.word}"><span class="chip-word">${w.word}</span><span class="chip-src src-${w.source}">${lexSource(w.source)}</span></span>
            ${w.source === "builtin" ? "" : html`<button class="chip-x" data-lex-del="${w.word}" aria-label="${t("settings.copy112")}">${icon("x", 12)}</button>`}
          </span>`)}
          <span class="chip-adder"><input class="input" data-lex-add placeholder="${t("settings.copy124")}" spellcheck="false"></span>
        </div>`, { flush: false })}`;
  }

  function coach() {
    const c = cfg();
    const dis = c.coach.enabled ? {} : { disabled: true };
    return html`
      ${group(t("settings.copy125"), "", html`
        ${row(t("settings.copy126"), t("settings.copy127"), toggle("coach.enabled", c.coach.enabled))}
        ${row(t("settings.copy128"), t("settings.copy129"), toggle("coach.auto_daily", c.coach.auto_daily, dis))}
        ${row(t("settings.copy130"), t("settings.copy131"), html`<div class="stepper" data-stepper="coach.min_utterances" data-min="3" data-max="50">
          <button data-step="-1" aria-label="${t("settings.copy68")}">${icon("minus", 14)}</button><span class="val">${c.coach.min_utterances}</span><button data-step="1" aria-label="${t("settings.copy69")}">${icon("plus", 14)}</button></div>`)}
        ${row(t("settings.copy132"), t("settings.copy133"),
          html`<input class="input is-mono set-input" data-path="coach.model" value="${c.coach.model}" placeholder="${c.llm.model || t("settings.copy134")}" spellcheck="false">`)}
      `)}`;
  }

  function privacy() {
    const c = cfg();
    return html`
      ${group(t("settings.copy135"), t("settings.copy136"), html`
        ${row(t("settings.copy137"), t("settings.copy138"), toggle("privacy.record_history", c.privacy.record_history))}
        ${row(t("settings.copy139"), t("settings.copy140"), select("privacy.retention_days", RETENTION, c.privacy.retention_days))}
        ${android ? html`
          ${row(t("settings.incognito"), t("settings.incognitoHelp"), toggle("privacy.skip_incognito", c.privacy.skip_incognito ?? true))}
          <div class="set-row set-note">${icon("shield", 16)}<span>${t("settings.passwordNote")}</span></div>`
        : row(t("settings.copy141"), t("settings.copy142"), toggle("audio.keep_audio", c.audio.keep_audio))}
      `)}
      ${group(t("settings.copy143"), t("settings.copy144"), html`<div class="chips">
        ${c.privacy.exclude_apps.map((a) => html`<span class="chip"><span class="mono">${a}</span><button class="chip-x" data-ex-del="${a}" aria-label="${t("settings.copy112")}">${icon("x", 12)}</button></span>`)}
        <span class="chip-adder"><input class="input is-mono" data-ex-add placeholder="${t("settings.copy145")}" spellcheck="false"></span>
      </div>`, { flush: false })}
      ${group(t("settings.copy146"), "", html`
        ${row(t("settings.copy147"), t("settings.copy148"), html`<button class="btn btn-secondary" data-export>${icon("export", 15)}${t("settings.copy149")}</button>`)}
        ${row(html`<span class="danger-text">${t("settings.copy150")}</span>`, t("settings.copy151"), html`<button class="btn btn-danger-ghost" data-clear>${icon("trash", 15)}${t("settings.copy152")}</button>`)}
      `)}`;
  }

  /** 「更新」分组的状态行：检查中 / 已是最新 / 检查失败 / 发现新版本 */
  function updateStatus() {
    const u = upd.info;
    const failed = upd.checkFailed || u?.error;
    const checked = u?.checked_at ? t("update.checkedAt", { time: relDateTime(u.checked_at) }) : t("update.neverChecked");
    let ico, label, desc, cls = "";
    if (upd.checking) [ico, label, desc] = [html`<span class="spinner"></span>`, t("update.checking"), t("update.checkingHelp")];
    else if (failed) [ico, label, desc, cls] = [icon("alert", 18), t("update.failed"), failed, "is-error"];
    else if (u?.available) {
      [ico, label, cls] = [icon("download", 18), t("update.available", { version: u.latest }), "is-new"];
      desc = [u.published_at ? t("update.published", { date: fmtPublished(u.published_at) }) : "", fmtSize(u.size)].filter(Boolean).join(" · ");
    } else if (u?.latest || u?.checked_at) [ico, label, desc, cls] = [icon("checkCircle", 18), t("update.upToDate"), checked, "is-ok"];
    else [ico, label, desc] = [icon("refresh", 18), t("update.check"), t("update.neverChecked")];
    const btnLabel = failed && !upd.checking ? t("common.retry") : t("update.check");
    return html`<div class="set-row upd-status ${cls}" id="upd-status">
      <div class="upd-ico">${ico}</div>
      <div class="set-text grow"><div class="set-label">${label}${u?.available && u.skipped ? html` <span class="tag is-sm">${t("update.skippedTag")}</span>` : ""}</div>
        ${desc ? html`<div class="set-desc">${desc}</div>` : ""}</div>
      <div class="set-ctl"><button class="btn btn-secondary btn-sm ${upd.checking ? "is-loading" : ""}" data-update-check aria-label="${btnLabel}" title="${btnLabel}" ${upd.checking ? raw("disabled") : ""}>${icon("refresh", 14)}${btnLabel}</button></div>
    </div>`;
  }

  /** 发现新版本时：更新说明 + 下载 / 安装 / 跳过 */
  function updatePanel() {
    const u = upd.info;
    if (!u?.available || upd.checking || upd.checkFailed || u.error) return "";
    const skip = u.skipped ? "" : html`<button class="btn btn-ghost btn-sm" data-update-skip>${t("update.skip")}</button>`;
    let actions;
    if (!u.can_install) {
      actions = html`<div class="upd-note">${icon("info", 16)}<span>${u.install_hint || t("update.cannotInstall")}</span></div>
        <div class="upd-actions">${skip}</div>`;
    } else if (upd.download) {
      actions = html`<div class="upd-job">${jobBox(upd.download.message, upd.download.progress)}</div>`;
    } else if (upd.downloadError) {
      actions = html`<div class="test-box is-error">${icon("alert", 16)}<div><b>${t(upd.errorFrom === "install" ? "update.installFailed" : "update.downloadFailed")}</b><p>${upd.downloadError}</p></div></div>
        <div class="upd-actions"><button class="btn btn-primary btn-sm" data-update-download>${icon("refresh", 14)}${t("update.redownload")}</button>${skip}</div>`;
    } else if (u.downloaded) {
      const st_ = upd.installStatus;
      const note = st_ === "needs_permission" ? html`<div class="upd-note is-warn">${icon("shield", 16)}<span>${t("update.needsPermission")}</span></div>`
        : st_ === "permission_back" ? html`<div class="upd-note is-warn">${icon("shield", 16)}<span>${t("update.permissionBack")}</span></div>`
        : st_ === "installing" ? html`<div class="upd-note">${icon("info", 16)}<span>${t("update.installingInline")}</span></div>`
        : st_ === "unsupported" ? html`<div class="upd-note">${icon("info", 16)}<span>${u.install_hint || t("update.cannotInstall")}</span></div>`
        : html`<div class="upd-note is-ok">${icon("checkCircle", 16)}<span>${t("update.readyToInstall")}</span></div>`;
      actions = html`${note}<div class="upd-actions">
        <button class="btn btn-primary btn-sm ${upd.installing ? "is-loading" : ""}" data-update-install ${upd.installing ? raw("disabled") : ""}>${icon("download", 14)}${t("update.install")}</button>${skip}</div>`;
    } else {
      actions = html`<div class="upd-actions">
        <button class="btn btn-primary btn-sm" data-update-download>${icon("download", 14)}${t("update.download")}${u.size ? html` <span class="upd-size num">${fmtSize(u.size)}</span>` : ""}</button>${skip}</div>`;
    }
    return html`<div class="upd-panel">
      ${u.notes ? html`<div class="upd-notes"><div class="mini-title">${t("update.notes")}</div><p class="selectable">${u.notes}</p></div>` : ""}
      ${actions}
    </div>`;
  }

  function updateGroup() {
    const c = cfg().update || {};
    return group(t("update.title"), "", html`
      <div id="upd-box">${updateStatus()}${updatePanel()}</div>
      ${row(t("update.autoCheck"), t("update.autoCheckHelp"), toggle("update.auto_check", c.auto_check ?? true))}`);
  }

  function about() {
    const info = ctx.app.info || {};
    return html`
      <div class="card about-hero">
        ${logo(56, "about-logo")}
        <div>
          <div class="about-name">Umless <span class="tag is-sm num">v${info.version || "—"}</span></div>
          <p class="about-tag">${t("settings.copy153")}</p>
        </div>
      </div>
      ${updateGroup()}
      ${android ? group(t("settings.diagnostics"), "", row(t("settings.exportLogs"), t("settings.exportLogsHelp"),
        html`<button class="btn btn-secondary btn-sm" data-export-logs>${icon("export", 14)}${t("settings.copy149")}</button>`))
      : group(t("settings.copy154"), "", html`
        ${row(t("settings.copy155"), html`<span class="mono path selectable">${info.data_dir || "—"}</span>`, html`<button class="btn btn-secondary btn-sm" data-open="data_dir">${icon("folder", 14)}${t("settings.copy156")}</button>`)}
        ${row(t("settings.copy157"), t("settings.copy158"), html`<button class="btn btn-secondary btn-sm" data-open="config">${icon("file", 14)}${t("settings.copy156")}</button>`)}
        ${row(t("settings.copy159"), t("settings.copy160"), html`<button class="btn btn-secondary btn-sm" data-open="log">${icon("doc", 14)}${t("settings.copy156")}</button>`)}
      `)}
      ${group(t("settings.tour"), t("settings.tourHelp"), row(t("settings.tour"), "", html`<button class="btn btn-secondary btn-sm" data-tour-start>${icon("play", 14)}${t("settings.startTour")}</button>`))}
      ${group(t("settings.copy161"), "", row(t("settings.copy162"), t("settings.copy163"), html`<a class="btn btn-secondary btn-sm" href="#onboarding">${icon("play", 14)}${t("settings.copy164")}</a>`))}`;
  }

  const RENDERERS = { keyboard, general, sounds: soundSettings, asr, polish, scenes, lexicon, coach, privacy, about };

  function renderNav() {
    render(navEl, html`${SECTIONS.map(([k, label, ico]) => html`<a class="set-nav-item ${k === st.section ? "is-active" : ""}" href="#settings/${k}">${icon(ico, 16)}<span class="grow">${label}</span>${icon("chevronRight", 16, "set-nav-chev")}</a>`)}`);
  }
  function renderMain() {
    titleEl.textContent = SECTIONS.find(([k]) => k === st.section)?.[1] || "";
    render(mainEl, RENDERERS[st.section]());
    mainEl.classList.remove("is-in");
    void mainEl.offsetWidth;
    mainEl.classList.add("is-in");
  }
  const rerender = () => { const y = document.getElementById("main").scrollTop; render(mainEl, RENDERERS[st.section]()); document.getElementById("main").scrollTop = y; };

  /* ------------------------------------------------------------ 事件 */

  async function download(name) {
    st.downloads[name] = { message: t("settings.copy165"), progress: 0 };
    rerender();
    try {
      await runJob("download_model", [name], (p) => {
        st.downloads[name] = p;
        const card = $(`[data-engine="${name}"] .eng-foot`, mainEl);
        if (card) render(card, html`<div class="eng-dl">${jobBox(p.message, p.progress)}</div>`);
      }, st.abort.signal);
      delete st.downloads[name];
      st.s = { ...st.s, models: (await call("get_settings")).models };
      toast(t("settings.downloaded", { name: st.s.models[name]?.name || t("settings.copy64") }));
    } catch (e) {
      delete st.downloads[name];
      if (e.aborted) return;
      toastError(e);
    }
    if (st.section === "asr") rerender();
  }

  const addChip = (input, path, transform = (x) => x) => {
    const v = transform(input.value.trim());
    if (!v) return;
    const list = get(cfg(), path) || [];
    if (list.includes(v)) { toast(t("settings.copy167"), "info"); input.select(); return; }
    queue(path, [...list, v]);
    rerender();
    $(`[${input.getAttributeNames().find((a) => a.startsWith("data-"))}]`, mainEl)?.focus();
  };

  // 场景规则：界面上的行（含还没填应用名的空行）保存在 cfg 里，与 DOM 行号一一对应；
  // 写入配置时去掉空行（后端不接受空应用名）
  const domRules = () => $$(".rule[data-rule]", mainEl).map((r) => ({
    app: $("[data-rule-app]", r).value.trim(), scene: $("[data-rule-scene]", r).value,
  }));
  function setRules(rules) {
    cfg().scenes.rules = rules;
    setIn(st.pending, "scenes.rules", rules.filter((r) => r.app));
    flush();
  }
  const saveRules = debounce(() => {
    if (!st.rulesDirty) return;
    st.rulesDirty = false;
    setRules(domRules());
  }, 500);

  const offs = [
    on(mainEl, "change", "[data-language]", async (e, el) => {
      try { flush.cancel(); if (await persistPending()) await ctx.app.setLanguage(el.value); } catch (err) { toastError(err); }
    }),
    on(mainEl, "change", "[data-keep-clipboard]", (e, el) => queue("output.restore_clipboard", !el.checked)),
    on(mainEl, "input", "[data-volume]", (e, el) => {
      queue("audio.volume", Number(el.value));
      $("[data-volume-value]", mainEl).textContent = `${el.value}%`;
    }),
    on(mainEl, "change", "[data-sound]", (e, el) => { queue(`audio.sounds.${el.dataset.sound}`, el.value); rerender(); }),
    on(mainEl, "click", "[data-sound-file]", async (e, el) => {
      try {
        const file = await call("pick_sound_file");
        if (file) { queue(`audio.sounds.${el.dataset.soundFile}`, file); rerender(); }
      } catch (err) { toastError(err); }
    }),
    on(mainEl, "click", "[data-sound-preview]", async (e, el) => {
      try { await call("preview_sound", cfg().audio.sounds?.[el.dataset.soundPreview] ?? "", cfg().audio.volume ?? 60); }
      catch (err) { toastError(err); }
    }),
    on(mainEl, "click", "[data-tour-start]", () => ctx.app.startTour()),
    on(mainEl, "keydown", "[data-engine]", (e, el) => {
      if (e.target !== el || !["Enter", " "].includes(e.key)) return;
      e.preventDefault(); el.click();
    }),
    // 通用：开关 / 下拉 / 文本输入
    on(mainEl, "change", "input[type=checkbox][data-key]", (e, el) => {
      queue(el.dataset.key, el.checked);
      if (["scenes.enabled", "coach.enabled", "llm.enabled", "audio.beep"].includes(el.dataset.key)) rerender();
    }),
    on(mainEl, "change", "select[data-path]", (e, el) => {
      const v = el.dataset.path === "privacy.retention_days" ? Number(el.value) : el.value;
      queue(el.dataset.path, v);
    }),

    // 热键：录制或选用常用热键，立即保存（常驻进程热加载后生效）
    on(mainEl, "click", "[data-hk-record]", async () => {
      st.hkRecording = true;
      rerender();
      try {
        const key = await captureHotkey();
        if (key) await setHotkey(key);
      } catch (err) { toastError(err); }
      st.hkRecording = false;
      if (!st.abort.signal.aborted && st.section === "general") rerender();
    }),
    on(mainEl, "click", "[data-hk-cancel]", () => cancelCapture()),
    on(mainEl, "click", "[data-hk-preset]", (e, b) => setHotkey(b.dataset.hkPreset).then(rerender)),
    on(mainEl, "input", "input[data-path]", (e, el) => {
      queue(el.dataset.path, el.value);
      if (el.dataset.path === "llm.base_url") {
        const sel = $("[data-provider]", mainEl);
        if (sel) sel.value = providerOf(el.value);
      }
    }),
    on(mainEl, "click", ".segmented[data-seg] button", (e, b) => {
      const path = b.closest("[data-seg]").dataset.seg;
      if (path === "lexFilter") { st.lexFilter = b.dataset.v; return rerender(); }
      queue(path, b.dataset.v);
      if (path === "app.theme") ctx.app.setTheme(b.dataset.v);
      rerender();
    }),
    on(mainEl, "change", "[data-autostart]", async (e, el) => {
      try {
        await call("set_autostart", el.checked);
        st.s.autostart = el.checked;
        showSaved(el.checked ? t("settings.copy168") : t("settings.copy169"));
      } catch (err) {
        el.checked = !el.checked;
        toastError(err);
      }
    }),
    on(mainEl, "click", "[data-reveal]", (e, b) => {
      const input = b.parentElement.querySelector("input");
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      render(b, icon(show ? "eyeOff" : "eye", 15));
      b.setAttribute("aria-label", t(show ? "settings.hideSecret" : "settings.copy31"));
    }),
    on(mainEl, "click", "[data-step]", (e, b) => {
      const box = b.closest("[data-stepper]");
      const path = box.dataset.stepper;
      const v = Math.max(Number(box.dataset.min), Math.min(Number(box.dataset.max), Number(get(cfg(), path)) + Number(b.dataset.step)));
      queue(path, v);
      $(".val", box).textContent = path === "hotkey.min_hold_ms" ? `${v} ms` : v;
    }),

    // 识别引擎
    on(mainEl, "click", "[data-engine]", (e, card) => {
      if (e.target.closest("[data-download]")) return;
      const id = card.dataset.engine;
      const eng = ENGINES.find((x) => x.id === id);
      if (eng.local && !st.s.models[id]?.downloaded) { toast(t("settings.copy170"), "info"); return; }
      if (cfg().asr.engine === id) return;
      queue("asr.engine", id);
      rerender();
    }),
    on(mainEl, "click", "[data-download]", (e, b) => download(b.dataset.download)),

    // 润色
    on(mainEl, "change", "[data-provider]", (e, sel) => {
      const p = st.s.providers.find((x) => x.id === sel.value);
      if (!p) return;
      queue("llm.base_url", p.base_url);
      if (p.models?.length && !p.models.includes(cfg().llm.model)) queue("llm.model", p.models[0]);
      st.models = p.models?.length ? p.models : null;
      st.test = null;
      rerender();
    }),
    on(mainEl, "click", "[data-fetch-models]", async (e, b) => {
      b.classList.add("is-loading");
      try {
        st.models = await call("list_llm_models", cfg().llm.base_url, cfg().llm.api_key);
        toast(t("settings.modelsFound", { count: st.models.length }));
        rerender();
      } catch (err) {
        b.classList.remove("is-loading");
        toastError(err);
      }
    }),
    on(mainEl, "click", "[data-model]", (e, b) => {
      queue("llm.model", b.dataset.model);
      rerender();
    }),
    on(mainEl, "click", "[data-test]", async () => {
      st.testing = true;
      st.test = null;
      rerender();
      const { base_url, api_key, model } = cfg().llm;
      try {
        st.test = await call("test_llm", { base_url, api_key, model });
      } catch (err) {
        st.test = { ok: false, error: err.message };
      }
      st.testing = false;
      if (st.section === "polish") rerender();
    }),

    // 场景规则
    on(mainEl, "input", "[data-rule-app]", () => { st.rulesDirty = true; saveRules(); }),
    on(mainEl, "change", "[data-rule-scene]", () => { st.rulesDirty = true; saveRules.flush(); }),
    on(mainEl, "click", "[data-rule-del]", (e, b) => {
      const i = Number(b.closest("[data-rule]").dataset.rule);
      saveRules.cancel();
      st.rulesDirty = false;
      setRules(domRules().filter((_, j) => j !== i));
      rerender();
    }),
    on(mainEl, "click", "[data-rule-add]", () => {
      saveRules.flush();
      cfg().scenes.rules = [...domRules(), { app: "", scene: "chat" }];
      rerender();
      $$("[data-rule-app]", mainEl).pop()?.focus();
    }),

    // 术语表 / 黑名单
    on(mainEl, "keydown", "[data-term-add], [data-ex-add], [data-lex-add]", (e, input) => {
      if (e.key !== "Enter" || e.isComposing) return;
      if (input.hasAttribute("data-term-add")) addChip(input, "glossary.terms");
      else if (input.hasAttribute("data-ex-add")) addChip(input, "privacy.exclude_apps");
      else addFiller(input);
    }),
    on(mainEl, "click", "[data-term-del]", (e, b) => { queue("glossary.terms", cfg().glossary.terms.filter((t) => t !== b.dataset.termDel)); rerender(); }),
    on(mainEl, "click", "[data-ex-del]", (e, b) => { queue("privacy.exclude_apps", cfg().privacy.exclude_apps.filter((t) => t !== b.dataset.exDel)); rerender(); }),

    // 口癖词表
    on(mainEl, "click", "[data-lex-toggle]", async (e, b) => {
      const w = st.lexicon.find((x) => x.word === b.dataset.lexToggle);
      try {
        await call("toggle_filler", w.word, !w.enabled);
        w.enabled = !w.enabled;
        rerender();
        showSaved(w.enabled ? t("settings.wordEnabled", { word: w.word }) : t("settings.wordDisabled", { word: w.word }));
      } catch (err) { toastError(err); }
    }),
    on(mainEl, "click", "[data-lex-del]", async (e, b) => {
      const word = b.dataset.lexDel;
      try {
        await call("remove_filler", word);
        st.lexicon = st.lexicon.filter((x) => x.word !== word);
        rerender();
        showSaved(t("settings.wordDeleted", { word }));
      } catch (err) { toastError(err); }
    }),

    // 隐私 / 关于
    on(mainEl, "click", "[data-export]", async (e, b) => {
      b.classList.add("is-loading");
      try {
        const { path } = await call("export_data");
        toast(`${t("settings.copy176")} ${path}`, "ok", 5000);
      } catch (err) { toastError(err); }
      b.classList.remove("is-loading");
    }),
    on(mainEl, "click", "[data-clear]", async () => {
      const ok = await confirmDialog({
        title: t("settings.copy177"),
        body: t("settings.copy178"),
        confirmText: t("settings.copy179"), danger: true, typeToConfirm: "DELETE",
      });
      if (!ok) return;
      try {
        await call("clear_data");
        st.lexicon = await call("get_lexicon");
        toast(t("settings.copy180"));
      } catch (err) { toastError(err); }
    }),
    on(mainEl, "click", "[data-ime-settings]", () => call("open_ime_settings").catch(toastError)),
    on(mainEl, "click", "[data-ime-picker]", () => call("show_ime_picker").catch(toastError)),
    on(mainEl, "click", "[data-export-logs]", async (e, b) => {
      b.classList.add("is-loading");
      try {
        const { path } = await call("export_logs");
        toast(`${t("settings.copy176")} ${path}`, "ok", 5000);
      } catch (err) { toastError(err); }
      b.classList.remove("is-loading");
    }),
    on(mainEl, "click", "[data-open]", async (e, b) => {
      try { await call("open_path", b.dataset.open); } catch (err) { toastError(err); }
    }),

    // 更新：状态变化由 subscribe 重绘「更新」分组
    on(mainEl, "click", "[data-update-check]", () => checkUpdate(true)),
    on(mainEl, "click", "[data-update-download]", () => downloadUpdate()),
    on(mainEl, "click", "[data-update-install]", () => installUpdate()),
    on(mainEl, "click", "[data-update-skip]", async () => {
      const version = upd.info?.latest;
      try {
        await skipUpdate();
        toast(t("update.skipped", { version }));
      } catch (err) { toastError(err); }
    }),
  ];
  const offUpdate = subscribe(() => {
    const box = st.section === "about" && $("#upd-box", mainEl);
    if (box) render(box, html`${updateStatus()}${updatePanel()}`);
  });

  async function setHotkey(key) {
    const prev = cfg().hotkey.key;
    if (key === prev) return;
    queue("hotkey.key", key);
    flush.cancel();
    if (await persistPending()) toast(t("hotkey.saved", { key: hotkeyName(key) }));
    else cfg().hotkey.key = prev; // 保存失败：界面退回原热键
  }

  async function addFiller(input) {
    const word = input.value.trim();
    if (!word) return;
    if (st.lexicon.some((x) => x.word === word)) { toast(t("settings.copy181"), "info"); return; }
    try {
      await call("add_filler", word);
      st.lexicon = await call("get_lexicon");
      rerender();
      $("[data-lex-add]", mainEl)?.focus();
      showSaved(t("settings.wordAdded", { word }));
    } catch (err) { toastError(err); }
  }

  /* ------------------------------------------------------------ 路由 */

  // Android「键盘」分组：每 1.5 秒刷新键盘状态（用户可能刚从系统设置回来）
  async function pollIme() {
    clearTimeout(st.imeTimer);
    if (!android || st.section !== "keyboard" || st.abort.signal.aborted) return;
    const ime = await ctx.app.refreshIme();
    if (st.abort.signal.aborted || st.section !== "keyboard") return;
    if (JSON.stringify(ime) !== JSON.stringify(st.ime)) {
      st.ime = ime;
      const box = $("#ime-rows", mainEl);
      if (box) render(box, imeRows());
    }
    st.imeTimer = setTimeout(pollIme, 1500);
  }

  function applyParams(params) {
    saveRules.flush();
    if (st.hkRecording) cancelCapture();
    const chosen = SECTIONS.some(([k]) => k === params[0]);
    st.section = chosen ? params[0] : SECTIONS[0][0];
    root.classList.toggle("has-section", chosen); // 手机布局：列表 ↔ 详情
    renderNav();
    renderMain();
    pollIme();
    if (st.section === "about") checkUpdate(false); // 取后端缓存的检查结果（更新说明随界面语言）
    return true;
  }
  mountApi.update = applyParams;
  applyParams(ctx.params);

  return () => {
    if (st.hkRecording) cancelCapture();
    clearTimeout(st.imeTimer);
    saveRules.flush();
    flush.flush();
    clearTimeout(savedTimer);
    st.abort.abort();
    offs.forEach((f) => f());
    offUpdate();
    if (mountApi.update === applyParams) mountApi.update = null;
  };
}

const mountApi = { update: null };
export function update(params) {
  return mountApi.update ? mountApi.update(params) : false;
}
