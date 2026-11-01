// 入口：启动、主题、侧栏（手机布局下为底部标签栏）、hash 路由、托盘跳转轮询、常驻进程横幅。
// Android（同一套界面跑在 WebView 里）：侧栏底部显示键盘状态，横幅提示启用 Umless 键盘，见 docs/ui-api.md「Android 桥接」。
// 横幅区还会显示新版本提示条（js/update.js，docs/ui-api.md「更新」）。
import { apiReady, call } from "./api.js";
import { html, render, $, on } from "./dom.js";
import { icon, logo } from "./icons.js";
import { hotkeyName, toast, toastError, hideTip, closePopover, todayIso } from "./ui.js";
import { t, th, lang, setLang, missingKeys } from "./i18n.js";
import { startTour } from "./tour.js";
import { setPlatform, isAndroid } from "./platform.js";
import { upd, subscribe, checkUpdate, bannerVisible, dismissBanner, skipUpdate } from "./update.js";

const ROUTES = {
  overview: () => import("./pages/overview.js"),
  report: () => import("./pages/report.js"),
  practice: () => import("./pages/practice.js"),
  history: () => import("./pages/history.js"),
  settings: () => import("./pages/settings.js"),
  onboarding: () => import("./pages/onboarding.js"),
};
const NAV = [
  ["overview", "nav.overview", "overview"],
  ["report", "nav.report", "report"],
  ["practice", "nav.practice", "practice"],
  ["history", "nav.history", "history"],
];

const appEl = $("#app");
const mainEl = $("#main");
const viewEl = $("#view");
const sidebarEl = $("#sidebar");
const bannerEl = $("#banner");

/** 全局状态，页面通过 ctx.app 访问 */
export const app = {
  info: null,
  ime: null, // Android：get_ime_status() 的结果 {enabled, selected}
  route: { name: "", params: [] },
  setTheme: applyTheme,
  refreshInfo,
  refreshIme,
  navigate,
  setLanguage,
  startTour: beginTour,
};

let current = { name: "", module: null, cleanup: null, token: 0 };

/* ---------------------------------------------------------------- 主题 */

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.dataset.theme = theme;
  else delete root.dataset.theme;
}

/* ---------------------------------------------------------------- 侧栏与横幅 */

function renderSidebar() {
  const r = app.route.name;
  const info = app.info || {};
  const key = hotkeyName(info.hotkey);
  sidebarEl.setAttribute("aria-label", t("nav.label"));
  render(sidebarEl, html`
    <div class="brand">${logo(28)}<div><div class="brand-name">Umless</div><div class="brand-tag">${t("brand.tagline")}</div></div></div>
    <nav class="nav">
      ${NAV.map(([name, label, ico]) => html`<a class="nav-item ${r === name ? "is-active" : ""}" href="#${name}" aria-current="${r === name ? "page" : "false"}">${icon(ico, 18)}<span>${t(label)}</span></a>`)}
    </nav>
    <div class="sidebar-spacer"></div>
    <nav class="nav">
      <a class="nav-item ${r === "settings" ? "is-active" : ""}" href="#settings">${icon("settings", 18)}<span>${t("nav.settings")}</span></a>
    </nav>
    ${isAndroid() ? imeStatus() : html`<div class="core-status ${info.core_running ? "is-on" : ""}">
      <span class="dot"></span>
      <span>${info.core_running ? html`<span class="core-title">${t("core.running")}</span><br><span class="tiny">${th("core.hotkey", { key })}</span>` : t("core.stopped")}</span>
    </div>`}`);
}

/** Android：侧栏底部的键盘状态（代替桌面端的"后台运行中"） */
function imeStatus() {
  const ime = app.ime;
  const ready = ime?.enabled && ime?.selected;
  const [title, action] = !ime ? [t("ime.checking"), ""]
    : ready ? [t("ime.ready"), html`<span class="tiny">${t("ime.readyHint")}</span>`]
    : ime.enabled ? [t("ime.notSelected"), html`<button class="link-btn core-action" data-ime-picker>${t("ime.switch")}</button>`]
    : [t("ime.notEnabled"), html`<button class="link-btn core-action" data-ime-settings>${t("ime.enable")}</button>`];
  return html`<div class="core-status is-ime ${ready ? "is-on" : ""}">
    <span class="dot"></span>
    <span><span class="core-title">${title}</span>${action ? html`<br>${action}` : ""}</span>
  </div>`;
}

function renderBanner() {
  if (app.route.name === "onboarding") {
    bannerEl.replaceChildren();
    return;
  }
  const parts = [platformBanner(), updateBanner()].filter(Boolean);
  if (!parts.length) bannerEl.replaceChildren();
  else render(bannerEl, html`<div class="banner-wrap">${parts}</div>`);
}

