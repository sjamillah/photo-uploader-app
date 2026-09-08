# syntax=docker/dockerfile:1

# Pinned by tag. To pin by digest instead, which makes a build reproducible:
#   docker buildx imagetools inspect python:3.12-slim
# Trivy in the build workflow is what catches a stale base image, by failing
# on any fixable CVE it carries.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

FROM base AS deps
# Only pyproject.toml lands in this layer, so editing application code does not
# re-resolve the dependencies. Reads [project.dependencies] and ignores the
# [dev] extra, which is why pytest and ruff never reach the image.
COPY pyproject.toml ./
COPY scripts/install-deps.sh ./scripts/
RUN bash scripts/install-deps.sh

FROM base AS runtime
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser

# Third-party packages only. The application is copied below rather than
# installed, so it exists in exactly one place.
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin/gunicorn /usr/local/bin/gunicorn

COPY --chown=10001:10001 darkroom/ /app/darkroom/

USER 10001

# Defaults, all overridable at run time without rebuilding the image.
ENV PORT=8080 \
    WEB_CONCURRENCY=2 \
    WEB_THREADS=4

EXPOSE 8080

# ECS sends SIGTERM, waits StopTimeout, then SIGKILL. Gunicorn reads SIGTERM
# as "finish in-flight requests, then exit", which is what makes a blue/green
# cutover lose nothing.
STOPSIGNAL SIGTERM

# sh -c so the variables above expand; exec so gunicorn replaces the shell and
# receives SIGTERM directly. Without exec the shell swallows it and the
# graceful shutdown never happens.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:$PORT --workers $WEB_CONCURRENCY --threads $WEB_THREADS --timeout 60 --graceful-timeout 30 --access-logfile - --error-logfile - darkroom.app:app"]
