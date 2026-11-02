# AI Property Video Generator

Turns a property listing (name, location, price, type, key features, contact / CTA text and 4-8 photos) into a
short, ready-to-share promo video **without opening a video editor**.

* **Input:** a web form, a JSON file, or CLI flags.
* **Output:** `video.mp4` (H.264 + AAC) + `video.srt` captions + `video.script.json` (the exact script used).
* **Formats:** 16:9 (web/YouTube), 9:16 (Reels/Shorts/WhatsApp status), 1:1 (feed).
* **Runs offline.** Script writing, narration and music need no API key.

A finished example is in [`sample_output/`](sample_output) (`sample_video.mp4`, 33 s, voice-over + music).

---

## 1. Quick start

Requirements: **Python 3.9+**. `ffmpeg` is bundled through `imageio-ffmpeg`; a system ffmpeg is used if present.

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Voice-over engine (optional but recommended; offline, free):
#   Ubuntu/Debian: sudo apt install espeak-ng        macOS: built-in `say` is used automatically
#   Windows: install espeak-ng from https://github.com/espeak-ng/espeak-ng/releases (or use Docker / WSL)
```

### a) Command line

```bash
python -m pvg --input sample_data/property.json --output sample_output/demo.mp4
```

### b) Web UI (form + upload + live progress + preview + download)

```bash
python app.py            # open http://127.0.0.1:5000  ->  click "Load sample listing" -> "Create video"
```

### c) Docker (includes the speech engine)

```bash
docker build -t pvg . && docker run -p 5000:5000 pvg
```

Without a speech engine the app still works: it shows a warning and produces the video without voice-over.

---

## 2. Input

JSON (`sample_data/property.json`); image paths are relative to the JSON file:

```json
{
  "name": "Sunrise Heights - 2 BHK Apartment",
  "location": "Baner, Pune",
  "price": "₹85 Lakhs",
  "property_type": "2 BHK apartment",
  "features": ["Modular kitchen with chimney", "Swimming pool and clubhouse"],
  "cta_text": "Book a site visit today",
  "contact": "+91 90000 00000 | sales@example.com",
  "brand": "Your agency (optional small tag)",
  "images": ["images/exterior.jpg", "images/kitchen.jpg", "images/swimming_pool.jpg"]
}
```

* Only **`name`** and **at least one usable image** are mandatory. Everything else is optional (see section 5).
* `features` items may also be objects `{"text": "...", "image": "kitchen.jpg"}` to pin a photo to a feature.
* `images_dir` can be used instead of listing files.
* CLI flags (`--name`, `--location`, `--price`, `--type`, `--feature` (repeat), `--cta`, `--contact`, `--images ...`)
  can be used instead of, or to override, the JSON.

### CLI options

| Option | Meaning |
|---|---|
| `--aspect 16:9 \| 9:16 \| 1:1` | output format (default 16:9, 1280x720) |
| `--quality draft \| standard \| high` | `draft` = half size, ~4x faster, handy for previews |
| `--no-narration`, `--no-music` | switch audio layers off |
| `--music-file track.mp3` | use your own royalty-free track instead of the generated music |
| `--voice`, `--rate` | espeak-ng voice / words per minute |
| `--accent "#E8692B"` | brand colour used for bars, price underline, progress bar |
| `--dry-run` | validate input and print the script without rendering |

Exit codes: `0` ok, `1` input/render error (clear message), `2` bad usage.

---

## 3. How it works

```
 form / JSON / CLI
        |
  1. validate  -----> InputError (clear message)  or  warnings (missing optional data)
        |
  2. load images: EXIF-rotate, skip corrupt/tiny/duplicate, blur/dark checks, focus point (edge-energy saliency)
        |
  3. script: template writer  ->  fact-check  ->  scenes
        |            title | 1 scene per feature (max 5) | price | call-to-action
  4. image <-> feature matching (file name words, e.g. swimming_pool.jpg <-> "Swimming pool and clubhouse");
        extra photos become "A closer look" gallery scenes; too few photos are re-used
        |
  5. voice-over per scene (espeak-ng / macOS say)  ->  scene durations follow the narration length
        |
  6. render frames with Pillow: Ken-Burns pan/zoom towards the photo's focal point, gradient scrim,
        animated headline + caption, fade / slide / wipe transitions, progress bar, CTA card
        |
  7. audio: generated ambient music (numpy synth, royalty-free) ducked under the voice-over
        |
  8. ffmpeg: frames piped to libx264, mixed with audio -> MP4 (+faststart), SRT captions, script JSON