/** 桌面：常驻进程未运行；Android：键盘未启用（已启用但没切换为当前输入法时只在侧栏 / 设置里显示，不打扰） */
function platformBanner() {
  const info = app.info || {};
  if (isAndroid()) {
    if (!app.ime || app.ime.enabled) return null;
    return html`<div class="banner" role="alert">
      ${icon("keyboard", 18)}
      <div class="grow"><b>${t("ime.bannerTitle")}</b><span class="muted">${t("ime.bannerBody")}</span></div>
      <button class="btn btn-primary btn-sm" data-ime-settings>${t("ime.enable")}</button>
    </div>`;
  }
  if (info.core_running) return null;
  return html`<div class="banner" role="alert">
    ${icon("alert", 18)}
    <div class="grow"><b>${t("core.bannerTitle")}</b><span class="muted">${t("core.bannerBody")}</span></div>
    <button class="btn btn-primary btn-sm" data-start-core>${icon("power", 14)}${t("core.start")}</button>
  </div>`;
}

/** 发现新版本：「查看」去「设置 → 关于」，「稍后」本次不再提示，「跳过此版本」以后不再自动提示。已在关于页时不显示 */
function updateBanner() {
  if (!bannerVisible()) return null;
  const r = app.route;
  if (r.name === "settings" && r.params[0] === "about") return null;
  return html`<div class="banner is-update" role="status">
    ${icon("download", 18)}
    <div class="grow"><b>${t("update.bannerTitle", { version: upd.info.latest })}</b><span class="muted">${t("update.bannerBody")}</span></div>
    <div class="banner-actions">
      <a class="btn btn-primary btn-sm" href="#settings/about" data-update-view>${t("update.view")}</a>
      <button class="btn btn-ghost btn-sm" data-update-later>${t("update.later")}</button>
      <button class="btn btn-ghost btn-sm" data-update-skip>${t("update.skip")}</button>
    </div>
  </div>`;
}

on(bannerEl, "click", "[data-update-later]", () => dismissBanner());
on(bannerEl, "click", "[data-update-skip]", async () => {
  const version = upd.info?.latest;
  try {
    await skipUpdate();
    toast(t("update.skipped", { version }));
  } catch (err) { toastError(err); }
});
subscribe(() => renderBanner());

on(bannerEl, "click", "[data-start-core]", async (e, btn) => {
  btn.classList.add("is-loading");
  try {
    await call("start_core");
    await refreshInfo();
    toast(t("core.started"));
  } catch (err) {
    toastError(err);
  } finally {
    btn.classList.remove("is-loading");
  }
});

// Android：打开系统输入法设置 / 输入法选择框；用户回到 App 时（visibilitychange）刷新状态
for (const root of [sidebarEl, bannerEl]) {
  on(root, "click", "[data-ime-settings]", () => call("open_ime_settings").catch(toastError));
  on(root, "click", "[data-ime-picker]", () => call("show_ime_picker").catch(toastError));
}
// 回到前台时再取一次更新检查结果（后端自己判断是否到了自动检查的时间，见 docs/ui-api.md「更新」）
document.addEventListener("visibilitychange", () => {
  if (document.hidden || !app.info) return;
  refreshInfo();
  if (app.info.onboarded) checkUpdate(false, { quiet: true });
});

/** Android：刷新键盘状态，变化时重绘侧栏与横幅；返回 {enabled, selected} */
async function refreshIme() {
  if (!isAndroid()) return null;
  try {
    const ime = await call("get_ime_status");
    const changed = JSON.stringify(ime) !== JSON.stringify(app.ime);
    app.ime = ime;
    if (changed) { renderSidebar(); renderBanner(); }
  } catch { /* 忽略：下次轮询再试 */ }
  return app.ime;
}

async function refreshInfo() {
  try {
    const info = await call("get_app_info");
    const changed = JSON.stringify(info) !== JSON.stringify(app.info);
    const themeChanged = info.theme !== app.info?.theme;
    app.info = info;
    setPlatform(info.platform);
    if (info.language !== lang()) {
      setLang(info.language);
      current.name = "";
      await route_();
    }
    if (themeChanged) applyTheme(info.theme);
    if (changed) { renderSidebar(); renderBanner(); }
  } catch { /* 忽略：下次轮询再试 */ }
  await refreshIme();
  return app.info;
}

async function setLanguage(language) {
  await call("save_settings", { app: { language } });
  await refreshInfo();
}

let activeTour = null;
function beginTour() {
  if (activeTour || !app.info?.onboarded) return;
  const steps = [
    ["overview", '[data-tour="overview-score"]', "overview"],
    [`report/${todayIso()}`, '[data-tour="report-diagnose"]', "report"],
    ["report/habits", '[data-tour="report-habits"]', "habits"],
    ["practice", '[data-tour="practice-topics"]', "practice"],
    ["history", '[data-tour="history-list"]', "history"],
    ["settings/polish", '[data-tour="settings-content"]', "polish"],
    // Android 没有提示音设置，最后一步改为介绍键盘与语音入口（文案用 tour.sounds.*.android）
    [isAndroid() ? "settings/keyboard" : "settings/sounds", '[data-tour="settings-content"]', "sounds"],
  ].map(([route, target, key]) => ({
    route, target, title: () => t(`tour.${key}.title`),
    body: () => key === "report" && document.querySelector('[data-tour="report-diagnose"]')?.disabled
      ? t("tour.report.emptyBody") : t(`tour.${key}.body`),
  }));
  activeTour = startTour(steps, {
    navigate,
    onEnd: async () => {
      activeTour = null;
      try {
        await call("save_settings", { app: { tour_done: true } });
        await refreshInfo();
      } catch (e) { toastError(e); }
    },
  });
}

