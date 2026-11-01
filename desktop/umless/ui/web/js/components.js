// 领域组件：分数块、维度条、习惯卡、原文标注、场景标签、任务进度、开关、分段控件。
import { html, raw } from "./dom.js";
import { icon } from "./icons.js";
import { ring } from "./charts.js";
import { t } from "./i18n.js";
import {
  sceneName, hlName, appName, fmtScore, fmtNum, fmtMD, relDay, fmtHM, isToday,
} from "./ui.js";

/** 四个维度：[key, 来源]，名称与说明随语言取 */
export const DIMS = [["fluency", "rule"], ["concision", "rule"], ["logic", "ai"], ["clarity", "ai"]];
export const dimName = (k) => t(`dim.${k}`);

/** 分数档位（参考 Oura：85+ 出色、70+ 不错，其余提示留意） */
export function verdict(score) {
  if (score == null) return { key: "none", label: t("verdict.none") };
  if (score >= 85) return { key: "great", label: t("verdict.great") };
  if (score >= 70) return { key: "good", label: t("verdict.good") };
  return { key: "watch", label: t("verdict.watch") };
}

/** 分数变化：▲ 3 / ▼ 2 / 持平 */
export function delta(d, { title = "" } = {}) {
  if (d == null || Number.isNaN(d)) return "";
  const v = Math.round(d);
  if (v === 0) return html`<span class="delta is-flat" title="${title}">${t("delta.flat")}</span>`;
  return html`<span class="delta ${v > 0 ? "is-up" : "is-down"}" title="${title}">${icon(v > 0 ? "triUp" : "triDown", 12)}${Math.abs(v)}</span>`;
}

/** 维度分数条。deltas 为各维度相对基线的变化（可选） */
export function meters(scores, { dims = DIMS, deltas = null, names = dimName } = {}) {
  return html`<div class="meters">
    ${dims.map(([k]) => {
      const v = scores?.[k];
      return html`<div class="meter-row">
        <span class="label">${names(k)}</span>
        <div class="bar"><i style="width:0" data-w="${v == null ? 0 : v}%"></i></div>
        <span class="val num ${v == null ? "is-null" : ""}">${fmtScore(v)}</span>
        ${deltas ? html`<span class="meter-delta">${delta(deltas[k], { title: t("delta.vsBaseline") })}</span>` : ""}
      </div>`;
    })}
  </div>`;
}

/** 环形综合分 + 档位词 */
export function scoreRing(overall, { size = 132, stroke = 8 } = {}) {
  const v = verdict(overall);
  const center = html`<div>
    <div class="score-big num" data-count="${overall ?? ""}">${fmtScore(overall)}</div>
    <div class="score-verdict is-${v.key}">${overall == null ? t("score.noOverall") : v.label}</div>
  </div>`;
  return ring(overall, { size, stroke, center, cls: "score-ring" });
}

/* ---------------------------------------------------------------- 原文标注 */

/** highlights: [[文本, 类型], ...]，返回转义后的 HTML */
export function highlight(highlights, fallback = "") {
  if (!Array.isArray(highlights) || !highlights.length) return html`<span>${fallback}</span>`;
  return html`${highlights.map(([text, kind]) =>
    kind === "keep" || !["filler", "repeat", "correction", "cut"].includes(kind)
      ? html`${text}`
      : html`<mark class="m-${kind}" title="${hlName(kind)}">${text}</mark>`)}`;
}

export function hlLegend(kinds = ["filler", "repeat", "correction", "cut"]) {
  return html`<div class="hl-legend">${kinds.map((k) => html`<span><i class="k-${k}"></i>${hlName(k)}</span>`)}</div>`;
}

/* ---------------------------------------------------------------- 场景 / 应用 */

export function sceneTag(scene) {
  const s = ["chat", "document", "code", "email"].includes(scene) ? scene : "default";
  return html`<span class="scene-tag"><span class="scene-dot scene-${s}"></span>${sceneName(s)}</span>`;
}

export function appBadge(exe) {
  const name = appName(exe);
  return html`<span class="app-badge"><span class="app-ava">${[...name][0]}</span>${name}</span>`;
}

/** 概览“最近记录”行 */
export function recentRow(item) {
  return html`<a class="recent-row" href="#history/${item.id}">
    <span class="recent-time num">${isToday(item.created_at) ? fmtHM(item.created_at) : relDay(item.created_at)}</span>
    <span class="recent-text ellipsis">${item.output_text}</span>
    <span class="recent-meta">${appBadge(item.app)}</span>
  </a>`;
}

/* ---------------------------------------------------------------- 习惯卡 */

const HABIT_ICON = { word: "chat", repeat: "repeat", correction: "pen", logic: "structure", clarity: "sun" };

/** 习惯的单位显示 */
const habitUnit = (h) => (h.unit === "分" ? t("common.unit.score") : h.unit === "次/百字" ? t("common.unit.per100") : h.unit || "");

