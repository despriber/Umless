// 运行平台：桌面（pywebview，Windows）或 Android（WebView + js/android-shim.js 桥接）。
// 以 get_app_info().platform 为准；拿到之前退回 Android 垫片注入的 window.__umlessPlatform。
// 本模块不依赖其他模块，i18n.js 用它选择平台专属文案（见 i18n.js 顶部说明）。

let infoPlatform = null;

/** 由 app.js 在拿到 get_app_info() 后调用 */
export function setPlatform(platform) {
  infoPlatform = platform === "android" || platform === "windows" ? platform : null;
  document.documentElement.dataset.platform = platform_();
}

function platform_() {
  return infoPlatform || (window.__umlessPlatform === "android" ? "android" : "windows");
}

export const platform = platform_;
export const isAndroid = () => platform_() === "android";

/** 手机布局（与 CSS 的 max-width: 719px 断点一致） */
export const isMobile = () => matchMedia("(max-width: 719px)").matches;

document.documentElement.dataset.platform = platform_();
