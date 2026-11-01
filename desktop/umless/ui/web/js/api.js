import { t } from "./i18n.js";
// 与 Python 端通信：window.pywebview.api.<方法>()，接口约定见 docs/ui-api.md。

let readyPromise = null;

/** 等待 pywebview 注入 API */
export function apiReady() {
  if (readyPromise) return readyPromise;
  readyPromise = new Promise((resolve, reject) => {
    const ok = () => window.pywebview?.api && typeof window.pywebview.api.get_app_info === "function";
    if (ok()) return resolve(window.pywebview.api);
    const onReady = () => ok() && (cleanup(), resolve(window.pywebview.api));
    const timer = setInterval(onReady, 50);
    const timeout = setTimeout(() => { cleanup(); reject(new Error(t("api.unavailable"))); }, 15000);
    const cleanup = () => {
      clearInterval(timer);
      clearTimeout(timeout);
      window.removeEventListener("pywebviewready", onReady);
    };
    window.addEventListener("pywebviewready", onReady);
  });
  return readyPromise;
}

/** 调用后端方法；异常统一为带中文 message 的 Error */
export async function call(name, ...args) {
  const api = await apiReady();
  const fn = api[name];
  if (typeof fn !== "function") throw new Error(t("api.missing", { name }));
  try {
    return await fn(...args);
  } catch (e) {
    const err = new Error(e?.message || String(e) || t("api.failed"));
    err.cause = e;
    throw err;
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * 轮询耗时任务（每 400ms），onProgress({progress, message}) 实时回调。
 * 返回 result；任务失败时 reject（message 为后端中文错误）。signal.aborted 为真时停止轮询。
 */
export async function pollJob(jobId, onProgress, signal) {
  for (;;) {
    if (signal?.aborted) throw Object.assign(new Error(t("api.cancelled")), { aborted: true });
    const st = await call("job_status", jobId);
    if (st.state === "done") {
      onProgress?.({ progress: 1, message: st.message || "" });
      return st.result;
    }
    if (st.state === "error") throw new Error(st.error || t("api.jobFailed"));
    onProgress?.({ progress: st.progress, message: st.message || "" });
    await sleep(400);
  }
}

/** 启动任务并等待结果 */
export async function runJob(name, args, onProgress, signal) {
  const { job_id } = await call(name, ...args);
  return pollJob(job_id, onProgress, signal);
}

export const api = new Proxy({}, { get: (_, name) => (...args) => call(name, ...args) });