/** 基线 → 当前 → 目标 三点进度轴 */
function habitTrack(h, pct) {
  const f = (v) => fmtNum(v, h.unit === "分" ? 0 : 1);
  const pos = Math.max(0, Math.min(100, pct));
  return html`<div class="htrack" style="--p:${pos}%">
    <div class="htrack-line"><i class="htrack-fill"></i><i class="htrack-knob"></i></div>
    <div class="htrack-labels">
      <span><span class="htrack-k">${t("habit.baseline")}</span><b class="num">${f(h.baseline)}</b></span>
      <span class="htrack-cur"><span class="htrack-k">${t("habit.current")}</span><b class="num">${f(h.current)}</b></span>
      <span class="is-end"><span class="htrack-k">${t("habit.target")}</span><b class="num">${f(h.target)}</b></span>
    </div>
  </div>`;
}

export function habitCard(h, { actions = true, compact = false, evidence = false } = {}) {
  const pct = Math.round((h.progress ?? 0) * 100);
  const achieved = h.status === "achieved";
  const archived = h.status === "archived";
  if (compact) {
    return html`<div class="habit is-compact ${achieved ? "is-achieved" : ""}" data-habit="${h.id}">
      <div class="habit-top">
        <span class="habit-ico">${icon(achieved ? "trophy" : HABIT_ICON[h.kind] || "target", 15)}</span>
        <span class="habit-title grow ellipsis">${h.title}</span>
        <span class="habit-pct num">${pct}%</span>
      </div>
      <div class="bar"><i style="width:0" data-w="${pct}%"></i></div>
      <div class="habit-mini muted">${t("habit.miniLine", {
        base: fmtNum(h.baseline, h.unit === "分" ? 0 : 1), cur: fmtNum(h.current, h.unit === "分" ? 0 : 1),
        target: fmtNum(h.target, h.unit === "分" ? 0 : 1), unit: habitUnit(h),
      })}</div>
    </div>`;
  }
  return html`<div class="habit ${archived ? "is-archived" : ""} ${achieved ? "is-achieved" : ""}" data-habit="${h.id}">
    <div class="habit-top">
      <span class="habit-ico">${icon(achieved ? "trophy" : HABIT_ICON[h.kind] || "target", 17)}</span>
      <div class="grow">
        <div class="habit-title">${h.title}</div>
        <div class="habit-advice">${h.advice}</div>
      </div>
      ${achieved ? html`<span class="tag is-accent is-sm">${icon("check", 12)}${t("habit.achieved")}</span>`
        : archived ? html`<span class="tag is-sm">${t("habit.archived")}</span>`
        : html`<span class="habit-pct num">${pct}%</span>`}
    </div>
    ${habitTrack(h, pct)}
    <div class="habit-unit">${habitUnit(h)}</div>
    ${evidence && h.evidence?.length ? html`<div class="habit-evi"><span class="habit-evi-k">${t("habit.example")}</span><span class="selectable">${h.evidence[0]}</span></div>` : ""}
    ${actions ? html`<div class="habit-foot">
      <span class="muted tiny">${achieved && h.achieved_at ? t("habit.achievedOn", { d: fmtMD(h.achieved_at) }) : h.created_at ? t("habit.startedOn", { d: fmtMD(h.created_at) }) : ""}</span>
      <span class="grow"></span>
      ${archived
        ? html`<button class="btn btn-ghost btn-sm" data-restore="${h.id}">${icon("undo", 14)}${t("habit.restore")}</button>`
        : html`<button class="btn btn-ghost btn-sm" data-archive="${h.id}">${icon("archive", 14)}${t("habit.archive")}</button>`}
    </div>` : ""}
  </div>`;
}

/* ---------------------------------------------------------------- 控件 */

export function toggle(name, checked, { disabled = false } = {}) {
  return html`<label class="switch"><input type="checkbox" data-key="${name}" ${checked ? raw("checked") : ""} ${disabled ? raw("disabled") : ""}><span class="track"></span></label>`;
}

export function segmented(name, options, active, { cls = "" } = {}) {
  return html`<div class="segmented ${cls}" role="tablist" data-seg="${name}">
    ${options.map(([v, label, ico]) => html`<button type="button" role="tab" data-v="${v}" class="${v === active ? "is-active" : ""}" aria-selected="${v === active}">${ico ? icon(ico, 14) : ""}${label}</button>`)}
  </div>`;
}

/** 耗时任务进度 */
export function jobBox(message, progress) {
  const ind = progress == null;
  return html`<div class="job">
    <div class="job-msg"><span class="row" style="gap:10px"><span class="spinner"></span>${message || t("common.processing")}</span>
      ${ind ? "" : html`<span class="num muted">${Math.round(progress * 100)}%</span>`}</div>
    <div class="bar is-thin ${ind ? "is-indeterminate" : ""}"><i style="width:${ind ? 35 : Math.round(progress * 100)}%"></i></div>
  </div>`;
}

/** ⓘ 按钮：点击后由页面调用 popover() 显示说明 */
export const infoBtn = (key) => html`<button class="info-btn" data-info="${key}" aria-label="${t("common.howCalculated")}">${icon("info", 15)}</button>`;
