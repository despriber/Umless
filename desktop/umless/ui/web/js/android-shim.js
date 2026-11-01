// Android 桥接垫片：桌面端的 index.html 不加载本文件；Android 端把本文件内容在页面脚本执行前注入
// （WebViewCompat.addDocumentStartJavaScript），让同一套界面像在 pywebview 里一样调用 window.pywebview.api.<方法>()。
// 协议见 docs/ui-api.md「Android 桥接」：
//   JS → 原生：UmlessBridge.call(id, method, argsJson)            （addJavascriptInterface 暴露的对象）
//   原生 → JS：window.__umlessResolve(id, ok, resultJson)         （原生在主线程 evaluateJavascript 回调）
// 本文件不能使用 import / export，也不能依赖页面里的其他脚本。
(() => {
  "use strict";
  if (window.pywebview && window.pywebview.api) return; // 重复注入（如页面内跳转）时保持原样
  const bridge = window.UmlessBridge;
  if (!bridge || typeof bridge.call !== "function") return;

  let seq = 0;
  const pending = new Map(); // id → { resolve, reject, method }

  /** 原生端回调：ok 为 true 时 resultJson 是返回值的 JSON；为 false 时是 {"message": "…"} */
  window.__umlessResolve = (id, ok, resultJson) => {
    const p = pending.get(Number(id));
    if (!p) return;
    pending.delete(Number(id));
    let value = null;
    try {
      value = resultJson == null || resultJson === "" ? null : JSON.parse(resultJson);
    } catch (e) {
      p.reject(new Error(`返回值不是合法的 JSON（${p.method}）`));
      return;
    }
    if (ok) p.resolve(value);
    else p.reject(new Error((value && value.message) || String(value || "操作失败")));
  };

  function invoke(method, args) {
    return new Promise((resolve, reject) => {
      const id = ++seq;
      pending.set(id, { resolve, reject, method });
      try {
        bridge.call(id, method, JSON.stringify(args));
      } catch (e) {
        pending.delete(id);
        reject(e instanceof Error ? e : new Error(String(e)));
      }
    });
  }

  // 任意方法名都转发给原生端；原生端没有的方法以 ok=false 回调。
  // "then" 必须返回 undefined：否则 Promise 会把 api 对象当成 thenable 去调用。
  const api = new Proxy({}, {
    get: (_, name) => (typeof name === "string" && name !== "then" && name !== "toJSON"
      ? (...args) => invoke(name, args) : undefined),
  });

  window.__umlessPlatform = "android";
  window.pywebview = { api, platform: "android" };
  const ready = () => window.dispatchEvent(new Event("pywebviewready"));
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready, { once: true });
  else ready();
})();
