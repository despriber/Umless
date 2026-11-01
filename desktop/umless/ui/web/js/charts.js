// 手写 SVG 图表（完全离线）：折线趋势（十字准线 + 提示）、环形进度、水平条形。
// 规范：2px 线、≥8px 端点带 2px 表面色描边、10% 面积底色、发丝网格、文字用文字色而非系列色。
import { html, raw, esc } from "./dom.js";
import { showTip, hideTip, parseDate, isoDate, fmtShortMD, fmtMD, fmtInt } from "./ui.js";
import { t } from "./i18n.js";

let gradSeq = 0;

const NS = "http://www.w3.org/2000/svg";

/* ---------------------------------------------------------------- 折线 */

/** 单调三次插值（Fritsch–Carlson），平滑但不过冲 */
function monotonePath(pts) {
  const n = pts.length;
  if (n === 0) return "";
  if (n === 1) return `M${pts[0][0]},${pts[0][1]}`;
  if (n === 2) return `M${pts[0][0]},${pts[0][1]}L${pts[1][0]},${pts[1][1]}`;
  const dx = [], dy = [], m = [];
  for (let i = 0; i < n - 1; i++) {
    dx.push(pts[i + 1][0] - pts[i][0]);
    dy.push(pts[i + 1][1] - pts[i][1]);
    m.push(dy[i] / dx[i]);
  }
  const t = [m[0]];
  for (let i = 1; i < n - 1; i++) t.push(m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2);
  t.push(m[n - 2]);
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
    const a = t[i] / m[i], b = t[i + 1] / m[i], s = a * a + b * b;
    if (s > 9) { const k = 3 / Math.sqrt(s); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
  }
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3;
    d += `C${pts[i][0] + h},${pts[i][1] + t[i] * h} ${pts[i + 1][0] - h},${pts[i + 1][1] - t[i + 1] * h} ${pts[i + 1][0]},${pts[i + 1][1]}`;
  }
  return d;
}

const r1 = (v) => Math.round(v * 10) / 10;

/**
 * 趋势折线图。data: [{date, <key>: number|null, ...}]
 * opts: { key, days, height, compact, tooltip(row) => SafeHtml, label }
 * 返回 { update(data, opts), destroy() }
 */
