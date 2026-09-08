"""Settings, read from the environment once at import.

Nothing else reads os.environ, so every default is visible in one place.
"""

import os
import secrets


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


# --- Required -----------------------------------------------------------

BUCKET = os.environ["S3_BUCKET"]

# --- Delivery -----------------------------------------------------------

# Leave unset only when CloudFront fronts the app and routes /photos/* to S3.
# Without that, the relative URLs storage builds have no route and images 404.
CDN_DOMAIN = os.environ.get("CLOUDFRONT_DOMAIN", "").strip()

# --- Uploads ------------------------------------------------------------

MAX_UPLOAD_BYTES = _int("MAX_UPLOAD_BYTES", 12 * 1024 * 1024)
MAX_DESCRIPTION = _int("MAX_DESCRIPTION", 280)

# Long edge of each rendition, and their WebP quality.
DISPLAY_MAX = _int("DISPLAY_MAX", 2048)
THUMB_MAX = _int("THUMB_MAX", 480)
DISPLAY_QUALITY = _int("DISPLAY_QUALITY", 82)
THUMB_QUALITY = _int("THUMB_QUALITY", 72)

# Memory guard, not a quality rule. 30 MP is ~90 MB of RGB and the pipeline
# holds about three copies, so this and the task memory move together.
MAX_IMAGE_PIXELS = _int("MAX_IMAGE_PIXELS", 30_000_000)

# --- Database -----------------------------------------------------------

PAGE_SIZE = _int("PAGE_SIZE", 24)

# 2 gunicorn workers x 4 = 8 connections per task; db.t3.micro allows ~85.
POOL_MIN = _int("POOL_MIN", 1)
POOL_MAX = _int("POOL_MAX", 4)
CONNECT_TIMEOUT = _int("DB_CONNECT_TIMEOUT", 5)

# --- Runtime ------------------------------------------------------------

PORT = _int("PORT", 8080)

# The test suite sets this so importing the app does not wait on a connection
# timeout. It defaults to running, so production is unchanged.
SKIP_DB_BOOTSTRAP = os.environ.get("SKIP_DB_BOOTSTRAP") == "1"

FLASK_SECRET = os.environ.get("FLASK_SECRET") or secrets.token_hex(32)


def database_url() -> str:
    """libpq connection string. ECS injects every value from Secrets Manager."""
    return (
        f"host={os.environ['DB_HOST']} "
        f"port={os.environ.get('DB_PORT', '5432')} "
        f"dbname={os.environ['DB_NAME']} "
        f"user={os.environ['DB_USER']} "
        f"password={os.environ['DB_PASSWORD']} "
        f"sslmode=require "
        f"connect_timeout={CONNECT_TIMEOUT}"
    )
