import { call } from "../api.js";
import { html, render, $, $$ } from "../dom.js";
import { icon } from "../icons.js";
import { t, th } from "../i18n.js";
import { lineChart, barList, heatmap, bindHeatTips, animateIn } from "../charts.js";
import { scoreRing, meters, delta, habitCard, recentRow } from "../components.js";
import { greeting, fmtMD, fmtWeekday, todayIso, fmtInt, fmtNum, hotkeyName, emptyState, animateNumber, toastError } from "../ui.js";

export async function mount(root, { app }) {
  let data, activity;
  try {
    [data, activity] = await Promise.all([call("get_overview"), call("get_trend", 90)]);
  } catch (e) { toastError(e); return; }
  const { today, score, trend, habits, latest_diagnosis: diag, top_fillers: fillers, recent } = data;
  const hasAny = activity.length > 0 || recent.length > 0;
  const stats = [
    ["chat", "overview.inputs", today.count, "common.unit.items", t("overview.average", { n: Math.round(today.chars / Math.max(today.count, 1)) })],
    ["type", "overview.characters", today.chars, "common.unit.chars", t("overview.spoken", { n: fmtNum(today.minutes_spoken) })],
    ["zap", "overview.saved", today.minutes_saved, "common.unit.min", t("overview.typingBaseline")],
    ["wave", "overview.fluency", today.fluency, "common.unit.score", t("overview.concision", { n: today.concision ?? "—" })],
  ];
  render(root, html`
    <header class="page-head">
      <div><h1 class="page-title">${greeting()}</h1><p class="page-sub">${fmtMD(todayIso())} · ${fmtWeekday(todayIso())} · ${t("overview.todayCount", { n: today.count })}</p></div>
      <div class="page-actions"><a class="btn btn-secondary" href="#practice">${icon("practice", 15)}${t("overview.practice")}</a><a class="btn btn-primary" href="#report">${icon("report", 15)}${t("overview.report")}</a></div>
    </header>
    <section class="card overview-hero" data-tour="overview-score">
      <div class="overview-score">
        ${scoreRing(score.overall, { size: 156 })}
        <div class="overview-score-copy"><div class="eyebrow">${t("overview.expression")}</div><h2>${t("overview.scoreTitle")}</h2>
          <p>${score.date ? t("overview.diagnosedOn", { d: fmtMD(score.date) }) : t("overview.awaiting")}</p>
          <div class="score-delta">${delta(score.delta)}${score.delta == null ? "" : html`<span class="muted small">${t("overview.previous")}</span>`}</div>
          <a class="link-btn" href="#report">${t("overview.fullReport")}${icon("chevronRight", 14)}</a>
        </div>
      </div>
      <div class="overview-dimensions">${meters(score)}<p class="muted tiny">${t("overview.aiNote")}</p></div>
    </section>
    <section class="card strip overview-stats">${stats.map(([ico, label, value, unit, sub]) => html`<div class="strip-item"><div class="stat-label">${icon(ico, 15)}${t(label)}</div><div class="stat-value"><span class="num" data-count="${value ?? ""}" data-digits="${unit === "common.unit.min" ? 1 : 0}">${value == null ? "—" : fmtNum(value)}</span><span class="stat-unit">${t(unit)}</span></div><p class="stat-sub">${sub}</p></div>`)}</section>
    ${hasAny ? "" : html`<div class="card welcome-empty">${emptyState({ art: "mic", title: t("pages.text67"), desc: th("pages.startSpeaking", { hotkey: hotkeyName(app.info?.hotkey) }) })}</div>`}
    <section class="grid-12">
      <div class="card span-7"><div class="card-head"><h2 class="card-title">${t("overview.trend")}</h2><span class="card-sub">${t("pages.text6")}</span></div>
        ${trend.filter((r) => r.overall != null).length >= 2 ? html`<div class="trend-mini" id="ov-trend"></div>` : emptyState({ art: "report", title: t("pages.text99"), desc: t("pages.text100"), compact: true })}</div>
      <div class="card span-5 overview-activity"><div class="card-head"><h2 class="card-title">${t("overview.activity")}</h2><span class="card-sub">${t("overview.weeks")}</span></div>${heatmap(activity, { weeks: 12, cell: 14, gap: 4 })}</div>
      <div class="card span-7"><div class="card-head"><h2 class="card-title">${icon("target", 16)}${t("overview.habits")}</h2><span class="spacer"></span><a class="link-btn" href="#report/habits">${t("common.all")}${icon("chevronRight", 14)}</a></div>
        ${habits.length ? html`<div class="habit-list">${habits.map((h) => habitCard(h, { compact: true, actions: false }))}</div>` : emptyState({ art: "habit", title: t("pages.text120"), desc: t("pages.text121"), compact: true })}</div>
      <div class="card span-5 diag-card"><div class="card-head"><h2 class="card-title">${icon("sparkles", 16)}${t("overview.latest")}</h2>${diag ? html`<span class="tag is-sm">${fmtMD(diag.date)}</span>` : ""}</div>
        ${diag ? html`<p class="diag-summary selectable pretty">${diag.summary}</p><a class="link-btn diag-link" href="#report/${diag.date}">${t("overview.fullReport")}${icon("chevronRight", 14)}</a>` : emptyState({ art: "report", title: t("overview.noDiagnosis"), desc: t("overview.diagnosisHint"), compact: true })}</div>
      <div class="card span-7"><div class="card-head"><h2 class="card-title">${t("overview.recent")}</h2><span class="spacer"></span><a class="link-btn" href="#history">${t("common.all")}${icon("chevronRight", 14)}</a></div>${recent.length ? html`<div class="recent-list">${recent.map(recentRow)}</div>` : emptyState({ art: "mic", title: t("pages.text67"), compact: true })}</div>
      <div class="card span-5"><div class="card-head"><h2 class="card-title">${t("overview.fillers")}</h2><span class="card-sub">${t("overview.lastWeek")}</span></div>${fillers.length ? barList(fillers.map((f) => ({ label: f.word, value: f.count, note: t("common.per100", { n: fmtNum(f.per100) }) })), { unit: t("common.unit.times") }) : emptyState({ art: "habit", title: t("pages.text96"), compact: true })}</div>
    </section>`);
  $$("[data-count]", root).forEach((n) => { if (n.dataset.count !== "") animateNumber(n, Number(n.dataset.count), { from: 0, format: (v) => n.dataset.digits === "1" ? fmtNum(v) : fmtInt(v) }); });
  animateIn(root);
  $$("[data-hm]", root).forEach((cell) => {
    const label = `${fmtMD(cell.dataset.hm)} · ${t("common.items", { n: Number(cell.dataset.n) })}`;
    cell.title = label;
    cell.setAttribute("role", "img");
    cell.setAttribute("aria-label", label);
  });
  bindHeatTips($(".overview-activity", root));
  const tc = $("#ov-trend", root);
  const chart = tc ? lineChart(tc, trend, { key: "overall", days: 30, height: 148, compact: true, label: t("pages.text6") }) : null;
  return () => chart?.destroy();
}
