"""The container, met the way ECS meets it: booted, not imported.

Skipped unless SMOKE_IMAGE names a built tag, so the rest of the suite still
runs on a laptop with no Docker.
"""

import os
import subprocess
import time
import urllib.request

import pytest

IMAGE = os.environ.get("SMOKE_IMAGE")

pytestmark = pytest.mark.skipif(not IMAGE, reason="set SMOKE_IMAGE to a built tag")


def docker(*args: str) -> str:
    return subprocess.run(
        ("docker", *args), capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture(scope="module")
def container():
    cid = docker(
        "run", "-d", "-p", "8080:8080",
        "-e", "S3_BUCKET=x", "-e", "DB_HOST=x", "-e", "DB_NAME=x",
        "-e", "DB_USER=x", "-e", "DB_PASSWORD=x",
        IMAGE,
    )
    try:
        yield cid
    finally:
        # Printed unconditionally: on a failure this is the only evidence, and
        # pytest keeps it out of the way when the test passed.
        subprocess.run(("docker", "logs", cid))
        subprocess.run(("docker", "rm", "-f", cid), capture_output=True)


def test_serves_health_before_any_database_exists(container):
    """The task starts against unreachable credentials, as it does on a cold
    deployment, and must still answer the target group."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://localhost:8080/health", timeout=2) as response:
                assert response.status == 200
                return
        except OSError:
            time.sleep(2)
    pytest.fail("/health never answered within 60s")


def test_runs_as_the_unprivileged_user(container):
    """USER in the Dockerfile is a declaration; this is the built proof."""
    assert docker("exec", container, "id", "-u") == "10001"
