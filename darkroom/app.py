"""Darkroom: a shared wall for photographs and what people say about them.

Routes only. Storage lives in storage.py, queries in db.py, image handling in
images.py, and everything tunable in config.py.
"""

import hashlib
import logging
import secrets
import sys
import uuid

from botocore.exceptions import ClientError
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from . import config, db, images, storage

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("darkroom")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES
app.secret_key = config.FLASK_SECRET


def as_json(row: dict) -> dict:
    return {
        "id": row["public_id"],
        "description": row["description"],
        "width": row["width"],
        "height": row["height"],
        "createdAt": row["created_at"].isoformat(),
        "thumbUrl": storage.public_url(row["public_id"], "thumb"),
        "fullUrl": storage.public_url(row["public_id"], "full"),
    }


def wants_json() -> bool:
    return (
        request.accept_mimetypes.best == "application/json"
        or request.headers.get("X-Requested-With") == "fetch"
    )


def _error(message: str, status: int):
    if wants_json():
        return jsonify(error=message), status
    flash(message)
    return redirect(url_for("index"))


# --- Health -------------------------------------------------------------


@app.get("/health")
def health():
    """Target group health check.

    Deliberately does not touch PostgreSQL. A deep check here would fail every
    target at once during an RDS failover and turn a short blip into an outage.
    """
    return {"status": "ok"}, 200


@app.get("/ready")
def ready():
    try:
        with db.get_pool().connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "database": "reachable"}, 200
    except Exception as exc:
        log.warning("readiness probe failed: %s", exc)
        return {"status": "degraded", "database": str(exc)}, 503


# --- Pages --------------------------------------------------------------


@app.get("/")
def index():
    query = (request.args.get("q") or "").strip() or None
    try:
        rows, next_cursor = db.list_photos(query=query)
        total = db.count_photos()
    except Exception:
        log.exception("could not load the gallery")
        flash("The gallery is temporarily unavailable. Try again in a moment.")
        rows, next_cursor, total = [], None, 0

    return render_template(
        "index.html",
        photos=[as_json(row) for row in rows],
        next_cursor=db.encode_cursor(next_cursor),
        query=query or "",
        total=total,
        max_bytes=config.MAX_UPLOAD_BYTES,
        max_description=config.MAX_DESCRIPTION,
    )


# --- API ----------------------------------------------------------------


@app.get("/api/photos")
def api_list():
    query = (request.args.get("q") or "").strip() or None
    rows, next_cursor = db.list_photos(
        cursor=db.parse_cursor(request.args.get("cursor")), query=query
    )
    return jsonify(
        photos=[as_json(row) for row in rows],
        nextCursor=db.encode_cursor(next_cursor),
    )


@app.post("/api/photos")
def api_upload():
    upload = request.files.get("photo")
    description = (request.form.get("description") or "").strip()
    description = description[: config.MAX_DESCRIPTION]

    if not upload or not upload.filename:
        return _error("Choose an image before uploading.", 400)

    try:
        rendition = images.process(upload.stream)
    except images.InvalidImage as exc:
        return _error(str(exc), 415)

    public_id = uuid.uuid4().hex

    # Only the hash is stored, so a database dump does not let anyone delete
    # other people's photos.
    manage_token = secrets.token_urlsafe(24)
    manage_hash = hashlib.sha256(manage_token.encode()).hexdigest()

    # Objects first, row last. A failed insert leaves bytes we clean up; the
    # other order leaves rows pointing at nothing.
    written: list[str] = []
    try:
        written = storage.store(public_id, rendition)
        row = db.insert_photo(
            public_id,
            description,
            rendition.width,
            rendition.height,
            len(rendition.display),
            manage_hash,
        )
    except ClientError:
        log.exception("object write failed for %s", public_id)
        storage.discard(written)
        return _error("Could not save the image. Please try again.", 502)
    except Exception:
        log.exception("database insert failed for %s", public_id)
        storage.discard(written)
        return _error("Could not save the description. Please try again.", 502)

    log.info(
        "stored %s (%dx%d, %d bytes)",
        public_id,
        rendition.width,
        rendition.height,
        len(rendition.display),
    )

    if wants_json():
        return jsonify(photo=as_json(row), manageToken=manage_token), 201
    return redirect(url_for("index"))


@app.delete("/api/photos/<public_id>")
def api_delete(public_id: str):
    token = request.headers.get("X-Manage-Token", "")
    if not token:
        return jsonify(error="A manage token is required."), 401

    token_hash = hashlib.sha256(token.encode()).hexdigest()

    # Same response for a wrong token as for a missing photo, so this cannot
    # be used to enumerate.
    if not db.delete_photo(public_id, token_hash):
        return jsonify(error="Not found."), 404

    storage.remove(public_id)
    log.info("deleted %s", public_id)
    return "", 204


@app.errorhandler(413)
def too_large(_):
    limit = config.MAX_UPLOAD_BYTES // (1024 * 1024)
    return _error(f"That image is larger than {limit} MB.", 413)


if not config.SKIP_DB_BOOTSTRAP:
    try:
        db.init_schema()
    except Exception:
        # Staying up keeps /health passing, so this shows in CloudWatch
        # instead of as tasks cycling forever.
        log.exception("schema bootstrap failed, continuing in degraded mode")


if __name__ == "__main__":
    # Local only; gunicorn serves this in the container, where binding all
    # interfaces is what makes it reachable.
    app.run(host="0.0.0.0", port=config.PORT)  # noqa: S104
