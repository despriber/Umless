"""Headless Edge UI checks against MockApi; never reads the user's configuration.

Run from desktop: uv run --with playwright python tools/ui_smoke.py
Screenshots and the JSON report go to .testhome/ui-smoke/ (Android/mobile pass: .testhome/ui-smoke/android/).

The Android pass loads the same web UI at 390x844 (touch, DPR 3) through js/android-shim.js with a fake
UmlessBridge backed by MockApi(platform="android"), exactly like the Android WebView would.
"""

from __future__ import annotations

import functools
import argparse
import inspect
import json
import re
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

DESKTOP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESKTOP))
from umless.ui.mock_api import MockApi

WEB = DESKTOP / "umless/ui/web"
OUTPUT = DESKTOP / ".testhome/ui-smoke"
ROUTES = ["overview", "report", "report/habits", "practice", "practice/result/3", "history",
          "settings/general", "settings/asr", "settings/polish", "settings/scenes",
          "settings/sounds", "settings/lexicon", "settings/coach", "settings/privacy",
          "settings/about", "onboarding/welcome", "onboarding/model", "onboarding/llm",
          "onboarding/mic", "onboarding/hotkey", "onboarding/done"]
ANDROID_ROUTES = ["overview", "report", "report/habits", "practice", "practice/result/3", "history",
                  "settings", "settings/keyboard", "settings/general", "settings/asr",
                  "settings/polish", "settings/scenes", "settings/lexicon", "settings/coach", "settings/privacy",
                  "settings/about", "onboarding/welcome", "onboarding/ime", "onboarding/model", "onboarding/mic",
                  "onboarding/llm", "onboarding/try", "onboarding/done"]
# Desktop-only controls that must never render on Android.
DESKTOP_ONLY = ["[data-hk-record]", "[data-hk-preset]", '[data-stepper="hotkey.min_hold_ms"]',
                '[data-key="audio.mic_always_on"]', '[data-key="ui.overlay"]', '[data-seg="output.mode"]',
                "[data-keep-clipboard]", "[data-autostart]", "[data-open]", '[data-engine="qwen3_asr"]',
                "[data-start-core]", ".core-status:not(.is-ime)", '[data-key="audio.keep_audio"]', ".ob-keyvis"]