/* ---------------------------------------------------------------- 路由 */

function parseHash() {
  let h;
  try { h = decodeURIComponent(location.hash.replace(/^#\/?/, "")); }
  catch { h = "overview"; }
  const [name, ...params] = h.split("/").filter(Boolean);
  return { name: ROUTES[name] ? name : "overview", params };
}

export function navigate(route, { replace = false } = {}) {
  const hash = `#${route}`;
  if (location.hash !== hash) history[replace ? "replaceState" : "pushState"](null, "", hash);
  return route_();
}

async function route_() {
  const { name, params } = parseHash();
  // 未完成引导时，只允许停留在引导页
  if (app.info && !app.info.onboarded && name !== "onboarding") {
    history.replaceState(null, "", "#onboarding");
    return route_();
  }
  const prev = current;
  app.route = { name, params };
  appEl.classList.toggle("is-bare", name === "onboarding");
  renderSidebar();
  renderBanner();

  // 同一页面只是参数变化：交给页面自行处理（如设置分组切换）
  if (prev.name === name && prev.module?.update && prev.module.update(params) !== false) return;

  hideTip();
  closePopover();
  const token = ++current.token;
  try { prev.cleanup?.(); } catch (e) { console.error(e); }
  const page = document.createElement("div");
  page.className = `page page-${name}`;
  viewEl.replaceChildren(page);
  mainEl.scrollTop = 0;
  try {
    const mod = await ROUTES[name]();
    if (token !== current.token) return;
    current = { name, module: mod, cleanup: null, token };
    const cleanup = (await mod.mount(page, { params, app })) || null;
    if (token !== current.token) { cleanup?.(); return; }
    current.cleanup = cleanup;
  } catch (e) {
    if (token !== current.token) return;
    console.error(e);
    window.__umless.errors.push(String(e.message || e));
    render(page, html`<div class="card"><div class="empty"><div class="empty-title">${t("common.pageError")}</div><div class="empty-desc">${e.message || String(e)}</div></div></div>`);
  }
}

window.addEventListener("hashchange", route_);

/* ---------------------------------------------------------------- 托盘跳转轮询 */

async function pollNav() {
  try {
    const page = await call("get_nav");
    // 可带子路由，如托盘的新版本通知打开 "settings/about"、Android 键盘打开 "settings/keyboard"
    if (page && ROUTES[String(page).split("/")[0]]) navigate(page);
  } catch { /* 忽略 */ }
}

/* ---------------------------------------------------------------- 预览 / 调试钩子 */

window.__umless = {
  ready: false,
  errors: [],
  go: (r) => navigate(r),
  setTheme: (t) => applyTheme(t),
  setLanguage,
  startTour: beginTour,
  missingKeys,
  update: upd, // 更新状态（js/update.js），预览脚本用来等待检查结果
  scrollPage: (i) => mainEl.scrollTo({ top: i * (mainEl.clientHeight - 60), behavior: "instant" }),
  atBottom: () => mainEl.scrollTop + mainEl.clientHeight >= mainEl.scrollHeight - 2,
  // 重置当前页面状态：下次导航时强制重新挂载
  reset: () => { current.name = ""; call("practice_cancel").catch(() => {}); },
};

// 收集运行时错误，便于预览脚本自检
window.addEventListener("error", (e) => window.__umless.errors.push(String(e.message)));
window.addEventListener("unhandledrejection", (e) => window.__umless.errors.push(String(e.reason?.message || e.reason)));

/* ---------------------------------------------------------------- 启动 */

async function boot() {
  try {
    await apiReady();
    app.info = await call("get_app_info");
  } catch (e) {
    appEl.classList.remove("is-booting");
    appEl.classList.add("is-bare");
    render(viewEl, html`<div class="fatal">${logo(48)}<h2>${t("api.unavailable")}</h2><p class="muted">${e.message || String(e)}</p></div>`);
    return;
  }
  setPlatform(app.info.platform);
  setLang(app.info.language);
  applyTheme(app.info.theme);
  await refreshIme();
  if (!app.info.onboarded) history.replaceState(null, "", "#onboarding");
  else if (!location.hash) history.replaceState(null, "", "#overview");
  await route_();
  appEl.classList.remove("is-booting");
  setInterval(pollNav, 1000);
  setInterval(refreshInfo, 5000);
  window.__umless.ready = true;
  if (app.info.onboarded && !app.info.tour_done) beginTour();
  if (app.info.onboarded) checkUpdate(false, { quiet: true }); // 有新版本时显示顶部提示条
}

boot();
