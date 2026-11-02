"""Audio: offline narration (espeak-ng / macOS say), generated ambient music and mixing.

The background music is *synthesised* with numpy (soft pad + sparse bell notes),
so it is original and 100 % royalty-free - no audio files are bundled or downloaded.
"""
from __future__ import annotations

import math
import platform
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .errors import RenderError
from .ffmpeg_util import run_ffmpeg
from .script import speech_normalize

SR = 44100


# ------------------------------------------------------------------ wav helpers
def read_wav_mono(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        n, ch, sw, sr = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(n)
    if sw != 2:
        raise RenderError("Unexpected WAV sample width from the speech engine.")
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != SR and len(a):
        x_old = np.linspace(0, 1, len(a), endpoint=False)
        x_new = np.linspace(0, 1, int(len(a) * SR / sr), endpoint=False)
        a = np.interp(x_new, x_old, a).astype(np.float32)
    return a


def write_wav_stereo(path: Path, stereo: np.ndarray) -> None:
    pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ------------------------------------------------------------------ narration
def narration_engine() -> Optional[str]:
    """Name of an available offline TTS engine, or None."""
    for exe in ("espeak-ng", "espeak"):
        if shutil.which(exe):
            return exe
    if platform.system() == "Darwin" and shutil.which("say"):
        return "say"
    try:
        import pyttsx3  # noqa: F401
        return "pyttsx3"
    except ImportError:
        pass
    return None


def synth_line(text: str, out_wav: Path, engine: str, voice: str = "en-us+f3", rate: int = 160) -> np.ndarray:
    """Synthesise one line to ``out_wav`` and return samples at 44.1 kHz mono."""
    spoken = speech_normalize(text)
    try:
        if engine in ("espeak-ng", "espeak"):
            subprocess.run([engine, "-v", voice, "-s", str(rate), "-p", "55", "-a", "170", "-w", str(out_wav), spoken],
                           check=True, capture_output=True, timeout=60)
        elif engine == "say":
            aiff = out_wav.with_suffix(".aiff")
            subprocess.run(["say", "-r", str(rate), "-o", str(aiff), spoken], check=True, capture_output=True, timeout=60)
            run_ffmpeg(["-i", str(aiff), "-ar", str(SR), "-ac", "1", str(out_wav)])
        elif engine == "pyttsx3":
            import pyttsx3
            try:  # Windows worker threads (the web UI) need COM initialised
                import pythoncom
                pythoncom.CoInitialize()
            except ImportError:
                pass
            raw = out_wav.with_name(out_wav.stem + "_raw.wav")
            eng = pyttsx3.init()
            eng.setProperty("rate", rate)
            for v in eng.getProperty("voices"):   # --voice "zira" picks a matching voice
                if voice and voice.lower() in v.name.lower():
                    eng.setProperty("voice", v.id)
                    break
            eng.save_to_file(spoken, str(raw))
            eng.runAndWait()
            eng.stop()
            run_ffmpeg(["-i", str(raw), "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)])
        else:
            raise RenderError(f"Unknown narration engine '{engine}'.")
    except (subprocess.SubprocessError, OSError) as exc:
        raise RenderError(f"Speech synthesis failed: {exc}") from exc
    return read_wav_mono(out_wav)


def synth_narration(lines: Sequence[str], workdir: Path, voice: str, rate: int) -> Tuple[List[np.ndarray], str]:
    engine = narration_engine()
    if engine is None:
        raise RenderError("No speech engine found (install espeak-ng, or run on macOS for `say`).")
    clips = []
    for i, line in enumerate(lines):
        clips.append(synth_line(line, workdir / f"line_{i}.wav", engine, voice, rate))
    return clips, engine


# ------------------------------------------------------------------ music
_CHORDS = [  # C major 7 -> A minor 7 -> F major 7 -> G sus/6  (warm, unobtrusive)
    [130.81, 196.00, 246.94, 329.63],
    [110.00, 164.81, 261.63, 329.63],
    [87.31, 174.61, 261.63, 329.63],
    [98.00, 146.83, 246.94, 329.63],
]


def _shift(x: np.ndarray, seconds: float) -> np.ndarray:
    n = int(seconds * SR)
    out = np.zeros_like(x)
    if n < len(x):
        out[n:] = x[: len(x) - n]
    return out


def generate_music(duration: float, seed: int = 7) -> np.ndarray:
    """Return stereo float32 array (n, 2), ~-20 dBFS, with fade in/out."""
    n = int((duration + 1.0) * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)
    left, right = np.zeros(n, np.float32), np.zeros(n, np.float32)
    chord_len = 4.0
    n_chords = int(math.ceil(duration / chord_len)) + 1
    for ci in range(n_chords):
        t0 = ci * chord_len
        notes = _CHORDS[ci % len(_CHORDS)]
        i0, i1 = int(t0 * SR), min(n, int((t0 + chord_len + 1.5) * SR))
        if i0 >= n:
            break
        tt = t[i0:i1] - t0
        env = np.minimum(1, tt / 1.4) * np.exp(-np.maximum(0, tt - chord_len) * 2.2)
        for f in notes:
            for det, buf in ((0.996, left), (1.004, right)):
                ph = rng.uniform(0, 2 * math.pi)
                tone = np.sin(2 * math.pi * f * det * tt + ph) + 0.35 * np.sin(2 * math.pi * 2 * f * det * tt + ph)
                buf[i0:i1] += (0.05 * env * tone).astype(np.float32)
    # sparse bell-like arpeggio
    step = 0.75
    for k in range(int(duration / step)):
        ts = 1.0 + k * step
        if k % 4 == 3:
            continue
        chord = _CHORDS[int(ts // chord_len) % len(_CHORDS)]
        f = chord[(k * 3) % len(chord)] * 2
        i0 = int(ts * SR)
        i1 = min(n, i0 + int(1.6 * SR))
        if i0 >= n:
            break
        tt = t[i0:i1] - ts
        bell = (np.sin(2 * math.pi * f * tt) + 0.3 * np.sin(2 * math.pi * 2.76 * f * tt)) * np.exp(-tt * 3.2)
        pan = 0.35 + 0.3 * ((k % 3) / 2)
        left[i0:i1] += (0.045 * (1 - pan) * 2 * bell).astype(np.float32)
        right[i0:i1] += (0.045 * pan * 2 * bell).astype(np.float32)
    st = np.stack([left, right], axis=1)
    # cheap echo "room"
    st = st + 0.28 * _shift(st, 0.37) + 0.16 * _shift(st, 0.61)
    st = st[: int(duration * SR)]
    peak = np.max(np.abs(st)) or 1.0
    st = st / peak * 0.5
    fade_in, fade_out = int(1.2 * SR), int(2.0 * SR)
    st[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    st[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    return st.astype(np.float32)


def load_music_file(path: str, duration: float) -> np.ndarray:
    """Decode any audio file with ffmpeg, loop/trim to ``duration``."""
    p = Path(path)
    if not p.is_file():
        raise RenderError(f"Music file not found: {p}")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "m.wav"
        run_ffmpeg(["-i", str(p), "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le", str(out)])
        with wave.open(str(out), "rb") as w:
            a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    a = a.reshape(-1, 2)
    if len(a) == 0:
        raise RenderError("Music file contains no audio.")
    need = int(duration * SR)
    reps = int(math.ceil(need / len(a)))
    a = np.tile(a, (reps, 1))[:need]
    peak = np.max(np.abs(a)) or 1.0
    a = a / peak * 0.5
    fo = min(len(a), int(2 * SR))
    a[-fo:] *= np.linspace(1, 0, fo)[:, None]
    return a


# ------------------------------------------------------------------ mixing
def mix_tracks(total: float, narration: Sequence[Tuple[float, np.ndarray]], music: Optional[np.ndarray]) -> np.ndarray:
    """Place narration clips at their start times and mix over music with ducking."""
    n = int(total * SR)
    voice = np.zeros(n, np.float32)
    for start, clip in narration:
        i0 = int(start * SR)
        if i0 >= n:
            continue
        seg = clip[: n - i0]
        voice[i0:i0 + len(seg)] += seg
    out = np.zeros((n, 2), np.float32)
    if music is not None:
        m = music[:n]
        if len(m) < n:
            m = np.pad(m, ((0, n - len(m)), (0, 0)))
        if np.any(voice):
            win = int(0.25 * SR)  # smooth voice-activity envelope for ducking
            env = np.convolve(np.abs(voice), np.ones(win, np.float32) / win, mode="same")
            env = np.convolve(env, np.ones(win, np.float32) / win, mode="same")
            act = np.clip(env * 25, 0, 1)
            gain = 1.0 - 0.6 * act          # music drops to 40 % under speech
            m = m * gain[:, None]
        out += m * (1.0 if np.any(voice) else 1.2)
    if np.any(voice):
        peak = np.max(np.abs(voice)) or 1.0
        out += (voice / peak * 0.85)[:, None]
    peak = np.max(np.abs(out))
    if peak > 0.95:
        out *= 0.95 / peak
    return out