# Desktop wording that must not leak into the Android UI (hotkeys, process names).
DESKTOP_WORDS = r"热键|hotkey|CapsLock|Caps Lock|Ctrl\+V|\.exe\b|进程名|process name|Windows"
# Fake native side of the bridge: forwards UmlessBridge.call to MockApi and answers via __umlessResolve.
FAKE_BRIDGE = """window.UmlessBridge = { call(id, method, argsJson) {
  window.androidCall(method, JSON.parse(argsJson)).then(
    (r) => window.__umlessResolve(id, true, JSON.stringify(r ?? null)),
    (e) => window.__umlessResolve(id, false, JSON.stringify({ message: String((e && e.message) || e) })));
} };"""
# Effective touch target of visible controls: the element box, or its transparent ::before hit area.
TOUCH_TARGETS = """() => {
  const sel = ".btn, .nav-item, .set-nav-item, .input, .select, .chip-x, .stepper button, .link-btn, .set-back, .switch input, .ob-provider";
  const bad = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || el.closest(".tour, [hidden]")) continue;
    const b = getComputedStyle(el, "::before");
    const w = Math.max(r.width, b.position === "absolute" ? parseFloat(b.width) || 0 : 0);
    const h = Math.max(r.height, b.position === "absolute" ? parseFloat(b.height) || 0 : 0);
    if (Math.min(w, h) < 43.5) bad.push(`${el.className || el.tagName} ${Math.round(w)}x${Math.round(h)}`);
  }
  return bad;
}"""


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interactions-only", action="store_true", help="Rerun interactions after a saved page suite")
    parser.add_argument("--empty-tour-only", action="store_true", help="Check all tour steps with a new empty profile")
    parser.add_argument("--android-only", action="store_true", help="Run only the Android / mobile pass")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(WEB)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results, errors, warnings = [], [], []
    if args.interactions_only and (OUTPUT / "report.json").exists():
        results = [r for r in json.loads((OUTPUT / "report.json").read_text(encoding="utf-8"))["checks"] if "language" in r]
    api = MockApi(onboarded=True, empty=args.empty_tour_only)
    methods = [name for name, method in inspect.getmembers(api, callable) if not name.startswith("_")]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1180, "height": 780}, device_scale_factor=1)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: warnings.append(m.text) if m.type == "warning" else None)
            page.expose_function("mockCall", lambda name, args: getattr(api, name)(*args))
            page.add_init_script("window.pywebview = {api: Object.fromEntries(" + json.dumps(methods)
                                 + ".map(name => [name, (...args) => window.mockCall(name, args)]))};")
            if args.android_only:
                android_pass(browser, server.server_port, results, errors, warnings)
                assert not errors, errors
                assert not warnings, warnings
                browser.close()
                print(f"Passed {len(results)} Android/mobile UI checks. Screenshots: {OUTPUT / 'android'}")
                return
            page.goto(f"http://127.0.0.1:{server.server_port}/index.html")
            page.wait_for_function("window.__umless?.ready", timeout=20000)
            assert page.evaluate("window.__umless.missingKeys()") == {"en": [], "zh": []}
            if args.empty_tour_only:
                for language in ("zh", "en"):
                    page.evaluate("language => window.__umless.setLanguage(language)", language)
                    page.evaluate("window.__umless.startTour()")
                    for step in range(7):
                        page.wait_for_selector(".tour:not(.is-moving) .tour-title")
                        assert page.locator(".tour-count").inner_text().strip() == f"{step + 1} / 7"
                        assert page.locator(".tour-spot").is_visible(), f"Missing empty-profile tour target: {step + 1}"
                        if step == 1:
                            assert page.locator('[data-tour="report-diagnose"]').is_disabled()
                            assert "3" in page.locator(".tour-body").inner_text()
                            page.wait_for_timeout(350)  # Let spotlight and card transitions settle for visual QA.
                            page.screenshot(path=str(OUTPUT / f"{language}-empty-report-tour.png"))
                        page.locator('[data-tour="next"]').click()
                        results.append({"empty_tour_language": language, "step": step + 1, "ok": True})
                    page.wait_for_selector(".tour", state="detached")
                    assert api.list_reports()["today"]["n"] == 0
                    assert api.list_reports()["items"] == []
                    assert api.get_habits(None) == []
                assert not errors, errors
                assert not warnings, warnings
                browser.close()
                print(f"Passed {len(results)} empty-profile tour checks.")
                return
            for language in (() if args.interactions_only else ("zh", "en")):
                page.evaluate("language => window.__umless.setLanguage(language)", language)
                for theme in ("light", "dark"):
                    page.evaluate("async theme => { await window.pywebview.api.save_settings({app:{theme}}); window.__umless.setTheme(theme); }", theme)
                    for route in ROUTES:
                        page.evaluate("window.__umless.reset()")
                        page.evaluate("route => window.__umless.go(route)", route)
                        page.wait_for_timeout(220)
                        page.wait_for_function("document.querySelector('#view .page')?.textContent.trim().length > 0")
                        page.wait_for_timeout(100)
                        assert not page.locator(".empty-title").filter(has_text="页面加载失败").count(), route
                        assert not page.locator(".empty-title").filter(has_text="This page failed to load").count(), route
                        assert not page.evaluate("window.__umless.errors"), page.evaluate("window.__umless.errors")
                        overflow = page.evaluate("document.querySelector('#view').scrollWidth > document.querySelector('#main').clientWidth")
                        assert not overflow, f"Horizontal overflow: {language}/{theme}/{route}"
                        results.append({"language": language, "theme": theme, "route": route, "ok": True})
                        if route in ("overview", "report", "settings/general", "settings/sounds", "settings/about", "onboarding/mic"):
                            page.screenshot(path=str(OUTPUT / f"{language}-{theme}-{route.replace('/', '-')}.png"))
            # The update banner was on every page above; hide it for this session (update_checks covers it in detail).
            page.evaluate("window.__umless.go('overview')")
            page.locator("[data-update-later]").click()
            page.wait_for_selector(".banner.is-update", state="detached")
            page.evaluate("window.__umless.go('settings/general')")
            page.locator("[data-language]").select_option("zh")
            page.wait_for_function("document.documentElement.lang === 'zh-CN'")
            page.wait_for_selector("[data-language]")
            assert page.locator(".page-title").inner_text() == "设置"
            page.locator("[data-keep-clipboard]").uncheck(force=True)
            page.locator("[data-language]").select_option("en")
            page.wait_for_function("document.documentElement.lang === 'en'")
            page.wait_for_function("async () => (await window.pywebview.api.get_settings()).config.output.restore_clipboard")
            assert not page.locator("[data-keep-clipboard]").is_checked()
            results.append({"interaction": "language switch persists pending settings", "ok": True})
            page.evaluate("window.__umless.go('settings/sounds')")
            page.locator('[data-sound="start"]').select_option("pop")
            page.locator("[data-volume]").fill("35")
            page.locator("[data-volume]").dispatch_event("input")
            page.wait_for_function("async () => { const c=(await window.pywebview.api.get_settings()).config; return c.audio.volume===35 && c.audio.sounds.start==='pop'; }")
            page.locator('[data-sound-preview="start"]').click()
            page.locator('[data-key="audio.beep"]').uncheck(force=True)
            page.wait_for_function("document.querySelector('[data-volume]').disabled")
            assert page.locator('[data-sound="start"]').is_disabled()
            results.append({"interaction": "sound selection, volume, preview and disable", "ok": True})
            page.evaluate("window.__umless.go('onboarding/mic')")
            page.locator("[data-dictation-input]").fill("Editable draft")
            assert page.locator("[data-dictation-input]").input_value() == "Editable draft"
            page.evaluate("window.pywebview.api.mock_dictate('A clear result.', 'Um, a clear result.')")
            page.wait_for_function("document.querySelector('[data-dictation-input]').value === 'A clear result.'")
            assert "Um, a clear result." in page.locator("#view").inner_text()
            page.screenshot(path=str(OUTPUT / "en-dictation-result.png"))
            page.locator("[data-mic-test]").click()
            page.wait_for_function("!document.querySelector('[data-mic-test]').disabled", timeout=15000)
            results.append({"interaction": "editable dictation, raw/output comparison and microphone job", "ok": True})
            page.evaluate("window.__umless.go('settings/privacy')")
            page.locator("[data-clear]").click()
            assert page.locator('[data-r="1"]').is_disabled()
            page.locator('[data-r="0"]').click()
            results.append({"interaction": "destructive clear requires confirmation text", "ok": True})
            page.set_viewport_size({"width": 960, "height": 640})
            for route in ("overview", "report", "settings/sounds", "onboarding/mic"):
                page.evaluate("r => window.__umless.go(r)", route)
                page.wait_for_timeout(200)
                assert not page.evaluate("document.querySelector('#view').scrollWidth > document.querySelector('#main').clientWidth"), route
                results.append({"minimum_window": route, "ok": True})
            page.set_viewport_size({"width": 1180, "height": 780})
            page.evaluate("window.__umless.startTour()")
            for i in range(7):
                page.wait_for_selector(".tour:not(.is-moving) .tour-title")
                assert page.locator(".tour-count").inner_text().strip() == f"{i + 1} / 7"
                page.locator('[data-tour="next"]').click()
            page.wait_for_selector(".tour", state="detached")
            page.wait_for_function("!document.querySelector('.tour')")
            results.append({"tour": "all seven steps", "ok": True})
            page.evaluate("window.__umless.startTour()")
            page.wait_for_selector(".tour:not(.is-moving) .tour-title")
            page.keyboard.press("Escape")
            page.wait_for_selector(".tour", state="detached")
            results.append({"tour": "replay and keyboard skip", "ok": True})
            update_checks(page, api, OUTPUT, results, android=False)
            network_checks(page, api, OUTPUT, results, android=False)
            android_pass(browser, server.server_port, results, errors, warnings)
            assert not errors, errors
            assert not warnings, warnings
            browser.close()
    finally:
        server.shutdown()
        report = {"checks": results, "errors": errors, "warnings": warnings}
        report_name = "empty-tour-report.json" if args.empty_tour_only else "report.json"
        (OUTPUT / report_name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Passed {len(results)} UI checks. Screenshots: {OUTPUT}")


def android_pass(browser, port, results, errors, warnings):
    """Android / mobile pass: 390x844 touch viewport, Android bridge shim, MockApi(platform="android")."""
    out = OUTPUT / "android"
    out.mkdir(parents=True, exist_ok=True)
    api = MockApi(onboarded=True, platform="android")
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                                  is_mobile=True, has_touch=True)
    page = context.new_page()
    page.on("pageerror", lambda e: errors.append(f"android: {e}"))
    page.on("console", lambda m: warnings.append(f"android: {m.text}") if m.type == "warning" else None)
    page.expose_function("androidCall", lambda name, args: getattr(api, name)(*args))
    page.add_init_script(FAKE_BRIDGE)
    page.add_init_script(path=str(WEB / "js/android-shim.js"))
    page.goto(f"http://127.0.0.1:{port}/index.html")
    page.wait_for_function("window.__umless?.ready", timeout=20000)
    assert page.evaluate("window.__umlessPlatform") == "android"
    assert page.evaluate("document.documentElement.dataset.platform") == "android"
    assert page.evaluate("window.__umless.missingKeys()") == {"en": [], "zh": []}

    def settle(route):
        page.evaluate("window.__umless.reset()")
        page.evaluate("route => window.__umless.go(route)", route)
        page.wait_for_timeout(240)
        page.wait_for_function("document.querySelector('#view .page')?.textContent.trim().length > 0")
        page.wait_for_timeout(120)

    def check_page(tag):
        assert not page.locator(".empty-title").filter(has_text="页面加载失败").count(), tag
        assert not page.locator(".empty-title").filter(has_text="This page failed to load").count(), tag
        assert not page.evaluate("window.__umless.errors"), (tag, page.evaluate("window.__umless.errors"))
        assert page.evaluate("document.scrollingElement.scrollWidth <= innerWidth"), f"Page overflow: {tag}"
        assert page.evaluate("document.querySelector('#view').scrollWidth <= document.querySelector('#main').clientWidth"), \
            f"Horizontal overflow: {tag}"
        wide = page.evaluate("""() => [...document.querySelectorAll('#view *')].filter(e => {
            const r = e.getBoundingClientRect(); return r.width && r.right > innerWidth + 1;
          }).slice(0, 3).map(e => String(e.className?.baseVal ?? e.className) || e.tagName)""")
        assert not wide, f"Element wider than the screen on {tag}: {wide}"
        for sel in DESKTOP_ONLY:
            assert not page.locator(sel).count(), f"Desktop-only control {sel} on {tag}"
        text = page.locator("#app").inner_text()
        leak = re.search(DESKTOP_WORDS, text, re.I)
        assert not leak, f"Desktop wording {leak.group(0)!r} on {tag}"
        small = page.evaluate(TOUCH_TARGETS)
        assert not small, f"Touch targets < 44px on {tag}: {small[:5]}"

    for language in ("zh", "en"):
        page.evaluate("language => window.__umless.setLanguage(language)", language)
        for theme in ("light", "dark"):
            page.evaluate("async theme => { await window.pywebview.api.save_settings({app:{theme}}); window.__umless.setTheme(theme); }", theme)
            for route in ANDROID_ROUTES:
                settle(route)
                tag = f"android/{language}/{theme}/{route}"
                check_page(tag)
                if not route.startswith("onboarding"):
                    tabs = page.evaluate("[...document.querySelectorAll('.sidebar .nav-item')].map(e => e.getBoundingClientRect()).map(r => [r.top, r.height])")
                    assert len(tabs) == 5 and all(h >= 44 and top > 844 - 120 for top, h in tabs), (tag, tabs)
                results.append({"android_language": language, "theme": theme, "route": route, "ok": True})
                name = f"{language}-{theme}-{route.replace('/', '-')}"
                if (language, theme) == ("zh", "light") or route in ("overview", "settings", "settings/keyboard", "onboarding/ime", "onboarding/try"):
                    page.screenshot(path=str(out / f"{name}.png"))
                if (language, theme) in (("zh", "light"), ("en", "dark")) and not page.evaluate("window.__umless.atBottom()"):
                    for i in (1, 2):
                        page.evaluate("i => window.__umless.scrollPage(i)", i)
                        page.wait_for_timeout(80)
                        page.screenshot(path=str(out / f"{name}-{i + 1}.png"))
                        if page.evaluate("window.__umless.atBottom()"):
                            break

    # The update banner was on every page above (overflow / touch-target coverage); hide it for this session so the
    # interactions below keep their positions (update_checks covers it in detail).
    settle("overview")
    page.locator("[data-update-later]").tap()
    page.wait_for_selector(".banner.is-update", state="detached")
    # Onboarding on Android: enable + select the keyboard, grant the microphone, test it, then try dictation.
    page.evaluate("window.pywebview.api.mock_ime(false, false)")
    settle("onboarding/ime")
    assert page.locator("[data-next]").is_disabled()
    page.screenshot(path=str(out / "en-onboarding-ime-todo.png"))
    page.locator("#view [data-ime-settings]").tap()
    page.wait_for_function("document.querySelectorAll('.ob-check.is-done').length === 1", timeout=5000)
    page.locator("#view [data-ime-picker]").tap()
    page.wait_for_function("document.querySelectorAll('.ob-check.is-done').length === 2", timeout=5000)
    assert not page.locator("[data-next]").is_disabled()
    page.locator("[data-next]").tap()
    page.wait_for_function("location.hash === '#onboarding/model'")  # Android：先下载模型，再测麦克风
    settle("onboarding/mic")
    page.wait_for_selector("[data-mic-perm]")
    page.screenshot(path=str(out / "en-onboarding-mic-permission.png"))
    page.locator("[data-mic-perm]").tap()
    page.wait_for_selector("[data-mic-test]")
    page.locator("[data-mic-test]").tap()
    page.wait_for_selector(".ob-mic.is-done", timeout=15000)
    check_page("android/onboarding/mic-done")
    page.screenshot(path=str(out / "en-onboarding-mic-done.png"))
    results.append({"android_interaction": "onboarding keyboard polling, mic permission and mic test", "ok": True})
    page.evaluate("window.__umless.go('onboarding/try')")
    page.wait_for_selector("[data-dictation-input]")
    page.locator("[data-dictation-input]").fill("Draft")
    page.evaluate("window.pywebview.api.mock_dictate('A clear result.', 'Um, a clear result.')")
    page.wait_for_function("document.querySelector('[data-dictation-input]').value === 'A clear result.'")
    assert "Um, a clear result." in page.locator("#view").inner_text()
    page.evaluate("document.activeElement.blur()")
    page.screenshot(path=str(out / "en-onboarding-try-result.png"))
    results.append({"android_interaction": "try step shows raw/output comparison", "ok": True})

    # Settings: list -> detail -> back, keyboard toggles persist, diagnostic log export.
    settle("settings")
    assert page.locator(".set-nav").is_visible() and not page.locator("#set-main").is_visible()
    page.locator('.set-nav-item[href="#settings/keyboard"]').tap()
    page.wait_for_selector("#set-main [data-key='voice.haptic']")
    assert not page.locator(".set-nav").is_visible()
    page.locator("[data-key='voice.haptic']").uncheck(force=True)
    page.wait_for_function("async () => (await window.pywebview.api.get_settings()).config.voice.haptic === false")
    page.locator(".set-back").tap()
    page.wait_for_function("location.hash === '#settings'")
    page.wait_for_selector(".set-nav", state="visible")
    assert not page.locator('.set-nav-item[href="#settings/sounds"]').count()  # Android 没有提示音设置
    settle("settings/about")
    page.locator("[data-export-logs]").tap()
    page.wait_for_selector(".toast", timeout=5000)
    page.wait_for_function("[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('umless-logs-'))")
    results.append({"android_interaction": "settings list/detail, keyboard toggle, log export", "ok": True})

    # Modal fits the screen.
    settle("settings/privacy")
    page.locator("[data-clear]").tap()
    box = page.locator(".modal").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390 and box["y"] >= 0 and box["y"] + box["height"] <= 844, box
    page.screenshot(path=str(out / "en-modal.png"))
    page.locator('[data-r="0"]').tap()
    results.append({"android_interaction": "confirm dialog fits the screen", "ok": True})

    # Hover tooltips also open on tap.
    settle("overview")
    page.locator(".hm-cell[data-hm]").last.tap()
    assert page.locator("#tooltip").is_visible(), "Heatmap tooltip did not open on tap"
    page.locator("#ov-trend").scroll_into_view_if_needed()
    page.locator("#ov-trend .hit").tap()
    assert page.locator("#tooltip").is_visible(), "Trend tooltip did not open on tap"
    page.screenshot(path=str(out / "en-tooltip-tap.png"))
    results.append({"android_interaction": "tooltips open on tap", "ok": True})

    # Feature tour adapts: full-width card inside the viewport for every step.
    page.evaluate("window.__umless.startTour()")
    for i in range(7):
        page.wait_for_selector(".tour:not(.is-moving) .tour-title")
        page.wait_for_timeout(260)
        card = page.locator(".tour-card").bounding_box()
        assert card["x"] >= 0 and card["x"] + card["width"] <= 390 and card["y"] >= 0 and card["y"] + card["height"] <= 844, (i, card)
        page.screenshot(path=str(out / f"en-tour-{i + 1}.png"))
        page.locator('[data-tour="next"]').tap()
    page.wait_for_selector(".tour", state="detached")
    results.append({"android_tour": "all seven steps fit a 390px screen", "ok": True})
    update_checks(page, api, out, results, android=True, check=check_page)
    network_checks(page, api, out, results, android=True, check=check_page)
    assert not page.evaluate("window.__umless.errors"), page.evaluate("window.__umless.errors")
    context.close()


