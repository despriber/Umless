import { t, th } from "../i18n.js";
// 历史：搜索、按日分组、加载更多；展开查看原文标注、指标，复制 / 删除。
import { call } from "../api.js";
import { html, render, $, on } from "../dom.js";
import { icon } from "../icons.js";
import { highlight, hlLegend, sceneTag, appBadge } from "../components.js";
import {
  intensityName, hotkeyName, fmtInt, fmtHM, fmtMD, fmtWeekday, fmtMs, fmtNum, fmtPct, relDay, debounce, emptyState,
  toast, toastError, confirmDialog, copyText,
} from "../ui.js";

const PAGE = 30;

/** 在文本中标出搜索词（全部经过转义） */
function markQuery(text, q) {
  if (!q) return html`${text}`;
  const lower = text.toLowerCase();
  const ql = q.toLowerCase();
  const parts = [];
  let i = 0;
  for (;;) {
    const j = lower.indexOf(ql, i);
    if (j < 0) { parts.push(html`${text.slice(i)}`); break; }
    parts.push(html`${text.slice(i, j)}<mark class="q">${text.slice(j, j + q.length)}</mark>`);
    i = j + q.length;
  }
  return html`${parts}`;
}

export async function mount(root, ctx) {
  const st = { q: "", items: [], total: 0, loading: false, open: new Set(), focusId: null, request: 0 };
  const hotkey = hotkeyName(ctx.app.info?.hotkey);

  render(root, html`
    <header class="page-head">
      <div>
        <h1 class="page-title">${t("pages.text48")}</h1>
        <p class="page-sub" id="hs-sub">${t("pages.text49")}</p>
      </div>
      <div class="page-actions">
        <div class="input-group hs-search">
          <span class="lead">${icon("search", 16)}</span>
          <input class="input has-lead" id="hs-q" type="search" placeholder="${t("pages.text50")}" autocomplete="off" spellcheck="false">
          <button class="btn btn-ghost btn-icon btn-sm trail" id="hs-clear" hidden aria-label="${t("pages.text51")}">${icon("x", 14)}</button>
        </div>
      </div>
    </header>
    <div id="hs-list" data-tour="history-list"></div>
    <div id="hs-more" class="hs-more"></div>`);

  const listEl = $("#hs-list", root);
  const moreEl = $("#hs-more", root);
  const subEl = $("#hs-sub", root);
  const qEl = $("#hs-q", root);

  function itemHtml(it) {
    const open = st.open.has(it.id);
    return html`<div class="hist-item ${open ? "is-open" : ""}" data-id="${it.id}">
      <button class="hist-head" aria-expanded="${open}">
        <span class="hist-time num">${fmtHM(it.created_at)}</span>
        <span class="hist-text ${open ? "" : "clamp-2"}">${markQuery(it.output_text, st.q)}</span>
        <span class="hist-meta">${appBadge(it.app)}${sceneTag(it.scene)}</span>
        ${icon("chevronDown", 16, "hist-chev")}
      </button>
      ${open ? detailHtml(it) : ""}
    </div>`;
  }

  function detailHtml(it) {
    const chip = (label, val, cls = "") => html`<span class="metric ${cls}"><span class="metric-k">${label}</span><b class="num">${val}</b></span>`;
    return html`<div class="hist-body">
      <div class="hist-raw-head"><span class="mini-title">${t("pages.text52")}</span><span class="grow"></span>${hlLegend()}</div>
      <p class="hl hist-raw selectable">${highlight(it.highlights, it.raw_text)}</p>
      <div class="metrics">
        ${chip(t("pages.text53"), it.filler_count, it.filler_count ? "is-filler" : "")}
        ${chip(t("pages.text54"), it.repeat_count, it.repeat_count ? "is-repeat" : "")}
        ${chip(t("pages.text55"), it.correction_count, it.correction_count ? "is-correction" : "")}
        ${chip(t("pages.text56"), it.fluency ?? "—")}
        ${chip(t("pages.text57"), fmtPct(it.rewrite_ratio))}
        ${chip(t("pages.text58"), intensityName(it.intensity))}
        ${chip(t("pages.text59"), t("fmt.sec", { n: fmtNum(it.duration_s) }))}
        ${chip(t("pages.text60"), fmtMs(it.asr_ms))}
        ${chip(t("pages.text61"), fmtMs(it.llm_ms))}
      </div>
      <div class="hist-actions">
        <button class="btn btn-secondary btn-sm" data-copy="out">${icon("copy", 14)}${t("pages.text62")}</button>
        <button class="btn btn-ghost btn-sm" data-copy="raw">${icon("file", 14)}${t("pages.text63")}</button>
        <span class="grow"></span>
        <button class="btn btn-danger-ghost btn-sm" data-del>${icon("trash", 14)}${t("pages.text64")}</button>
      </div>
    </div>`;
  }

  function renderList() {
    subEl.textContent = st.q
      ? t("history.found", { n: fmtInt(st.total), q: st.q })
      : st.total ? t("history.total", { n: fmtInt(st.total) }) : t("pages.text49");
    if (!st.items.length) {
      render(listEl, html`<div class="card">${st.q
        ? emptyState({ art: "search", title: t("pages.text65"), desc: t("pages.text66") })
        : emptyState({ art: "mic", title: t("pages.text67"), desc: th("pages.startSpeaking", { hotkey }) })}</div>`);
      moreEl.replaceChildren();
      return;
    }
    // 按日期分组
    const groups = [];
    for (const it of st.items) {
      const day = it.created_at.slice(0, 10);
      if (!groups.length || groups[groups.length - 1].day !== day) groups.push({ day, items: [] });
      groups[groups.length - 1].items.push(it);
    }
    render(listEl, html`${groups.map((g) => html`<section class="hist-group">
      <h3 class="hist-day">${relDay(g.day)}<span class="muted">${`${fmtMD(g.day)} · ${fmtWeekday(g.day)}`}</span></h3>
      <div class="card is-flush hist-card">${g.items.map(itemHtml)}</div>
    </section>`)}`);
    renderMore();
  }

  function renderMore() {
    const left = st.total - st.items.length;
    render(moreEl, left > 0
      ? html`<button class="btn btn-secondary" data-more>${st.loading ? html`<span class="spinner"></span>` : icon("chevronDown", 15)}${t("pages.text68")}</button>
          <span class="muted small">${t("history.shown", { n: st.items.length, total: st.total })}</span>`
      : st.items.length > 8 ? html`<span class="muted small">${t("pages.text69")}</span>` : html``);
  }

  async function load(reset) {
    if (st.loading && !reset) return;
    const request = ++st.request;
    st.loading = true;
    if (!reset) renderMore();
    try {
      const res = await call("list_history", st.q, reset ? 0 : st.items.length, PAGE);
      if (request !== st.request || !root.isConnected) return;
      st.total = res.total;
      st.items = reset ? res.items : st.items.concat(res.items);
    } catch (e) {
      if (request === st.request) toastError(e);
    } finally {
      if (request === st.request) st.loading = false;
    }
    if (request !== st.request || !root.isConnected) return;
    renderList();
  }

  const search = debounce(() => {
    st.q = qEl.value.trim();
    $("#hs-clear", root).hidden = !qEl.value;
    st.open.clear();
    load(true);
  }, 280);

  const find = (node) => st.items.find((x) => x.id === Number(node.closest("[data-id]").dataset.id));

  const offs = [
    on(root, "input", "#hs-q", search),
    on(root, "click", "#hs-clear", () => { qEl.value = ""; search.flush(); qEl.focus(); }),
    on(root, "click", ".hist-head", (e, b) => {
      const it = find(b);
      if (!it) return;
      if (window.getSelection()?.toString()) return; // 正在选择文字时不折叠
      st.open.has(it.id) ? st.open.delete(it.id) : st.open.add(it.id);
      const wrap = b.closest(".hist-item");
      const fresh = document.createElement("template");
      render(fresh, itemHtml(it));
      wrap.replaceWith(fresh.content.firstElementChild);
    }),
    on(root, "click", "[data-copy]", (e, b) => {
      const it = find(b);
      copyText(b.dataset.copy === "raw" ? it.raw_text : it.output_text, call);
    }),
    on(root, "click", "[data-del]", async (e, b) => {
      const it = find(b);
      const ok = await confirmDialog({
        title: t("pages.text70"), body: t("pages.text71"), confirmText: t("pages.text64"), danger: true, iconName: "trash",
      });
      if (!ok) return;
      try {
        await call("delete_history", it.id);
        st.items = st.items.filter((x) => x.id !== it.id);
        st.total -= 1;
        renderList();
        toast(t("pages.text72"));
      } catch (err) {
        toastError(err);
      }
    }),
    on(root, "click", "[data-more]", () => load(false)),
  ];

  // #history/<id>：展开并定位到某条记录
  const focusId = Number(ctx.params[0]) || null;
  if (focusId) st.open.add(focusId);
  await load(true);
  if (focusId) $(`.hist-item[data-id="${focusId}"]`, root)?.scrollIntoView({ block: "center" });

  return () => { st.request++; search.cancel(); offs.forEach((f) => f()); };
}
