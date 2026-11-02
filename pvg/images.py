"""Image loading, quality checks, feature<->image matching and focus detection."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import IMAGE_EXTS, MAX_IMAGES, MIN_IMAGE_SIDE, MIN_RECOMMENDED_IMAGES, Feature

Image.MAX_IMAGE_PIXELS = 120_000_000  # guard against decompression bombs


@dataclass
class LoadedImage:
    path: Path
    image: Image.Image            # RGB, EXIF-rotated, downscaled
    focus: Tuple[float, float]    # (x, y) in 0..1 - most "interesting" point
    tokens: set                   # words from the file name, for feature matching

    @property
    def name(self) -> str:
        return self.path.name


_STOP = {"and", "with", "the", "a", "an", "of", "in", "for", "to", "at", "img", "image", "photo",
         "pic", "jpg", "jpeg", "png", "copy", "final", "new", "room", "private", "bright", "large",
         "attached", "spacious"}


def _tokens(text: str) -> set:
    words = re.findall(r"[a-z]+", text.lower().replace("_", " ").replace("-", " "))
    out = set()
    for w in words:
        if len(w) < 3 or w in _STOP:
            continue
        out.add(w[:-1] if w.endswith("s") and len(w) > 4 else w)
    return out


def compute_focus(img: Image.Image) -> Tuple[float, float]:
    """Cheap saliency: weighted centroid of edge energy on a small grayscale copy.

    Used to steer the Ken-Burns pan/zoom towards the busiest region of a photo
    instead of always zooming into the centre. Pure numpy - no model download.
    """
    small = ImageOps.grayscale(img.resize((96, 96)))
    a = np.asarray(small, dtype=np.float32)
    gy, gx = np.gradient(a)
    mag = np.hypot(gx, gy)
    mag[:6, :] = mag[-6:, :] = 0  # ignore border artefacts
    mag[:, :6] = mag[:, -6:] = 0
    total = mag.sum()
    if total < 1e-3:
        return 0.5, 0.5
    ys, xs = np.mgrid[0:96, 0:96]
    cx, cy = float((mag * xs).sum() / total) / 95, float((mag * ys).sum() / total) / 95
    # pull towards centre so the motion stays subtle and composition-safe
    return 0.5 + (cx - 0.5) * 0.6, 0.5 + (cy - 0.5) * 0.6


def quality_notes(img: Image.Image, name: str) -> List[str]:
    notes: List[str] = []
    g = np.asarray(ImageOps.grayscale(img.resize((min(img.width, 640), min(img.height, 640)))), dtype=np.float32)
    lap = (-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    if lap.var() < 12:
        notes.append(f"'{name}' looks blurry or very flat; it may look soft in the video.")
    mean = g.mean()
    if mean < 40:
        notes.append(f"'{name}' is very dark.")
    elif mean > 225:
        notes.append(f"'{name}' is very bright / washed out.")
    return notes


def load_images(paths: Sequence[Path], max_side: int = 2000) -> Tuple[List[LoadedImage], List[str]]:
    """Load as many usable images as possible. Bad files become warnings."""
    loaded: List[LoadedImage] = []
    warnings: List[str] = []
    seen = set()
    for p in paths:
        p = Path(p)
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            warnings.append(f"Duplicate image skipped: {p.name}")
            continue
        seen.add(key)
        if not p.exists():
            warnings.append(f"Image not found, skipped: {p}")
            continue
        if p.suffix.lower() not in IMAGE_EXTS:
            warnings.append(f"Unsupported image type, skipped: {p.name} (use JPG, PNG, WEBP or BMP)")
            continue
        try:
            with Image.open(p) as im:
                im.load()
                im = ImageOps.exif_transpose(im)
                if im.mode in ("RGBA", "LA", "P"):
                    im = im.convert("RGBA")
                    bg = Image.new("RGB", im.size, (255, 255, 255))
                    bg.paste(im, mask=im.split()[-1])
                    im = bg
                else:
                    im = im.convert("RGB")
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            warnings.append(f"Could not read image {p.name} ({exc.__class__.__name__}), skipped.")
            continue
        if min(im.size) < MIN_IMAGE_SIDE:
            warnings.append(f"Image {p.name} is very small ({im.width}x{im.height}); skipped. "
                            f"Use images of at least {MIN_IMAGE_SIDE}px on the short side.")
            continue
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.LANCZOS)
        warnings.extend(quality_notes(im, p.name))
        loaded.append(LoadedImage(path=p, image=im, focus=compute_focus(im), tokens=_tokens(p.stem)))

    if len(loaded) > MAX_IMAGES:
        warnings.append(f"{len(loaded)} images supplied; only the first {MAX_IMAGES} are used.")
        loaded = loaded[:MAX_IMAGES]
    if 0 < len(loaded) < MIN_RECOMMENDED_IMAGES:
        warnings.append(f"Only {len(loaded)} usable image(s); 4-8 give the best result. Images will be reused.")
    return loaded, warnings


def match_images_to_features(features: Sequence[Feature], images: Sequence[LoadedImage]) -> List[Optional[int]]:
    """Return for each feature the index of the best image (or None).

    1. explicit ``Feature.image`` (file name) wins;
    2. otherwise word overlap between the feature text and the image file
       name ("Swimming pool and clubhouse" <-> ``swimming_pool.jpg``).
    Each image is used at most once.
    """
    result: List[Optional[int]] = [None] * len(features)
    used = set()
    by_name = {im.name.lower(): i for i, im in enumerate(images)}
    by_stem = {im.path.stem.lower(): i for i, im in enumerate(images)}

    for fi, f in enumerate(features):
        if f.image:
            key = Path(f.image).name.lower()
            idx = by_name.get(key, by_stem.get(Path(key).stem))
            if idx is not None and idx not in used:
                result[fi] = idx
                used.add(idx)

    scored = []
    for fi, f in enumerate(features):
        if result[fi] is not None:
            continue
        ft = _tokens(f.text)
        for ii, im in enumerate(images):
            overlap = len(ft & im.tokens)
            if overlap and ii not in used:
                scored.append((overlap, -fi, fi, ii))
    for overlap, _, fi, ii in sorted(scored, reverse=True):
        if result[fi] is None and ii not in used:
            result[fi] = ii
            used.add(ii)
    return result