def update_checks(page, api, out, results, *, android, check=None):
    """App updates (docs/ui-api.md「更新」): top banner (view / later / skip, foreground re-check on Android) and
    Settings → About states: available → download → install, download failure → retry, up to date, check error,
    running from source (desktop). zh runs in light, en in dark."""
    act = (lambda loc: loc.tap()) if android else (lambda loc: loc.click())
    latest = "0.2.0" if android else "0.4.0"
    banner = page.locator(".banner.is-update")
    prefix = "android/" if android else ""

    def verify(tag):
        if check:
            check(f"android/update/{tag}")
        else:
            assert not page.evaluate("window.__umless.errors"), page.evaluate("window.__umless.errors")
            assert not page.evaluate("document.querySelector('#view').scrollWidth > document.querySelector('#main').clientWidth"), tag

    def reload():
        """New session, starting on the overview page (the banner is hidden on Settings → About)."""
        page.evaluate("history.replaceState(null, '', '#overview')")
        page.reload()
        page.wait_for_function("window.__umless?.ready && window.__umless.update.info", timeout=20000)

    def shot(name):
        page.wait_for_timeout(350)  # Let the page-in animation finish.
        page.screenshot(path=str(out / f"{name}.png"))

    def about():
        page.evaluate("window.__umless.go('settings/about')")
        page.wait_for_selector("#upd-status")

    def check_now(state_cls):
        act(page.locator("[data-update-check]"))
        page.wait_for_selector(f".upd-status.{state_cls}", timeout=10000)

    for language, theme in (("zh", "light"), ("en", "dark")):
        api.mock_update("available")
        api.skip_update("")
        page.evaluate("language => window.__umless.setLanguage(language)", language)
        page.evaluate("async theme => { await window.pywebview.api.save_settings({app:{theme}}); window.__umless.setTheme(theme); }", theme)
        tag = f"{language}-{theme}"

        # Banner at startup; 稍后 hides it for this session.
        reload()
        banner.wait_for()
        assert latest in banner.inner_text(), banner.inner_text()
        verify(f"{tag}/banner")
        shot(f"{tag}-update-banner")
        act(banner.locator("[data-update-later]"))
        banner.wait_for(state="detached")
        page.evaluate("window.__umless.go('history')")
        page.wait_for_timeout(200)
        assert not banner.count(), "Banner came back after Later"
        results.append({"update": f"{prefix}{tag} banner shows at startup and Later hides it", "ok": True})

        # View → Settings → About: version, date, size, release notes with line breaks.
        reload()
        banner.wait_for()
        act(banner.locator("[data-update-view]"))
        page.wait_for_function("location.hash === '#settings/about'")
        page.wait_for_selector(".upd-status.is-new")
        assert not banner.count(), "Banner shown on Settings → About"
        assert latest in page.locator("#upd-status").inner_text()
        assert "MB" in page.locator("#upd-status").inner_text()
        assert "\n" in page.locator(".upd-notes p").inner_text(), "Release notes lost their line breaks"
        verify(f"{tag}/available")
        shot(f"{tag}-update-available")
        act(page.locator("[data-update-download]"))
        page.wait_for_selector(".upd-job .job")
        page.wait_for_function("/[1-9]/.test(document.querySelector('.upd-job .num')?.textContent || '')", timeout=10000)
        verify(f"{tag}/downloading")
        page.screenshot(path=str(out / f"{tag}-update-downloading.png"))
        page.wait_for_selector("[data-update-install]", timeout=15000)
        verify(f"{tag}/downloaded")
        shot(f"{tag}-update-ready")
        act(page.locator("[data-update-install]"))
        if android:
            # First install opens the system "install unknown apps" page; tap Install again afterwards.
            page.wait_for_selector(".upd-note.is-warn")
            verify(f"{tag}/needs-permission")
            shot(f"{tag}-update-permission")
            # Back from the system settings page: the APK is still there, the hint asks to tap Install again.
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_function("t => document.querySelector('.upd-note.is-warn')?.textContent.includes(t)",
                                   arg="再点一次" if language == "zh" else "tap Install again")
            assert not page.locator("[data-update-download]").count()
            act(page.locator("[data-update-install]"))
            page.wait_for_function("!document.querySelector('.upd-note.is-warn') && !document.querySelector('[data-update-install]').disabled")
            assert not page.locator(".upd-overlay").count()
            shot(f"{tag}-update-installing")
        else:
            overlay = page.locator(".upd-overlay")
            overlay.wait_for()
            assert ("自动重新启动" if language == "zh" else "restart") in overlay.inner_text()
            shot(f"{tag}-update-installing")
            page.evaluate("document.querySelector('.upd-overlay').remove()")
        results.append({"update": f"{prefix}{tag} about: available → download → install", "ok": True})

        # Download failure, then download again.
        api.mock_update("available", fail_download=True)
        check_now("is-new")
        act(page.locator("[data-update-download]"))
        page.wait_for_selector(".upd-panel .test-box.is-error", timeout=10000)
        verify(f"{tag}/download-failed")
        shot(f"{tag}-update-download-failed")
        api.mock_update("available")
        act(page.locator("[data-update-download]"))
        page.wait_for_selector("[data-update-install]", timeout=15000)
        # install_update rejects (installer missing / checksum mismatch): show the error and offer to download again.
        api.mock_update("available")
        act(page.locator("[data-update-install]"))
        page.wait_for_selector(".upd-panel .test-box.is-error [data-update-download], .upd-panel .test-box.is-error ~ .upd-actions [data-update-download]")
        assert ("无法安装" if language == "zh" else "Couldn't install") in page.locator(".upd-panel .test-box").inner_text()
        verify(f"{tag}/install-failed")
        act(page.locator("[data-update-download]"))
        page.wait_for_selector("[data-update-install]", timeout=15000)
        results.append({"update": f"{prefix}{tag} download / install failure and retry", "ok": True})

        # Skip this version from the banner: no banner on the next start; About still shows it, tagged as skipped.
        api.mock_update("available")
        reload()
        banner.wait_for()
        act(banner.locator("[data-update-skip]"))
        banner.wait_for(state="detached")
        assert api.get_settings()["config"]["update"]["skipped_version"] == latest
        reload()
        page.wait_for_timeout(300)
        assert not banner.count(), "Banner shown for a skipped version"
        about()
        page.wait_for_selector(".upd-status.is-new .tag")
        assert not page.locator("[data-update-skip]").count()
        api.skip_update("")
        if android:
            # Coming back to the foreground re-reads the check result.
            api.mock_update("latest")
            reload()
            page.wait_for_timeout(300)
            assert not banner.count()
            api.mock_update("available")
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            banner.wait_for(timeout=5000)
        results.append({"update": f"{prefix}{tag} skip this version" + (" and foreground re-check" if android else ""), "ok": True})

        # Never checked (all fields null) → 检查更新 → up to date / check error / running from source.
        about()
        api.mock_update("unchecked")
        about()
        page.wait_for_function("t => document.querySelector('#upd-status')?.textContent.includes(t)",
                               arg="还没有检查过" if language == "zh" else "Not checked yet")
        assert not page.locator(".upd-status.is-ok, .upd-status.is-new, .upd-status.is-error").count()
        verify(f"{tag}/unchecked")
        check_now("is-ok")
        api.mock_update("latest")
        check_now("is-ok")
        assert ("已是最新版本" if language == "zh" else "up to date") in page.locator("#upd-status").inner_text()
        assert not page.locator(".upd-panel").count()
        verify(f"{tag}/latest")
        shot(f"{tag}-update-latest")
        api.mock_update("error")
        check_now("is-error")
        assert page.locator("[data-update-check]").get_attribute("aria-label") == ("重试" if language == "zh" else "Retry")
        assert "GitHub" in page.locator("#upd-status").inner_text()
        verify(f"{tag}/error")
        shot(f"{tag}-update-error")
        # Repository without a published release yet: the manifest is 404 on every source — not an error.
        api.mock_update("not_published")
        check_now("is-empty")
        assert ("还没有发布版本" if language == "zh" else "nothing has been released") in page.locator("#upd-status").inner_text()
        assert page.locator("[data-update-check]").get_attribute("aria-label") == ("检查更新" if language == "zh" else "Check for updates")
        assert not page.locator(".upd-panel").count() and not banner.count()
        verify(f"{tag}/not-published")
        shot(f"{tag}-update-not-published")
        if not android:
            api.mock_update("source")
            check_now("is-new")
            assert "git pull" in page.locator(".upd-panel").inner_text()
            assert not page.locator("[data-update-download]").count()
            verify(f"{tag}/source")
            shot(f"{tag}-update-source")
        results.append({"update": f"{prefix}{tag} up to date, error, not published" + ("" if android else ", running from source"), "ok": True})

        # Automatic check toggle.
        page.locator('[data-key="update.auto_check"]').uncheck(force=True)
        page.wait_for_function("async () => (await window.pywebview.api.get_settings()).config.update.auto_check === false")
        page.locator('[data-key="update.auto_check"]').check(force=True)
        page.wait_for_function("async () => (await window.pywebview.api.get_settings()).config.update.auto_check === true")
        results.append({"update": f"{prefix}{tag} auto-check toggle persists", "ok": True})
    api.mock_update("available")


