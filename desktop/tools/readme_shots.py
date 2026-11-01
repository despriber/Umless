"""Screenshots and composed images for the GitHub READMEs, rendered from MockApi only.

Run from desktop/:
    uv run --with playwright --with imagequant python tools/readme_shots.py              # capture + compose + optimize
    uv run --with playwright --with imagequant python tools/readme_shots.py --raw-only   # only refresh the raw captures

Same approach as tools/ui_smoke.py: the web UI is served from umless/ui/web, loaded in headless Microsoft Edge,
and every backend call goes to MockApi (fixed-seed sample data). Nothing reads %APPDATA%\\Umless, recordings/ or
any real profile. The listening overlay pills are drawn by umless/overlay_render.py (the code the app uses).

Outputs:
    .testhome/readme-shots/raw/        raw page captures (DPR 2 desktop, DPR 3 Android)
    docs/assets/readme/                optimized PNGs used by README.md
    ../Umless-Android/docs/assets/readme/   the Android repo's copies (if that checkout exists)
"""

from __future__ import annotations

import argparse
import base64
import functools
import inspect
import io
import json
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

DESKTOP = Path(__file__).resolve().parent.parent
REPO = DESKTOP.parent
sys.path.insert(0, str(DESKTOP))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from umless.ui.mock_api import MockApi  # noqa: E402

WEB = DESKTOP / "umless/ui/web"
RAW = DESKTOP / ".testhome/readme-shots/raw"
OUT = REPO / "docs/assets/readme"
ANDROID_REPO = REPO.parent / "Umless-Android"
ANDROID_OUT = ANDROID_REPO / "docs/assets/readme"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FAKE_BRIDGE = """window.UmlessBridge = { call(id, method, argsJson) {
  window.androidCall(method, JSON.parse(argsJson)).then(
    (r) => window.__umlessResolve(id, true, JSON.stringify(r ?? null)),
    (e) => window.__umlessResolve(id, false, JSON.stringify({ message: String((e && e.message) || e) })));
} };"""

# (file name, route, theme, JS to run after navigation, scroll: None | px | (selector, offset px), wait ms)
HIST_OPEN = "(document.querySelector('.hist-item .hist-head')||{click(){}}).click()"
DESKTOP_SHOTS = [
    ("desktop-overview-light", "overview", "light", [], None, 900),
    ("desktop-report-light", "report", "light", [], None, 900),
    ("desktop-report-examples-light", "report", "light", [], (".issues > :nth-child(2)", 28), 900),
    ("desktop-habits-light", "report/habits", "light", [], None, 900),
    ("desktop-practice-result-light", "practice/result/3", "light", [], None, 900),
    ("desktop-history-light", "history", "light", [HIST_OPEN], None, 900),
    ("desktop-history-dark", "history", "dark", [HIST_OPEN], None, 900),
    ("desktop-settings-polish-light", "settings/polish", "light", [], (".intensity-card", 90), 700),
    ("desktop-settings-scenes-light", "settings/scenes", "light", [], None, 700),
]
ANDROID_SHOTS = [
    ("android-overview-light", "overview", "light", [], None, 900),
    ("android-overview-dark", "overview", "dark", [], None, 900),
    ("android-report-light", "report", "light", [], None, 900),
    ("android-report-examples-light", "report", "light", [], (".examples", 14), 900),
    ("android-history-light", "history", "light", [HIST_OPEN], None, 900),
    ("android-settings-keyboard-light", "settings/keyboard", "light", [], None, 700),
    ("android-onboarding-ime-light", "onboarding/ime", "light", [], None, 700),
]
SCROLL_TO = """([sel, offset]) => {
  const m = document.querySelector('#main'), el = document.querySelector(sel);
  if (!el) throw new Error('missing ' + sel);
  const pick = el.previousElementSibling && el.previousElementSibling.matches('.section-title') ? el.previousElementSibling : el;
  m.scrollTo({top: pick.getBoundingClientRect().top - m.getBoundingClientRect().top + m.scrollTop - offset, behavior: 'instant'});
}"""


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


def _bind_mock(page, api) -> None:
    methods = [name for name, m in inspect.getmembers(api, callable) if not name.startswith("_")]
    page.expose_function("mockCall", lambda name, args: getattr(api, name)(*args))
    page.add_init_script("window.pywebview = {api: Object.fromEntries(" + json.dumps(methods)
                         + ".map(name => [name, (...args) => window.mockCall(name, args)]))};")


def _shoot_all(page, api, shots, errors) -> None:
    for name, route, theme, js, scroll, wait in shots:
        page.evaluate("async theme => { await window.pywebview.api.save_settings({app:{theme}});"
                      " window.__umless.setTheme(theme); }", theme)
        page.evaluate("window.__umless.reset()")
        page.evaluate("route => window.__umless.go(route)", route)
        page.wait_for_function("document.querySelector('#view .page')?.textContent.trim().length > 0")
        page.wait_for_timeout(300)
        for code in js:
            page.evaluate(code)
        if isinstance(scroll, tuple):
            page.evaluate(SCROLL_TO, list(scroll))
        elif scroll:
            page.evaluate("y => document.querySelector('#main').scrollTo({top: y, behavior: 'instant'})", scroll)
        page.wait_for_timeout(wait)
        page.screenshot(path=str(RAW / f"{name}.png"))
        if page.evaluate("window.__umless.errors.length"):
            errors.append((name, page.evaluate("window.__umless.errors")))
        print("raw:", name)


