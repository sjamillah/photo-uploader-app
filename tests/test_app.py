"""Each test pins down one decision from the build book."""

import io
from datetime import datetime, timezone

import pytest
from PIL import Image

from darkroom import app as appmod
from darkroom import config, db, images, storage


def jpeg(width=200, height=120, orientation=None) -> io.BytesIO:
    """A JPEG with real detail, optionally claiming an EXIF rotation.

    Noise, not flat colour: a flat image compresses to nothing at any size,
    so size comparisons would be meaningless.
    """
    image = Image.effect_noise((width, height), 40).convert("RGB")
    buffer = io.BytesIO()
    if orientation:
        exif = image.getexif()
        exif[274] = orientation  # 274 = Orientation
        image.save(buffer, format="JPEG", exif=exif)
    else:
        image.save(buffer, format="JPEG")
    buffer.seek(0)
    return buffer


@pytest.fixture
def client():
    appmod.app.config.update(TESTING=True)
    return appmod.app.test_client()


# --------------------------------------------------------------- health
def test_health_does_not_touch_the_database(client, monkeypatch):
    """Break the pool entirely; /health must still return 200.

    Guards against someone "improving" it later by adding a SELECT 1, which
    would fail every target at once during an RDS failover.
    """

    def explode():
        raise RuntimeError("database is unreachable")

    monkeypatch.setattr(db, "get_pool", explode)

    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_ready_reports_degraded_when_the_database_is_down(client, monkeypatch):
    def explode():
        raise RuntimeError("database is unreachable")

    monkeypatch.setattr(db, "get_pool", explode)

    response = client.get("/ready")
    assert response.status_code == 503
    assert response.get_json()["status"] == "degraded"


# ------------------------------------------------------- image handling
def test_a_renamed_text_file_is_rejected():
    """Content decides, not the filename the client picked."""
    fake = io.BytesIO(b"#!/bin/sh\necho definitely not a photograph")
    with pytest.raises(images.InvalidImage):
        images.process(fake)


def test_exif_orientation_is_applied_then_discarded():
    """Orientation 6 is "rotate 90 clockwise", so landscape in, portrait out.

    Also checks nothing survives the re-encode: EXIF is where GPS lives.
    """
    rendition = images.process(jpeg(200, 120, orientation=6))

    assert rendition.height > rendition.width, "exif_transpose did not run"

    out = Image.open(io.BytesIO(rendition.display))
    assert out.format == "WEBP"
    assert not dict(out.getexif()), "EXIF survived the re-encode"


def test_both_renditions_are_produced_and_bounded():
    rendition = images.process(jpeg(4000, 3000))
    assert max(rendition.width, rendition.height) == config.DISPLAY_MAX
    assert len(rendition.thumb) < len(rendition.display)


# --------------------------------------------------------------- upload
def test_upload_writes_two_objects_and_returns_a_one_time_token(client, monkeypatch):
    written = []
    monkeypatch.setattr(storage.s3, "put_object", lambda **kw: written.append(kw["Key"]))
    monkeypatch.setattr(
        appmod.db,
        "insert_photo",
        lambda *a, **k: {
            "public_id": "abc123",
            "description": "a test photograph",
            "width": 200,
            "height": 120,
            "created_at": datetime(2026, 9, 7, tzinfo=timezone.utc),
            "id": 1,
        },
    )

    response = client.post(
        "/api/photos",
        data={"photo": (jpeg(), "holiday.jpg"), "description": "a test photograph"},
        content_type="multipart/form-data",
        headers={"Accept": "application/json", "X-Requested-With": "fetch"},
    )

    assert response.status_code == 201
    assert response.get_json()["manageToken"], "no token means nobody can delete it"

    # The id is generated inside the handler, so assert the shape, not the
    # value: two objects under one prefix, and no trace of the filename.
    prefixes = {key.rsplit("/", 1)[0] for key in written}
    assert len(prefixes) == 1
    assert prefixes.pop().startswith("photos/")
    assert sorted(key.rsplit("/", 1)[1] for key in written) == ["full.webp", "thumb.webp"]
    assert all("holiday" not in key for key in written)


def test_delete_without_a_token_is_refused(client):
    assert client.delete("/api/photos/abc123").status_code == 401


def test_delete_with_a_wrong_token_looks_the_same_as_missing(client, monkeypatch):
    """Wrong token and missing photo must be indistinguishable.

    Otherwise the endpoint tells you which photos exist.
    """
    monkeypatch.setattr(db, "delete_photo", lambda *a: False)

    response = client.delete("/api/photos/abc123", headers={"X-Manage-Token": "not-the-real-one"})
    assert response.status_code == 404


# -------------------------------------------------------------- gallery
def test_descriptions_are_escaped_in_the_rendered_page(client, monkeypatch):
    monkeypatch.setattr(db, "count_photos", lambda: 1)
    monkeypatch.setattr(
        appmod.db,
        "list_photos",
        lambda **k: (
            [
                {
                    "public_id": "abc123",
                    "description": "<script>alert(1)</script>",
                    "width": 200,
                    "height": 120,
                    "created_at": datetime(2026, 9, 7, tzinfo=timezone.utc),
                    "id": 1,
                }
            ],
            None,
        ),
    )

    body = client.get("/").data.decode()
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_images_are_served_from_cloudfront_not_s3(client, monkeypatch):
    monkeypatch.setattr(db, "count_photos", lambda: 1)
    monkeypatch.setattr(
        appmod.db,
        "list_photos",
        lambda **k: (
            [
                {
                    "public_id": "abc123",
                    "description": "A test photo",
                    "width": 200,
                    "height": 120,
                    "created_at": datetime(2026, 9, 7, tzinfo=timezone.utc),
                    "id": 1,
                }
            ],
            None,
        ),
    )

    body = client.get("/").data.decode()
    assert "https://cdn.test.invalid/photos/abc123/thumb.webp" in body
    assert "s3.amazonaws.com" not in body
    assert "test-bucket" not in body


def test_object_keys_come_from_one_place():
    """One function owns the layout, so URL, upload and delete cannot drift."""
    assert storage.object_key("abc", "full") == "photos/abc/full.webp"
    assert storage.public_url("abc", "thumb").endswith("photos/abc/thumb.webp")
