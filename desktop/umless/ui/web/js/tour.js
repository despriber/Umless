// 功能导览：聚光灯高亮页面上的元素 + 旁边的说明卡片，可跨页面、可回退、可跳过，Esc 退出、方向键翻页。
// 步骤：{ route, target, title, body, placement, pad, before }。target 找不到时退化为屏幕居中的卡片。
// 手机布局（宽度 < 720px）：卡片占满宽度，停靠在高亮区域上方或下方空间更大的一侧（放不下时停靠在屏幕底部）。
import { html, render, el } from "./dom.js";
import { icon } from "./icons.js";
import { t } from "./i18n.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 等待元素出现并完成布局（页面是异步挂载的） */
async function waitFor(selector, timeout = 3000) {
  const t0 = performance.now();
  while (performance.now() - t0 < timeout) {
    const node = document.querySelector(selector);
    if (node) {
      const r = node.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) return node;
    }
    await sleep(60);
  }
  return null;
}

/**
 * 启动导览。steps 中的文案在调用时求值（随界面语言）。
 * navigate(route) 负责切换页面；onEnd(completed) 在结束时调用。
 */
export function startTour(steps, { navigate, onEnd }) {
  if (!steps.length) return { end() {} };
  const previousFocus = document.activeElement;
  let i = 0;
  let target = null;
  let alive = true;
  let busy = false;

  const root = el(html`<div class="tour" role="dialog" aria-modal="true" aria-live="polite">
    <div class="tour-shield"></div>
    <div class="tour-spot" hidden></div>
    <div class="tour-card" tabindex="-1"></div>
  </div>`);
  const spot = root.querySelector(".tour-spot");
  const card = root.querySelector(".tour-card");
  const shield = root.querySelector(".tour-shield");
  document.body.appendChild(root);
  requestAnimationFrame(() => root.classList.add("is-on"));

  function place() {
    if (!alive) return;
    const step = steps[i];
    const vw = innerWidth, vh = innerHeight, gap = 14, margin = 16;
    const cw = card.offsetWidth, ch = card.offsetHeight;
    if (!target) {
      spot.hidden = true;
      shield.classList.add("is-dim");
      card.dataset.side = "center";
      card.style.left = `${Math.round((vw - cw) / 2)}px`;
      card.style.top = `${Math.round((vh - ch) / 2)}px`;
      return;
    }
    shield.classList.remove("is-dim");
    const pad = step.pad ?? 6;
    const r = target.getBoundingClientRect();
    const box = {
      left: Math.max(4, r.left - pad), top: Math.max(4, r.top - pad),
      right: Math.min(vw - 4, r.right + pad), bottom: Math.min(vh - 4, r.bottom + pad),
    };
    spot.hidden = false;
    Object.assign(spot.style, {
      left: `${box.left}px`, top: `${box.top}px`,
      width: `${box.right - box.left}px`, height: `${box.bottom - box.top}px`,
      borderRadius: step.radius ?? getComputedStyle(target).borderRadius ?? "12px",
    });

    if (vw < 720) {
      // 手机：上下停靠。底部要让开固定在屏幕底部的标签栏（含系统手势区）
      const bar = document.getElementById("sidebar")?.getBoundingClientRect();
      const bottomInset = bar && bar.height && bar.top > vh / 2 ? vh - bar.top : 0;
      const above = box.top - gap - margin;
      const below = vh - bottomInset - box.bottom - gap - margin;
      const side = below >= ch || below >= above ? "bottom" : "top";
      const top = side === "bottom" ? Math.min(box.bottom + gap, vh - bottomInset - ch - margin) : Math.max(margin, box.top - gap - ch);
      card.dataset.side = side;
      card.style.left = `${margin}px`;
      card.style.top = `${Math.round(Math.max(margin, top))}px`;
      return;
    }

    // 卡片位置：优先使用指定方向，放不下时依次尝试其他方向
    const fits = {
      right: vw - box.right - gap >= cw + margin,
      left: box.left - gap >= cw + margin,
      bottom: vh - box.bottom - gap >= ch + margin,
      top: box.top - gap >= ch + margin,
    };
    const order = [step.placement || "bottom", "right", "bottom", "left", "top"];
    const side = order.find((s) => fits[s]) || "inside";
    let left, top;
    if (side === "right" || side === "left") {
      left = side === "right" ? box.right + gap : box.left - gap - cw;
      top = (box.top + box.bottom) / 2 - ch / 2;
    } else if (side === "bottom" || side === "top") {
      top = side === "bottom" ? box.bottom + gap : box.top - gap - ch;
      left = (box.left + box.right) / 2 - cw / 2;
    } else {
      left = box.right - cw - margin;
      top = box.bottom - ch - margin;
    }
    left = Math.max(margin, Math.min(vw - cw - margin, left));
    top = Math.max(margin, Math.min(vh - ch - margin, top));
    card.dataset.side = side;
    card.style.left = `${Math.round(left)}px`;
    card.style.top = `${Math.round(top)}px`;
  }

  function drawCard() {
    const step = steps[i];
    const n = steps.length;
    const last = i === n - 1;
    render(card, html`
      <button class="tour-close btn btn-ghost btn-icon" data-tour="skip" aria-label="${t("tour.skip")}" title="${t("tour.skip")}">${icon("x", 16)}</button>
      ${step.eyebrow ? html`<div class="tour-eyebrow">${icon(step.icon || "sparkles", 13)}${step.eyebrow()}</div>` : ""}
      <h3 class="tour-title">${step.title()}</h3>
      <div class="tour-body">${step.body()}</div>
      <div class="tour-foot">
        <div class="tour-dots" aria-label="${t("tour.progress", { i: i + 1, n })}">${steps.map((_, j) => html`<i class="${j === i ? "is-on" : j < i ? "is-past" : ""}"></i>`)}</div>
        <span class="tour-count num">${i + 1} / ${n}</span>
        <span class="grow"></span>
        ${i > 0 ? html`<button class="btn btn-ghost btn-sm" data-tour="prev">${t("common.back")}</button>` : html`<button class="btn btn-ghost btn-sm" data-tour="skip">${t("tour.skip")}</button>`}
        <button class="btn btn-primary btn-sm" data-tour="next">${last ? t("tour.finish") : t("common.next")}${last ? "" : icon("arrowRight", 14)}</button>
      </div>`);
  }

  async function go(next) {
    if (busy || next < 0 || next >= steps.length) return;
    busy = true;
    i = next;
    const step = steps[i];
    root.classList.add("is-moving");
    if (step.route && location.hash !== `#${step.route}`) {
      await navigate(step.route);
    }
    await step.before?.();
    target = step.target ? await waitFor(step.target) : null;
    if (!alive) return;
    if (target) {
      // 手机屏幕矮：把目标顶部滚到可见区域上方，给说明卡片留出下方空间
      target.scrollIntoView({ block: innerWidth < 720 ? "start" : "nearest", behavior: "instant" });
      await sleep(30);
    }
    drawCard();
    place();
    root.classList.remove("is-moving");
    card.focus({ preventScroll: true });
    busy = false;
  }

  function end(completed) {
    if (!alive) return;
    alive = false;
    removeEventListener("resize", place);
    document.removeEventListener("keydown", onKey, true);
    document.getElementById("main")?.removeEventListener("scroll", place);
    root.classList.remove("is-on");
    setTimeout(() => root.remove(), 220);
    if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    onEnd?.(completed);
  }

  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); end(false); }
    else if (e.key === "Tab") {
      const controls = [...card.querySelectorAll("button:not(:disabled)")];
      if (!controls.length) return;
      const first = controls[0], last = controls[controls.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === card)) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && (document.activeElement === last || !card.contains(document.activeElement))) {
        e.preventDefault(); first.focus();
      }
    } else if (!busy && e.key === "ArrowRight") { e.preventDefault(); i === steps.length - 1 ? end(true) : go(i + 1); }
    else if (!busy && e.key === "ArrowLeft") { e.preventDefault(); go(i - 1); }
    else if (!busy && e.key === "Enter" && document.activeElement === card) { e.preventDefault(); i === steps.length - 1 ? end(true) : go(i + 1); }
  }

  card.addEventListener("click", (e) => {
    const b = e.target.closest("[data-tour]");
    if (!b || (busy && b.dataset.tour !== "skip")) return;
    const a = b.dataset.tour;
    if (a === "next") i === steps.length - 1 ? end(true) : go(i + 1);
    else if (a === "prev") go(i - 1);
    else end(false);
  });
  addEventListener("resize", place);
  document.addEventListener("keydown", onKey, true);
  document.getElementById("main")?.addEventListener("scroll", place, { passive: true });

  go(0);
  return { end: () => end(false) };
}