def capture(browser, port: int) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    errors: list = []
    # Desktop: 1280x800 at DPR 2, Chinese UI, no update banner.
    api = MockApi(onboarded=True, update="latest")
    page = browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=2)
    page.on("pageerror", lambda e: errors.append(("desktop", str(e))))
    _bind_mock(page, api)
    page.goto(f"http://127.0.0.1:{port}/index.html")
    page.wait_for_function("window.__umless?.ready", timeout=20000)
    page.evaluate("window.__umless.setLanguage('zh')")
    _shoot_all(page, api, DESKTOP_SHOTS, errors)
    page.close()

    # Android: the same web UI through js/android-shim.js and a fake UmlessBridge, 390x844 at DPR 3.
    api = MockApi(onboarded=True, platform="android", update="latest")
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                              is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(("android", str(e))))
    page.expose_function("androidCall", lambda name, args: getattr(api, name)(*args))
    page.add_init_script(FAKE_BRIDGE)
    page.add_init_script(path=str(WEB / "js/android-shim.js"))
    page.goto(f"http://127.0.0.1:{port}/index.html")
    page.wait_for_function("window.__umless?.ready", timeout=20000)
    page.evaluate("window.__umless.setLanguage('zh')")
    _shoot_all(page, api, ANDROID_SHOTS, errors)
    ctx.close()
    if errors:
        raise SystemExit(f"front-end errors: {errors}")


# ---------------------------------------------------------------- composition (HTML/CSS rendered by Edge)

FONT = '"Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", sans-serif'
LOGO = """<svg viewBox="0 0 32 32" width="{s}" height="{s}"><defs><linearGradient id="g{s}" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#2dd4bf"/><stop offset="1" stop-color="#0d9488"/></linearGradient></defs>
<rect width="32" height="32" rx="9" fill="url(#g{s})"/><g stroke="#fff" stroke-width="2.6" stroke-linecap="round">
<path d="M8 14v4" opacity=".75"/><path d="M12 11.5v9"/><path d="M16 8.5v15"/><path d="M20 11.5v9"/><path d="M24 14v4" opacity=".75"/></g></svg>"""
CSS = """
* { box-sizing: border-box; margin: 0; }
html, body { background: transparent; font-family: %s; }
#stage { display: inline-block; position: relative; padding: 40px 56px 76px; }
.win { width: 1280px; border-radius: 10px; overflow: hidden; background: #eef1f0;
       box-shadow: 0 0 0 1px rgba(16,36,30,.10), 0 16px 40px rgba(16,40,34,.18), 0 4px 12px rgba(16,40,34,.08); }
.win.dark { background: #121514; box-shadow: 0 0 0 1px rgba(255,255,255,.10), 0 16px 40px rgba(0,0,0,.3), 0 4px 12px rgba(0,0,0,.18); }
.bar { height: 34px; display: flex; align-items: center; padding-left: 14px; font-size: 12px; color: #17201c; }
.dark .bar { color: #eceeed; }
.bar svg:first-child { margin-right: 9px; }
.ctl { margin-left: auto; display: flex; }
.ctl i { width: 46px; height: 34px; display: grid; place-items: center; }
.ctl svg { stroke: currentColor; stroke-width: 1; fill: none; opacity: .8; }
.win img.shot { display: block; width: 1280px; }
.phone { width: 414px; padding: 12px; border-radius: 58px; background: #0c0f0e;
         box-shadow: 0 0 0 1.5px #3a403e inset, 0 0 0 1px rgba(16,36,30,.10), 0 16px 40px rgba(16,40,34,.20), 0 4px 12px rgba(16,40,34,.10); }
.screen { position: relative; border-radius: 46px; overflow: hidden; }
.screen .sb { height: 30px; }
.screen .cam { position: absolute; top: 10px; left: 50%%; width: 12px; height: 12px; margin-left: -6px; border-radius: 50%%; background: #0c0f0e; }
.screen img { display: block; width: 390px; }
""" % FONT
MIN = '<svg width="10" height="10"><path d="M0 5.5h10"/></svg>'
MAX = '<svg width="10" height="10"><rect x=".5" y=".5" width="9" height="9" rx="1.5"/></svg>'
CLOSE = '<svg width="10" height="10"><path d="M.5.5l9 9M9.5.5l-9 9"/></svg>'


def _uri(img: Path | Image.Image) -> str:
    if isinstance(img, Path):
        data = img.read_bytes()
    else:
        buf = io.BytesIO()
        img.save(buf, "PNG")
        data = buf.getvalue()
    return "data:image/png;base64," + base64.b64encode(data).decode()


