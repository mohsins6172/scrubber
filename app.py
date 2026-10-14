import io
import os
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from flask import Flask, request, send_file, render_template, abort
from werkzeug.exceptions import HTTPException

from scrub import scrub_file, build_args, ScrubError
from provenance import inspect_file

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"),
            static_folder=str(BASE_DIR / "static"))

# Upload size cap. Override with the MAX_UPLOAD_MB environment variable.
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "100"))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

WORK = Path(tempfile.gettempdir()) / "scrubber"
WORK.mkdir(exist_ok=True)

JOB_MAX_AGE_SECONDS = 3600  # leftover job folders older than this are removed
MAX_FILES_PER_REQUEST = 20


def sweep_old_jobs() -> None:
    """Delete job folders left behind by earlier requests (for example, after a crash)."""
    now = time.time()
    for folder in WORK.iterdir():
        if folder.is_dir() and now - folder.stat().st_mtime > JOB_MAX_AGE_SECONDS:
            shutil.rmtree(folder, ignore_errors=True)


@app.after_request
def security_headers(response):
    # Basic hardening. No third-party scripts or styles are loaded.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; "
        "img-src 'self' data:; form-action 'self'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(HTTPException)
def handle_http_error(exc):
    # Friendly page for errors, instead of the default unstyled error text.
    return render_template("error.html", code=exc.code, message=exc.description), exc.code


@app.get("/")
def index():
    return render_template("index.html", max_mb=MAX_UPLOAD_MB)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/scrub")
def scrub():
    sweep_old_jobs()

    uploads = [f for f in request.files.getlist("files") if f.filename]
    if not uploads:
        abort(400, "Choose at least one file to clean.")
    if len(uploads) > MAX_FILES_PER_REQUEST:
        abort(413, f"You can clean up to {MAX_FILES_PER_REQUEST} files at a time.")

    job = WORK / uuid.uuid4().hex
    job.mkdir()
    try:
        cleaned = []  # (download name, cleaned bytes)
        for f in uploads:
            name = Path(f.filename).name  # strip any path parts
            ext = Path(name).suffix.lower()
            if build_args(ext) is None:
                abort(415, f"“{name}” is not a supported file type.")
            src = job / f"in_{name}"
            dst = job / f"clean_{name}"
            f.save(src)
            try:
                scrub_file(src, dst)
            except ScrubError as exc:
                abort(422, f"“{name}” could not be cleaned. {exc}")
            cleaned.append((name, dst.read_bytes()))

        if len(cleaned) == 1:
            name, data = cleaned[0]
            return send_file(io.BytesIO(data), as_attachment=True, download_name=name)

        # Multiple files: bundle into a zip in memory, numbering duplicate names.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            used = set()
            for name, data in cleaned:
                unique, n = name, 2
                stem, suffix = Path(name).stem, Path(name).suffix
                while unique in used:
                    unique = f"{stem} ({n}){suffix}"
                    n += 1
                used.add(unique)
                z.writestr(unique, data)
        buf.seek(0)
        return send_file(buf, as_attachment=True, download_name="cleaned_files.zip")
    finally:
        # Delete the job folder whether or not the request succeeded.
        shutil.rmtree(job, ignore_errors=True)


@app.get("/inspect")
def inspect_page():
    return render_template("inspect.html", max_mb=MAX_UPLOAD_MB, reports=None)


@app.post("/inspect")
def inspect_upload():
    """Read-only: report AI provenance markers. Never changes or returns the file."""
    sweep_old_jobs()
    uploads = [f for f in request.files.getlist("files") if f.filename]
    if not uploads:
        abort(400, "Choose at least one file to check.")
    if len(uploads) > MAX_FILES_PER_REQUEST:
        abort(413, f"You can check up to {MAX_FILES_PER_REQUEST} files at a time.")

    job = WORK / uuid.uuid4().hex
    job.mkdir()
    try:
        reports = []
        for f in uploads:
            name = Path(f.filename).name
            src = job / f"inspect_{name}"
            f.save(src)
            try:
                reports.append(inspect_file(src))
            except RuntimeError as exc:
                reports.append({"file": name, "error": str(exc)})
        return render_template("inspect.html", max_mb=MAX_UPLOAD_MB, reports=reports)
    finally:
        shutil.rmtree(job, ignore_errors=True)


if __name__ == "__main__":
    app.run(debug=False)
