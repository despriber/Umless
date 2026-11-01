import { t } from "../i18n.js";
// 新手引导（桌面）：欢迎 → 下载识别模型 → 配置润色 → 测试麦克风 → 热键与开机自启 → 完成。
// 新手引导（Android）：欢迎 → 启用键盘 → 麦克风权限与测试 → 识别模型 → 润色 → 试一试 → 完成。
import { call, runJob } from "../api.js";
import { html, render, raw, $, $$, on } from "../dom.js";
import { icon, logo } from "../icons.js";
import { jobBox, highlight } from "../components.js";
import { toast, toastError, fmtMs } from "../ui.js";
import { hotkeyName, hotkeyParts, hotkeyPicker, captureHotkey, cancelCapture } from "../hotkey.js";
import { isAndroid } from "../platform.js";

const BARS = 29;

export async function mount(root, ctx) {
  const android = isAndroid();
  const STEPS = android ? [
    ["welcome", t("onboarding.copy183")],
    ["ime", t("onboarding.ime.step")],
    // 先下载模型再测麦克风：测试时就能看到识别出的文字
    ["model", t("onboarding.copy184")],
    ["mic", t("onboarding.copy185")],
    ["llm", t("settings.copy3")],
    ["try", t("onboarding.try.step")],
    ["done", t("onboarding.copy187")],
  ] : [
    ["welcome", t("onboarding.copy183")],
    ["model", t("onboarding.copy184")],
    ["llm", t("settings.copy3")],
    ["mic", t("onboarding.copy185")],
    ["hotkey", t("onboarding.copy186")],
    ["done", t("onboarding.copy187")],
  ];
  const DICTATION_STEP = android ? "try" : "mic"; // 显示"试一试"并轮询最近一次输出的步骤

  const st = {
    step: "welcome",
    s: null,
    dl: null, // 下载进度
    llm: null, // {base_url, api_key, model}
    provider: null,
    test: null,
    testing: false,
    mic: { state: "idle", result: null, levels: new Array(BARS).fill(0.06) }, // idle | listening | recognizing | done
    autostart: false,
    abort: new AbortController(),
    timer: null,
    dictationTimer: null,
    dictationText: "",
    lastOutput: null,
    lastOutputAt: null,
    core: null,
    hkRecording: false,
    ime: ctx.app.ime, // Android：键盘状态 {enabled, selected}
    imeTimer: null,
    perm: null, // Android：麦克风权限 null 未请求 · true 已授权 · false 被拒绝
    requesting: false,
  };

  try {
    st.s = await call("get_settings");
  } catch (e) {
    toastError(e);
    return;
  }
  if (!root.isConnected) { st.abort.abort(); return; }
  st.llm = { ...st.s.config.llm };
  st.autostart = st.s.autostart;
  st.provider = st.s.providers.find((p) => p.base_url === st.llm.base_url)?.id || "custom";

  const idx = () => STEPS.findIndex(([k]) => k === st.step);
  const go = (k) => ctx.app.navigate(`onboarding/${k}`);
  const next = () => go(STEPS[Math.min(idx() + 1, STEPS.length - 1)][0]);
  const prev = () => go(STEPS[Math.max(idx() - 1, 0)][0]);

  /* ------------------------------------------------------------ 外框 */

  function stepper() {
    const i = idx();
    const mid = STEPS.slice(1, -1);
    return html`<ol class="ob-steps" aria-label="${t("onboarding.copy188")}">
      ${mid.map(([k, label], j) => {
        const n = j + 1;
        const state = n < i ? "is-done" : n === i ? "is-current" : "";
        return html`<li class="ob-step ${state}"><span class="ob-dot">${n < i ? icon("check", 12) : n}</span><span class="ob-label">${label}</span></li>${j < mid.length - 1 ? html`<li class="ob-line ${n < i ? "is-done" : ""}" aria-hidden="true"></li>` : ""}`;
      })}
    </ol>`;
  }

  function frame(body, { foot = "" } = {}) {
    const showSteps = st.step !== "welcome" && st.step !== "done";
    return html`<div class="ob">
      <div class="ob-bg" aria-hidden="true"></div>
      <div class="ob-top">
        <div class="ob-brand">${logo(24)}<span>Umless</span></div>
        ${showSteps ? stepper() : html`<span></span>`}
        <span class="ob-top-right"><select class="select input-sm" data-language aria-label="${t("settings.language")}"><option value="auto" ${st.s.config.app.language === "auto" ? raw("selected") : ""}>${t("settings.autoLanguage")}</option><option value="zh" ${st.s.config.app.language === "zh" ? raw("selected") : ""}>简体中文</option><option value="en" ${st.s.config.app.language === "en" ? raw("selected") : ""}>English</option></select>${showSteps ? html`<span class="muted small num">${idx()} / ${STEPS.length - 2}</span>` : ""}</span>
      </div>
      <div class="ob-center">
        <div class="ob-card ob-card--${st.step}">${body}</div>
        ${foot ? html`<div class="ob-foot">${foot}</div>` : ""}
      </div>
    </div>`;
  }

  const navFoot = ({ nextLabel = t("onboarding.copy189"), nextDisabled = false, skip = "" } = {}) => html`
    <button class="btn btn-ghost" data-prev>${icon("chevronLeft", 15)}${t("onboarding.copy190")}</button>
    <span class="grow"></span>
    ${skip ? html`<button class="btn btn-ghost" data-skip>${skip}</button>` : ""}
    <button class="btn btn-primary btn-lg" data-next ${nextDisabled ? raw("disabled") : ""}>${nextLabel}${icon("arrowRight", 16)}</button>`;

  /* ------------------------------------------------------------ 各步骤 */

  const views = {
    welcome() {
      return frame(html`
        <div class="ob-hero">
          <div class="ob-logo">${logo(76, "ob-logo-svg")}</div>
          <h1>${t("onboarding.copy191")}</h1>
          <p class="ob-lead">${t("onboarding.copy192")}<br>${t("onboarding.copy193")}</p>
        </div>
        <div class="ob-features">
          <div class="ob-feature"><span class="ob-f-ico">${icon("wave", 18)}</span><b>${t("onboarding.copy194")}</b><span>${t("onboarding.copy195")}</span></div>
          <div class="ob-feature"><span class="ob-f-ico">${icon("wand", 18)}</span><b>${t("onboarding.copy196")}</b><span>${t("onboarding.copy197")}</span></div>
          <div class="ob-feature"><span class="ob-f-ico">${icon("trend", 18)}</span><b>${t("settings.copy125")}</b><span>${t("onboarding.copy198")}</span></div>
        </div>
        <div class="ob-cta">
          <button class="btn btn-primary btn-lg" data-next>${t("onboarding.copy199")}${icon("arrowRight", 16)}</button>
          <span class="muted small">${icon("shield", 13)} ${t("onboarding.copy200")}</span>
        </div>`);
    },

    model() {
      const m = st.s.models.sensevoice;
      const ready = m?.downloaded;
      return frame(html`
        <div class="ob-head"><span class="ob-icon">${icon("download", 22)}</span>
          <h2>${t("onboarding.copy201")}</h2>
          <p>${t("onboarding.copy202")}</p></div>
        <div class="ob-model ${ready ? "is-ready" : ""}">
          <span class="ob-model-ico">${icon("zap", 20)}</span>
          <div class="grow">
            <div class="strong">${m?.name || "SenseVoice-Small"}</div>
            <div class="muted small">${t("onboarding.copy203")} ${m?.size_mb ?? 166} ${t("onboarding.copy204")}</div>
          </div>
          ${ready ? html`<span class="tag is-accent">${icon("check", 13)}${t("onboarding.copy205")}</span>`
            : st.dl ? "" : html`<button class="btn btn-primary" data-download>${icon("download", 15)}${t("onboarding.copy206")}</button>`}
        </div>
        ${st.dl ? html`<div class="ob-dl">${jobBox(st.dl.message, st.dl.progress)}</div>` : ""}
        <p class="ob-note">${icon("info", 14)}${t("onboarding.copy207")}</p>`,
      { foot: navFoot({ nextDisabled: !ready, skip: ready ? "" : t("onboarding.copy208") }) });
    },

    llm() {
      const providers = [...st.s.providers, { id: "custom", name: t("settings.copy81"), base_url: "", models: [] }];
      const result = st.test;
      return frame(html`
        <div class="ob-head"><span class="ob-icon">${icon("wand", 22)}</span>
          <h2>${t("onboarding.copy209")}</h2>
          <p>${t("onboarding.copy210")}</p></div>
        <div class="ob-providers">
          ${providers.map((p) => html`<button class="ob-provider ${st.provider === p.id ? "is-active" : ""}" data-provider="${p.id}">
            <span class="ob-p-name">${p.name}</span>
            <span class="ob-p-sub">${p.id === "ollama" ? t("onboarding.copy211") : p.id === "custom" ? t("settings.copy14") : t("onboarding.copy212")}</span>
          </button>`)}
        </div>
        <div class="form ob-form">
          ${st.provider === "custom" ? html`<label class="field"><span class="field-label">${t("settings.copy63")}</span>
            <input class="input is-mono" data-llm="base_url" value="${st.llm.base_url}" placeholder="https://…/v1" spellcheck="false"></label>` : ""}
          <div class="form-row">
            <label class="field"><span class="field-label">API Key</span>
              <div class="input-group"><input class="input is-mono" type="password" data-llm="api_key" value="${st.llm.api_key}" placeholder="${st.provider === "ollama" ? t("settings.copy86") : "sk-…"}" autocomplete="off" spellcheck="false">
              <button class="btn btn-ghost btn-icon btn-sm trail" data-reveal aria-label="${t("settings.copy31")}">${icon("eye", 15)}</button></div></label>
            <label class="field"><span class="field-label">${t("settings.copy64")}</span>
              <input class="input is-mono" data-llm="model" value="${st.llm.model}" list="ob-models" spellcheck="false">
              <datalist id="ob-models">${(st.s.providers.find((p) => p.id === st.provider)?.models || []).map((m) => html`<option value="${m}">`)}</datalist></label>
          </div>
          <div class="test-row">
            <button class="btn btn-secondary" data-test ${st.testing ? raw("disabled") : ""}>${st.testing ? html`<span class="spinner"></span>` : icon("zap", 15)}${t("settings.copy88")}</button>
            ${result?.ok ? html`<span class="ob-test-ok">${icon("checkCircle", 15)}${t("onboarding.copy213")} <span class="num">${fmtMs(result.latency_ms)}</span></span>` : ""}
            ${result && !result.ok ? html`<span class="ob-test-err">${icon("alert", 15)}${result.error || t("settings.copy71")}</span>` : ""}
          </div>
          ${result?.ok ? html`<div class="ob-sample"><span class="muted">${result.input}</span>${icon("arrowRight", 14)}<b>${result.output}</b></div>` : ""}
        </div>`,
      { foot: navFoot({ skip: t("onboarding.copy214") }) });
    },

    ime() {
      const ready = st.ime?.enabled && st.ime?.selected;
      return frame(html`
        <div class="ob-head"><span class="ob-icon">${icon("keyboard", 22)}</span>
          <h2>${t("onboarding.ime.title")}</h2>
          <p>${t("onboarding.ime.lead")}</p></div>
        <ol class="ob-checks">${imeChecks()}</ol>
        <p class="ob-note">${icon("shield", 14)}${t("onboarding.ime.note")}</p>`,
      { foot: navFoot({ nextDisabled: !ready, skip: ready ? "" : t("onboarding.ime.later") }) });
    },

    mic() {
      const m = st.mic;
      const head = html`<div class="ob-head"><span class="ob-icon">${icon("practice", 22)}</span>
          <h2>${t("onboarding.copy215")}</h2>
          <p>${t("onboarding.copy216")}</p></div>`;
      if (android) {
        return frame(html`${head}${st.perm === true ? micBox() : permBox()}`,
          { foot: navFoot({ skip: m.state === "done" ? "" : t("onboarding.copy227") }) });
      }
      return frame(html`${head}${micBox()}${dictationBox()}`,
        { foot: navFoot({ skip: m.state === "done" ? "" : t("onboarding.copy227") }) });
    },

    try() {
      return frame(html`
        <div class="ob-head"><span class="ob-icon">${icon("chat", 22)}</span>
          <h2>${t("onboarding.tryTitle")}</h2>
          <p>${t("onboarding.try.lead")}</p></div>
        ${dictationBox()}`,
      { foot: navFoot() });
    },

    hotkey() {
      const key = st.s.config.hotkey.key;
      const label = hotkeyName(key);
      return frame(html`
        <div class="ob-head"><span class="ob-icon">${icon("keyboard", 22)}</span>
          <h2>${t("onboarding.holdKey", { key: label })}</h2>
          <p>${t("onboarding.copy230")}</p></div>
        <div class="ob-keyvis" aria-hidden="true">
          ${key === "caps_lock"
            ? html`<div class="keycap is-wide"><span class="keycap-top"><i class="keycap-led"></i>Caps Lock</span></div>`
            : hotkeyParts(key).map((part, i) => html`${i ? html`<span class="keycap-plus">+</span>` : ""}<div class="keycap ${part.length > 4 ? "is-wide" : ""}"><span class="keycap-top">${part}</span></div>`)}
          <div class="ob-key-wave">${[0.4, 0.7, 1, 0.75, 0.5, 0.85, 0.6, 0.35].map((h, i) => html`<i style="--h:${h};--d:${i * 90}ms"></i>`)}</div>
        </div>
        <ul class="ob-tips">
          <li><span class="ob-tip-no">1</span><span><b>${t("onboarding.copy228")}</b>${t("onboarding.copy231")}</span></li>
          <li><span class="ob-tip-no">2</span><span><b>${t("onboarding.copy232")}</b>${t("onboarding.copy233")}</span></li>
          ${key === "caps_lock" ? html`<li><span class="ob-tip-no">3</span><span><b>${t("onboarding.copy234")}</b> ${t("onboarding.copy235")}</span></li>` : ""}
        </ul>
        <div class="ob-rows">
          <div class="set-row"><div class="set-text"><div class="set-label">${t("onboarding.copy186")}</div><div class="set-desc">${t("onboarding.copy236")}</div></div>
            <div class="set-ctl">${hotkeyPicker(key, { recording: st.hkRecording })}</div></div>
          <div class="set-row"><div class="set-text"><div class="set-label">${t("settings.copy54")}</div><div class="set-desc">${t("settings.copy55")}</div></div>
            <div class="set-ctl"><label class="switch"><input type="checkbox" data-autostart ${st.autostart ? raw("checked") : ""}><span class="track"></span></label></div></div>
        </div>`,
      { foot: navFoot() });
    },

    done() {
      const label = hotkeyName(st.s.config.hotkey?.key);
      return frame(html`
        <div class="ob-hero">
          <div class="ob-done-mark">${icon("check", 40)}</div>
          <h1>${t("onboarding.copy237")}</h1>
          <p class="ob-lead">${t("onboarding.copy238")} ${android ? "" : html`<kbd>${label}</kbd> `}${t("onboarding.copy239")}</p>
        </div>
        <div class="ob-features is-two">
          <div class="ob-feature"><span class="ob-f-ico">${icon("report", 18)}</span><b>${t("onboarding.copy240")}</b><span>${t("onboarding.copy241")}</span></div>
          <div class="ob-feature"><span class="ob-f-ico">${icon("practice", 18)}</span><b>${t("onboarding.copy242")}</b><span>${t("onboarding.copy243")}</span></div>
        </div>
        <div class="ob-cta"><button class="btn btn-primary btn-lg" data-finish>${t("onboarding.copy244")}${icon("arrowRight", 16)}</button></div>`);
    },
  };

  /** 麦克风测试：声波 + 状态 + 识别结果 */
  function micBox() {
    const m = st.mic;
    const peak = m.result?.peak ?? 0;
    return html`
        <div class="ob-mic is-${m.state}">
          <div class="wave ob-wave" id="ob-wave" aria-hidden="true">${Array.from({ length: BARS }, (_, i) => html`<i style="transform:scaleY(${m.levels[i].toFixed(3)})"></i>`)}</div>
          <div class="ob-mic-status" id="ob-mic-status">${
            m.state === "listening" ? html`<span class="rec-dot"></span>${t("onboarding.copy217")}`
            : m.state === "recognizing" ? html`<span class="spinner"></span>${t("onboarding.copy218")}`
            : m.state === "done" ? html`${icon("checkCircle", 16)}${t("onboarding.copy219")}`
            : t("onboarding.copy220")}</div>
          ${m.state === "done" ? html`<div class="ob-mic-result">
              <span class="mini-title">${t("onboarding.copy221")}</span>
              <p class="selectable">${m.result.text || (m.result.model_ready === false ? t("onboarding.micNoModel") : t("onboarding.copy222"))}</p>
              <span class="ob-mic-peak ${peak < 0.15 ? "is-low" : ""}">${peak < 0.15 ? t("onboarding.copy223") : t("onboarding.copy224")}</span>
            </div>` : ""}
          <button class="btn ${m.state === "done" ? "btn-secondary" : "btn-primary"} btn-lg" data-mic-test ${m.state === "listening" || m.state === "recognizing" ? raw("disabled") : ""}>
            ${icon(m.state === "done" ? "refresh" : "practice", 16)}${m.state === "done" ? t("onboarding.copy225") : t("onboarding.copy226")}</button>
        </div>`;
  }

  /** Android：申请麦克风权限（被拒绝时可再次请求；原生端在"不再询问"时跳转到应用设置） */
  function permBox() {
    const denied = st.perm === false;
    return html`<div class="ob-perm ${denied ? "is-denied" : ""}">
      <span class="ob-model-ico">${icon(denied ? "alert" : "practice", 20)}</span>
      <div class="grow">
        <div class="strong">${t(denied ? "onboarding.perm.deniedTitle" : "onboarding.perm.title")}</div>
        <div class="muted small">${t(denied ? "onboarding.perm.denied" : "onboarding.perm.why")}</div>
      </div>
      <button class="btn btn-primary" data-mic-perm ${st.requesting ? raw("disabled") : ""}>${icon("practice", 15)}${t(denied ? "onboarding.perm.retry" : "onboarding.perm.allow")}</button>
    </div>`;
  }

  /** 试一试：可编辑的试写区 + 原文 → 实际输出对比（桌面在麦克风步骤里，Android 单独一步） */
  function dictationBox() {
    return html`<section class="ob-dictation" data-tour="onboarding-dictation">
      ${android ? "" : html`<h3>${t("onboarding.tryTitle")}</h3>`}
      <p class="muted small">${t("onboarding.tryHelp", { key: hotkeyName(st.s.config.hotkey?.key) })}</p>
      <div id="dictation-status" class="ob-note" aria-live="polite">${dictationStatus()}</div>
      <label class="field"><span class="field-label">${t("onboarding.inputLabel")}</span><textarea class="textarea" rows="3" data-dictation-input placeholder="${t("onboarding.inputPlaceholder")}">${st.dictationText}</textarea></label>
      <div id="dictation-result" aria-live="polite">${dictationResult()}</div>
    </section>`;
  }

  /** Android：启用键盘的两步清单 */
  function imeChecks() {
    const enabled = !!st.ime?.enabled, selected = !!st.ime?.selected;
    const item = (n, done, title, help, action) => html`<li class="ob-check ${done ? "is-done" : ""}">
      <span class="ob-tip-no">${done ? icon("check", 13) : n}</span>
      <div class="grow"><b>${title}</b><span>${help}</span></div>
      ${done ? html`<span class="tag is-accent">${icon("check", 13)}${t("onboarding.ime.done")}</span>` : action}
    </li>`;
    return html`
      ${item(1, enabled, t("onboarding.ime.enable"), t("onboarding.ime.enableHelp"),
        html`<button class="btn btn-primary btn-sm" data-ime-settings>${t("ime.enable")}</button>`)}
      ${item(2, selected, t("onboarding.ime.select"), t("onboarding.ime.selectHelp"),
        html`<button class="btn btn-secondary btn-sm" data-ime-picker ${enabled ? "" : raw("disabled")}>${t("ime.switch")}</button>`)}`;
  }

  function dictationStatus() {
    if (android) {
      // Android：试写区要用 Umless 键盘输入
      const ime = st.ime;
      if (!ime) return html`${t("onboarding.checkingCore")}`;
      if (ime.enabled && ime.selected) return html`${icon("checkCircle", 14)}${t("onboarding.imeReady")}`;
      if (ime.enabled) return html`${icon("info", 14)}${t("ime.notSelected")} <button class="btn btn-secondary btn-sm" data-ime-picker>${t("ime.switch")}</button>`;
      return html`${icon("info", 14)}${t("ime.notEnabled")} <button class="btn btn-secondary btn-sm" data-ime-settings>${t("ime.enable")}</button>`;
    }
    if (!st.core) return html`${t("onboarding.checkingCore")}`;
    if (st.core.ready) return html`${icon("checkCircle", 14)}${t("onboarding.coreReady")}`;
    if (st.core.running) return html`${icon("info", 14)}${t("onboarding.coreLoading")}`;
    return html`${icon("info", 14)}${t("onboarding.coreStopped")} <button class="btn btn-secondary btn-sm" data-start-core>${t("onboarding.startCore")}</button>`;
  }

  function dictationResult() {
    const output = st.lastOutput;
    if (!output) return html`<p class="muted small">${t("onboarding.waitingOutput")}</p>`;
    return html`<div class="test-box is-ok"><div class="test-io">
      <div><span class="mini-title">${t("onboarding.raw")}</span><p class="selectable">${output.highlights?.length ? highlight(output.highlights) : output.raw}</p></div>
      ${icon("arrowRight", 16, "test-arrow")}
      <div><span class="mini-title">${t("onboarding.polished")}</span><p class="selectable is-out">${output.text}</p></div>
    </div><p class="muted small">${t("onboarding.clipboardHint")}</p></div>`;
  }

  let dictationPolling = false;
  async function startDictationPoll() {
    clearTimeout(st.dictationTimer);
    if (dictationPolling || st.abort.signal.aborted || st.step !== DICTATION_STEP) return;
    dictationPolling = true;
    try {
      const [core, output] = await Promise.all([call(android ? "get_ime_status" : "core_status"), call("get_last_output")]);
      if (st.abort.signal.aborted || st.step !== DICTATION_STEP) return;
      const changed = JSON.stringify(android ? st.ime : st.core) !== JSON.stringify(core);
      if (android) st.ime = core;
      else st.core = core;
      if (changed) render($("#dictation-status", root), dictationStatus());
      if (st.lastOutputAt === null) st.lastOutputAt = output?.at ?? "";
      else if (output && output.at !== st.lastOutputAt) {
        st.lastOutputAt = output.at;
        st.lastOutput = output;
        st.dictationText = output.text;
        const input = $("[data-dictation-input]", root);
        if (input) input.value = output.text;
        render($("#dictation-result", root), dictationResult());
      }
    } catch { /* Retry while this step is visible. */ }
    finally {
      dictationPolling = false;
      if (!st.abort.signal.aborted && st.step === DICTATION_STEP) st.dictationTimer = setTimeout(startDictationPoll, 700);
    }
  }

  /** Android「启用键盘」步骤：每秒查询一次键盘状态，变化时重绘（用户从系统设置返回后自动打勾） */
  async function pollImeStep() {
    clearTimeout(st.imeTimer);
    if (st.abort.signal.aborted || st.step !== "ime") return;
    try {
      const ime = await call("get_ime_status");
      if (st.abort.signal.aborted || st.step !== "ime") return;
      if (JSON.stringify(ime) !== JSON.stringify(st.ime)) {
        st.ime = ime;
        render(root, views.ime());
      }
    } catch { /* 下一秒再试 */ }
    st.imeTimer = setTimeout(pollImeStep, 1000);
  }

  function draw() {
    clearTimeout(st.timer);
    render(root, views[st.step]());
    if (st.step === DICTATION_STEP) startDictationPoll();
    if (st.step === "ime") pollImeStep();
  }

  /* ------------------------------------------------------------ 动作 */

  async function saveLlm() {
    try {
      const res = await call("save_settings", { llm: { base_url: st.llm.base_url, api_key: st.llm.api_key, model: st.llm.model } });
      st.s.models = res.models;
      return true;
    } catch (e) {
      toastError(e);
      return false;
    }
  }

  async function download() {
    st.dl = { message: t("settings.copy165"), progress: 0 };
    draw();
    try {
      await runJob("download_model", ["sensevoice"], (p) => {
        st.dl = p;
        const box = $(".ob-dl", root);
        if (box) render(box, jobBox(p.message, p.progress));
      }, st.abort.signal);
      st.s.models = (await call("get_settings")).models;
      st.dl = null;
      toast(t("onboarding.copy245"));
    } catch (e) {
      st.dl = null;
      if (e.aborted) return;
      toastError(e);
    }
    if (st.step === "model") draw();
  }

  async function setHotkey(key) {
    const prev = st.s.config.hotkey.key;
    if (key === prev) return;
    st.s.config.hotkey.key = key;
    try {
      await call("save_settings", { hotkey: { key } });
    } catch (err) {
      st.s.config.hotkey.key = prev;
      toastError(err);
    }
  }

  async function micTest() {
    if (["listening", "recognizing"].includes(st.mic.state)) return;
    st.mic = { ...st.mic, state: "listening", result: null };
    draw();
    const poll = async () => {
      if (st.step !== "mic" || st.mic.state !== "listening") return;
      try {
        const lv = await call("mic_level");
        const half = (BARS - 1) / 2;
        $$("#ob-wave i", root).forEach((b, i) => {
          const d = Math.abs(i - half) / (half + 1);
          const env = Math.pow(Math.cos((d * Math.PI) / 2), 1.3);
          const target = Math.max(0.06, (lv.recording ? lv.level : 0) * 1.3 * env * (0.55 + 0.45 * Math.random()));
          st.mic.levels[i] += (target - st.mic.levels[i]) * 0.55;
          b.style.transform = `scaleY(${Math.min(1, st.mic.levels[i]).toFixed(3)})`;
        });
      } catch { /* 忽略 */ }
      st.timer = setTimeout(poll, 83);
    };
    poll();
    try {
      const result = await runJob("mic_test", [], (p) => {
        if (st.mic.state === "listening" && p.progress != null && p.progress >= 0.85) {
          st.mic.state = "recognizing";
          st.mic.levels.fill(0.06);
          if (st.step === "mic") draw();
        }
      }, st.abort.signal);
      st.mic = { ...st.mic, state: "done", result };
    } catch (e) {
      if (e.aborted) return;
      st.mic = { ...st.mic, state: "idle" };
      toastError(e);
    }
    st.mic.levels.fill(0.06);
    if (st.step === "mic") draw();
  }

  const offs = [
    on(root, "change", "[data-language]", async (e, el) => {
      try { await ctx.app.setLanguage(el.value); } catch (err) { toastError(err); }
    }),
    on(root, "input", "[data-dictation-input]", (e, el) => { st.dictationText = el.value; }),
    on(root, "click", "[data-start-core]", async (e, el) => {
      el.disabled = true;
      try { await call("start_core"); } catch (err) { toastError(err); }
      el.disabled = false;
    }),
    on(root, "click", "[data-next]", async (e, b) => {
      if (st.step !== "llm") return next();
      if (b.disabled) return;
      b.disabled = true; // 保存期间禁用，双击不会跳过下一步
      try {
        if (await saveLlm()) next();
      } finally {
        b.disabled = false;
      }
    }),
    on(root, "click", "[data-prev]", prev),
    on(root, "click", "[data-skip]", next),
    on(root, "click", "[data-download]", download),
    on(root, "click", "[data-provider]", (e, b) => {
      st.provider = b.dataset.provider;
      const p = st.s.providers.find((x) => x.id === st.provider);
      if (p) {
        st.llm.base_url = p.base_url;
        if (!p.models.includes(st.llm.model)) st.llm.model = p.models[0] || "";
      }
      st.test = null;
      draw();
    }),
    on(root, "input", "[data-llm]", (e, input) => { st.llm[input.dataset.llm] = input.value; }),
    on(root, "click", "[data-reveal]", (e, b) => {
      const input = b.parentElement.querySelector("input");
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      render(b, icon(show ? "eyeOff" : "eye", 15));
      b.setAttribute("aria-label", t(show ? "settings.hideSecret" : "settings.copy31"));
    }),
    on(root, "click", "[data-test]", async () => {
      st.testing = true;
      st.test = null;
      draw();
      try {
        st.test = await call("test_llm", { base_url: st.llm.base_url, api_key: st.llm.api_key, model: st.llm.model });
      } catch (e) {
        st.test = { ok: false, error: e.message };
      }
      st.testing = false;
      if (st.step === "llm") draw();
    }),
    on(root, "click", "[data-mic-test]", micTest),
    on(root, "click", "[data-ime-settings]", () => call("open_ime_settings").catch(toastError)),
    on(root, "click", "[data-ime-picker]", () => call("show_ime_picker").catch(toastError)),
    on(root, "click", "[data-mic-perm]", async () => {
      if (st.requesting) return;
      st.requesting = true;
      draw();
      try {
        st.perm = !!(await call("request_mic_permission"))?.granted;
      } catch (err) {
        st.perm = false;
        toastError(err);
      }
      st.requesting = false;
      if (st.step === "mic") draw();
    }),
    on(root, "click", "[data-hk-record]", async () => {
      st.hkRecording = true;
      draw();
      try {
        const key = await captureHotkey();
        if (key) await setHotkey(key);
      } catch (err) { toastError(err); }
      st.hkRecording = false;
      if (st.step === "hotkey") draw();
    }),
    on(root, "click", "[data-hk-cancel]", () => cancelCapture()),
    on(root, "click", "[data-hk-preset]", async (e, b) => { await setHotkey(b.dataset.hkPreset); draw(); }),
    on(root, "change", "[data-autostart]", async (e, el) => {
      try {
        await call("set_autostart", el.checked);
        st.autostart = el.checked;
      } catch (err) {
        el.checked = !el.checked;
        toastError(err);
      }
    }),
    on(root, "click", "[data-finish]", async (e, b) => {
      b.disabled = true;
      b.classList.add("is-loading");
      try {
        await call("finish_onboarding");
        await ctx.app.refreshInfo();
        await ctx.app.navigate("overview");
        ctx.app.startTour();
      } catch (err) {
        b.disabled = false;
        b.classList.remove("is-loading");
        toastError(err);
      }
    }),
  ];

  function applyParams(params) {
    const k = STEPS.some(([s]) => s === params[0]) ? params[0] : "welcome";
    clearTimeout(st.dictationTimer);
    clearTimeout(st.imeTimer);
    st.step = k;
    draw();
    return true;
  }
  mountApi.update = applyParams;
  applyParams(ctx.params);

  return () => {
    if (st.hkRecording) cancelCapture();
    clearTimeout(st.timer);
    clearTimeout(st.dictationTimer);
    clearTimeout(st.imeTimer);
    st.abort.abort();
    offs.forEach((f) => f());
    if (mountApi.update === applyParams) mountApi.update = null;
  };
}

const mountApi = { update: null };
export function update(params) {
  return mountApi.update ? mountApi.update(params) : false;
}
