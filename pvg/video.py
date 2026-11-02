"""Encode frames (+ optional audio) to an H.264/AAC MP4 through ffmpeg."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

from PIL import Image

from .errors import RenderError
from .ffmpeg_util import find_ffmpeg

_PRESETS = {"draft": ("ultrafast", "26"), "standard": ("veryfast", "20"), "high": ("medium", "18")}


def encode_mp4(frames: Iterable[Image.Image], n_frames: int, size: Tuple[int, int], fps: int,
               out_path: Path, audio_wav: Optional[Path] = None, quality: str = "standard",
               on_progress: Optional[Callable[[int, int], None]] = None) -> None:
    preset, crf = _PRESETS.get(quality, _PRESETS["standard"])
    W, H = size
    if W % 2 or H % 2:
        raise RenderError("Video width and height must be even numbers.")
    cmd = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "pipe:0"]
    if audio_wav:
        cmd += ["-i", str(audio_wav)]
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-movflags", "+faststart"]
    if audio_wav:
        cmd += ["-c:a", "aac", "-b:a", "160k", "-shortest"]
    cmd += [str(out_path)]

    with tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)
        except OSError as exc:
            raise RenderError(f"Could not start ffmpeg: {exc}") from exc
        try:
            for i, frame in enumerate(frames):
                if frame.size != (W, H) or frame.mode != "RGB":
                    frame = frame.convert("RGB").resize((W, H))
                proc.stdin.write(frame.tobytes())  # type: ignore[union-attr]
                if on_progress and i % 6 == 0:
                    on_progress(i + 1, n_frames)
            proc.stdin.close()  # type: ignore[union-attr]
        except BrokenPipeError:
            pass  # ffmpeg died early; the return code below tells us why
        except Exception:
            proc.kill()
            raise
        rc = proc.wait()
        if rc != 0:
            err.seek(0)
            msg = err.read().decode("utf-8", "replace").strip()[-400:]
            raise RenderError(f"Video encoding failed (ffmpeg exit {rc}): {msg}")
    if on_progress:
        on_progress(n_frames, n_frames)


def probe_duration(path: Path) -> float:
    """Duration in seconds, parsed from ffmpeg's banner (no ffprobe required)."""
    import re
    p = subprocess.run([find_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr)
    if not m:
        raise RenderError("Could not read the duration of the generated video.")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)