export function lineChart(container, data, opts = {}) {
  let state = { data, opts: { key: "overall", days: 30, height: 180, compact: false, ...opts } };
  container.classList.add("chart");
  const ro = new ResizeObserver(() => draw());
  ro.observe(container);
  let lastW = 0;

  function draw(force = false) {
    const w = Math.floor(container.clientWidth);
    if (!w || (!force && w === lastW)) return;
    lastW = w;
    const { key, days, height, compact, tooltip, label } = state.opts;
    const rows = state.data || [];
    const H = height;
    const padL = compact ? 2 : 30, padR = compact ? 30 : 38, padT = 12, padB = 24;
    const iw = w - padL - padR, ih = H - padT - padB;

    // x：按日期定位，缺失的日期留出空隙
    const end = parseDate(isoDate(new Date()));
    const start = new Date(end); start.setDate(end.getDate() - (days - 1));
    const span = Math.max(1, (end - start) / 86400000);
    const xOf = (ds) => padL + ((parseDate(ds) - start) / 86400000 / span) * iw;

    const vals = rows.map((r) => r[key]).filter((v) => v != null);
    let lo = vals.length ? Math.min(...vals) : 50, hi = vals.length ? Math.max(...vals) : 100;
    lo = Math.max(0, Math.floor((lo - 8) / 10) * 10);
    hi = Math.min(100, Math.ceil((hi + 6) / 10) * 10);
    if (hi - lo < 20) lo = Math.max(0, hi - 20);
    const yOf = (v) => padT + (1 - (v - lo) / (hi - lo)) * ih;
    const ticks = [lo, Math.round((lo + hi) / 2), hi];

    // 缺值的日期（如未诊断日没有综合分）直接跳过、前后相连；x 按日期定位，间隔自然体现
    const cur = rows.filter((r) => r[key] != null).map((r) => [r1(xOf(r.date)), r1(yOf(r[key])), r]);
    const segs = cur.length ? [cur] : [];
    const pts = segs.flat();

    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("width", w);
    svg.setAttribute("height", H);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", label || t("chart.trend"));

    const gid = `area-grad-${++gradSeq}`;
    let s = `<defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="grad-top"/><stop offset="1" class="grad-bottom"/></linearGradient></defs>`;
    for (const t of ticks) {
      const y = Math.round(yOf(t)) + 0.5;
      s += `<line class="${t === lo ? "base-line" : "grid-line"}" x1="${padL}" x2="${w - padR + 8}" y1="${y}" y2="${y}"/>`;
      if (!compact) s += `<text class="tick" x="${padL - 8}" y="${y + 4}" text-anchor="end">${t}</text>`;
    }
    // x 轴标签：首、中、尾
    const xl = compact ? [start, end] : [start, new Date(start.getTime() + (end - start) / 2), end];
    xl.forEach((d, i) => {
      const anchor = i === 0 ? "start" : i === xl.length - 1 ? "end" : "middle";
      const ds = isoDate(d);
      s += `<text class="tick" x="${r1(xOf(ds))}" y="${H - 6}" text-anchor="${anchor}">${esc(fmtShortMD(ds))}</text>`;
    });
    for (const seg of segs) {
      if (seg.length > 1) {
        const line = monotonePath(seg.map((p) => [p[0], p[1]]));
        const base = Math.round(yOf(lo)) + 0.5;
        s += `<path class="area" fill="url(#${gid})" d="${line}L${seg[seg.length - 1][0]},${base}L${seg[0][0]},${base}Z"/>`;
        s += `<path class="series" d="${line}"/>`;
      } else {
        s += `<circle class="gap-dot" cx="${seg[0][0]}" cy="${seg[0][1]}" r="2.5"/>`;
      }
    }
    if (pts.length) {
      const [lx, ly, lr] = pts[pts.length - 1];
      s += `<circle class="dot" cx="${lx}" cy="${ly}" r="4"/>`;
      s += `<text class="end-label" x="${lx + 9}" y="${ly + 4}">${Math.round(lr[key])}</text>`;
    }
    s += `<line class="crosshair" x1="0" x2="0" y1="${padT}" y2="${padT + ih}" visibility="hidden"/>`;
    s += `<circle class="dot-hover" r="4.5" visibility="hidden"/>`;
    s += `<rect class="hit" x="${padL}" y="0" width="${iw}" height="${H}"/>`;
    svg.innerHTML = s; // 仅含数字与已转义的日期文本
    container.replaceChildren(svg);

    if (!pts.length) return;
    const cross = svg.querySelector(".crosshair");
    const hdot = svg.querySelector(".dot-hover");
    const hit = svg.querySelector(".hit");
    const move = (e) => {
      const box = svg.getBoundingClientRect();
      const x = e.clientX - box.left;
      let best = pts[0];
      for (const p of pts) if (Math.abs(p[0] - x) < Math.abs(best[0] - x)) best = p;
      cross.setAttribute("x1", best[0]); cross.setAttribute("x2", best[0]);
      cross.setAttribute("visibility", "visible");
      hdot.setAttribute("cx", best[0]); hdot.setAttribute("cy", best[1]);
      hdot.setAttribute("visibility", "visible");
      const row = best[2];
      const content = tooltip ? tooltip(row) : html`<div class="tt-title">${fmtMD(row.date)}</div>
        <div class="tt-row is-main"><span class="tt-key"></span>${label || t("chart.value")}<span class="tt-val">${Math.round(row[key])}</span></div>`;
      showTip(content, box.left + best[0], box.top + best[1]);
    };
    const leave = (e) => {
      if (e.pointerType && e.pointerType !== "mouse") return; // 触屏抬起手指后保留提示，点别处再关
      cross.setAttribute("visibility", "hidden");
      hdot.setAttribute("visibility", "hidden");
      hideTip();
    };
    hit.addEventListener("pointermove", move);
    hit.addEventListener("pointerdown", move); // 触屏点按也显示提示
    hit.addEventListener("pointerleave", leave);
  }

  draw(true);
  return {
    update(data, opts) {
      state = { data, opts: { ...state.opts, ...(opts || {}) } };
      container.style.opacity = "";
      draw(true);
    },
    destroy() { ro.disconnect(); hideTip(); },
  };
}

/* ---------------------------------------------------------------- 环形进度 */

