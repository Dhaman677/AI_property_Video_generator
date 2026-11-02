"""End-to-end orchestration: validate -> script -> scenes -> audio -> render -> MP4."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple, Union

from .audio import SR, generate_music, load_music_file, mix_tracks, narration_engine, synth_narration, write_wav_stereo
from .errors import InputError, PVGError, RenderError
from .images import LoadedImage, load_images, match_images_to_features
from .models import PropertyDetails, RenderOptions
from .render import SceneRenderer, Timeline, hex_to_rgb
from .script import (KIND_CTA, KIND_FEATURE, KIND_GALLERY, KIND_PRICE, KIND_TITLE, Scene,
                     generate_script, scenes_to_json)
from .video import encode_mp4, probe_duration

Progress = Callable[[float, str], None]

BASE_DURATION = {KIND_TITLE: 4.4, KIND_FEATURE: 3.8, KIND_GALLERY: 3.0, KIND_PRICE: 4.0, KIND_CTA: 5.2}
HERO_WORDS = {"exterior", "facade", "building", "front", "outside", "hero", "elevation", "tower", "villa", "house"}


@dataclass
class GenerationResult:
    video_path: Path
    srt_path: Path
    script_path: Path
    duration: float
    warnings: List[str] = field(default_factory=list)
    scenes: List[Scene] = field(default_factory=list)
    script_writer: str = "template"
    narration: Optional[str] = None   # engine name or None
    music: bool = False
    size: Tuple[int, int] = (0, 0)


# ------------------------------------------------------------------ helpers
def validate_details(d: PropertyDetails) -> List[str]:
    """Raise InputError for unusable input, return warnings for anything merely incomplete."""
    problems, warnings = [], []
    if not d.name:
        problems.append("Property name is required (e.g. \"Sunrise Heights - 2 BHK Apartment\").")
    if not d.images:
        problems.append("No images supplied. Provide 4-8 property photos (JPG/PNG/WEBP).")
    if problems:
        raise InputError("Cannot create the video.", problems)
    if len(d.name) > 90:
        warnings.append("Property name is very long; it was shortened on screen.")
        d.name = d.name[:89].rstrip() + "…"
    for label, val in (("location", d.location), ("price", d.price), ("property type", d.property_type),
                       ("contact details", d.contact), ("call-to-action text", d.cta_text)):
        if not val:
            note = {"call-to-action text": "a default call to action is used.",
                    "price": "the price scene is skipped.",
                    "contact details": "the closing scene shows no contact line."}.get(label, "that line is left out.")
            warnings.append(f"Missing {label}: {note}")
    if not d.features:
        warnings.append("No key features supplied: the video shows the title, a photo gallery, the price (if given) and the call to action.")
    for f in d.features:
        if len(f.text) > 70:
            f.text = f.text[:69].rstrip() + "…"
            warnings.append("A feature was longer than 70 characters and was shortened.")
    return warnings


def _assign_images(scenes: List[Scene], images: List[LoadedImage], feature_texts: List[str], d: PropertyDetails) -> List[Scene]:
    """Pick an image for each scene; extra photos become 'gallery' scenes."""
    n = len(images)
    used: List[int] = []

    # title image: prefer a "hero" looking file name
    hero = next((i for i, im in enumerate(images) if im.tokens & HERO_WORDS), None)
    title_idx = hero if hero is not None else 0
    used.append(title_idx)

    feat_scenes = [s for s in scenes if s.kind == KIND_FEATURE]
    matches = match_images_to_features(d.features[:len(feat_scenes)], [im for im in images])
    for fi, s in enumerate(feat_scenes):
        idx = matches[fi] if fi < len(matches) else None
        if idx is not None and idx in used:
            idx = None
        if idx is None:
            idx = next((i for i in range(n) if i not in used and i not in [m for m in matches if m is not None]), None)
        if idx is None:
            idx = next((i for i in range(n) if i not in used), None)
        if idx is None:  # fewer photos than scenes: re-use, cycling
            idx = (len(used)) % n
        s.image_index = idx
        used.append(idx)
    scenes[0].image_index = title_idx

    leftover = [i for i in range(n) if i not in used]
    price = next((s for s in scenes if s.kind == KIND_PRICE), None)
    cta = scenes[-1]
    if price is not None:
        price.image_index = leftover.pop(0) if leftover else (used[-2] if len(used) > 1 else title_idx)
    cta.image_index = leftover.pop(0) if leftover else title_idx

    extra = [Scene(KIND_GALLERY, "A closer look", d.location or d.name, "", i) for i in leftover]
    if extra:
        pos = next((k for k, s in enumerate(scenes) if s.kind == KIND_PRICE), len(scenes) - 1)
        scenes[pos:pos] = extra
    return scenes


def _fmt_ts(t: float) -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(cues: List[Tuple[float, float, str]]) -> str:
    out = []
    for i, (a, b, text) in enumerate(cues, 1):
        out.append(f"{i}\n{_fmt_ts(a)} --> {_fmt_ts(b)}\n{text}\n")
    return "\n".join(out)


# ------------------------------------------------------------------ main entry
def generate_video(details: PropertyDetails, output_path: Union[str, Path], options: Optional[RenderOptions] = None,
                   progress: Optional[Progress] = None) -> GenerationResult:
    """Create the MP4 (+ .srt captions + script JSON). Raises ``PVGError`` subclasses only."""
    opts = options or RenderOptions()
    say = progress or (lambda f, m: None)
    warnings: List[str] = []
    out = Path(output_path)
    if out.suffix.lower() != ".mp4":
        out = out.with_suffix(".mp4")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InputError(f"Cannot create the output folder {out.parent}: {exc}")

    say(0.02, "Checking input")
    warnings += opts.validate()
    warnings += validate_details(details)
    size = opts.size()

    say(0.05, "Loading images")
    images, img_warn = load_images(details.images)
    warnings += img_warn
    if not images:
        raise InputError("None of the supplied images could be used.", ["None of the supplied images could be used."] + img_warn)

    say(0.10, "Writing script")
    scenes, script_warn, writer = generate_script(details)
    warnings += script_warn
    scenes = _assign_images(scenes, images, [f.text for f in details.features], details)

    with tempfile.TemporaryDirectory(prefix="pvg_") as td:
        work = Path(td)

        # ---- narration
        clips = None
        engine = None
        if opts.narration:
            say(0.15, "Recording voice-over")
            try:
                spoken = [s.narration for s in scenes]
                idx = [i for i, t in enumerate(spoken) if t]
                got, engine = synth_narration([spoken[i] for i in idx], work, opts.voice, opts.speech_rate)
                clips = {i: c for i, c in zip(idx, got)}
            except RenderError as exc:
                warnings.append(f"Voice-over skipped: {exc}")
                clips, engine = None, None

        # ---- timing
        n = len(scenes)
        durations: List[float] = []
        for i, s in enumerate(scenes):
            read = 1.4 + 0.045 * len(s.headline + s.caption)
            dur = max(BASE_DURATION[s.kind], read)
            if clips and i in clips:
                need = Timeline.trans_in(i, n) + 0.25 + len(clips[i]) / SR + 0.55
                dur = max(dur, need)
            durations.append(dur)
        starts = Timeline.plan(durations, n)
        total = starts[-1] + durations[-1]
        if total > 42:
            warnings.append(f"The video is {total:.0f}s long (target 20-40s); use fewer features/photos for a shorter cut.")
        elif total < 12:
            warnings.append(f"The video is only {total:.0f}s long; add features or photos for a fuller story.")

        # ---- captions (srt) + narration placement
        cues, placed = [], []
        for i, s in enumerate(scenes):
            t0 = starts[i] + Timeline.trans_in(i, n) + 0.25
            if clips and i in clips:
                length = len(clips[i]) / SR
                placed.append((t0, clips[i]))
                cues.append((t0, t0 + length, s.narration))
            elif s.narration:
                cues.append((t0, min(starts[i] + durations[i], t0 + 3.2), s.narration))

        # ---- audio
        audio_path = None
        if opts.music or clips:
            say(0.25, "Mixing audio")
            music = None
            if opts.music:
                try:
                    music = load_music_file(opts.music_file, total) if opts.music_file else generate_music(total)
                except RenderError as exc:
                    warnings.append(f"Background music skipped: {exc}")
            if music is not None or clips:
                mix = mix_tracks(total, placed, music)
                audio_path = work / "mix.wav"
                write_wav_stereo(audio_path, mix)

        # ---- render
        say(0.30, "Rendering scenes")
        accent = hex_to_rgb(opts.accent)
        renderers = [SceneRenderer(s, images[s.image_index], size, durations[i], accent, details.brand, i, n)
                     for i, s in enumerate(scenes)]
        timeline = Timeline(renderers, durations, accent, opts.fps)

        def on_frame(done: int, tot: int) -> None:
            say(0.30 + 0.68 * done / max(1, tot), f"Encoding video ({done}/{tot} frames)")

        tmp_out = work / "out.mp4"
        encode_mp4(timeline.frames(), timeline.n_frames, size, opts.fps, tmp_out, audio_path, opts.quality, on_frame)

        try:
            shutil.copyfile(tmp_out, out)
        except OSError as exc:
            raise RenderError(f"Could not save the video to {out}: {exc}") from exc
        duration = probe_duration(out)

    srt_path = out.with_suffix(".srt")
    script_path = out.with_suffix(".script.json")
    srt_path.write_text(build_srt(cues), encoding="utf-8")
    script_path.write_text(scenes_to_json(scenes), encoding="utf-8")
    say(1.0, "Done")
    return GenerationResult(video_path=out, srt_path=srt_path, script_path=script_path, duration=duration,
                            warnings=warnings, scenes=scenes, script_writer=writer, narration=engine if clips else None,
                            music=audio_path is not None and opts.music, size=size)