def network_checks(page, api, out, results, *, android, check=None):
    """Download source (Settings → General, docs/ui-api.md「下载源」): mode switch, mirror address validation /
    normalization / reset, test connection results (one failing + suggestion, both ok + preferred). zh light, en dark."""
    act = (lambda loc: loc.tap()) if android else (lambda loc: loc.click())
    prefix = "android/" if android else ""
    saved = "async v => JSON.stringify((await window.pywebview.api.get_settings()).config.network) === v"

    def wait_saved(source, url):
        page.wait_for_function(saved, arg=json.dumps({"download_source": source, "mirror_url": url}, separators=(",", ":")))

    def verify(tag):
        if check:
            check(f"android/network/{tag}")
        else:
            assert not page.evaluate("window.__umless.errors"), page.evaluate("window.__umless.errors")
            assert not page.evaluate("document.querySelector('#view').scrollWidth > document.querySelector('#main').clientWidth"), tag

    def shot(name):
        page.locator("#net-result").evaluate("e => e.closest('.set-group').scrollIntoView({block: 'start'})")
        page.wait_for_timeout(350)
        page.screenshot(path=str(out / f"{name}.png"))

    def mirror(value):
        box = page.locator("[data-mirror-url]")
        box.fill(value)
        box.press("Enter")

    default = "https://gh-proxy.com/"
    for language, theme in (("zh", "light"), ("en", "dark")):
        api.mock_network(None, "mirror")
        api.save_settings({"network": {"download_source": "auto", "mirror_url": default}})
        page.evaluate("language => window.__umless.setLanguage(language)", language)
        page.evaluate("async theme => { await window.pywebview.api.save_settings({app:{theme}}); window.__umless.setTheme(theme); }", theme)
        tag = f"{language}-{theme}"
        page.evaluate("window.__umless.go('overview')")  # remount Settings so it reads the reset config
        page.wait_for_timeout(200)
        page.evaluate("window.__umless.go('settings/general')")
        page.wait_for_selector("[data-seg='network.download_source']")
        assert ("下载源" if language == "zh" else "Download source") in page.locator("#set-main").inner_text()
        assert page.locator("[data-mirror-reset]").is_hidden()
        verify(f"{tag}/default")

        # Mode: GitHub direct → saved, description follows the mode.
        act(page.locator("[data-seg='network.download_source'] [data-v='github']"))
        wait_saved("github", default)
        assert ("只直连" if language == "zh" else "directly") in page.locator(".net-mode").inner_text()

        # Mirror address: invalid → inline error, nothing saved; valid → normalized with a trailing "/"; reset.
        mirror("gh-proxy.com")
        page.wait_for_selector("#mirror-error:not([hidden])")
        assert page.locator("[data-mirror-url]").get_attribute("aria-invalid") is not None
        assert api.get_settings()["config"]["network"]["mirror_url"] == default
        verify(f"{tag}/mirror-invalid")
        shot(f"{tag}-network-mirror-invalid")
        mirror(" https://ghfast.top ")
        wait_saved("github", "https://ghfast.top/")
        assert page.locator("[data-mirror-url]").input_value() == "https://ghfast.top/"
        assert page.locator("#mirror-error").is_hidden()
        page.wait_for_selector("[data-mirror-reset]:not([hidden])")
        act(page.locator("[data-mirror-reset]"))
        wait_saved("github", default)
        assert page.locator("[data-mirror-reset]").is_hidden()
        results.append({"network": f"{prefix}{tag} mode switch, mirror validation / normalization / reset", "ok": True})

        # Test connection: GitHub direct fails while it is the chosen mode → suggestion to switch.
        api.mock_network("github", "mirror")
        act(page.locator("[data-net-test]"))
        page.wait_for_selector(".net-res[data-net-res='github'].is-error", timeout=5000)
        assert "timed out" in page.locator(".net-res[data-net-res='github']").inner_text()
        assert "ms" in page.locator(".net-res[data-net-res='mirror']").inner_text()
        assert page.locator(".net-res[data-net-res='mirror'] .tag").count() == 1
        assert page.locator("#net-result .upd-note.is-warn").count() == 1
        assert not page.locator("[data-net-test]").is_disabled()
        verify(f"{tag}/test-github-failed")
        shot(f"{tag}-network-test-failed")

        # Auto + both reachable: the faster one (mirror) is what auto prefers; no suggestion.
        act(page.locator("[data-seg='network.download_source'] [data-v='auto']"))
        wait_saved("auto", default)
        api.mock_network(None, "github")
        act(page.locator("[data-net-test]"))
        page.wait_for_selector(".net-res[data-net-res='github'].is-ok", timeout=5000)
        assert page.locator(".net-res.is-ok").count() == 2
        assert page.locator(".net-res[data-net-res='mirror'] .tag").count() == 1
        assert not page.locator("#net-result .upd-note").count()
        verify(f"{tag}/test-ok")
        shot(f"{tag}-network-test-ok")
        results.append({"network": f"{prefix}{tag} test connection results, preferred source and suggestion", "ok": True})
    api.mock_network(None, "mirror")
    api.save_settings({"network": {"download_source": "auto", "mirror_url": default}})


if __name__ == "__main__":
    main()
