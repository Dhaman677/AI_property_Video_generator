"""Data models and input validation.

Design goal: *never crash on bad input*. Anything that can be worked around
becomes a warning; only truly unusable input (no name, no usable image)
raises ``InputError`` with a clear, actionable message.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .errors import InputError

MAX_IMAGES = 8
MIN_RECOMMENDED_IMAGES = 4
MAX_FEATURES = 5
MIN_IMAGE_SIDE = 320
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_text(value: Any, limit: int = 0) -> str:
    """Normalise user text: str, strip control chars, collapse whitespace."""
    if value is None:
        return ""
    text = _CTRL.sub("", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


@dataclass
class Feature:
    text: str
    image: Optional[str] = None  # optional image file name this feature should use


@dataclass
class PropertyDetails:
    name: str = ""
    location: str = ""
    price: str = ""
    property_type: str = ""
    features: List[Feature] = field(default_factory=list)
    cta_text: str = ""
    contact: str = ""
    brand: str = ""
    images: List[Path] = field(default_factory=list)

    # --- construction helpers -------------------------------------------------
    @classmethod
    def from_dict(cls, data: Dict[str, Any], base_dir: Union[str, Path, None] = None) -> "PropertyDetails":
        """Build from a plain dict (e.g. parsed JSON). Image paths are resolved
        relative to ``base_dir`` (the folder of the JSON file)."""
        if not isinstance(data, dict):
            raise InputError("Property data must be a JSON object with fields like 'name' and 'location'.")
        base = Path(base_dir) if base_dir else Path.cwd()

        feats: List[Feature] = []
        raw_feats = data.get("features") or []
        if isinstance(raw_feats, str):  # allow "a, b, c" or one-per-line
            raw_feats = [p for p in re.split(r"[\n;]|,(?![^()]*\))", raw_feats)]
        for item in raw_feats:
            if isinstance(item, dict):
                text = clean_text(item.get("text"))
                img = clean_text(item.get("image")) or None
            else:
                text, img = clean_text(item), None
            if text:
                feats.append(Feature(text=text, image=img))

        images: List[Path] = []
        raw_imgs = data.get("images") or []
        if isinstance(raw_imgs, (str, Path)):
            raw_imgs = [raw_imgs]
        for p in raw_imgs:
            pp = Path(str(p)).expanduser()
            images.append(pp if pp.is_absolute() else (base / pp))

        # An 'images_dir' may be given instead of / in addition to explicit paths.
        img_dir = data.get("images_dir")
        if img_dir:
            d = Path(str(img_dir)).expanduser()
            d = d if d.is_absolute() else base / d
            if d.is_dir():
                images.extend(sorted(p for p in d.iterdir() if p.suffix.lower() in IMAGE_EXTS))
            else:
                images.append(d)  # will be reported as missing by validation

        return cls(
            name=clean_text(data.get("name")),
            location=clean_text(data.get("location")),
            price=clean_text(data.get("price")),
            property_type=clean_text(data.get("property_type") or data.get("type")),
            features=feats,
            cta_text=clean_text(data.get("cta_text") or data.get("cta")),
            contact=clean_text(data.get("contact")),
            brand=clean_text(data.get("brand")),
            images=images,
        )

    @classmethod
    def from_json_file(cls, path: Union[str, Path]) -> "PropertyDetails":
        p = Path(path)
        if not p.is_file():
            raise InputError(f"Property file not found: {p}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InputError(f"{p.name} is not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}")
        return cls.from_dict(data, base_dir=p.parent)

    def facts_text(self) -> str:
        """All supplied facts concatenated - the only source the script may use."""
        parts = [self.name, self.location, self.price, self.property_type, self.cta_text, self.contact, self.brand]
        parts += [f.text for f in self.features]
        return " | ".join(x for x in parts if x)


@dataclass
class RenderOptions:
    aspect: str = "16:9"           # "16:9", "9:16" or "1:1"
    fps: int = 24
    accent: str = "#E8692B"
    narration: bool = True         # voice-over (needs espeak-ng / macOS `say`)
    music: bool = True             # generated royalty-free ambient bed
    music_file: Optional[str] = None  # optional user supplied background track
    voice: str = "en-us+f3"        # espeak-ng voice
    speech_rate: int = 160         # words per minute (espeak-ng)
    quality: str = "standard"      # "draft" (fast) | "standard" | "high"

    SIZES = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1000, 1000)}

    def size(self) -> tuple:
        if self.aspect not in self.SIZES:
            raise InputError(f"Unknown aspect ratio '{self.aspect}'. Choose one of: {', '.join(self.SIZES)}.")
        w, h = self.SIZES[self.aspect]
        if self.quality == "draft":
            w, h = int(w * 0.5) // 2 * 2, int(h * 0.5) // 2 * 2
        return w, h

    def validate(self) -> List[str]:
        warnings: List[str] = []
        self.size()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", self.accent or ""):
            warnings.append(f"Accent colour '{self.accent}' is not a #RRGGBB hex value; using the default.")
            self.accent = "#E8692B"
        if not 10 <= int(self.fps) <= 60:
            warnings.append("fps must be between 10 and 60; using 24.")
            self.fps = 24
        if self.quality not in ("draft", "standard", "high"):
            warnings.append(f"Unknown quality '{self.quality}'; using 'standard'.")
            self.quality = "standard"
        return warnings