def window(name: str, dark: bool = False) -> str:
    return (f'<div class="win{" dark" if dark else ""}"><div class="bar">{LOGO.format(s=16)}<span>Umless</span>'
            f'<div class="ctl"><i>{MIN}</i><i>{MAX}</i><i>{CLOSE}</i></div></div>'
            f'<img class="shot" src="{_uri(RAW / (name + ".png"))}"></div>')


def phone(name: str) -> str:
    raw = Image.open(RAW / f"{name}.png").convert("RGB")
    top = "#%02x%02x%02x" % raw.getpixel((raw.width // 2, 2))
    return (f'<div class="phone"><div class="screen" style="background:{top}"><div class="sb"></div><div class="cam"></div>'
            f'<img src="{_uri(RAW / (name + ".png"))}"></div></div>')


def render(browser, body: str, out: Path, width: int, extra_css: str = "") -> Path:
    """Render body inside #stage at DPR 2 with a transparent background, then downscale to `width` px."""
    page = browser.new_page(viewport={"width": 2400, "height": 1400}, device_scale_factor=2)
    page.set_content(f"<!doctype html><meta charset=utf-8><style>{CSS}{extra_css}</style><div id=stage>{body}</div>")
    page.wait_for_timeout(250)
    tmp = out.with_suffix(".full.png")
    page.locator("#stage").screenshot(path=str(tmp), omit_background=True)
    page.close()
    img = Image.open(tmp)
    if img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    tmp.unlink()
    img.save(out)
    print("composed:", out.name, img.size)
    return out


def overlay_pills() -> list[Image.Image]:
    """The listening overlay drawn by the app's own renderer: recording -> polishing -> done."""
    import overlay_preview as OP

    from umless import i18n
    from umless import overlay_render as R

    i18n.set_language("zh")
    loud = OP.recording_frames(OP.speech_rms(4, loud=0.2, seed=5), 3.0)
    rec = max(loud[40:], key=lambda f: sum(f.bars))
    return [R.render(rec, 2.0), R.render(R.Frame("polishing", t=1.0, clock=1.1), 2.0),
            R.render(R.Frame("done", t=0.6), 2.0)]


OVERLAY_CSS = """
.ov { width: 1180px; padding: 40px 36px 34px; border-radius: 18px; position: relative; overflow: hidden;
      background: linear-gradient(180deg, #f8faf9, #eef1f0); box-shadow: 0 0 0 1px rgba(16,36,30,.08); }
.doc { position: absolute; top: 30px; left: 40px; right: 40px; display: flex; flex-direction: column; gap: 17px; opacity: .55; }
.doc i { display: block; height: 9px; border-radius: 5px; background: #dde3e1; }
.row { position: relative; display: flex; align-items: flex-start; justify-content: center; gap: 4px; padding-top: 118px; }
.st { display: flex; flex-direction: column; align-items: center; width: 330px; }
.st b { margin-top: 6px; font-size: 17px; color: #17201c; }
.st span { margin-top: 4px; font-size: 13.5px; color: #59625e; }
.arr { font-size: 28px; color: #0d9488; margin-top: 18px; }
"""
OVERLAY_STEPS = [("按住热键说话", "实时声波，随时可以松开"),
                 ("松开：识别 + 润色", "本机识别，再交给你配置的大模型"),
                 ("写入光标处", "说完只提示「已输入」，不打扰")]


def compose(browser) -> None:
    comp = RAW.parent / "composed"
    comp.mkdir(parents=True, exist_ok=True)
    # Hero: the desktop window with the Android app in front of it.
    render(browser, f"""{window("desktop-overview-light")}
        <div style="position:absolute; left:1262px; top:210px">{phone("android-report-examples-light")}</div>""",
           comp / "hero.png", 1800, "#stage { padding: 40px 400px 300px 56px; }")
    for name in ("desktop-report-light", "desktop-report-examples-light", "desktop-habits-light",
                 "desktop-practice-result-light", "desktop-history-light", "desktop-history-dark",
                 "desktop-settings-polish-light", "desktop-settings-scenes-light"):
        render(browser, window(name, dark=name.endswith("dark")),
               comp / (name.replace("desktop-", "").replace("-light", "") + ".png"), 1600)
    # Android: three phones side by side, the middle one raised a little.
    def row(items):
        return '<div style="display:flex; gap:44px">' + "".join(
            f'<div style="margin-top:{m}px">{phone(n)}</div>' for n, m in items) + "</div>"
    render(browser, row([("android-overview-light", 40), ("android-report-examples-light", 0),
                         ("android-settings-keyboard-light", 40)]), comp / "android-trio.png", 1500)
    render(browser, row([("android-onboarding-ime-light", 0), ("android-history-light", 40),
                         ("android-overview-dark", 0)]), comp / "android-more.png", 1500)
    # Listening overlay: three states drawn by overlay_render on a quiet "document" backdrop.
    pills = overlay_pills()
    lines = "".join(f'<i style="width:{w}%"></i>' for w in (92, 78, 86, 54))
    steps = ""
    for k, (pill, (cap, sub)) in enumerate(zip(pills, OVERLAY_STEPS)):
        steps += (f'<div class="st"><img src="{_uri(pill)}" style="width:{pill.width // 2}px">'
                  f'<b>{cap}</b><span>{sub}</span></div>')
        if k < 2:
            steps += '<div class="arr">&rarr;</div>'
    render(browser, f'<div class="ov"><div class="doc">{lines}</div><div class="row">{steps}</div></div>',
           comp / "overlay.png", 1600, OVERLAY_CSS)


def optimize(src: Path, dst: Path) -> None:
    """256-colour palette PNG via libimagequant (keeps the alpha shadows); a flat UI survives this well, ~3x smaller."""
    import imagequant

    img = Image.open(src).convert("RGBA")
    q = imagequant.quantize_pil_image(img, dithering_level=0.6, max_colors=256, min_quality=0, max_quality=95)
    dst.parent.mkdir(parents=True, exist_ok=True)
    q.save(dst, optimize=True)
    print(f"  {dst.name}: {dst.stat().st_size / 1024:.0f} KB")


# ---------------------------------------------------------------- hand-drawn SVGs (banner, before/after, coach loop)
# Two variants each (README picks one with <picture> + prefers-color-scheme). Colours are the UI tokens (css/tokens.css).
# Chinese text is laid out at 1 em per character (true for CJK fonts on every OS), so highlight boxes line up anywhere.

SVG_FONT = ("'Segoe UI Variable Display','Segoe UI',-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB',"
            "'Microsoft YaHei','Noto Sans CJK SC','Noto Sans SC',sans-serif")
THEMES = {
    "light": dict(bg1="#f1f8f6", bg2="#ffffff", line="#dfe8e5", card="#ffffff", card2="#f6f9f8", text="#17201c",
                  text2="#4b5550", text3="#7a837f", accent="#0d9488", accent_text="#0f766e", soft="#e8f5f2",
                  filler="#a95a06", filler_bg="#fbf0dc", repeat="#6b45cc", repeat_bg="#f0ebfc", correction="#2d5fcf",
                  correction_bg="#e7eefc", cut="#8b938f", mark="#e0a03a", glow="#2dd4bf", glow_op=".16"),
    "dark": dict(bg1="#0f1614", bg2="#161d1b", line="#2a3330", card="#1f2322", card2="#252a28", text="#eceeed",
                 text2="#c0c8c4", text3="#8c9591", accent="#2dd4bf", accent_text="#5eead4", soft="#173330",
                 filler="#f5b94a", filler_bg="#3a3020", repeat="#bba7fb", repeat_bg="#2e2a40", correction="#8fb5fa",
                 correction_bg="#1f2c42", cut="#737b77", mark="#d99a2b", glow="#2dd4bf", glow_op=".12"),
}


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _logo_mark(cx: float, cy: float, r: float, uid: str) -> str:
    """The app icon: teal gradient circle (Android round launcher icon) with five symmetric bars."""
    k = r / 54  # bars from the 108dp adaptive icon (umless_ic_launcher_foreground.xml), centred at 54,54
    bars = [(33, 48.5, 59.5, .75), (43.5, 40.5, 67.5, 1), (54, 32, 76, 1), (64.5, 40.5, 67.5, 1), (75, 48.5, 59.5, .75)]
    paths = "".join(f'<path d="M{cx + (x - 54) * k:.1f} {cy + (y1 - 54) * k:.1f}V{cy + (y2 - 54) * k:.1f}" '
                    f'opacity="{o}"/>' for x, y1, y2, o in bars)
    return (f'<defs><linearGradient id="lg{uid}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#2dd4bf"/>'
            f'<stop offset="1" stop-color="#0d9488"/></linearGradient></defs>'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#lg{uid})"/>'
            f'<g stroke="#fff" stroke-width="{7 * k:.1f}" stroke-linecap="round">{paths}</g>')


def svg_banner(theme: str, name: str, tagline: str, sub: str, chips: list[str]) -> str:
    c = THEMES[theme]
    w, h = 1280, 350
    # Decoration: a waveform whose amber bars (the "嗯 / 那个") drop out, then clean lines of text.
    heights = [18, 30, 46, 64, 40, 82, 56, 100, 70, 44, 88, 60, 34, 74, 50, 28, 40, 22]
    filler_idx = {3, 8, 12}
    x0, mid = 806, 182
    bars = ""
    for i, bh in enumerate(heights):
        x = x0 + i * 14
        if i in filler_idx:
            bars += (f'<rect x="{x - 3.5}" y="{mid - bh / 2}" width="7" height="{bh}" rx="3.5" fill="{c["mark"]}" opacity=".55"/>'
                     f'<path d="M{x - 8} {mid + 9}L{x + 8} {mid - 9}" stroke="{c["mark"]}" stroke-width="2.4" stroke-linecap="round"/>')
        else:
            bars += f'<rect x="{x - 3.5}" y="{mid - bh / 2}" width="7" height="{bh}" rx="3.5" fill="url(#wv)"/>'
    lx = x0 + len(heights) * 14 + 26
    lines = "".join(f'<rect x="{lx + 34}" y="{mid - 38 + j * 26}" width="{lw}" height="11" rx="5.5" fill="{c["accent"]}" '
                    f'opacity="{op}"/>' for j, (lw, op) in enumerate([(104, .9), (82, .55), (94, .35)]))
    arrow = (f'<path d="M{lx - 8} {mid}H{lx + 18}M{lx + 10} {mid - 7}L{lx + 18} {mid}L{lx + 10} {mid + 7}" fill="none" '
             f'stroke="{c["accent"]}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>')
    pills, px = "", 250
    for chip in chips:
        cw = sum(15 if ord(ch) > 0x2E80 else 8.6 for ch in chip) + 26
        pills += (f'<rect x="{px}" y="276" width="{cw:.0f}" height="30" rx="15" fill="{c["soft"]}"/>'
                  f'<text x="{px + cw / 2:.0f}" y="296" font-size="14.5" text-anchor="middle" fill="{c["accent_text"]}">{_esc(chip)}</text>')
        px += cw + 10
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{_esc(name)} — {_esc(tagline)}">
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c["bg1"]}"/><stop offset="1" stop-color="{c["bg2"]}"/></linearGradient>
  <radialGradient id="glow" cx="0.78" cy="0.45" r="0.42"><stop offset="0" stop-color="{c["glow"]}" stop-opacity="{c["glow_op"]}"/><stop offset="1" stop-color="{c["glow"]}" stop-opacity="0"/></radialGradient>
  <linearGradient id="wv" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2dd4bf"/><stop offset="1" stop-color="#0d9488"/></linearGradient>
</defs>
<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="26" fill="url(#bg)" stroke="{c["line"]}" stroke-width="1.5"/>
<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="26" fill="url(#glow)"/>
{_logo_mark(150, 188, 62, theme)}
<g font-family="{SVG_FONT}">
  <text x="248" y="160" font-size="66" font-weight="700" fill="{c["text"]}" letter-spacing="-1">{_esc(name)}</text>
  <text x="250" y="212" font-size="27" fill="{c["text2"]}">{_esc(tagline)}</text>
  <text x="251" y="250" font-size="16.5" fill="{c["text3"]}">{_esc(sub)}</text>
  {pills}
</g>
{bars}{arrow}{lines}
</svg>
'''


# Real output of umless.metrics.analyze() for this sentence (checked in svg_before_after); polished text is the example.
BA_RAW = "嗯那个这个方案我觉得就是周三不对周四之前能做完然后测试测试那边再加两个人"
BA_OUT = "这个方案我觉得周四之前能做完，测试那边再加两个人。"


def svg_before_after(theme: str) -> str:
    from umless.metrics import analyze

    c = THEMES[theme]
    m = analyze(BA_RAW, BA_OUT, 9.0)
    assert (m["filler_count"], m["repeat_count"], m["correction_count"]) == (4, 1, 1), m
    fs, w, h, pad = 26, 960, 452, 44
    # Break the highlighted transcript into two lines after "周四之前能做完".
    segs = m["highlights"]
    split = next(i for i, (t, _) in enumerate(segs) if t == "周四之前能做完") + 1
    body = ""
    for line, (y, items) in enumerate([(128, segs[:split]), (178, segs[split:])]):
        x = pad
        for text, kind in items:
            n = len(text)
            if kind == "keep":
                body += f'<text x="{x}" y="{y}" fill="{c["text"]}">{text}</text>'
                x += n * fs
                continue
            x += 3
            col = c["cut"] if kind == "cut" else c[kind]
            if kind != "cut":
                body += f'<rect x="{x - 3}" y="{y - fs + 1}" width="{n * fs + 6}" height="{fs + 10}" rx="5" fill="{c[kind + "_bg"]}"/>'
            body += (f'<text x="{x}" y="{y}" fill="{col}">{text}</text>'
                     f'<path d="M{x - 1} {y - fs * .34:.1f}H{x + n * fs + 1}" stroke="{col}" stroke-width="2" opacity=".6"/>')
            x += n * fs + 3
    chips, cx = "", pad
    for label, value, fg, bg in [("口癖", m["filler_count"], c["filler"], c["filler_bg"]),
                                 ("重复", m["repeat_count"], c["repeat"], c["repeat_bg"]),
                                 ("改口", m["correction_count"], c["correction"], c["correction_bg"]),
                                 ("流畅度", round(m["fluency"]), c["text2"], c["card2"]),
                                 ("简洁度", round(m["concision"]), c["text2"], c["card2"])]:
        cw = len(label) * 15 + len(str(value)) * 9 + 30
        chips += (f'<rect x="{cx}" y="214" width="{cw}" height="30" rx="7" fill="{bg}"/>'
                  f'<text x="{cx + 12}" y="234" font-size="15" fill="{fg}">{label}<tspan font-weight="700" dx="5">{value}</tspan></text>')
        cx += cw + 10
    legend, lx = "", w - pad
    for label, col in reversed([("口癖", c["mark"]), ("重复", c["repeat"]), ("改口", c["correction"]), ("删改", c["cut"])]):
        lx -= 54
        legend += (f'<rect x="{lx}" y="51" width="10" height="10" rx="2.5" fill="{col}"/>'
                   f'<text x="{lx + 16}" y="61" font-size="14" fill="{c["text3"]}">{label}</text>')
    out_x = pad
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="原话：{BA_RAW}；Umless 输出：{BA_OUT}">
<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="20" fill="{c["card"]}" stroke="{c["line"]}" stroke-width="1.5"/>
<g font-family="{SVG_FONT}">
  <text x="{pad}" y="62" font-size="15" font-weight="700" fill="{c["text3"]}" letter-spacing=".5">原话 · 本机识别的逐字稿（留给表达教练）</text>
  {legend}
  <g font-size="{fs}">{body}</g>
  {chips}
  <path d="M{pad} 284H{w / 2 - 120}M{w / 2 + 120} 284H{w - pad}" stroke="{c["line"]}" stroke-width="1.5"/>
  <rect x="{w / 2 - 112}" y="268" width="224" height="32" rx="16" fill="{c["soft"]}"/>
  <text x="{w / 2}" y="289" font-size="15" text-anchor="middle" fill="{c["accent_text"]}">↓ 大模型润色 · 适度</text>
  <text x="{pad}" y="338" font-size="15" font-weight="700" fill="{c["accent_text"]}" letter-spacing=".5">写入光标处</text>
  <text x="{out_x}" y="384" font-size="{fs}" fill="{c["text"]}">{BA_OUT}</text>
  <text x="{pad}" y="426" font-size="13" fill="{c["text3"]}">示例句子；高亮与计数是 Umless 规则指标（spec/metrics.md）对这句话的实际计算结果</text>
</g>
</svg>
'''


COACH_TEXT = {
    "zh": dict(steps=[("1", "每次说话", "逐字稿只存在本机", "录音默认不保存"),
                      ("2", "规则指标", "口癖 · 重复 · 改口", "本地即时计算"),
                      ("3", "每日诊断", "流畅 简洁 逻辑 清晰", "原话 → 更好的说法"),
                      ("4", "习惯卡 + 练习", "最多 3 张进行中", "每天 5 道口语题")],
               loop="第二天说得更干净一点，进度看得见", body=14.5,
               label="表达教练：每次说话 → 规则指标 → 每日诊断 → 习惯卡与练习"),
    "en": dict(steps=[("1", "Every utterance", "transcript stays local", "audio not kept"),
                      ("2", "Rule metrics", "fillers · repeats · fixes", "instant, on device"),
                      ("3", "Daily diagnosis", "four scores, issues", "original → better"),
                      ("4", "Habits + practice", "up to 3 active cards", "5 prompts a day")],
               loop="Speak a bit cleaner the next day, and see it", body=13.5,
               label="Expression coach: every utterance → rule metrics → daily diagnosis → habits and practice"),
}


def svg_coach_loop(theme: str, lang: str = "zh") -> str:
    c = THEMES[theme]
    tx = COACH_TEXT[lang]
    w, h = 960, 330
    steps = tx["steps"]
    cw, ch, gap, y0 = 198, 158, 36, 34
    x0 = (w - 4 * cw - 3 * gap) / 2
    parts = ""
    for i, (num, title, l1, l2) in enumerate(steps):
        x = x0 + i * (cw + gap)
        mx = x + cw / 2
        parts += (f'<rect x="{x}" y="{y0}" width="{cw}" height="{ch}" rx="16" fill="{c["card2"]}" stroke="{c["line"]}" stroke-width="1.5"/>'
                  f'<circle cx="{mx}" cy="{y0 + 34}" r="15" fill="{c["soft"]}"/>'
                  f'<text x="{mx}" y="{y0 + 40}" font-size="15" font-weight="700" text-anchor="middle" fill="{c["accent_text"]}">{num}</text>'
                  f'<text x="{mx}" y="{y0 + 84}" font-size="19" font-weight="700" text-anchor="middle" fill="{c["text"]}">{_esc(title)}</text>'
                  f'<text x="{mx}" y="{y0 + 113}" font-size="{tx["body"]}" text-anchor="middle" fill="{c["text2"]}">{_esc(l1)}</text>'
                  f'<text x="{mx}" y="{y0 + 136}" font-size="{tx["body"]}" text-anchor="middle" fill="{c["text2"]}">{_esc(l2)}</text>')
        if i < 3:
            ax = x + cw + 6
            parts += (f'<path d="M{ax} {y0 + ch / 2}H{ax + gap - 12}M{ax + gap - 19} {y0 + ch / 2 - 6}L{ax + gap - 12} {y0 + ch / 2}'
                      f'L{ax + gap - 19} {y0 + ch / 2 + 6}" fill="none" stroke="{c["accent"]}" stroke-width="2.2" '
                      f'stroke-linecap="round" stroke-linejoin="round"/>')
    left, right = x0 + cw / 2, x0 + 3 * (cw + gap) + cw / 2
    yb, yl = y0 + ch + 6, y0 + ch + 74
    loop = (f'<path d="M{right} {yb}V{yl - 20}Q{right} {yl} {right - 20} {yl}H{left + 20}Q{left} {yl} {left} {yl - 20}V{yb + 10}" '
            f'fill="none" stroke="{c["accent"]}" stroke-width="2.2" stroke-dasharray="6 6" stroke-linecap="round"/>'
            f'<path d="M{left - 7} {yb + 16}L{left} {yb + 6}L{left + 7} {yb + 16}" fill="none" stroke="{c["accent"]}" '
            f'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<rect x="{w / 2 - 170}" y="{yl - 17}" width="340" height="34" rx="17" fill="{c["card"]}" stroke="{c["line"]}" stroke-width="1.5"/>'
            f'<text x="{w / 2}" y="{yl + 5}" font-size="15" text-anchor="middle" fill="{c["accent_text"]}">{_esc(tx["loop"])}</text>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{_esc(tx["label"])}">
<g font-family="{SVG_FONT}">{parts}{loop}</g>
</svg>
'''


PIPE_TEXT = {
    "zh": dict(top=[("按住说话", "热键 / 长按空格", True), ("本机识别", "SenseVoice · sherpa-onnx", True),
                    ("场景识别", "聊天 · 文档 · 代码 · 邮件", True), ("大模型润色", "忠实 / 适度 / 重构", False),
                    ("写入光标处", "润色失败时写入原文", True)],
               bottom=[("本机 SQLite", "逐字稿 + 润色稿", True), ("规则指标", "口癖 · 重复 · 改口", True),
                       ("每日诊断", "大模型 · 每天一次", False), ("表达教练", "评分 · 习惯卡 · 练习", True)],
               cap1="每次说话", cap2="后台进行，不拖慢输入", local="本机", llm="你配置的大模型",
               t1="逐字稿", t2="润色稿", title=17, sub=13.5,
               label="工作原理：按住说话 → 本机识别 → 场景识别 → 大模型润色 → 写入光标处；逐字稿存入本机 SQLite，供规则指标与每日诊断使用"),
    "en": dict(top=[("Hold to talk", "hotkey / long-press space", True), ("On-device ASR", "SenseVoice · sherpa-onnx", True),
                    ("Scene", "chat · doc · code · email", True), ("LLM polish", "three levels", False),
                    ("Typed at cursor", "raw text if the LLM fails", True)],
               bottom=[("Local SQLite", "transcript + polished", True), ("Rule metrics", "fillers · repeats · fixes", True),
                       ("Daily diagnosis", "LLM, once a day", False), ("Coach", "scores · habits · practice", True)],
               cap1="Every utterance", cap2="In the background, never slows typing", local="On device", llm="Your LLM",
               t1="transcript", t2="polished", title=16, sub=12.5,
               label="How it works: hold to talk → on-device ASR → scene → LLM polish → typed at cursor; transcripts go to a local SQLite database used by rule metrics and the daily diagnosis"),
}


def svg_pipeline(theme: str, lang: str = "zh") -> str:
    c = THEMES[theme]
    tx = PIPE_TEXT[lang]
    w, h = 1000, 344
    bw, bh, gap, x0 = 176, 70, 26, 8
    y1, y2 = 46, 214

    def head(x, y, down=False):
        d = f"M{x - 6} {y - 7}L{x} {y}L{x + 6} {y - 7}" if down else f"M{x - 7} {y - 6}L{x} {y}L{x - 7} {y + 6}"
        return (f'<path d="{d}" fill="none" stroke="{c["accent"]}" stroke-width="2" stroke-linecap="round" '
                f'stroke-linejoin="round"/>')

    def box(x, y, title, sub, local):
        fill, dash = (c["soft"], "") if local else (c["card"], ' stroke-dasharray="5 4"')
        return (f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="12" fill="{fill}" stroke="{c["accent"]}" '
                f'stroke-opacity=".45" stroke-width="1.5"{dash}/>'
                f'<text x="{x + bw / 2}" y="{y + 30}" font-size="{tx["title"]}" font-weight="700" text-anchor="middle" '
                f'fill="{c["text"]}">{_esc(title)}</text>'
                f'<text x="{x + bw / 2}" y="{y + 53}" font-size="{tx["sub"]}" text-anchor="middle" '
                f'fill="{c["text2"]}">{_esc(sub)}</text>')

    def chain(items, xs, y):
        out = ""
        for i, (t, sub, local) in enumerate(items):
            x = xs + i * (bw + gap)
            out += box(x, y, t, sub, local)
            if i < len(items) - 1:
                out += (f'<path d="M{x + bw + 4} {y + bh / 2}H{x + bw + gap - 4}" stroke="{c["accent"]}" stroke-width="2"/>'
                        + head(x + bw + gap - 4, y + bh / 2))
        return out

    bx0 = x0 + (bw + gap)  # the bottom row starts under the second column
    parts = chain(tx["top"], x0, y1) + chain(tx["bottom"], bx0, y2)
    # transcript: ASR -> SQLite (straight down)
    ax = bx0 + bw / 2 - 30
    parts += (f'<path d="M{ax} {y1 + bh + 4}V{y2 - 4}" stroke="{c["accent"]}" stroke-width="2" stroke-dasharray="5 5"/>'
              + head(ax, y2 - 4, True)
              + f'<text x="{ax - 8}" y="{(y1 + bh + y2) / 2 + 5}" font-size="13" text-anchor="end" '
                f'fill="{c["text3"]}">{_esc(tx["t1"])}</text>')
    # polished: LLM polish -> SQLite (down, left, down)
    lx = x0 + 3 * (bw + gap) + bw / 2
    my = (y1 + bh + y2) / 2 + 2
    px = bx0 + bw / 2 + 30
    parts += (f'<path d="M{lx} {y1 + bh + 4}V{my - 12}Q{lx} {my} {lx - 12} {my}H{px + 12}Q{px} {my} {px} {my + 12}V{y2 - 4}" '
              f'fill="none" stroke="{c["accent"]}" stroke-width="2" stroke-dasharray="5 5"/>' + head(px, y2 - 4, True)
              + f'<text x="{(lx + px) / 2}" y="{my - 8}" font-size="13" text-anchor="middle" '
                f'fill="{c["text3"]}">{_esc(tx["t2"])}</text>')
    caps = (f'<text x="{x0}" y="{y1 - 16}" font-size="14" font-weight="700" fill="{c["text3"]}">{_esc(tx["cap1"])}</text>'
            f'<text x="{bx0}" y="{y2 + bh + 30}" font-size="14" font-weight="700" fill="{c["text3"]}">{_esc(tx["cap2"])}</text>')
    lg = w - 8
    legend = (f'<rect x="{lg - 250}" y="{y1 - 30}" width="14" height="14" rx="4" fill="{c["soft"]}" stroke="{c["accent"]}" '
              f'stroke-opacity=".45"/>'
              f'<text x="{lg - 230}" y="{y1 - 18}" font-size="13" fill="{c["text3"]}">{_esc(tx["local"])}</text>'
              f'<rect x="{lg - 150}" y="{y1 - 30}" width="14" height="14" rx="4" fill="{c["card"]}" stroke="{c["accent"]}" '
              f'stroke-opacity=".45" stroke-dasharray="3 2"/>'
              f'<text x="{lg - 130}" y="{y1 - 18}" font-size="13" fill="{c["text3"]}">{_esc(tx["llm"])}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="{_esc(tx["label"])}">\n<g font-family="{SVG_FONT}">{caps}{legend}{parts}</g>\n</svg>\n')


def art() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = {}
    for theme in ("light", "dark"):
        files[f"banner-{theme}.svg"] = svg_banner(theme, "Umless", "会帮你改掉口头禅的语音输入工具",
                                                  "Voice typing that cleans up your words, and helps you speak cleaner.",
                                                  ["Windows", "Android", "本机识别", "自带大模型", "数据只在本地", "开源"])
        files[f"banner-android-{theme}.svg"] = svg_banner(theme, "Umless 输入法", "会帮你改掉口头禅的中文语音输入法",
                                                          "Pinyin keyboard (fcitx5) + on-device speech + expression coach.",
                                                          ["拼音 / 双拼", "长按空格说话", "本机识别", "表达教练", "LGPL-2.1"])
        files[f"before-after-{theme}.svg"] = svg_before_after(theme)
        files[f"coach-loop-{theme}.svg"] = svg_coach_loop(theme)
        files[f"coach-loop-en-{theme}.svg"] = svg_coach_loop(theme, "en")
        files[f"pipeline-{theme}.svg"] = svg_pipeline(theme)
        files[f"pipeline-en-{theme}.svg"] = svg_pipeline(theme, "en")
    for name, text in files.items():
        is_android_banner = name.startswith("banner-android")
        targets = [] if is_android_banner else [OUT]
        if ANDROID_REPO.exists() and (is_android_banner or name.startswith(("before-after", "coach-loop"))):
            targets.append(ANDROID_OUT)
        for d in targets:
            d.mkdir(parents=True, exist_ok=True)
            (d / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"  {name}: {len(text.encode()) / 1024:.1f} KB -> {', '.join(t.parent.parent.parent.name for t in targets)}")


# Composed images each README uses.
DESKTOP_ASSETS = ["hero", "report", "report-examples", "habits", "practice-result", "history",
                  "history-dark", "settings-polish", "settings-scenes", "overlay", "android-trio"]
ANDROID_ASSETS = ["android-trio", "android-more"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-only", action="store_true", help="only refresh the raw captures")
    ap.add_argument("--compose-only", action="store_true", help="reuse the raw captures")
    ap.add_argument("--art-only", action="store_true", help="only regenerate the SVG illustrations")
    args = ap.parse_args()
    print("svg ->", OUT)
    art()
    if args.art_only:
        return
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(WEB)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        if not args.compose_only:
            capture(browser, server.server_port)
        if not args.raw_only:
            compose(browser)
        browser.close()
    server.shutdown()
    if args.raw_only:
        return
    comp = RAW.parent / "composed"
    print("optimized ->", OUT)
    for n in DESKTOP_ASSETS:
        optimize(comp / f"{n}.png", OUT / f"{n}.png")
    if ANDROID_REPO.exists():
        print("optimized ->", ANDROID_OUT)
        for n in ANDROID_ASSETS:
            optimize(comp / f"{n}.png", ANDROID_OUT / f"{n}.png")


if __name__ == "__main__":
    main()