/** 环形进度（0–100）。center 为中心内容（SafeHtml） */
export function ring(value, { size = 104, stroke = 9, center = "", cls = "" } = {}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = value == null ? 0 : Math.max(0, Math.min(100, value));
  const off = c * (1 - v / 100);
  return html`<div class="ring ${cls}" style="width:${size}px;height:${size}px">
    <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">
      <circle class="ring-track" cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-width="${stroke}"/>
      <circle class="ring-fill" cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-width="${stroke}"
        stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${c.toFixed(2)}" data-off="${off.toFixed(2)}"
        ${value == null ? raw('visibility="hidden"') : ""}/>
    </svg>
    <div class="ring-center">${center}</div>
  </div>`;
}

/** 渲染后调用：让环形与进度条从 0 过渡到目标值 */
export function animateIn(root) {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    root.querySelectorAll(".ring-fill[data-off]").forEach((n) => { n.style.strokeDashoffset = n.dataset.off; });
    root.querySelectorAll("[data-w]").forEach((n) => { n.style.width = n.dataset.w; });
  }));
}

/* ---------------------------------------------------------------- 水平条形 */

/** 水平条形列表。items: [{label, value, note}]，值标注在条形末端 */
export function barList(items, { max, accent = false, unit = t("common.unit.times") } = {}) {
  const m = max ?? Math.max(1, ...items.map((i) => i.value));
  return html`<div class="barlist ${accent ? "is-accent" : ""}">
    ${items.map((it) => {
      const pct = Math.max(2, (it.value / m) * 100);
      return html`<div class="barlist-row" title="${it.label}：${it.value} ${unit}${it.note ? ` · ${it.note}` : ""}">
        <span class="label">${it.label}</span>
        <span class="track"><span class="fill" style="width:0" data-w="${pct.toFixed(1)}%"></span>
          <span class="val"><b>${it.value}</b> ${unit}${it.note ? html`<span class="muted"> · ${it.note}</span>` : ""}</span></span>
      </div>`;
    })}
  </div>`;
}

/* ---------------------------------------------------------------- 活跃热力图 */

/**
 * GitHub 式活跃热力图：按周分列（周一在上），weeks 列。
 * data: [{date, count}]；颜色按当天条数分 5 档。返回 SafeHtml，悬停提示由调用方用 data-* 实现。
 */
export function heatmap(data, { weeks = 13, cell = 13, gap = 3 } = {}) {
  const byDate = new Map((data || []).map((d) => [d.date, d.count || 0]));
  const today = parseDate(isoDate(new Date()));
  const dow = (today.getDay() + 6) % 7; // 周一 = 0
  const start = new Date(today);
  start.setDate(today.getDate() - dow - (weeks - 1) * 7);
  const level = (n) => (n <= 0 ? 0 : n < 5 ? 1 : n < 12 ? 2 : n < 25 ? 3 : 4);
  const cols = [];
  for (let w = 0; w < weeks; w++) {
    const cells = [];
    for (let d = 0; d < 7; d++) {
      const day = new Date(start);
      day.setDate(start.getDate() + w * 7 + d);
      if (day > today) { cells.push(html`<i class="hm-cell is-future"></i>`); continue; }
      const ds = isoDate(day);
      const n = byDate.get(ds) || 0;
      cells.push(html`<i class="hm-cell hm-${level(n)} ${ds === isoDate(today) ? "is-today" : ""}" data-hm="${ds}" data-n="${n}"></i>`);
    }
    cols.push(html`<span class="hm-col">${cells}</span>`);
  }
  return html`<div class="heatmap" style="--cell:${cell}px;--gap:${gap}px">${cols}</div>
    <div class="hm-legend"><span>${t("chart.less")}</span>${[0, 1, 2, 3, 4].map((l) => html`<i class="hm-cell hm-${l}"></i>`)}<span>${t("chart.more")}</span></div>`;
}

/** 绑定热力图悬停提示 */
export function bindHeatTips(root) {
  const show = (e) => {
    const c = e.target.closest("[data-hm]");
    if (!c) return;
    const r = c.getBoundingClientRect();
    showTip(html`<div class="tt-title">${fmtMD(c.dataset.hm)}</div>${t("common.items", { n: fmtInt(Number(c.dataset.n)) })}`, r.right, r.top + r.height / 2);
  };
  root.addEventListener("pointerover", show);
  root.addEventListener("pointerdown", show); // 触屏：ui.js 在捕获阶段先关闭旧提示，这里再显示
  // 触屏的 pointerover 在点按时触发；抬起手指后的 pointerout 不关闭提示（点别处或滚动时由 ui.js 关闭）
  root.addEventListener("pointerout", (e) => { if (e.pointerType === "mouse" && e.target.closest("[data-hm]")) hideTip(); });
}
