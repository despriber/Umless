import { t, th } from "../i18n.js";
// 练习：今日题目 → 录音（倒计时环 + 实时声波）→ 评测中 → 结果；历史练习。
import { call, runJob } from "../api.js";
import { html, render, $, $$, on } from "../dom.js";
import { icon } from "../icons.js";
import { ring, animateIn } from "../charts.js";
import { meters, highlight, hlLegend, segmented, jobBox } from "../components.js";
import {
  fmtMD, fmtHM, fmtNum, relDateTime, emptyState, toast, toastError, copyText, animateNumber,
} from "../ui.js";

const practiceDims = () => [
  ["structure", t("pages.text1")], ["logic", t("pages.text2")], ["fluency", t("pages.text3")], ["concision", t("pages.text4")], ["wording", t("pages.text7")],
];
const WAVE_BARS = 35; // 声波柱数（奇数，左右对称）

function verdict(score) {
  if (score == null) return "";
  if (score >= 88) return t("pages.text8");
  if (score >= 78) return t("pages.text9");
  if (score >= 68) return t("pages.text10");
  return t("pages.text11");
}

export async function mount(root, ctx) {
  const st = {
    view: "list", // list | recording | evaluating | result
    topics: undefined,
    history: [],
    gen: null, // 生成题目任务进度
    customDuration: 60,
    topic: null,
    result: null,
    levels: new Array(WAVE_BARS).fill(0.06),
    timer: null,
    abort: new AbortController(),
    stopping: false,
  };

  /* ------------------------------------------------------------ 列表 */

  function renderList() {
    const topics = st.topics;
    render(root, html`
      <header class="page-head">
        <div>
          <h1 class="page-title">${t("pages.text12")}</h1>
          <p class="page-sub">${t("pages.text13")}</p>
        </div>
        <div class="page-actions">
          ${topics ? html`<button class="btn btn-secondary" data-refresh ${st.gen ? "disabled" : ""}>${icon("refresh", 15)}${t("pages.text14")}</button>` : ""}
        </div>
      </header>

      ${st.gen ? html`<div class="pr-gen">${jobBox(st.gen.message, st.gen.progress)}</div>` : ""}

      ${topics === undefined ? html`<div class="card loading-card"><span class="spinner is-lg"></span></div>`
        : topics === null ? html`<div class="card" data-tour="practice-topics">${emptyState({
          art: "practice", title: t("pages.text15"),
          desc: t("pages.text16"),
          action: st.gen ? "" : html`<button class="btn btn-primary" data-generate>${icon("sparkles", 15)}${t("pages.text17")}</button>`,
        })}</div>`
        : html`<div data-tour="practice-topics" class="topic-grid ${st.gen ? "is-dim" : ""}">
          ${topics.topics.map((tp, i) => topicCard(tp, i))}
          ${customCard()}
        </div>`}

      <h3 class="section-title">${t("pages.text18")}<span class="muted">${st.history.length ? t("common.times", { n: st.history.length }) : ""}</span></h3>
      ${st.history.length
        ? html`<div class="card is-flush pr-history">${st.history.map((p) => html`<a class="pr-row" href="#practice/result/${p.id}">
            <span class="pr-score ${scoreCls(p.overall)} num">${p.overall ?? "—"}</span>
            <span class="grow">
              <span class="pr-topic ellipsis">${p.topic}</span>
              <span class="pr-meta">${relDateTime(p.created_at)} · ${t("practice.duration", { n: Math.round(p.duration_s) })}</span>
            </span>
            <span class="tag is-sm">${p.category || t("pages.text19")}</span>
            ${icon("chevronRight", 16, "pr-chev")}
          </a>`)}</div>`
        : html`<p class="pr-empty">${icon("clock", 15)}${t("pages.text20")}</p>`}
    `);
  }

  const scoreCls = (s) => (s == null ? "" : s >= 80 ? "is-high" : s >= 65 ? "is-mid" : "is-low");

  function topicCard(tp, i) {
    return html`<article class="card is-interactive topic" data-start="${i}" role="button" tabindex="0">
      <div class="topic-top">
        <span class="tag is-sm">${tp.category}</span>
        ${tp.personal ? html`<span class="tag is-sm is-accent">${icon("user", 12)}${t("pages.text21")}</span>` : ""}
        <span class="grow"></span>
        <span class="topic-dur">${icon("timer", 14)}${t("fmt.sec", { n: tp.duration })}</span>
      </div>
      <h3 class="topic-title">${tp.title}</h3>
      <p class="topic-prompt">${tp.prompt}</p>
      <div class="topic-hint">${icon("structure", 14)}<span>${tp.hint}</span></div>
      <div class="topic-go"><span>${t("pages.text22")}</span>${icon("arrowRight", 15)}</div>
    </article>`;
  }

  function customCard() {
    return html`<article class="card topic topic-custom">
      <div class="topic-top"><span class="tag is-sm is-outline">${icon("pen", 12)}${t("pages.text19")}</span><span class="grow"></span>
        ${segmented("dur", [["60", t("pages.text133")], ["90", t("pages.text134")]], String(st.customDuration))}</div>
      <h3 class="topic-title">${t("pages.text23")}</h3>
      <textarea class="textarea" id="custom-title" rows="3" maxlength="120" placeholder="${t("pages.text24")}"></textarea>
      <button class="btn btn-primary" data-start-custom>${icon("practice", 15)}${t("pages.text25")}</button>
    </article>`;
  }

  /* ------------------------------------------------------------ 录音 */

  function renderRecording() {
    const tp = st.topic;
    const size = 232;
    render(root, html`<div class="rec">
      <div class="rec-topic">
        <div class="row" style="justify-content:center;gap:6px">
          <span class="tag is-sm">${tp.category || t("pages.text19")}</span>
          ${tp.personal ? html`<span class="tag is-sm is-accent">${icon("user", 12)}${t("pages.text21")}</span>` : ""}
        </div>
        <h2 class="rec-title">${tp.title}</h2>
        ${tp.prompt ? html`<p class="rec-prompt">${tp.prompt}</p>` : ""}
        ${tp.hint ? html`<div class="topic-hint is-center">${icon("structure", 14)}<span>${tp.hint}</span></div>` : ""}
      </div>
      <div class="rec-stage">
        ${ring(100, {
          size, stroke: 8, cls: "rec-ring",
          center: html`<div><div class="rec-time num" id="rec-time">${fmtClock(tp.duration || 60)}</div><div class="rec-cap">${t("pages.text26")}</div></div>`,
        })}
      </div>
      <div class="wave" id="wave" aria-hidden="true">${Array.from({ length: WAVE_BARS }, () => html`<i></i>`)}</div>
      <div class="rec-status"><span class="rec-dot"></span><span id="rec-elapsed">${t("practice.recording", { time: "0:00" })}</span></div>
      <div class="rec-actions">
        <button class="btn btn-ghost btn-lg" data-cancel>${icon("x", 16)}${t("pages.text27")}</button>
        <button class="btn btn-primary btn-lg rec-stop" data-stop>${icon("stop", 16)}${t("pages.text28")}</button>
      </div>
      <p class="rec-tip">${th("practice.stopHint")}</p>
    </div>`);
    const fill = $(".rec-ring .ring-fill", root);
    fill.style.transition = "stroke-dashoffset 120ms linear, stroke 300ms";
    fill.style.strokeDashoffset = "0";
  }

  function fmtClock(s) {
    s = Math.max(0, Math.ceil(s));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  async function startRecording(topic) {
    try {
      await call("practice_start", topic);
    } catch (e) {
      toastError(e);
      return;
    }
    st.topic = { duration: 60, ...topic };
    st.view = "recording";
    st.stopping = false;
    st.levels.fill(0.06);
    renderRecording();
    tick();
  }

  async function tick() {
    if (st.view !== "recording") return;
    const t0 = performance.now();
    let lv;
    try {
      lv = await call("mic_level");
    } catch {
      lv = null;
    }
    if (st.view !== "recording") return;
    if (lv) {
      const dur = st.topic.duration || 60;
      const left = dur - lv.elapsed;
      const timeEl = $("#rec-time", root);
      if (timeEl) timeEl.textContent = fmtClock(left);
      const el = $("#rec-elapsed", root);
      if (el) el.textContent = t("practice.recording", { time: fmtClock(lv.elapsed) });
      const fill = $(".rec-ring .ring-fill", root);
      if (fill) {
        const c = Number(fill.getAttribute("stroke-dasharray"));
        fill.style.strokeDashoffset = String(c * Math.min(1, lv.elapsed / dur));
        fill.classList.toggle("is-ending", left <= 10);
      }
      pushLevel(lv.recording ? lv.level : 0);
      if (lv.elapsed >= dur) return stop();
    }
    st.timer = setTimeout(tick, Math.max(0, 83 - (performance.now() - t0)));
  }

  // 对称声波：中间高、两侧低的包络 × 当前音量 × 每根柱子的轻微随机起伏，逐帧平滑
  function pushLevel(level) {
    const bars = $$("#wave i", root);
    const half = (WAVE_BARS - 1) / 2;
    st.levels = st.levels.length === WAVE_BARS ? st.levels : new Array(WAVE_BARS).fill(0.06);
    bars.forEach((b, i) => {
      const d = Math.abs(i - half) / (half + 1);
      const env = Math.pow(Math.cos(d * Math.PI / 2), 1.4);
      const jitter = 0.55 + 0.45 * Math.random();
      const target = Math.max(0.06, level * 1.25 * env * jitter);
      const v = st.levels[i] + (target - st.levels[i]) * 0.55;
      st.levels[i] = v;
      b.style.transform = `scaleY(${Math.min(1, v).toFixed(3)})`;
    });
  }

  async function stop() {
    if (st.stopping) return;
    st.stopping = true;
    clearTimeout(st.timer);
    st.view = "evaluating";
    renderEvaluating({ message: t("pages.text29"), progress: 0.05 });
    try {
      const result = await runJob("practice_stop", [], (p) => updateEvaluating(p), st.abort.signal);
      st.result = result;
      st.history = await call("list_practice", 20).catch(() => st.history);
      history.replaceState(null, "", `#practice/result/${result.id}`);
      showResult();
    } catch (e) {
      if (e.aborted) return;
      toastError(e);
      st.view = "list";
      renderList();
    }
  }

  async function cancel() {
    clearTimeout(st.timer);
    st.view = "list";
    try { await call("practice_cancel"); } catch (e) { toastError(e); }
    renderList();
    toast(t("pages.text30"), "info");
  }

  function renderEvaluating(p) {
    render(root, html`<div class="rec is-eval">
      <div class="rec-topic"><h2 class="rec-title">${st.topic.title}</h2></div>
      <div class="rec-stage">
        ${ring(0, {
          size: 232, stroke: 8, cls: "rec-ring is-eval",
          center: html`<div><div class="eval-ico">${icon("sparkles", 30)}</div><div class="rec-cap" id="eval-pct">${t("pages.text31")}</div></div>`,
        })}
      </div>
      <div class="eval-msg" id="eval-msg">${p.message}</div>
      <p class="rec-tip">${t("pages.text32")}</p>
    </div>`);
    updateEvaluating(p);
  }

  function updateEvaluating(p) {
    const fill = $(".rec-ring .ring-fill", root);
    if (fill && p.progress != null) {
      const c = Number(fill.getAttribute("stroke-dasharray"));
      fill.style.strokeDashoffset = String(c * (1 - p.progress));
      const pct = $("#eval-pct", root);
      if (pct) pct.textContent = t("practice.evaluating", { n: Math.round(p.progress * 100) });
    }
    const m = $("#eval-msg", root);
    if (m && p.message) m.textContent = p.message;
  }

  /* ------------------------------------------------------------ 结果 */

  function showResult() {
    st.view = "result";
    const r = st.result;
    render(root, html`
      <header class="page-head res-head">
        <div class="grow">
          <a class="link-btn back-link" href="#practice">${icon("chevronLeft", 14)}${t("pages.text33")}</a>
          <h1 class="page-title res-title">${r.topic}</h1>
          <p class="page-sub">${fmtMD(r.created_at)} ${fmtHM(r.created_at)} · ${r.category || t("pages.text19")} · ${t("practice.duration", { n: Math.round(r.duration_s) })}</p>
        </div>
        <div class="page-actions">
          <button class="btn btn-primary" data-again>${icon("refresh", 15)}${t("pages.text34")}</button>
        </div>
      </header>

      <div class="grid-12">
        <div class="card span-5 res-score">
          ${ring(r.overall, {
            size: 136, stroke: 10,
            center: html`<div><div class="score-big num" data-count="${r.overall ?? ""}">${r.overall ?? "—"}</div><div class="score-cap">${t("pages.text35")}</div></div>`,
          })}
          <div class="res-verdict">
            <div class="res-verdict-title">${verdict(r.overall)}</div>
            <div class="res-facts">
              <span>${icon("clock", 14)}<b class="num">${Math.round(r.duration_s)}</b> ${t("common.unit.sec")}</span>
              <span>${icon("gauge", 14)}<b class="num">${fmtNum(r.cpm, 0)}</b> ${t("common.unit.cpm")}</span>
              <span>${icon("wave", 14)}${t("pages.text53")} <b class="num">${r.filler_count}</b> ${t("common.unit.times")}</span>
            </div>
          </div>
        </div>
        <div class="card span-7">
          <div class="card-head"><div class="card-title">${t("pages.text36")}</div></div>
          ${meters(r.scores, { dims: practiceDims(), names: (k) => practiceDims().find(([key]) => key === k)[1] })}
        </div>

        <div class="card span-6">
          <div class="card-head"><div class="card-title"><span class="ok-ico">${icon("check", 13)}</span>${t("pages.text37")}</div></div>
          <ul class="bullets">${r.strengths.map((s) => html`<li class="selectable">${s}</li>`)}</ul>
        </div>
        <div class="card span-6">
          <div class="card-head"><div class="card-title"><span class="ok-ico is-warn">${icon("arrowUp", 13)}</span>${t("pages.text38")}</div></div>
          <ul class="bullets is-warn">${r.improvements.map((s) => html`<li class="selectable">${s}</li>`)}</ul>
        </div>

        <div class="card span-5">
          <div class="card-head"><div class="card-title">${icon("structure", 16)}${t("pages.text39")}</div></div>
          <ol class="outline">${r.outline.map((o, i) => {
            const k = o.indexOf("：");
            return html`<li><span class="outline-no num">${i + 1}</span><span class="selectable">${k > 0 ? html`<b>${o.slice(0, k)}</b>${o.slice(k)}` : o}</span></li>`;
          })}</ol>
        </div>
        <div class="card span-7 better">
          <div class="card-head"><div class="card-title">${icon("sparkles", 16)}${t("pages.text40")}</div>
            <span class="card-sub">${t("pages.text41")}</span><span class="spacer"></span>
            <button class="btn btn-ghost btn-sm" data-copy-better>${icon("copy", 14)}${t("pages.text42")}</button></div>
          <p class="better-text selectable pretty">${r.better_version}</p>
        </div>

        <div class="card span-12">
          <div class="card-head"><div class="card-title">${t("pages.text43")}</div><span class="spacer"></span>${hlLegend(["filler", "repeat", "correction"])}</div>
          <p class="hl selectable">${highlight(r.highlights, r.raw_text)}</p>
        </div>
      </div>`);
    animateIn(root);
    $$("[data-count]", root).forEach((n) => n.dataset.count !== "" && animateNumber(n, Number(n.dataset.count), { from: 0 }));
  }

  async function openResult(id) {
    st.view = "result";
    render(root, html`<div class="card loading-card"><span class="spinner is-lg"></span></div>`);
    try {
      st.result = await call("get_practice", id);
      showResult();
    } catch (e) {
      toastError(e);
      ctx.app.navigate("practice", { replace: true });
    }
  }

  /* ------------------------------------------------------------ 题目生成 */

  async function generate(refresh) {
    st.gen = { message: t("pages.text44"), progress: null };
    renderList();
    try {
      st.topics = await runJob("generate_topics", [refresh], (p) => {
        st.gen = p;
        const box = $(".pr-gen", root);
        if (box) render(box, jobBox(p.message, p.progress));
      }, st.abort.signal);
      st.gen = null;
      renderList();
      toast(refresh ? t("pages.text45") : t("pages.text46"));
    } catch (e) {
      st.gen = null;
      if (e.aborted) return;
      renderList();
      toastError(e);
    }
  }

  /* ------------------------------------------------------------ 事件 */

  const startFromCard = (card) => {
    const tp = st.topics?.topics?.[Number(card.dataset.start)];
    if (tp) startRecording(tp);
  };
  const onKey = (e) => {
    if (st.view === "recording" && e.key === "Escape") cancel();
    if (st.view === "list" && (e.key === "Enter" || e.key === " ") && e.target.matches?.(".topic[data-start]")) { e.preventDefault(); startFromCard(e.target); }
  };
  document.addEventListener("keydown", onKey);

  const offs = [
    on(root, "click", "[data-start]", (e, card) => startFromCard(card)),
    on(root, "click", "[data-start-custom]", () => {
      const title = $("#custom-title", root).value.trim();
      if (!title) { $("#custom-title", root).focus(); toast(t("pages.text47"), "info"); return; }
      startRecording({ title, duration: st.customDuration });
    }),
    on(root, "click", '[data-seg="dur"] button', (e, b) => {
      st.customDuration = Number(b.dataset.v);
      $$('[data-seg="dur"] button', root).forEach((x) => { x.classList.toggle("is-active", x === b); x.setAttribute("aria-selected", String(x === b)); });
    }),
    on(root, "click", "[data-refresh]", () => generate(true)),
    on(root, "click", "[data-generate]", () => generate(false)),
    on(root, "click", "[data-stop]", () => stop()),
    on(root, "click", "[data-cancel]", () => cancel()),
    on(root, "click", "[data-again]", () => {
      const r = st.result;
      const tp = st.topics?.topics?.find((t) => t.title === r.topic) || { title: r.topic, category: r.category, duration: r.duration_s > 62 ? 90 : 60 };
      startRecording(tp);
    }),
    on(root, "click", "[data-copy-better]", () => copyText(st.result.better_version, call)),
  ];

  /* ------------------------------------------------------------ 路由 */

  async function applyParams(params) {
    if (st.view === "recording" || st.view === "evaluating") return; // 录音中不响应
    if (params[0] === "result" && params[1]) return openResult(Number(params[1]));
    st.view = "list";
    renderList();
  }

  const updatePage = (params) => { applyParams(params); return true; };
  const cleanup = () => {
    clearTimeout(st.timer);
    st.abort.abort();
    if (st.view === "recording") call("practice_cancel").catch(() => {});
    document.removeEventListener("keydown", onKey);
    offs.forEach((f) => f());
    if (mountApi.update === updatePage) mountApi.update = null;
  };

  try {
    const [topics, hist] = await Promise.all([call("get_practice_topics"), call("list_practice", 20)]);
    st.topics = topics;
    st.history = hist;
  } catch (e) {
    toastError(e);
    st.topics = null;
  }
  if (!root.isConnected) { cleanup(); return; }
  await applyParams(ctx.params);
  if (!root.isConnected) { cleanup(); return; }
  mountApi.update = updatePage;
  return cleanup;
}

const mountApi = { update: null };
export function update(params) {
  return mountApi.update ? mountApi.update(params) : false;
}