```

Typical result: 8 scenes, 30-35 s. Rendering the 33 s HD sample takes about a minute on a small 2-core machine
(`--quality draft`: ~15 s).

### Key decisions

* **Template logic.** A real-estate promo is a *claims* document: a wrong number or an
  invented amenity is a liability. Deterministic templates are cheap, reproducible and cannot hallucinate.
* **Pillow + ffmpeg pipe instead of a video-editing library.** Few dependencies, full control over timing, easy to
  install, and frames stream to ffmpeg so memory stays flat.
* **Synthesised music.** Nothing is downloaded or bundled, so there is no licensing question ("royalty-free" by construction).
* **Narration drives timing.** Each scene lasts at least as long as its voice-over, so audio and captions stay in sync.
* **Never crash on imperfect data.** Bad files, missing fields and absent TTS are warnings; only "no name" or "no
  usable image" stop the run, with a message saying what to fix.

---

## 4. Error handling

| Situation | Behaviour |
|---|---|
| No name / no image | Stops with a list of what to supply (`sample_output/error_handling_examples.txt`) |
| Image missing, corrupt, wrong type, < 320 px, duplicate | Skipped, warning names the file; run continues |
| All images unusable | Clear error listing why each one failed |
| < 4 images | Warning, photos are re-used; > 8 images: first 8 used |
| > 5 features | First 5 used (keeps video ~20-40 s), warning |
| Missing price / location / contact / CTA / features | That scene or line is left out (default CTA is used), warning |
| Very long text | Auto-shrinks, wraps, then ellipsises; never overflows the frame |
| No speech engine / bad music file | Warning; video is created without that audio layer |
| ffmpeg problem | `RenderError` with the tail of ffmpeg's message |
| Web: bad upload, huge upload, unknown job | HTTP 400/413/404 JSON messages; no stack traces shown |

---

## 5. Tests

```bash
python -m unittest discover -s tests -v          # ~25 tests, ~25 s
```

Covers validation, JSON errors, bad/corrupt images, image-feature matching, the template script, the fact-checker
(hallucinated numbers, claims and names), speech normalisation (`₹85 Lakhs`, `2 BHK`, `24x7`),
audio mixing/clipping, SRT format, real end-to-end renders (landscape, vertical with narration, many/few images,
partial failures) and the web API (validation, path traversal).

---

## 6. Project layout

```
pvg/                 core package
  models.py          input dataclasses + validation, RenderOptions
  images.py          loading, quality checks, focus detection, image<->feature matching
  script.py          template writer, fact-checker, speech normalisation
  render.py          Ken-Burns, overlays, transitions, timeline
  audio.py           TTS wrapper, synthesised music, ducking mixer
  video.py           ffmpeg encode (frames piped), duration probe
  pipeline.py        orchestration (generate_video)
  cli.py             command-line interface
webapp/              Flask UI (templates, static css/js)
app.py               starts the UI
sample_data/         property.json, property_minimal.json, 6 synthetic photos
sample_output/       sample_video.mp4, vertical version, captions, script, contact sheet, error examples
scripts/make_sample_images.py   regenerates the synthetic sample photos
tests/               unit + end-to-end tests
assets/fonts/        Poppins (OFL) + DejaVu fallback, see FONT_LICENSES.md
```

## 7. Limitations / next steps

* The offline voice (espeak-ng) is intelligible but robotic. The narration layer is isolated in `pvg/audio.py`; a cloud
  or neural TTS can be plugged into `synth_line`.
* Narration and templates are English only; fonts support Latin and the rupee sign (Devanagari text would need a font swap).
* Sample photos are synthetic illustrations (no real or personal data). Real photos work the same way.
* Rendering is single-process Python; for bulk jobs run several CLI processes in parallel.
* The web UI keeps jobs in memory and in a temp folder for one hour; it is a demo server, not a multi-user service
  (no authentication). Run it on localhost or behind your own auth.

See also [`THIRD_PARTY_AND_AI_NOTES.md`](THIRD_PARTY_AND_AI_NOTES.md) and [`docs/DEMO.md`](docs/DEMO.md).
