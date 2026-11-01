// 应用更新：check_update / download_update / install_update / skip_update（见 docs/ui-api.md「更新」）。
// 状态放在本模块里，顶部提示条与「设置 → 关于」共用；下载任务由本模块轮询，切换页面不会中断。
// 页面通过 subscribe(fn) 在状态变化时重绘；本模块不渲染任何界面（桌面安装时的全屏提示除外）。
import { call, pollJob } from "./api.js";
import { html, render } from "./dom.js";
import { logo } from "./icons.js";
import { t } from "./i18n.js";
import { isAndroid } from "./platform.js";

export const upd = {
  info: null, // check_update() 的结果
  checking: false, // 正在手动检查（check_update(true)）
  checkFailed: "", // check_update 本身失败（如后端没有该方法）时的错误信息
  download: null, // 下载中：{progress, message}
  downloadError: "", // 下载失败（或安装时发现安装包不可用）的错误信息
  errorFrom: "", // download / install：错误来自哪一步
  installing: false, // install_update 请求中
  installStatus: "", // 最近一次 install_update 的结果：installing / needs_permission / unsupported；
  // Android 从「安装未知应用」设置页回来后为 permission_back（提示再点一次「安装」）
  dismissed: "", // 本次会话点过「稍后」的版本（提示条不再出现）
};

const listeners = new Set();
/** 订阅状态变化；返回取消订阅的函数 */
export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
function emit() {
  for (const fn of listeners) {
    try { fn(upd); } catch (e) { console.error(e); }
  }
}

function setInfo(info) {
  const prev = upd.info;
  upd.info = info;
  upd.checkFailed = "";
  // 换了一个版本：之前的下载 / 安装状态不再适用
  if (prev && info && prev.latest !== info.latest) { upd.downloadError = ""; upd.installStatus = ""; }
}

/** 检查更新。force=false 取后端缓存的结果（启动、回到前台时）；force=true 立即联网检查（「检查更新」按钮）。
 *  quiet=true 时失败不记录（自动检查失败静默）。 */
export async function checkUpdate(force = false, { quiet = false } = {}) {
  if (force) { upd.checking = true; emit(); }
  try {
    setInfo(await call("check_update", !!force));
  } catch (e) {
    if (!quiet) upd.checkFailed = e.message || String(e);
  } finally {
    upd.checking = false;
    emit();
  }
  return upd.info;
}

/** 下载更新：进度写入 upd.download；完成后 info.downloaded = true */
export async function downloadUpdate() {
  if (upd.download) return;
  upd.download = { progress: 0, message: t("update.preparing") };
  upd.downloadError = "";
  upd.installStatus = "";
  emit();
  try {
    const { job_id } = await call("download_update");
    await pollJob(job_id, (p) => { upd.download = p; emit(); });
    if (upd.info) upd.info = { ...upd.info, downloaded: true };
  } catch (e) {
    upd.downloadError = e.message || String(e);
    upd.errorFrom = "download";
  } finally {
    upd.download = null;
    emit();
  }
}

/** 安装已下载的更新；返回 install_update 的 status。桌面 installing 时显示全屏提示（应用随后退出并自动重启）。
 *  后端拒绝（安装包不存在、校验失败）时按下载失败处理：显示错误并提供「重新下载」 */
export async function installUpdate() {
  if (upd.installing) return "";
  upd.installing = true;
  emit();
  try {
    const res = await call("install_update");
    const status = res?.status || "";
    upd.installStatus = status;
    if (res?.install_hint && upd.info) upd.info = { ...upd.info, install_hint: res.install_hint };
    if (status === "installing" && !isAndroid()) showInstallingOverlay();
    return status;
  } catch (e) {
    upd.downloadError = e.message || String(e);
    upd.errorFrom = "install";
    upd.installStatus = "";
    if (upd.info) upd.info = { ...upd.info, downloaded: false };
    return "";
  } finally {
    upd.installing = false;
    emit();
  }
}

/** 跳过此版本：提示条不再出现（手动检查仍会显示） */
export async function skipUpdate() {
  const version = upd.info?.latest;
  if (!version) return;
  await call("skip_update", version);
  upd.info = { ...upd.info, skipped: true };
  emit();
}

// Android：needs_permission 时系统设置页盖在 App 上；回到前台说明用户处理过了，提示再点一次「安装」。
// （安装包仍在，回到前台时 app.js 会再取 check_update(false)，关于页直接显示「安装」按钮）
document.addEventListener("visibilitychange", () => {
  if (document.hidden || upd.installStatus !== "needs_permission") return;
  upd.installStatus = "permission_back";
  emit();
});

/** 本次会话不再显示提示条 */
export function dismissBanner() {
  upd.dismissed = upd.info?.latest || "";
  emit();
}

/** 顶部提示条是否显示：有新版本、没跳过、本次会话没点「稍后」 */
export const bannerVisible = () => !!(upd.info?.available && !upd.info.skipped && upd.dismissed !== upd.info.latest);

/** 桌面：安装程序运行期间的全屏提示（Umless 会退出，安装结束后自动重新启动） */
function showInstallingOverlay() {
  if (document.querySelector(".upd-overlay")) return;
  const el = document.createElement("div");
  el.className = "upd-overlay";
  el.setAttribute("role", "alertdialog");
  el.setAttribute("aria-live", "assertive");
  render(el, html`<div class="upd-overlay-card">
    ${logo(48)}
    <h2>${t("update.installingTitle")}</h2>
    <p>${t("update.installingBody")}</p>
    <span class="spinner"></span>
  </div>`);
  document.body.append(el);
}

/** 字节数 → "56.0 MB" */
export function fmtSize(bytes) {
  if (bytes == null) return "";
  const mb = bytes / 1024 / 1024;
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}
