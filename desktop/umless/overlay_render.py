"""状态提示条的绘制（纯 Pillow，与窗口系统无关，可单独导出预览图）。

深色胶囊 + 柔和投影；录音时边缘带极淡的青绿描边与外发光，中间是青绿色左右对称声波。

性能要点（2560x1600 @200% 下每帧需远小于 10ms）：
- 投影 / 胶囊底 / 描边 / 发光只按缩放比例绘制一次（4 倍超采样），按"三段拉伸"复用到任意宽度：
  胶囊左右两端是半圆，中间各列完全相同，所以只要复制中心那一列即可得到任意宽度，不必每帧模糊。
- 图标、声波这类小块图形在小画布上超采样后缩小；文字直接按物理像素渲染（字形更清晰）并缓存。
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from .i18n import t

# ---- 尺寸（逻辑像素） ----
HEIGHT = 44
PAD_X = 18
GAP = 10
MARGIN = 18  # 给阴影 / 外发光留的边距
ICON = 14  # 图标占位宽度
CONTENT_PAD = 5  # 内容图层两侧的透明留白
MIN_W = 100  # 胶囊最小宽度（也是三段拉伸底图的宽度，需让中段远离两端的模糊范围）
BAR_HALF = 8  # 声波：中心一根 + 两侧各 8 根 = 17 根
BAR_COUNT = 2 * BAR_HALF + 1
BAR_W, BAR_GAP = 3, 3
WAVE_W = BAR_COUNT * (BAR_W + BAR_GAP) - BAR_GAP
WAVE_H = 24  # 声波最大高度
WAVE_GAP = 14  # 声波与两侧文字的间距（比普通间距略大，让声波"透气"）

SS_BASE = 4  # 底图（缓存）的超采样倍数
SS_ICON = 4  # 图标、声波的超采样倍数（画布很小，开销可忽略）

# ---- 颜色 ----
BG_TOP = (36, 36, 42)  # 胶囊底色：自上而下轻微变暗，增加一点体积感
BG_BOTTOM = (24, 24, 28)
BG_ALPHA = 240
TEXT = (255, 255, 255, 236)
SUBTEXT = (255, 255, 255, 140)
RED = (255, 69, 58, 255)
GREEN = (48, 209, 88, 255)
AMBER = (255, 204, 0, 255)
TEAL_LIGHT = (94, 234, 212)  # #5eead4
TEAL = (45, 212, 191)  # #2dd4bf
TEAL_DEEP = (20, 184, 166)  # #14b8a6
ICON_INK = (28, 28, 30, 255)  # 黄 / 红圆底上的感叹号


@dataclass
class Frame:
    state: str  # recording | transcribing | polishing | done | info | error
    t: float = 0.0  # 进入当前状态后的秒数（驱动动画）
    elapsed: float = 0.0  # 录音时长
    levels: list[float] = field(default_factory=list)  # 最近的音量（RMS），每 40ms 一个
    text: str = ""
    # 平滑后的半边声波高度（中心 -> 外侧，共 BAR_HALF+1 个，0..1），由 Wave 维护；
    # 为 None 时直接由 levels 计算（无平滑，供离线预览）
    bars: Sequence[float] | None = None
    # 跨状态连续的全局时钟（转圈动画用，避免"识别中 -> 润色中"时转圈跳角度）；None 时用 t
    clock: float | None = None


# ====================================================================== 声波


def level_to_amp(rms: float) -> float:
    """RMS -> 0..1。按分贝映射更符合听感：约 -50dB（底噪）为 0，-14dB（大声）为 1。"""
    db = 20 * math.log10(max(rms, 1e-6))
    x = min(max((db + 50) / 36, 0.0), 1.0)
    return x**1.25  # 略压低小值，让静音更"静"、说话起伏更明显


# 越往外越矮，形成中间高两边低的包络
_PROFILE = [1 - 0.5 * (d / BAR_HALF) ** 1.5 for d in range(BAR_HALF + 1)]
# 每根柱子各自的轻微抖动（频率 / 相位互不相同），让持续发音时不至于僵直
_WOBBLE = [(1.3 + 0.37 * d, 1.7 * d + 0.4) for d in range(BAR_HALF + 1)]


def _bar_targets(levels: Sequence[float], now: float) -> list[float]:
    """最新的音量在中心，越旧的越靠外（声波从中间向两侧扩散）。"""
    recent = list(levels[-(BAR_HALF + 1):])[::-1]
    recent += [0.0] * (BAR_HALF + 1 - len(recent))
    out = []
    for d, rms in enumerate(recent):
        f, ph = _WOBBLE[d]
        wob = 0.86 + 0.14 * math.sin(2 * math.pi * f * now + ph)
        out.append(level_to_amp(rms) * _PROFILE[d] * wob)
    return out


class Wave:
    """声波高度的平滑器：起音快、释放慢，按真实时间步长计算，与帧率无关。"""

    ATTACK, RELEASE = 0.035, 0.18  # 时间常数（秒）

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.bars = [0.0] * (BAR_HALF + 1)
        self._last: float | None = None

    def update(self, levels: Sequence[float], now: float) -> tuple[float, ...]:
        dt = 0.025 if self._last is None else min(max(now - self._last, 0.0), 0.1)
        self._last = now
        for d, target in enumerate(_bar_targets(levels, now)):
            cur = self.bars[d]
            tau = self.ATTACK if target > cur else self.RELEASE
            self.bars[d] = cur + (target - cur) * (1 - math.exp(-dt / tau))
        return tuple(self.bars)


# ====================================================================== 缓存资源


@lru_cache(maxsize=16)
def _font(px: int, kind: str = "ui") -> ImageFont.FreeTypeFont:
    try:
        if kind == "num":  # 计时用 Segoe UI Semibold（等宽数字，秒数变化时不抖动）
            return ImageFont.truetype("C:/Windows/Fonts/seguisb.ttf", px)
        return ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", px, index=1)  # Microsoft YaHei UI
    except OSError:
        return ImageFont.load_default(px)


def _fonts(scale: float) -> tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
    return _font(round(14 * scale)), _font(round(12.5 * scale), "num")


@lru_cache(maxsize=32)
def _baseline(font: ImageFont.FreeTypeFont, ref: str) -> float:
    """基线相对"墨迹中心"的偏移：用参考字形的实际墨迹居中，比 anchor="lm" 视觉上更正。"""
    _, top, _, bottom = font.getbbox(ref, anchor="ls")
    return -(top + bottom) / 2


@lru_cache(maxsize=64)
def _text(text: str, font: ImageFont.FreeTypeFont, rgba: tuple, ref: str) -> tuple[Image.Image, int, int]:
    """按物理像素渲染文字（先画灰度遮罩再上色，避免半透明边缘发黑）。返回图像及相对 (x, 中线) 的偏移。"""
    base = _baseline(font, ref)
    x0, y0, x1, y1 = font.getbbox(text, anchor="ls")
    x0, y0 = x0 - 1, int(math.floor(y0 + base)) - 1
    w, h = int(x1) - x0 + 2, int(math.ceil(y1 + base)) - y0 + 2
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((-x0, -y0 + base), text, font=font, fill=255, anchor="ls")
    img = Image.new("RGBA", (w, h), rgba[:3] + (0,))
    img.putalpha(mask.point(lambda v: v * rgba[3] // 255))
    return img, x0, y0


@dataclass
class _Base:
    """某个缩放比例下、宽度为 MIN_W 的胶囊底图（含边距），用于三段拉伸。"""

    plain: Image.Image  # 投影 + 胶囊底 + 中性描边
    glow: Image.Image  # 青绿外发光 + 描边（胶囊内部透明），按强度叠加在 plain 上
    mask: Image.Image  # 胶囊形状（L，无边距），用于裁剪内容
    m: int  # 边距（物理像素）
    h: int  # 胶囊高度（物理像素）


def _pill_mask(w: int, h: int, inset: float, ss: int) -> Image.Image:
    """抗锯齿的胶囊形状遮罩（物理像素尺寸 w x h，向内缩 inset 个物理像素）。"""
    big = Image.new("L", (w * ss, h * ss), 0)
    i = inset * ss
    ImageDraw.Draw(big).rounded_rectangle((i, i, w * ss - 1 - i, h * ss - 1 - i), (h * ss - 2 * i) / 2, fill=255)
    return big.reduce(ss)


def _vgrad(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    """竖直方向线性渐变（各通道独立插值）。"""
    col = Image.new("RGBA" if len(top) == 4 else "RGB", (1, h))
    for y in range(h):
        f = y / max(h - 1, 1)
        col.putpixel((0, y), tuple(round(a + (b - a) * f) for a, b in zip(top, bottom)))
    return col.resize((w, h), Image.NEAREST)


def _with_alpha(rgb: Image.Image, alpha: Image.Image) -> Image.Image:
    img = rgb.convert("RGBA")
    img.putalpha(alpha)
    return img


def _scale_alpha(mask: Image.Image, f: float) -> Image.Image:
    return mask.point(lambda v: round(v * f))


@lru_cache(maxsize=4)
def _base(scale: float) -> _Base:
    w, h, m = round(MIN_W * scale), round(HEIGHT * scale), round(MARGIN * scale)
    W, H = w + 2 * m, h + 2 * m
    full = (W, H)

    def placed(mask: Image.Image, dx: int = 0, dy: int = 0) -> Image.Image:
        out = Image.new("L", full, 0)
        out.paste(mask, (m + dx, m + dy))
        return out

    pill = _pill_mask(w, h, 0, SS_BASE)
    inner = _pill_mask(w, h, max(1.0, scale), SS_BASE)  # 描边宽 1 逻辑像素
    ring = ImageChops.subtract(pill, inner)

    # 投影：大范围的环境阴影 + 贴近边缘的接触阴影
    amb = _scale_alpha(placed(pill, 0, round(4 * scale)).filter(ImageFilter.GaussianBlur(7 * scale)), 0.42)
    contact = _scale_alpha(placed(pill, 0, round(1 * scale)).filter(ImageFilter.GaussianBlur(1.5 * scale)), 0.30)
    plain = Image.new("RGBA", full, (0, 0, 0, 0))
    plain.putalpha(ImageChops.lighter(amb, contact))

    body = _with_alpha(_vgrad(w, h, BG_TOP, BG_BOTTOM), _scale_alpha(pill, BG_ALPHA / 255))
    plain.alpha_composite(body, (m, m))
    # 中性描边：上沿亮、下沿暗，像有一点顶光
    edge = _vgrad(w, h, (255, 255, 255, 46), (255, 255, 255, 14))
    edge.putalpha(ImageChops.multiply(edge.getchannel("A"), ring))
    plain.alpha_composite(edge, (m, m))

    # 发光：胶囊外侧一圈很淡的青绿光晕 + 青绿描边；内部保持透明，不染色胶囊底
    halo = placed(pill).filter(ImageFilter.GaussianBlur(5 * scale))
    halo = ImageChops.multiply(_scale_alpha(halo, 0.24), ImageChops.invert(placed(pill)))
    glow = Image.new("RGBA", full, TEAL + (0,))
    glow.putalpha(halo)
    tealring = _vgrad(w, h, TEAL_LIGHT + (118,), TEAL_DEEP + (62,))
    tealring.putalpha(ImageChops.multiply(tealring.getchannel("A"), ring))
    glow.alpha_composite(tealring, (m, m))
    return _Base(plain, glow, pill, m, h)


def _stretch(img: Image.Image, w: int) -> Image.Image:
    """三段拉伸：保留左右两端，复制正中那一列填满中段。"""
    W, H = img.size
    if w == W:
        return img.copy()
    out = Image.new(img.mode, (w, H))
    if w < W:  # 比底图还窄（理论上不会出现）：直接拼接两端
        out.paste(img.crop((0, 0, w // 2, H)), (0, 0))
        out.paste(img.crop((W - (w - w // 2), 0, W, H)), (w // 2, 0))
        return out
    cx = W // 2
    out.paste(img.crop((0, 0, cx, H)), (0, 0))
    out.paste(img.crop((cx, 0, cx + 1, H)).resize((w - W + 1, H), Image.NEAREST), (cx, 0))
    out.paste(img.crop((cx + 1, 0, W, H)), (w - (W - cx - 1), 0))
    return out


@lru_cache(maxsize=4)
def _wave_paint(scale: float) -> Image.Image:
    """声波的颜色层：中线处亮青绿（#5eead4）向上下两端过渡到 #14b8a6。"""
    w, h = round(WAVE_W * scale), round(WAVE_H * scale)
    half = _vgrad(1, (h + 1) // 2, TEAL_DEEP, TEAL_LIGHT)
    col = Image.new("RGB", (1, h))
    col.paste(half, (0, 0))
    col.paste(half.transpose(Image.FLIP_TOP_BOTTOM), (0, h - half.height))
    return col.resize((w, h), Image.NEAREST).convert("RGBA")


# ====================================================================== 内容（图标 + 文字 + 声波）


def _ease_out(x: float) -> float:
    return 1 - (1 - min(max(x, 0.0), 1.0)) ** 3


def _ease_in_out(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 3 * x * x - 2 * x * x * x


def _back_out(x: float) -> float:
    """带一点回弹的缓出（完成勾的圆点"弹"出来）。"""
    x = min(max(x, 0.0), 1.0)
    c = 1.4
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def _label(frame: Frame) -> str:
    """状态标签跟随界面语言；info / error 显示调用方传入的文字（已按当前语言取好）。"""
    if frame.state in ("recording", "transcribing", "polishing"):
        return t(f"overlay.{frame.state}")
    if frame.state == "done":
        return frame.text or t("overlay.pasted")
    return frame.text


def _timer(frame: Frame) -> str:
    secs = int(frame.elapsed)
    return f"{secs // 60}:{secs % 60:02d}"


def content_width(frame: Frame, scale: float = 1.0) -> float:
    """内容（不含左右内边距）的逻辑宽度，只取决于状态和文字。"""
    font, num = _fonts(scale)
    w = ICON + GAP + font.getlength(_label(frame)) / scale
    if frame.state == "recording":
        w += WAVE_GAP + WAVE_W + WAVE_GAP + num.getlength("0:00") / scale
    return w


def pill_width(frame: Frame, scale: float = 1.0) -> float:
    """该帧内容自然需要的胶囊逻辑宽度。"""
    return max(MIN_W, content_width(frame, scale) + 2 * PAD_X)


def _icon(frame: Frame, scale: float) -> Image.Image:
    """图标区：ICON+8 见方（留出呼吸光晕的空间），超采样绘制后缩小。"""
    box = ICON + 8
    side = round(box * scale)
    k = side * SS_ICON / box  # 逻辑像素 -> 超采样像素
    img = Image.new("RGBA", (side * SS_ICON, side * SS_ICON), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = side * SS_ICON / 2

    def circle(r: float, fill) -> None:
        d.ellipse((c - r * k, c - r * k, c + r * k, c + r * k), fill=fill)

    def dot(x: float, y: float, r: float, fill) -> None:  # 线段端点的圆头
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill)

    if frame.state == "recording":
        pulse = (math.sin(frame.t * 2 * math.pi / 1.4 - math.pi / 2) + 1) / 2  # 1.4 秒一次呼吸
        circle(5 + 3.5 * pulse, RED[:3] + (int(64 * (1 - pulse)),))
        circle(4.6 + 0.4 * pulse, RED)
    elif frame.state in ("transcribing", "polishing"):
        r, sw = 6.5, 2.0
        bb = (c - r * k, c - r * k, c + r * k, c + r * k)
        d.ellipse(bb, outline=(255, 255, 255, 34), width=round(sw * k))
        # 弧长在 18°~270° 间伸缩，同时整体匀速旋转（类似 Material 的不定进度圈）
        t = frame.clock if frame.clock is not None else frame.t
        period, grow = 1.5, 250
        cycle, p = divmod(t / period, 1.0)
        head = _ease_in_out(p * 2) * grow
        tail = _ease_in_out(p * 2 - 1) * grow
        rot = t * 300 + cycle * grow - 90
        a0, a1 = rot + tail, rot + head + 18
        rr = (r - sw / 2) * k  # 圆弧中线半径
        d.arc(bb, a0, a1, fill=TEAL + (255,), width=round(sw * k))
        for a in (a0, a1):
            dot(c + rr * math.cos(math.radians(a)), c + rr * math.sin(math.radians(a)), sw * k / 2, TEAL + (255,))
    elif frame.state == "done":
        s = _back_out(frame.t / 0.32)
        circle(7 * s, GREEN)
        # 对勾在圆弹出后被"画"出来
        prog = _ease_out((frame.t - 0.1) / 0.28)
        if prog > 0:
            pts = [(-3.1, 0.2), (-0.9, 2.5), (3.3, -2.3)]
            pts = [(c + x * k, c + y * k) for x, y in pts]
            segs = [math.dist(pts[0], pts[1]), math.dist(pts[1], pts[2])]
            left = prog * sum(segs)
            path = [pts[0]]
            for (p0, p1), seg in zip(zip(pts, pts[1:]), segs):
                if left <= 0:
                    break
                f = min(1.0, left / seg)
                path.append((p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f))
                left -= seg
            lw = 1.8 * k
            white = (255, 255, 255, 255)
            d.line(path, fill=white, width=round(lw), joint="curve")
            for x, y in (path[0], path[-1]):
                dot(x, y, lw / 2, white)
    else:  # info / error
        circle(7, AMBER if frame.state == "info" else RED)
        lw = 2.0 * k
        d.line([(c, c - 3.6 * k), (c, c + 0.6 * k)], fill=ICON_INK, width=round(lw))
        dot(c, c - 3.6 * k, lw / 2, ICON_INK)
        dot(c, c + 0.6 * k, lw / 2, ICON_INK)
        dot(c, c + 3.3 * k, 1.15 * k, ICON_INK)
    return img.reduce(SS_ICON)


def _wave(frame: Frame, scale: float) -> Image.Image:
    bars = frame.bars if frame.bars is not None else _bar_targets(frame.levels, frame.t)
    w, h = round(WAVE_W * scale), round(WAVE_H * scale)
    k = scale * SS_ICON
    mask = Image.new("L", (w * SS_ICON, h * SS_ICON), 0)
    d = ImageDraw.Draw(mask)
    cy = h * SS_ICON / 2
    bw = BAR_W * k
    for i in range(BAR_COUNT):
        dist = abs(i - BAR_HALF)
        a = min(max(bars[dist], 0.0), 1.0)
        bh = bw + (WAVE_H * k - bw) * a
        x = i * (BAR_W + BAR_GAP) * k
        # 静音时是一排暗淡的小圆点；越响越亮；越靠外越淡
        alpha = (0.42 + 0.58 * min(1.0, a * 2.5)) * (1 - 0.45 * (dist / BAR_HALF) ** 1.3)
        d.rounded_rectangle((x, cy - bh / 2, x + bw - 1, cy + bh / 2 - 1), bw / 2, fill=round(255 * alpha))
    img = _wave_paint(scale).copy()
    img.putalpha(mask.reduce(SS_ICON))
    return img


def _content(frame: Frame, scale: float) -> Image.Image:
    """按物理像素绘制一帧内容，宽度为 content_width，高度为胶囊高度。"""
    font, num = _fonts(scale)
    pad = CONTENT_PAD * scale  # 两侧留白，容纳呼吸光晕
    w, h = math.ceil((content_width(frame, scale) + 2 * CONTENT_PAD) * scale), round(HEIGHT * scale)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    cy = h / 2

    icon = _icon(frame, scale)
    out.alpha_composite(icon, (round(pad + ICON / 2 * scale - icon.width / 2), round(cy - icon.height / 2)))
    x = pad + (ICON + GAP) * scale

    def text(s: str, f, rgba, ref: str) -> None:
        img, dx, dy = _text(s, f, rgba, ref)
        out.alpha_composite(img, (round(x) + dx, round(cy) + dy))

    label = _label(frame)
    text(label, font, TEXT, "中")
    x += font.getlength(label)

    if frame.state == "recording":
        x += WAVE_GAP * scale
        wave = _wave(frame, scale)
        out.alpha_composite(wave, (round(x), round(cy - wave.height / 2)))
        x += (WAVE_W + WAVE_GAP) * scale
        text(_timer(frame), num, SUBTEXT, "0")
    return out


def _glow_of(frame: Frame | None) -> float:
    """录音时发光强度随音量轻微起伏（0.65~1），其余状态不发光。"""
    if frame is None or frame.state != "recording":
        return 0.0
    bars = frame.bars if frame.bars is not None else _bar_targets(frame.levels, frame.t)
    return 0.65 + 0.35 * min(1.0, max(bars[:3]) * 1.4)


# ====================================================================== 合成


def _edge_fade(w: int, h: int, scale: float) -> Image.Image:
    """左右两端各约 14 逻辑像素的水平渐隐：过渡中被裁掉的内容柔和地"没入"边缘，而不是被硬切。"""
    f = max(1, round(14 * scale))
    row = Image.new("L", (w, 1), 255)
    for i in range(min(f, w // 2)):
        v = round(255 * _ease_in_out((i + 0.5) / f))
        row.putpixel((i, 0), v)
        row.putpixel((w - 1 - i, 0), v)
    return row.resize((w, h), Image.NEAREST)


def render(
    frame: Frame,
    scale: float = 1.0,
    width: float | None = None,
    prev: Frame | None = None,
    mix: float = 1.0,
) -> Image.Image:
    """返回含阴影边距的 RGBA 图像（未预乘 alpha），尺寸为物理像素。

    width: 胶囊逻辑宽度（宽度过渡动画用），默认取 pill_width(frame)。
    prev / mix: 状态切换时的上一帧内容与过渡进度（0 -> 1），用于内容交叉淡入淡出。
    """
    base = _base(scale)
    if width is None:
        width = pill_width(frame, scale)
    pw = max(round(width * scale), 1)
    m = base.m
    if prev is None:
        mix = 1.0

    canvas = _stretch(base.plain, pw + 2 * m)
    glow = _glow_of(prev) + (_glow_of(frame) - _glow_of(prev)) * _ease_in_out(mix)
    if glow > 0.01:
        g = _stretch(base.glow, pw + 2 * m)
        g.putalpha(_scale_alpha(g.getchannel("A"), glow))
        canvas.alpha_composite(g)

    # 内容各自居中于胶囊；旧内容先淡出，新内容稍后淡入，二者只短暂重叠
    layers = []
    if prev is not None and mix < 1:
        layers.append((prev, 1 - _ease_out(mix / 0.4)))
    layers.append((frame, _ease_out((mix - 0.22) / 0.78) if prev is not None else 1.0))
    imgs = []
    for f, a in layers:
        if a > 0.01:
            img = _content(f, scale)
            if a < 0.99:
                img.putalpha(_scale_alpha(img.getchannel("A"), a))
            imgs.append((img, (pw - img.width) // 2))
    # 内容离胶囊边缘不少于正常内边距时不可能溢出，直接合成；
    # 过渡中内容比胶囊宽时，先合成到胶囊大小的图层，再按胶囊形状裁剪
    min_x = round((PAD_X - CONTENT_PAD) * scale) - 1
    if all(x >= min_x for _, x in imgs):
        for img, x in imgs:
            canvas.alpha_composite(img, (m + x, m))
    else:
        fg = Image.new("RGBA", (pw, base.h), (0, 0, 0, 0))
        for img, x in imgs:
            fg.alpha_composite(img, (x, 0))
        clip = ImageChops.multiply(_stretch(base.mask, pw), _edge_fade(pw, base.h, scale))
        fg.putalpha(ImageChops.multiply(fg.getchannel("A"), clip))
        canvas.alpha_composite(fg, (m, m))
    return canvas
