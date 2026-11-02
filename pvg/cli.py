"""Command-line interface:  python -m pvg --input sample_data/property.json --output out/video.mp4"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional

from .errors import PVGError
from .models import Feature, PropertyDetails, RenderOptions, IMAGE_EXTS
from .audio import narration_engine
from .pipeline import generate_video
from .script import generate_script, scenes_to_json


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pvg",
        description="Turn property details + photos into a short promotional MP4 video.",
        epilog="Example: python -m pvg --input sample_data/property.json --output sample_output/sample_video.mp4",
    )
    src = p.add_argument_group("property details (JSON file and/or flags; flags override the file)")
    src.add_argument("-i", "--input", help="JSON file with the property details (see sample_data/property.json)")
    src.add_argument("--name"), src.add_argument("--location"), src.add_argument("--price")
    src.add_argument("--type", dest="property_type", help="property type, e.g. '2 BHK apartment'")
    src.add_argument("--feature", action="append", default=[], metavar="TEXT", help="key feature (repeat the flag, max 5 are shown)")
    src.add_argument("--cta", help="call-to-action text, e.g. 'Book a site visit today'")
    src.add_argument("--contact", help="phone / e-mail shown on the last scene")
    src.add_argument("--brand", help="small brand tag shown on screen")
    src.add_argument("--images", nargs="*", default=[], metavar="PATH", help="image files and/or folders (4-8 photos)")

    out = p.add_argument_group("output")
    out.add_argument("-o", "--output", default="output/property_video.mp4", help="MP4 path (default: %(default)s)")
    out.add_argument("--aspect", choices=list(RenderOptions.SIZES), default="16:9",
                     help="16:9 (YouTube/web), 9:16 (Reels/Shorts/WhatsApp status), 1:1 (feed)")
    out.add_argument("--quality", choices=["draft", "standard", "high"], default="standard")
    out.add_argument("--fps", type=int, default=24)
    out.add_argument("--accent", default="#E8692B", help="accent colour as #RRGGBB")

    aud = p.add_argument_group("script & audio")
    aud.add_argument("--no-narration", action="store_true", help="skip the voice-over")
    aud.add_argument("--no-music", action="store_true", help="skip the generated background music")
    aud.add_argument("--music-file", help="use your own royalty-free track instead of the generated music")
    aud.add_argument("--voice", default="en-us+f3", help="espeak-ng voice (default: %(default)s)")
    aud.add_argument("--rate", type=int, default=160, help="speech rate, words/minute")

    p.add_argument("--dry-run", action="store_true", help="validate the input and print the script, do not render")
    p.add_argument("-q", "--quiet", action="store_true")
    return p


def _collect(args: argparse.Namespace) -> PropertyDetails:
    d = PropertyDetails.from_json_file(args.input) if args.input else PropertyDetails()
    for attr, val in (("name", args.name), ("location", args.location), ("price", args.price),
                      ("property_type", args.property_type), ("cta_text", args.cta),
                      ("contact", args.contact), ("brand", args.brand)):
        if val:
            setattr(d, attr, " ".join(val.split()))
    if args.feature:
        d.features = [Feature(" ".join(f.split())) for f in args.feature if f.strip()]
    for item in args.images:
        p = Path(item).expanduser()
        if p.is_dir():
            d.images.extend(sorted(x for x in p.iterdir() if x.suffix.lower() in IMAGE_EXTS))
        else:
            d.images.append(p)
    return d


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.input and not args.name:
        print("error: provide --input property.json or at least --name and --images.\n"
              "Try:  python -m pvg --input sample_data/property.json", file=sys.stderr)
        return 2
    try:
        details = _collect(args)
        opts = RenderOptions(aspect=args.aspect, fps=args.fps, accent=args.accent, narration=not args.no_narration,
                             music=not args.no_music, music_file=args.music_file, voice=args.voice,
                             speech_rate=args.rate, quality=args.quality)
        if args.dry_run:
            from .pipeline import validate_details
            from .images import load_images
            warns = validate_details(details)
            _, iw = load_images(details.images)
            scenes, sw, writer = generate_script(details)
            print(f"Script writer: {writer}\n{scenes_to_json(scenes)}")
            for w in warns + iw + sw:
                print(f"warning: {w}", file=sys.stderr)
            return 0

        t0 = time.time()
        last = [-1]

        def progress(frac: float, msg: str) -> None:
            if args.quiet:
                return
            pct = int(frac * 100)
            if pct != last[0] and (pct % 5 == 0 or frac >= 1):
                last[0] = pct
                print(f"\r[{pct:3d}%] {msg:<48}", end="", flush=True)

        res = generate_video(details, args.output, opts, progress)
        if not args.quiet:
            print()
            for w in res.warnings:
                print(f"warning: {w}", file=sys.stderr)
            nar = res.narration or "off"
            print(f"\nVideo     : {res.video_path}  ({res.duration:.1f}s, {res.size[0]}x{res.size[1]})"
                  f"\nCaptions  : {res.srt_path}\nScript    : {res.script_path} (writer: {res.script_writer})"
                  f"\nNarration : {nar} | music: {'on' if res.music else 'off'} | took {time.time() - t0:.1f}s")
        return 0
    except PVGError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
