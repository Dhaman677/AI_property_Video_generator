"""Small Flask UI: fill a form, upload photos, watch progress, preview and download the MP4.

Run:  python app.py   ->  http://127.0.0.1:5000
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Optional

from flask import Flask, abort, jsonify, render_template, request, send_from_directory
from PIL import Image
from werkzeug.utils import secure_filename

from pvg.audio import narration_engine
from pvg.errors import InputError, PVGError
from pvg.models import IMAGE_EXTS, MAX_IMAGES, PropertyDetails, RenderOptions
from pvg.pipeline import generate_video

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "sample_data"
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_UPLOADS = 12
JOB_TTL_SECONDS = 3600


class Job:
    def __init__(self, job_id: str, workdir: Path):
        self.id, self.dir = job_id, workdir
        self.status, self.progress, self.message = "queued", 0.0, "Queued"
        self.result: Optional[dict] = None
        self.error: Optional[dict] = None
        self.created = time.time()

    def public(self) -> dict:
        return {"id": self.id, "status": self.status, "progress": round(self.progress, 3), "message": self.message,
                "result": self.result, "error": self.error}


def create_app(jobs_root: Optional[Path] = None) -> Flask:
    app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"),
                static_folder=str(Path(__file__).parent / "static"))
    app.config["MAX_CONTENT_LENGTH"] = 120 * 1024 * 1024
    root = Path(jobs_root) if jobs_root else Path(tempfile.gettempdir()) / "pvg_jobs"
    root.mkdir(parents=True, exist_ok=True)
    jobs: Dict[str, Job] = {}
    lock = threading.Lock()
    pool = ThreadPoolExecutor(max_workers=2)

    def cleanup() -> None:
        now = time.time()
        with lock:
            for jid in [j for j, job in jobs.items() if now - job.created > JOB_TTL_SECONDS and job.status in ("done", "error")]:
                shutil.rmtree(jobs[jid].dir, ignore_errors=True)
                del jobs[jid]

    # ------------------------------------------------------------ pages
    @app.get("/")
    def index():
        return render_template("index.html", engine=narration_engine(), max_images=MAX_IMAGES)

    @app.get("/fonts/<path:name>")
    def fonts(name: str):
        return send_from_directory(ROOT / "assets" / "fonts", name, max_age=86400)

    @app.get("/api/sample")
    def sample():
        data = json.loads((SAMPLE_DIR / "property.json").read_text(encoding="utf-8"))
        data["images"] = [f"/api/sample-image/{Path(p).name}" for p in data["images"]]
        return jsonify(data)

    @app.get("/api/sample-image/<name>")
    def sample_image(name: str):
        return send_from_directory(SAMPLE_DIR / "images", name, max_age=3600)

    # ------------------------------------------------------------ generation
    @app.post("/api/generate")
    def generate():
        cleanup()
        form = request.form
        job_id = uuid.uuid4().hex
        wd = root / job_id
        (wd / "uploads").mkdir(parents=True)
        problems = []

        paths = []
        if form.get("use_sample") == "1":
            data = json.loads((SAMPLE_DIR / "property.json").read_text(encoding="utf-8"))
            paths = [SAMPLE_DIR / p for p in data["images"]]
        else:
            files = request.files.getlist("images")[:MAX_UPLOADS]
            for i, f in enumerate(files):
                if not f or not f.filename:
                    continue
                name = secure_filename(f.filename) or f"image_{i}.jpg"
                if Path(name).suffix.lower() not in IMAGE_EXTS:
                    problems.append(f"'{f.filename}' is not a supported image (use JPG, PNG, WEBP).")
                    continue
                dest = wd / "uploads" / f"{i:02d}_{name}"
                f.save(dest)
                try:  # reject files that are not really images
                    with Image.open(dest) as im:
                        im.verify()
                except Exception:  # noqa: BLE001
                    problems.append(f"'{f.filename}' is not a valid image file.")
                    dest.unlink(missing_ok=True)
                    continue
                paths.append(dest)
        details = PropertyDetails.from_dict({
            "name": form.get("name"), "location": form.get("location"), "price": form.get("price"),
            "property_type": form.get("property_type"), "features": form.get("features", ""),
            "cta_text": form.get("cta_text"), "contact": form.get("contact"), "brand": form.get("brand"),
        })
        details.images = list(paths)

        opts = RenderOptions(
            aspect=form.get("aspect", "16:9") if form.get("aspect") in RenderOptions.SIZES else "16:9",
            quality=form.get("quality", "standard") if form.get("quality") in ("draft", "standard", "high") else "standard",
            narration=form.get("narration") == "1", music=form.get("music") == "1",
        )
        if not details.name:
            problems.append("Property name is required.")
        if not paths:
            problems.append("Add at least one photo (4-8 recommended), or tick 'use sample photos'.")
        if problems:
            shutil.rmtree(wd, ignore_errors=True)
            return jsonify({"error": "Please fix the following:", "problems": problems}), 400

        job = Job(job_id, wd)
        with lock:
            jobs[job_id] = job

        def run() -> None:
            job.status = "running"

            def prog(frac: float, msg: str) -> None:
                job.progress, job.message = frac, msg

            try:
                res = generate_video(details, wd / "property_video.mp4", opts, prog)
                job.result = {
                    "video": f"/files/{job_id}/property_video.mp4",
                    "captions": f"/files/{job_id}/property_video.srt",
                    "script": f"/files/{job_id}/property_video.script.json",
                    "duration": round(res.duration, 1), "size": list(res.size), "warnings": res.warnings,
                    "writer": res.script_writer, "narration": res.narration, "music": res.music,
                    "scenes": [{"kind": s.kind, "headline": s.headline, "caption": s.caption, "narration": s.narration}
                               for s in res.scenes],
                }
                job.status = "done"
            except InputError as exc:
                job.error, job.status = {"message": "The input could not be used.", "problems": exc.problems}, "error"
            except PVGError as exc:
                job.error, job.status = {"message": str(exc), "problems": []}, "error"
            except Exception:  # noqa: BLE001 - never leak a stack trace to the browser
                app.logger.exception("generation failed")
                job.error = {"message": "Unexpected error while creating the video. Check the server log.", "problems": []}
                job.status = "error"

        pool.submit(run)
        return jsonify({"job_id": job_id}), 202

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id: str):
        if not JOB_ID.match(job_id) or job_id not in jobs:
            abort(404)
        return jsonify(jobs[job_id].public())

    @app.get("/files/<job_id>/<name>")
    def files(job_id: str, name: str):
        if not JOB_ID.match(job_id) or job_id not in jobs or name not in (
                "property_video.mp4", "property_video.srt", "property_video.script.json"):
            abort(404)
        return send_from_directory(jobs[job_id].dir, name, as_attachment=request.args.get("download") == "1",
                                   conditional=True)

    @app.errorhandler(413)
    def too_big(_e):
        return jsonify({"error": "Upload too large (limit 120 MB in total).", "problems": []}), 413

    return app
