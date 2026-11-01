"""聆听提示条的预览 / 截图 / 计时工具。

用法（在 desktop/ 下）：
    uv run python tools/overlay_preview.py shots   # 离线渲染各状态，合成到浅色 / 深色背景，存到 .testhome/overlay-shots/
    uv run python tools/overlay_preview.py bench   # 计时：scale=2.0 下每帧绘制 + 上屏数据准备的耗时
    uv run python tools/overlay_preview.py live    # 真机：启动 Overlay，模拟音量依次切换状态，截屏并检查不抢焦点
"""

import ctypes
import math
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from PIL import Image, ImageDraw  # noqa: E402

from umless import overlay_render as R  # noqa: E402

OUT = ROOT / ".testhome" / "overlay-shots"
TICK = 1 / 40  # 渲染帧间隔
CHUNK = 0.04  # 录音器每 40ms 追加一个 RMS


def speech_rms(seconds: float, loud: float = 1.0, seed: int = 1) -> list[float]:
    """合成一段"像说话"的 RMS 序列：约 4~5 个音节 / 秒，音节间有停顿，叠加底噪。"""
    rnd = random.Random(seed)
    n = int(seconds / CHUNK)
    out = [0.0] * n
    t = 0.15
    while t < seconds:
        dur = rnd.uniform(0.12, 0.26)
        peak = loud * rnd.uniform(0.35, 1.0)
        for i in range(n):
            x = (i * CHUNK - t) / dur
            if 0 <= x <= 1:
                out[i] = max(out[i], peak * math.sin(math.pi * x) ** 1.5)
        t += dur + rnd.uniform(0.02, 0.18) + (0.35 if rnd.random() < 0.15 else 0)
    return [0.0015 + rnd.uniform(0, 0.001) + v for v in out]


def recording_frames(rms: list[float], until: float, every: float = TICK) -> list[R.Frame]:
    """用 Wave 平滑器按真实帧率模拟录音，返回每一帧。"""
    wave = R.Wave()
    frames = []
    t = 0.0
    while t <= until + 1e-9:
        levels = rms[: int(t / CHUNK) + 1]
        bars = wave.update(levels, t)
        frames.append(R.Frame("recording", t=t, elapsed=t, levels=levels, bars=bars, clock=t))
        t += every
    return frames


