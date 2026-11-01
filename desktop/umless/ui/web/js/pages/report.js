import { t } from "../i18n.js";
// 报告：左侧日期列表 + 右侧日报详情（分数、总结、亮点、问题、对比学习、口癖、趋势、新发现）；习惯卡分页。
import { call, runJob } from "../api.js";
import { html, render, $, $$, on } from "../dom.js";
import { icon } from "../icons.js";
import { lineChart, barList, animateIn } from "../charts.js";
import { scoreRing, meters, delta, habitCard, segmented, jobBox, DIMS, dimName } from "../components.js";
import {
  fmtMD, fmtWeekday, fmtHM, fmtNum, fmtPct, relDay, todayIso, issueType, parseDate, isoDate, emptyState, toast, toastError,
  animateNumber,
} from "../ui.js";

const metrics = () => [["overall", t("pages.text6")], ["fluency", t("pages.text3")], ["concision", t("pages.text4")], ["logic", t("pages.text2")], ["clarity", t("pages.text5")]];
const habitTabs = () => [["active", t("pages.text76")], ["achieved", t("pages.text77")], ["archived", t("pages.text78")]];

export async function mount(root, ctx) {
  const st = {
    tab: "daily",
    list: null,
    date: null,
    report: undefined,
    job: null, // {message, progress, date}
    abort: new AbortController(),
    trend: { days: 30, metric: "overall", data: [] },
    habits: [],
    habitTab: "active",
    chart: null,
  };

  root.innerHTML = "";
  const head = document.createElement("div");
  const body = document.createElement("div");
  root.append(head, body);

  function renderHead() {
    render(head, html`<header class="page-head">
      <div>
        <h1 class="page-title">${t("pages.text73")}</h1>
        <p class="page-sub">${t("pages.text74")}</p>
      </div>
      <div class="page-actions" data-tour="report-habits">
        ${segmented("tab", [["daily", t("pages.text75"), "calendar"], ["habits", `${t("report.habits")}${countActive() ? ` · ${countActive()}` : ""}`, "target"]], st.tab, { cls: "is-lg" })}
      </div>
    </header>`);
  }
  const countActive = () => st.habits.filter((h) => h.status === "active").length;

  /* ------------------------------------------------------------ 数据 */

  async function loadList() {
    st.list = await call("list_reports");
  }
  async function loadHabits() {
    st.habits = await call("get_habits", null);
  }
  async function loadReport(date) {
    st.date = date;
    st.report = undefined;
    renderBody();
    try {
      const [r, t] = await Promise.all([call("get_report", date), call("get_trend", 90)]);
      if (st.date !== date) return;
      st.report = r;
      st.trend.data = t;
    } catch (e) {
      if (st.date !== date || !root.isConnected) return;
      toastError(e);
      st.report = null;
    }
    renderBody();
  }

  /* ------------------------------------------------------------ 渲染 */

  function chartRows() {
    const start = parseDate(todayIso()); start.setDate(start.getDate() - st.trend.days + 1);
    return st.trend.data.filter((row) => row.date >= isoDate(start));
  }

  function renderBody() {
    if (!root.isConnected) return;
    st.chart?.destroy();
    st.chart = null;
    if (st.tab === "habits") return renderHabits();
    const { list } = st;
    const hasAny = list && (list.today.n > 0 || list.items.length > 0 || st.report?.n > 0);
    if (!hasAny) {
      render(body, html`<div class="card">
        <label class="report-date-label" for="report-date">${t("report.chooseDate")}</label>
        <input id="report-date" class="input report-date" type="date" value="${st.date || todayIso()}" max="${todayIso()}">
        ${emptyState({
        art: "report", title: t("pages.text79"),
        desc: t("pages.text80"),
        action: html`<div class="col" style="align-items:center" data-tour="report-start">
          <button class="btn btn-primary" data-tour="report-diagnose" data-diagnose disabled aria-describedby="report-start-hint">
            ${icon("sparkles", 15)}${st.date && st.date !== todayIso() ? t("pages.text88") : t("pages.text87")}
          </button>
          <p id="report-start-hint" class="muted small">${t("report.startHint")}</p>
        </div>`,
      })}</div>`);
      return;
    }
    render(body, html`<div class="report-layout">
      <aside class="date-col">
        <label class="report-date-label" for="report-date">${t("report.chooseDate")}</label>
        <input id="report-date" class="input report-date" type="date" value="${st.date || list.today.date}" max="${todayIso()}">
        <div class="card is-flush date-list" role="listbox" aria-label="${t("pages.text81")}">
          ${dateItem(list.today.date, { today: true, n: list.today.n, diagnosed: list.today.diagnosed, overall: list.today.diagnosed && st.report?.date === list.today.date ? st.report.scores.overall : null })}
          ${list.items.length ? html`<div class="date-sep">${t("pages.text82")}</div>` : ""}
          ${list.items.filter((it) => it.date !== list.today.date).map((it) => dateItem(it.date, { n: it.n, diagnosed: true, overall: it.overall }))}
        </div>
      </aside>
      <div class="report-main" id="rp-main">${detail()}</div>
    </div>`);
    animateIn(body);
    $$(".report-main [data-count]", body).forEach((n) => n.dataset.count !== "" && animateNumber(n, Number(n.dataset.count), { from: 0 }));
    const tc = $("#rp-trend", body);
    if (tc) {
      st.chart = lineChart(tc, chartRows(), {
        key: st.trend.metric, days: st.trend.days, height: 220,
        label: metrics().find((m) => m[0] === st.trend.metric)[1],
        tooltip: (r) => html`<div class="tt-title">${fmtMD(r.date)} · ${t("common.items", { n: r.count })}</div>
          ${metrics().map(([k, l]) => html`<div class="tt-row ${k === st.trend.metric ? "is-main" : ""}">${k === st.trend.metric ? html`<span class="tt-key"></span>` : ""}${l}<span class="tt-val">${r[k] ?? "—"}</span></div>`)}`,
      });
    }
  }

  function dateItem(date, { today = false, n, diagnosed, overall }) {
    const active = st.date === date;
    return html`<button class="date-item ${active ? "is-active" : ""} ${today ? "is-today" : ""}" data-date="${date}" role="option" aria-selected="${active}">
      <span class="grow">
        <span class="date-main">${today ? t("pages.text83") : fmtMD(date)}<span class="date-week">${today ? fmtMD(date) : fmtWeekday(date)}</span></span>
        <span class="date-meta">${t("common.items", { n })}${today && !diagnosed ? ` · ${t("report.notDiagnosed")}` : ""}</span>
      </span>
      ${overall != null ? html`<span class="date-score num">${overall}</span>` : today && !diagnosed ? html`<span class="date-dot"></span>` : ""}
    </button>`;
  }

  function weekDelta(key, report) {
    const end = parseDate(report.date);
    const start = new Date(end); start.setDate(start.getDate() - 7);
    const values = st.trend.data.filter((row) => row.date >= isoDate(start) && row.date < report.date && row[key] != null).map((row) => row[key]);
    return values.length && report.scores[key] != null ? report.scores[key] - values.reduce((a, b) => a + b, 0) / values.length : null;
  }

  function detail() {
    const r = st.report;
    if (r === undefined) return html`<div class="card loading-card"><span class="spinner is-lg"></span></div>`;
    if (r === null) {
      return html`<div class="card">${emptyState({ art: "report", title: t("pages.text84"), desc: t("pages.text85") })}</div>`;
    }
    const isToday = r.date === todayIso();
    const running = st.job && st.job.date === r.date;
    const canDiagnose = r.n >= 3;
    const diagBtn = html`<button class="btn ${r.diagnosed ? "btn-secondary" : "btn-primary"}" data-tour="report-diagnose" data-diagnose ${running || !canDiagnose ? "disabled" : ""}>
      ${icon(r.diagnosed ? "refresh" : "sparkles", 15)}${r.diagnosed ? t("pages.text86") : isToday ? t("pages.text87") : t("pages.text88")}</button>`;

    return html`
      <div class="rp-head">
        <div>
          <h2 class="rp-title">${isToday ? t("pages.text83") : fmtMD(r.date)}<span class="rp-week">${isToday ? `${fmtMD(r.date)} ${fmtWeekday(r.date)}` : fmtWeekday(r.date)}</span></h2>
          <p class="rp-meta">${t("common.items", { n: r.n })}${r.diagnosed && r.created_at ? html` · ${t("report.generated", { model: r.model || "AI", date: `${relDay(r.created_at)} ${fmtHM(r.created_at)}` })}` : ` · ${t("report.notDiagnosed")}`}</p>
        </div>
        ${diagBtn}
      </div>
      ${running ? html`<div class="rp-job">${jobBox(st.job.message, st.job.progress)}</div>` : ""}

      <div class="grid-12">
        <div class="card span-8">
          <div class="card-head"><div class="card-title">${t("pages.text89")}</div>
            ${r.diagnosed ? "" : html`<span class="card-sub">${t("pages.text90")}</span>`}</div>
          <div class="score-block">${scoreRing(r.scores.overall, { size: 124 })}${meters(r.scores)}</div>
          <div class="dimension-details">${DIMS.map(([key]) => html`<div><h3>${dimName(key)} ${delta(weekDelta(key, r), { title: t("report.weekDelta") })}</h3><p>${t(`report.explain.${key}`)}</p></div>`)}</div>
          <p class="muted tiny">${t("report.weekDelta")}</p>
        </div>
        <div class="card span-4 rp-facts">
          <div class="fact"><span class="fact-label">${icon("chat", 14)}${t("pages.text91")}</span><span class="fact-val"><b class="num">${r.n}</b> ${t("common.unit.items")}</span></div>
          <div class="fact"><span class="fact-label">${icon("wand", 14)}${t("pages.text92")}</span><span class="fact-val"><b class="num">${fmtPct(r.rewrite_ratio)}</b></span></div>
          <div class="fact"><span class="fact-label">${icon("wave", 14)}${t("pages.text53")}</span><span class="fact-val"><b class="num">${r.filler_stats.reduce((a, b) => a + b.count, 0)}</b> ${t("common.unit.times")}</span></div>
          <p class="fact-note">${t("pages.text93")}</p>
        </div>
      </div>

      ${r.diagnosed ? diagnosedSections(r) : undiagnosedCta(r, canDiagnose, isToday, running)}

      <div class="grid-12 rp-gap">
        <div class="card ${r.diagnosed ? "span-6" : "span-12"}">
          <div class="card-head"><div class="card-title">${t("pages.text94")}</div><span class="card-sub">${t("pages.text95")}</span></div>
          ${r.filler_stats.length
            ? barList(r.filler_stats.map((f) => ({ label: f.word, value: f.count, note: t("common.per100", { n: fmtNum(f.per100) }) })), { unit: t("common.unit.times") })
            : emptyState({ art: "habit", title: t("pages.text96"), desc: t("pages.text97"), compact: true })}
        </div>
        ${r.diagnosed ? discoveries(r) : ""}
      </div>

      <div class="card rp-gap">
        <div class="card-head">
          <div class="card-title">${t("pages.text98")}</div>
          <span class="spacer"></span>
          ${segmented("metric", metrics(), st.trend.metric)}
          ${segmented("days", [["7", t("pages.text130")], ["30", t("pages.text131")], ["90", t("pages.text132")]], String(st.trend.days))}
        </div>
        ${chartRows().filter((row) => row[st.trend.metric] != null).length >= 2 ? html`<div id="rp-trend" class="rp-trend"></div>`
          : emptyState({ art: "report", title: t("pages.text99"), desc: t("pages.text100"), compact: true })}
      </div>`;
  }

  function undiagnosedCta(r, canDiagnose, isToday, running) {
    return html`<div class="card rp-gap cta-card">
      <div class="cta-art">${icon("sparkles", 26)}</div>
      <div class="grow">
        <div class="cta-title">${t("report.notDiagnosed")}</div>
        <p class="cta-desc">${t("report.analysisDesc", { n: r.n })}</p>
        ${canDiagnose ? "" : html`<p class="cta-warn">${icon("info", 14)}${t("pages.text102")}</p>`}
      </div>
      <button class="btn btn-primary btn-lg" data-diagnose ${running || !canDiagnose ? "disabled" : ""}>${icon("sparkles", 16)}${isToday ? t("pages.text87") : t("pages.text103")}</button>
    </div>`;
  }

  function diagnosedSections(r) {
    return html`
      <div class="card rp-gap summary-card">
        <div class="card-head"><div class="card-title">${icon("sparkles", 16)}${t("pages.text104")}</div></div>
        <p class="summary-text selectable pretty">${r.summary}</p>
        ${r.strengths.length ? html`<div class="strengths">
          <div class="mini-title">${t("pages.text105")}</div>
          <ul>${r.strengths.map((s) => html`<li><span class="ok-ico">${icon("check", 13)}</span><span class="selectable">${s}</span></li>`)}</ul>
        </div>` : ""}
      </div>

      ${r.issues.length ? html`<h3 class="section-title">${t("pages.text106")}<span class="muted">${t("report.issueCount", { n: r.issues.length })}</span></h3>
      <div class="issues">${r.issues.map((it, i) => {
        const [label, cls] = issueType(it.type);
        return html`<article class="card issue">
          <div class="issue-head"><span class="issue-no num">${i + 1}</span><span class="issue-title">${it.title}</span><span class="tag is-sm ${cls}">${label}</span></div>
          ${it.evidence?.length ? html`<div class="evidence">${it.evidence.map((e) => html`<blockquote class="selectable">${e}</blockquote>`)}</div>` : ""}
          ${it.advice ? html`<div class="advice">${icon("bulb", 16)}<span class="selectable">${it.advice}</span></div>` : ""}
        </article>`;
      })}</div>` : ""}

      ${r.examples.length ? html`<h3 class="section-title">${icon("sparkles", 16)}${t("pages.text108")}<span class="muted">${t("pages.text109")}</span></h3>
      <div class="examples">${r.examples.map((ex) => html`<article class="example">
        <div class="ex-pair">
          <div class="ex-side ex-before">
            <div class="ex-label">${t("pages.text110")}</div>
            <p class="selectable">${ex.original}</p>
          </div>
          <div class="ex-arrow">${icon("arrowRight", 16)}</div>
          <div class="ex-side ex-after">
            <div class="ex-label">${icon("sparkles", 13)}${t("pages.text111")}</div>
            <p class="selectable">${ex.better}</p>
          </div>
        </div>
        <div class="ex-why">${icon("bulb", 15)}<span class="selectable">${ex.why}</span></div>
      </article>`)}</div>` : ""}`;
  }

  function discoveries(r) {
    const nothing = !r.personal_fillers.length && !r.terms.length;
    return html`<div class="card span-6">
      <div class="card-head"><div class="card-title">${t("pages.text112")}</div><span class="card-sub">${t("pages.text113")}</span></div>
      ${nothing ? emptyState({ art: "search", title: t("pages.text114"), compact: true }) : ""}
      ${r.personal_fillers.length ? html`<div class="disc-group">
        <div class="mini-title">${t("pages.text115")}</div>
        ${r.personal_fillers.map((f) => html`<div class="disc-row">
          <span class="disc-word">「${f.word}」</span><span class="grow"></span>
          ${addBtn(f.added, "filler", f.word, t("pages.text116"))}
        </div>`)}
      </div>` : ""}
      ${r.terms.length ? html`<div class="disc-group">
        <div class="mini-title">${t("pages.text117")}</div>
        ${r.terms.map((term) => html`<div class="disc-row">
          <span class="term-wrong">${term.wrong}</span>${icon("arrowRight", 13, "term-arrow")}<span class="term-right">${term.right}</span><span class="grow"></span>
          ${addBtn(term.added, "term", term.right, t("pages.text118"))}
        </div>`)}
      </div>` : ""}
    </div>`;
  }

  function addBtn(added, kind, value, label) {
    return added
      ? html`<button class="btn btn-sm is-done" disabled>${icon("check", 14)}${t("pages.text119")}</button>`
      : html`<button class="btn btn-secondary btn-sm" data-add="${kind}" data-value="${value}">${icon("plus", 14)}${label}</button>`;
  }

  function renderHabits() {
    const counts = Object.fromEntries(habitTabs().map(([k]) => [k, st.habits.filter((h) => h.status === k).length]));
    const items = st.habits.filter((h) => h.status === st.habitTab);
    const empties = {
      active: [t("pages.text120"), t("pages.text121")],
      achieved: [t("pages.text122"), t("pages.text123")],
      archived: [t("pages.text124"), t("pages.text125")],
    };
    render(body, html`
      <div class="habit-toolbar">
        ${segmented("habitTab", habitTabs().map(([k, l]) => [k, `${l} ${counts[k]}`]), st.habitTab)}
        <span class="muted small">${t("pages.text126")}</span>
      </div>
      ${items.length ? html`<div class="habit-grid">${items.map((h) => html`<div class="card habit-card">${habitCard(h, { evidence: true })}</div>`)}</div>`
        : html`<div class="card">${emptyState({ art: "habit", title: empties[st.habitTab][0], desc: empties[st.habitTab][1] })}</div>`}`);
    animateIn(body);
  }

  /* ------------------------------------------------------------ 交互 */

  async function diagnose() {
    if (st.job || !st.report || st.report.n < 3) return;
    const date = st.date;
    st.job = { date, message: t("pages.text127"), progress: 0 };
    renderBody();
    try {
      const result = await runJob("run_diagnosis", [date], (p) => {
        st.job = { date, ...p };
        const box = $(".rp-job", body);
        if (box && st.date === date) render(box, jobBox(p.message, p.progress));
      }, st.abort.signal);
      st.job = null;
      await Promise.all([loadList(), loadHabits()]);
      renderHead();
      if (st.date === date) {
        st.report = result;
        st.trend.data = await call("get_trend", 90);
      }
      renderBody();
      toast(t("pages.text128"));
    } catch (e) {
      st.job = null;
      if (e.aborted) return;
      renderBody();
      toastError(e);
    }
  }

  const offs = [
    on(body, "change", "#report-date", (e, input) => {
      if (!input.value || input.value > todayIso()) return;
      history.replaceState(null, "", `#report/${input.value}`);
      loadReport(input.value);
    }),
    on(head, "click", '[data-seg="tab"] button', (e, b) => {
      ctx.app.navigate(b.dataset.v === "habits" ? "report/habits" : st.date ? `report/${st.date}` : "report");
    }),
    on(body, "click", ".date-item", (e, b) => {
      history.replaceState(null, "", `#report/${b.dataset.date}`);
      $$(".date-item", body).forEach((x) => x.classList.toggle("is-active", x === b));
      loadReport(b.dataset.date);
    }),
    on(body, "click", "[data-diagnose]", () => diagnose()),
    on(body, "click", '[data-seg="metric"] button', (e, b) => {
      st.trend.metric = b.dataset.v;
      renderBody();
    }),
    on(body, "click", '[data-seg="days"] button', async (e, b) => {
      st.trend.days = Number(b.dataset.v);
      $("#rp-trend", body)?.style.setProperty("opacity", ".5");
      try { st.trend.data = await call("get_trend", 90); } catch (err) { toastError(err); }
      renderBody();
    }),
    on(body, "click", "[data-add]", async (e, b) => {
      const { add, value } = b.dataset;
      b.classList.add("is-loading");
      try {
        await call(add === "term" ? "add_term" : "add_filler", value);
        const list = add === "term" ? st.report.terms : st.report.personal_fillers;
        list.filter((x) => (add === "term" ? x.right : x.word) === value).forEach((x) => { x.added = true; });
        renderBody();
        toast(add === "term" ? t("report.termAdded", { value }) : t("report.fillerAdded", { value }));
      } catch (err) {
        b.classList.remove("is-loading");
        toastError(err);
      }
    }),
    on(body, "click", '[data-seg="habitTab"] button', (e, b) => {
      st.habitTab = b.dataset.v;
      renderHabits();
    }),
    on(body, "click", "[data-archive], [data-restore]", async (e, b) => {
      const archive = "archive" in b.dataset;
      const id = Number(archive ? b.dataset.archive : b.dataset.restore);
      b.classList.add("is-loading");
      try {
        await call(archive ? "archive_habit" : "restore_habit", id);
        await loadHabits();
        renderHead();
        renderHabits();
        toast(archive ? t("pages.text78") : t("pages.text129"));
      } catch (err) {
        b.classList.remove("is-loading");
        toastError(err);
      }
    }),
  ];

  /* ------------------------------------------------------------ 启动 */

  async function applyParams(params) {
    const p = params[0];
    st.tab = p === "habits" ? "habits" : "daily";
    renderHead();
    if (st.tab === "habits") return renderBody();
    let date = /^\d{4}-\d{2}-\d{2}$/.test(p || "") ? p : null;
    if (!date) {
      const { today, items } = st.list;
      // 默认：今天已诊断则看今天，否则看最近一次诊断；都没有时看今天
      date = today.diagnosed || !items.length ? today.date : items[0].date;
    }
    if (date !== st.date || st.report === undefined) await loadReport(date);
    else renderBody();
  }

  const updatePage = (params) => { applyParams(params); return true; };
  const cleanup = () => {
    st.abort.abort();
    st.chart?.destroy();
    offs.forEach((f) => f());
    if (mountApi.update === updatePage) mountApi.update = null;
  };

  try {
    await Promise.all([loadList(), loadHabits()]);
  } catch (e) {
    toastError(e);
    cleanup();
    return;
  }
  if (!root.isConnected) { cleanup(); return; }
  await applyParams(ctx.params);
  if (!root.isConnected) { cleanup(); return; }
  mountApi.update = updatePage;
  return cleanup;
}

// 路由在同一页面内切换参数时调用（日期 / 习惯卡）
const mountApi = { update: null };
export function update(params) {
  return mountApi.update ? mountApi.update(params) : false;
}
