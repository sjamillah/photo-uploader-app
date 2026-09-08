"""Object storage: where a photo lives, and how it is reached.

object_key owns the layout. Everything else derives from it, so moving to a
different prefix is a one-line change.
"""

import logging

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from . import config

log = logging.getLogger(__name__)

VARIANTS = ("full", "thumb")

s3 = boto3.client(
    "s3",
    config=Config(
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=3,
        read_timeout=15,
    ),
)


def object_key(public_id: str, variant: str) -> str:
    return f"photos/{public_id}/{variant}.webp"


def public_url(public_id: str, variant: str) -> str:
    key = object_key(public_id, variant)
    return f"https://{config.CDN_DOMAIN}/{key}" if config.CDN_DOMAIN else f"/{key}"


def store(public_id: str, rendition) -> list[str]:
    """Write both renditions and return the keys, so a caller that fails
    afterwards can undo exactly what it created."""
    written = []
    for variant, payload in zip(VARIANTS, (rendition.display, rendition.thumb), strict=True):
        key = object_key(public_id, variant)
        s3.put_object(
            Bucket=config.BUCKET,
            Key=key,
            Body=payload,
            ContentType="image/webp",
            # Keys are immutable, so the edge can hold them forever.
            CacheControl="public, max-age=31536000, immutable",
        )
        written.append(key)
    return written


def discard(keys: list[str]) -> None:
    """Best effort. Called when a write partly succeeded, so raising here
    would only replace one problem with a worse one."""
    for key in keys:
        try:
            s3.delete_object(Bucket=config.BUCKET, Key=key)
        except ClientError:
            log.warning("could not remove orphaned object %s", key)


def remove(public_id: str) -> None:
    discard([object_key(public_id, variant) for variant in VARIANTS])