def on_bg(img: Image.Image, dark: bool) -> Image.Image:
    """合成到背景上：浅色背景带一点"文档"纹理，深色背景模拟深色编辑器。"""
    w, h = img.width + 80, img.height + 40
    bg = Image.new("RGBA", (w, h), (24, 24, 27, 255) if dark else (246, 246, 248, 255))
    d = ImageDraw.Draw(bg)
    ink = (60, 60, 66, 255) if dark else (222, 222, 228, 255)
    for y in range(14, h, 22):
        d.rounded_rectangle((20, y, w - 20 - (y * 37) % 160, y + 7), 3, fill=ink)
    bg.alpha_composite(img, ((w - img.width) // 2, (h - img.height) // 2))
    return bg


def sheet(items: list[tuple[str, Image.Image]], dark: bool, cols: int = 1) -> Image.Image:
    tiles = [on_bg(img, dark) for _, img in items]
    tw = max(t.width for t in tiles)
    th = max(t.height for t in tiles)
    rows = math.ceil(len(tiles) / cols)
    out = Image.new("RGBA", (tw * cols, (th + 18) * rows), (12, 12, 14, 255) if dark else (255, 255, 255, 255))
    d = ImageDraw.Draw(out)
    font = R._font(13)
    for i, ((name, _), tile) in enumerate(zip(items, tiles)):
        x, y = (i % cols) * tw, (i // cols) * (th + 18)
        d.text((x + 6, y + 2), name, font=font, fill=(150, 150, 160, 255))
        out.alpha_composite(tile, (x + (tw - tile.width) // 2, y + 18))
    return out


def transition(prev: R.Frame, nxt: R.Frame, scale: float, mixes: list[float]) -> list[tuple[str, Image.Image]]:
    w0, w1 = R.pill_width(prev, scale), R.pill_width(nxt, scale)
    out = []
    for p in mixes:
        e = 1 - (1 - p) ** 3
        out.append((f"{prev.state}->{nxt.state} p={p:.2f}", R.render(nxt, scale, w0 + (w1 - w0) * e, prev, p)))
    return out


def cmd_shots(scale: float = 2.0) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    quiet = speech_rms(4, loud=0.03, seed=3)
    loud = speech_rms(4, loud=0.2, seed=5)
    silent = [0.0015] * 100
    rq = recording_frames(quiet, 3.0)
    rl = recording_frames(loud, 3.0)
    rs = recording_frames(silent, 1.0)

    def pick(frames, t):
        return min(frames, key=lambda f: abs(f.t - t))

    rec_loud = max(rl[40:], key=lambda f: sum(f.bars))
    states = [
        ("recording 静音 t=0.2", R.render(pick(rs, 0.2), scale)),
        ("recording 静音 t=0.9（呼吸）", R.render(pick(rs, 0.9), scale)),
        ("recording 小声", R.render(max(rq[40:], key=lambda f: sum(f.bars)), scale)),
        ("recording 大声", R.render(rec_loud, scale)),
        ("transcribing", R.render(R.Frame("transcribing", t=0.4, clock=0.4), scale)),
        ("polishing", R.render(R.Frame("polishing", t=1.0, clock=1.1), scale)),
        ("done t=0.12", R.render(R.Frame("done", t=0.12, text="已输入"), scale)),
        ("done", R.render(R.Frame("done", t=0.6, text="已输入"), scale)),
        ("info", R.render(R.Frame("info", t=0.5, text="未识别到内容"), scale)),
        ("error", R.render(R.Frame("error", t=0.5, text="润色失败，已输出原文"), scale)),
    ]
    for dark in (False, True):
        sheet(states, dark, cols=2).save(OUT / f"states-{'dark' if dark else 'light'}.png")

    # 宽度过渡的中间帧：录音 -> 识别、识别 -> 润色、润色 -> 完成
    mixes = [0.0, 0.15, 0.3, 0.45, 0.6, 0.8, 1.0]
    tr = R.Frame("transcribing", t=0.1, clock=3.1)
    trans = transition(rec_loud, tr, scale, mixes)
    trans += transition(tr, R.Frame("polishing", t=0.1, clock=3.2), scale, [0.3, 0.6])
    trans += transition(R.Frame("polishing", t=1, clock=4), R.Frame("done", t=0.1, text="已输入"), scale, [0.3, 0.6])
    trans += transition(R.Frame("done", t=0.5, text="已输入"), R.Frame("recording", t=0.05, clock=0), scale, [0.4])
    for dark in (False, True):
        sheet(trans, dark, cols=2).save(OUT / f"transition-{'dark' if dark else 'light'}.png")

    # 声波胶片：大声说话时连续 16 帧（25ms 一帧）、小声 8 帧
    strip = [(f"loud t={f.t:.3f}", R.render(f, scale)) for f in rl[60:76]]
    strip += [(f"quiet t={f.t:.3f}", R.render(f, scale)) for f in rq[60:68]]
    sheet(strip, True, cols=3).save(OUT / "wave-strip-dark.png")

    # 转圈弧长、对勾描绘的时间序列
    spin = [(f"spin {t:.2f}", R.render(R.Frame("transcribing", t=t, clock=t), scale)) for t in
            [0.0, 0.2, 0.4, 0.6, 0.75, 0.9, 1.1, 1.3]]
    spin += [(f"done {t:.2f}", R.render(R.Frame("done", t=t, text="已输入"), scale)) for t in
             [0.0, 0.08, 0.16, 0.24, 0.32, 0.45]]
    sheet(spin, True, cols=4).save(OUT / "anim-dark.png")
    # 1x 缩放下也看一眼
    sheet([(n, R.render(f, 1.0)) for n, f in [("1x recording", rec_loud),
                                             ("1x done", R.Frame("done", t=0.6, text="已输入"))]],
          False, cols=2).save(OUT / "scale1-light.png")
    print("已保存到", OUT)


def to_bgra(img: Image.Image) -> bytes:
    """与 overlay._blit 相同的上屏数据准备：预乘 alpha 的 BGRA。"""
    return img.convert("RGBa").tobytes("raw", "BGRa")


def cmd_bench(scale: float = 2.0) -> None:
    rms = speech_rms(6, loud=0.15)
    frames = recording_frames(rms, 5.0)
    R.render(frames[0], scale)  # 预热缓存

    def run(name, fn, items):
        ts = []
        for it in items:
            t0 = time.perf_counter()
            fn(it)
            ts.append((time.perf_counter() - t0) * 1000)
        ts.sort()
        p95 = ts[int(len(ts) * 0.95) - 1]
        print(f"{name:<28} n={len(ts):<4} 平均 {statistics.mean(ts):5.2f}ms  p95 {p95:5.2f}ms  最大 {ts[-1]:5.2f}ms")

    run("录音帧 render+BGRA", lambda f: to_bgra(R.render(f, scale)), frames)
    tr = R.Frame("transcribing", t=0.1, clock=1)
    w0, w1 = R.pill_width(frames[-1], scale), R.pill_width(tr, scale)
    mixes = [i / 40 for i in range(41)] * 3
    run("过渡帧 render+BGRA", lambda p: to_bgra(R.render(tr, scale, w0 + (w1 - w0) * p, frames[-1], p)), mixes)
    run("识别中 render+BGRA", lambda t: to_bgra(R.render(R.Frame("transcribing", t=t, clock=t), scale)),
        [i * TICK for i in range(120)])
    run("完成 render+BGRA", lambda t: to_bgra(R.render(R.Frame("done", t=t, text="已输入"), scale)),
        [i * TICK for i in range(120)])


def grab_screen(box: tuple[int, int, int, int]) -> Image.Image:
    """截屏（含分层窗口）。PIL.ImageGrab 的 BitBlt 不带 CAPTUREBLT，截不到 UpdateLayeredWindow 的窗口。"""
    from ctypes import wintypes

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    for fn in (gdi32.CreateCompatibleDC, gdi32.CreateCompatibleBitmap, gdi32.SelectObject, user32.GetDC):
        fn.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.BitBlt.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_void_p] + [ctypes.c_int] * 2 + [wintypes.DWORD]
    gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    sdc = user32.GetDC(None)
    mdc = gdi32.CreateCompatibleDC(sdc)
    bmp = gdi32.CreateCompatibleBitmap(sdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    gdi32.BitBlt(mdc, 0, 0, w, h, sdc, x0, y0, 0x00CC0020 | 0x40000000)  # SRCCOPY | CAPTUREBLT
    hdr = (ctypes.c_uint32 * 10)(40, w, (-h) & 0xFFFFFFFF, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, hdr, 0)
    gdi32.SelectObject(mdc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(None, sdc)
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX").convert("RGBA")


def cmd_live() -> None:
    user32 = ctypes.windll.user32
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    user32.GetForegroundWindow.restype = ctypes.c_void_p

    from umless.overlay import Overlay, _monitor_of_foreground

    OUT.mkdir(parents=True, exist_ok=True)
    rms = speech_rms(8, loud=0.12, seed=7)
    t_rec = [0.0]

    def levels():  # 模拟录音器：每 40ms 追加一个值，最多保留 64 个
        n = int((time.perf_counter() - t_rec[0]) / CHUNK) + 1
        return rms[max(0, n - 64): n]

    work, scale = _monitor_of_foreground()
    box = (work.left + (work.right - work.left) // 2 - int(260 * scale), work.bottom - int(140 * scale),
           work.left + (work.right - work.left) // 2 + int(260 * scale), work.bottom)
    fg0 = user32.GetForegroundWindow()
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(ctypes.c_void_p(fg0), title, 256)
    locked = "锁屏" in title.value or "Lock" in title.value
    ov = Overlay(levels)
    blits = []  # 记录每次上屏的位置与不透明度
    orig_blit = ov._blit
    ov._blit = lambda img, x, y, op: (blits.append((x, y, op)), orig_blit(img, x, y, op))
    ov.start()
    shots = []

    def grab(name: str) -> None:
        shot = grab_screen(box)
        hwnd = ctypes.c_void_p(ov._hwnd)
        if locked and ov._dib is not None and user32.IsWindowVisible(hwnd):
            # 锁屏时窗口被锁屏界面盖住、截不到：读回交给 UpdateLayeredWindow 的预乘 BGRA 位图，
            # 按窗口真实位置和不透明度合成，验证位置 / 尺寸 / 像素格式
            r = (ctypes.c_long * 4)()
            user32.GetWindowRect(hwnd, r)
            w, h = ov._dib[2]
            raw = ctypes.string_at(ov._dib[1], w * h * 4)
            layer = Image.frombuffer("RGBa", (w, h), raw, "raw", "BGRa", 0, 1).convert("RGBA")
            assert (r[2] - r[0], r[3] - r[1]) == (w, h), "窗口尺寸与位图不符"
            op = blits[-1][2]
            layer.putalpha(layer.getchannel("A").point(lambda v: round(v * op)))
            shot.alpha_composite(layer, (r[0] - box[0], r[1] - box[1]))
            name += "（读回位图合成）"
        shots.append((name, shot))

    t_rec[0] = time.perf_counter()
    ov.show("recording")
    time.sleep(0.3)
    grab("recording 0.3s")
    time.sleep(1.2)
    grab("recording 1.5s")
    ov.show("transcribing")
    time.sleep(0.09)
    grab("-> transcribing 过渡中")
    time.sleep(0.5)
    grab("transcribing")
    ov.show("polishing")
    time.sleep(0.6)
    grab("polishing")
    ov.show("done", "已输入")
    time.sleep(0.5)
    grab("done")
    time.sleep(1.0)  # 自动淡出
    ov.show("info", "未识别到内容")
    time.sleep(0.5)
    grab("info")
    ov.hide()
    time.sleep(0.4)
    grab("hidden")
    fg1 = user32.GetForegroundWindow()
    ticks = sorted(ov.tick_ms)
    ov.stop()
    time.sleep(0.1)

    sheet_img = Image.new("RGBA", (box[2] - box[0], (box[3] - box[1] + 20) * len(shots)), (255, 255, 255, 255))
    d = ImageDraw.Draw(sheet_img)
    for i, (name, img) in enumerate(shots):
        y = i * (img.height + 20)
        d.text((6, y + 2), name, font=R._font(14), fill=(0, 0, 0, 255))
        sheet_img.alpha_composite(img, (0, y + 20))
    sheet_img.save(OUT / "live.png")
    print(f"屏幕缩放 {scale:.2f}，截图已保存到 {OUT / 'live.png'}")
    if locked:
        print("注意：当前处于锁屏，屏幕截图里看不到提示条，截图为“锁屏背景 + 读回的窗口位图”合成")
    print(f"前台窗口：之前 {fg0:#x}，之后 {fg1:#x}，{'未变（不抢焦点）' if fg0 == fg1 else '发生变化！'}")
    if ticks:
        print(f"每帧绘制+上屏：n={len(ticks)} 平均 {statistics.mean(ticks):.2f}ms  "
              f"p95 {ticks[int(len(ticks) * 0.95) - 1]:.2f}ms  最大 {ticks[-1]:.2f}ms")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "shots"
    {"shots": cmd_shots, "bench": cmd_bench, "live": cmd_live}[cmd]()
