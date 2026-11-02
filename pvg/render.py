"""Frame rendering: Ken-Burns motion, text overlays and scene transitions (Pillow only)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .images import LoadedImage
from .script import KIND_CTA, KIND_FEATURE, KIND_GALLERY, KIND_PRICE, KIND_TITLE, Scene

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
NAVY = (20, 33, 61)
WHITE = (255, 255, 255)

TRANSITION_CYCLE = ["fade", "slide", "fade", "wipe"]
TRANSITION_LEN = {"fade": 0.8, "slide": 0.7, "wipe": 0.7}


def hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def smooth(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


# ------------------------------------------------------------------ fonts & text
_WEIGHT_FILES = {"bold": "Poppins-Bold.ttf", "medium": "Poppins-Medium.ttf", "regular": "Poppins-Regular.ttf"}
_FALLBACK = {"bold": "DejaVuSans-Bold.ttf", "medium": "DejaVuSans.ttf", "regular": "DejaVuSans.ttf"}


@lru_cache(maxsize=64)
def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for name in (_WEIGHT_FILES[weight], _FALLBACK[weight]):
        try:
            return ImageFont.truetype(str(FONT_DIR / name), size)
        except OSError:
            continue
    for name in ("DejaVuSans.ttf", "Arial.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


_GLUE = {"-", "–", "—", "|", "&", "/"}


def wrap(text: str, fnt: ImageFont.FreeTypeFont, max_w: int) -> List[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if cur and w in _GLUE:  # keep dashes / separators on the previous line
            cur = trial
        elif fnt.getlength(trial) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit(text: str, weight: str, max_w: int, max_lines: int, start: int, minimum: int):
    """Largest font size (start..minimum) so the text fits in ``max_lines``; ellipsise as last resort."""
    size = start
    while True:
        f = font(weight, size)
        lines = wrap(text, f, max_w)
        too_wide = any(f.getlength(l) > max_w for l in lines)
        if len(lines) <= max_lines and not too_wide:
            return f, lines
        if size <= minimum:
            lines = lines[:max_lines]
            while lines and f.getlength(lines[-1] + "…") > max_w and len(lines[-1]) > 1:
                lines[-1] = lines[-1][:-1]
            if lines:
                lines[-1] = lines[-1].rstrip() + "…"
            return f, lines
        size -= 2


def _shadowed_text(layer: Image.Image, xy, text, fnt, fill, anchor="la", blur=5, offset=3):
    sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).text((xy[0] + offset, xy[1] + offset), text, font=fnt, fill=(0, 0, 0, 170), anchor=anchor)
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    layer.alpha_composite(sh)
    ImageDraw.Draw(layer).text(xy, text, font=fnt, fill=fill, anchor=anchor)


def _gradient_scrim(size, top_frac: float, max_alpha: int, tint=(8, 14, 30)) -> Image.Image:
    w, h = size
    start = int(h * top_frac)
    col = Image.new("L", (1, h), 0)
    px = col.load()
    for y in range(start, h):
        px[0, y] = int(max_alpha * smooth((y - start) / max(1, h - start)) ** 0.9)
    alpha = col.resize((w, h))
    img = Image.new("RGBA", size, tint + (0,))
    img.putalpha(alpha)
    return img


# ------------------------------------------------------------------ overlays
@dataclass
class Overlay:
    scrim_rgb: Image.Image
    scrim_a: Image.Image
    text_rgb: Image.Image
    text_a: Image.Image


def _split(layer: Image.Image) -> Tuple[Image.Image, Image.Image]:
    return layer.convert("RGB"), layer.getchannel("A")


def build_overlay(scene: Scene, size: Tuple[int, int], accent: Tuple[int, int, int], brand: str, index: int, total: int) -> Overlay:
    W, H = size
    u = min(W, H) / 720.0
    m = int(60 * u)
    max_w = W - 2 * m
    acc = accent + (255,)
    scrim = Image.new("RGBA", size, (0, 0, 0, 0))
    text = Image.new("RGBA", size, (0, 0, 0, 0))
    td = ImageDraw.Draw(text)

    if scene.kind in (KIND_TITLE, KIND_FEATURE, KIND_GALLERY):
        scrim = _gradient_scrim(size, 0.38 if scene.kind != KIND_TITLE else 0.30, 215)
        is_title = scene.kind == KIND_TITLE
        f_head, head = fit(scene.headline, "bold", max_w, 4 if is_title else 3, int((70 if is_title else 56) * u), int(30 * u))
        f_cap = font("medium" if is_title else "regular", int((32 if is_title else 29) * u))
        cap_lines = wrap(scene.caption, f_cap, max_w)[:2] if scene.caption else []
        lh = int(f_head.size * 1.18)
        block_h = len(head) * lh + (int(14 * u) + len(cap_lines) * int(f_cap.size * 1.35) if cap_lines else 0)
        bottom = H - int(70 * u)
        y = bottom - block_h
        td.rounded_rectangle((m, y - int(30 * u), m + int(84 * u), y - int(22 * u)), radius=int(4 * u), fill=acc)
        for line in head:
            _shadowed_text(text, (m, y), line, f_head, WHITE + (255,))
            y += lh
        y += int(14 * u)
        for line in cap_lines:
            _shadowed_text(text, (m, y), line, f_cap, (255, 255, 255, 225), blur=4, offset=2)
            y += int(f_cap.size * 1.35)
    elif scene.kind == KIND_PRICE:
        scrim = Image.new("RGBA", size, (8, 14, 30, 120))
        f_cap = font("medium", int(38 * u))
        f_price, plines = fit(scene.headline, "bold", int(max_w * 0.9), 2, int(120 * u), int(54 * u))
        lh = int(f_price.size * 1.15)
        total_h = int(f_cap.size * 1.4) + len(plines) * lh + int(40 * u)
        y = (H - total_h) // 2
        if scene.caption:
            _shadowed_text(text, (W // 2, y), scene.caption, f_cap, (255, 255, 255, 230), anchor="ma")
        y += int(f_cap.size * 1.4)
        for line in plines:
            _shadowed_text(text, (W // 2, y), line, f_price, WHITE + (255,), anchor="ma", blur=7, offset=4)
            y += lh
        td.rounded_rectangle((W // 2 - int(60 * u), y + int(14 * u), W // 2 + int(60 * u), y + int(22 * u)), radius=int(4 * u), fill=acc)
    else:  # CTA
        scrim = Image.new("RGBA", size, (8, 14, 30, 150))
        card_w = min(max_w, int(980 * u))
        pad = int(44 * u)
        inner = card_w - 2 * pad
        f_head, hl = fit(scene.headline, "bold", inner, 3, int(54 * u), int(30 * u))
        f_con = font("medium", int(30 * u))
        con_lines: List[str] = []
        if scene.caption:
            for part in scene.caption.replace(" | ", "\n").split("\n"):
                con_lines.extend(wrap(part.strip(), f_con, inner)[:2])
            con_lines = con_lines[:4]
        f_brand = font("regular", int(22 * u))
        h_head = len(hl) * int(f_head.size * 1.2)
        h_con = len(con_lines) * int(f_con.size * 1.45)
        h_brand = int(f_brand.size * 2) if brand else 0
        card_h = pad * 2 + h_head + int(26 * u) + h_con + h_brand
        x0, y0 = (W - card_w) // 2, (H - card_h) // 2
        td.rounded_rectangle((x0, y0, x0 + card_w, y0 + card_h), radius=int(28 * u), fill=(255, 255, 255, 240))
        td.rounded_rectangle((x0 + pad, y0 + pad - int(18 * u), x0 + pad + int(70 * u), y0 + pad - int(11 * u)), radius=int(3 * u), fill=acc)
        y = y0 + pad
        for line in hl:
            td.text((x0 + pad, y), line, font=f_head, fill=NAVY + (255,))
            y += int(f_head.size * 1.2)
        y += int(26 * u)
        for line in con_lines:
            td.text((x0 + pad, y), line, font=f_con, fill=(60, 72, 100, 255))
            y += int(f_con.size * 1.45)
        if brand:
            td.text((x0 + pad, y + int(f_brand.size * 0.5)), brand, font=f_brand, fill=accent + (255,))

    if brand and scene.kind != KIND_CTA:  # small brand tag, top-left
        f_b = font("medium", int(22 * u))
        tw = int(f_b.getlength(brand))
        pad = int(14 * u)
        td.rounded_rectangle((m, int(34 * u), m + tw + 2 * pad, int(34 * u) + int(f_b.size * 1.9)), radius=int(18 * u), fill=(8, 14, 30, 150))
        td.text((m + pad, int(34 * u) + int(f_b.size * 0.45)), brand, font=f_b, fill=(255, 255, 255, 235))

    srgb, sa = _split(scrim)
    trgb, ta = _split(text)
    return Overlay(srgb, sa, trgb, ta)


# ------------------------------------------------------------------ scene renderer
class SceneRenderer:
    def __init__(self, scene: Scene, loaded: LoadedImage, size: Tuple[int, int], duration: float,
                 accent: Tuple[int, int, int], brand: str, index: int, total: int):
        self.scene, self.size, self.duration = scene, size, duration
        W, H = size
        img = loaded.image
        # cover-fit to 1.35x output so motion never reveals edges and resize stays cheap
        scale = max(W / img.width, H / img.height) * 1.35
        base = img.resize((max(W, int(img.width * scale)), max(H, int(img.height * scale))), Image.LANCZOS)
        if scene.kind == KIND_CTA:
            base = base.filter(ImageFilter.GaussianBlur(10 * min(W, H) / 720))
        self.base = base
        # largest box with the output aspect that fits in base
        r = W / H
        bw = min(base.width, base.height * r)
        self.box_w, self.box_h = bw, bw / r
        fx, fy = loaded.focus
        motions = [
            (1.00, 1.14, (0.5, 0.5), (fx, fy)),             # push in towards focal point
            (1.12, 1.00, (fx, fy), (0.5, 0.5)),             # pull out
            (1.10, 1.10, (0.38, fy), (0.62, fy)),            # pan right
            (1.10, 1.10, (0.62, 0.45), (0.38, 0.55)),        # pan left
        ]
        self.z0, self.z1, self.c0, self.c1 = motions[index % len(motions)]
        self.overlay = build_overlay(scene, size, accent, brand, index, total)
        self._lut_cache = {}

    def _mask(self, k: float) -> Image.Image:
        key = round(k * 40)
        if key not in self._lut_cache:
            kk = key / 40
            self._lut_cache[key] = [int(i * kk) for i in range(256)]
        return self.overlay.text_a.point(self._lut_cache[key])

    def frame(self, t: float) -> Image.Image:
        W, H = self.size
        p = smooth(t / self.duration)
        z = self.z0 + (self.z1 - self.z0) * p
        cx = self.c0[0] + (self.c1[0] - self.c0[0]) * p
        cy = self.c0[1] + (self.c1[1] - self.c0[1]) * p
        bw, bh = self.box_w / z, self.box_h / z
        bx = min(max(cx * self.base.width - bw / 2, 0), self.base.width - bw)
        by = min(max(cy * self.base.height - bh / 2, 0), self.base.height - bh)
        im = self.base.resize((W, H), Image.BILINEAR, box=(bx, by, bx + bw, by + bh))
        im.paste(self.overlay.scrim_rgb, (0, 0), self.overlay.scrim_a)
        # text: fade + gentle rise after the scene has started
        k = smooth((t - 0.35) / 0.55)
        if k > 0:
            dy = int((1 - k) * 22 * min(W, H) / 720)
            im.paste(self.overlay.text_rgb, (0, dy), self._mask(k))
        return im


# ------------------------------------------------------------------ timeline & transitions
def blend(a: Image.Image, b: Image.Image, p: float, kind: str) -> Image.Image:
    e = smooth(p)
    W, H = a.size
    if kind == "slide":
        out = Image.new("RGB", (W, H))
        out.paste(a, (-int(e * W * 0.3), 0))
        out.paste(b, (int((1 - e) * W), 0))
        return out
    if kind == "wipe":
        out = a.copy()
        w = int(e * W)
        if w > 0:
            out.paste(b.crop((0, 0, w, H)), (0, 0))
        return out
    return Image.blend(a, b, e)


@dataclass
class Slot:
    renderer: SceneRenderer
    start: float
    duration: float
    transition: str   # transition INTO this scene ("" for first)
    trans_len: float

    @property
    def end(self) -> float:
        return self.start + self.duration


class Timeline:
    def __init__(self, renderers: List[SceneRenderer], durations: List[float], accent: Tuple[int, int, int], fps: int):
        self.slots: List[Slot] = []
        self.accent, self.fps = accent, fps
        t = 0.0
        for i, (r, d) in enumerate(zip(renderers, durations)):
            kind = "" if i == 0 else ("fade" if i == len(renderers) - 1 else TRANSITION_CYCLE[(i - 1) % len(TRANSITION_CYCLE)])
            tl = TRANSITION_LEN.get(kind, 0.0)
            start = t if i == 0 else t - tl
            self.slots.append(Slot(r, start, d, kind, tl))
            t = start + d
        self.total = t
        self.size = renderers[0].size

    @staticmethod
    def plan(durations: List[float], n: int) -> List[float]:
        """Scene start times for ``n`` scenes with the given durations (used for audio sync)."""
        starts, t = [], 0.0
        for i, d in enumerate(durations):
            kind = "" if i == 0 else ("fade" if i == n - 1 else TRANSITION_CYCLE[(i - 1) % len(TRANSITION_CYCLE)])
            start = t if i == 0 else t - TRANSITION_LEN.get(kind, 0.0)
            starts.append(start)
            t = start + d
        return starts

    @staticmethod
    def trans_in(i: int, n: int) -> float:
        if i == 0:
            return 0.0
        kind = "fade" if i == n - 1 else TRANSITION_CYCLE[(i - 1) % len(TRANSITION_CYCLE)]
        return TRANSITION_LEN.get(kind, 0.0)

    def frame_at(self, t: float) -> Image.Image:
        active = [s for s in self.slots if s.start <= t < s.end]
        if not active:
            s = self.slots[-1]
            active = [s]
        if len(active) == 1:
            s = active[0]
            im = s.renderer.frame(min(t - s.start, s.duration - 1e-3))
        else:
            a, b = active[0], active[-1]
            p = (t - b.start) / b.trans_len if b.trans_len else 1.0
            im = blend(a.renderer.frame(t - a.start), b.renderer.frame(t - b.start), p, b.transition)
        # thin progress bar along the bottom edge
        W, H = self.size
        bar_h = max(3, int(5 * min(W, H) / 720))
        ImageDraw.Draw(im).rectangle((0, H - bar_h, int(W * min(1.0, t / self.total)), H), fill=self.accent)
        return im

    def frames(self) -> Iterator[Image.Image]:
        n = int(math.ceil(self.total * self.fps))
        for i in range(n):
            yield self.frame_at(i / self.fps)

    @property
    def n_frames(self) -> int:
        return int(math.ceil(self.total * self.fps))
