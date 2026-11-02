"""Locate ffmpeg (system binary first, then the one bundled with imageio-ffmpeg)."""
from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from typing import List

from .errors import RenderError


@lru_cache(maxsize=1)
def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        raise RenderError(
            "ffmpeg was not found. Install it (e.g. `sudo apt install ffmpeg`, `brew install ffmpeg`) "
            "or run `pip install imageio-ffmpeg`."
        ) from exc


def run_ffmpeg(args: List[str], timeout: int = 600) -> None:
    cmd = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RenderError("ffmpeg timed out.") from exc
    if proc.returncode != 0:
        raise RenderError(f"ffmpeg failed: {proc.stderr.strip()[-400:]}")
