FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

FROM base AS deps

COPY pyproject.toml ./
# Dependencies only, never the project itself: installing it would drop a
# darkroom.egg-info into the tree and nothing reads it.
RUN python -c "import tomllib; print(chr(10).join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > deps.txt \
 && pip install --no-cache-dir -r deps.txt \
 && rm deps.txt

FROM base AS runtime
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser

COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin/gunicorn /usr/local/bin/gunicorn

COPY --chown=10001:10001 darkroom/ /app/darkroom/

USER 10001

ENV PORT=8080 \
    WEB_CONCURRENCY=2 \
    WEB_THREADS=4

EXPOSE 8080

STOPSIGNAL SIGTERM

CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:$PORT --workers $WEB_CONCURRENCY --threads $WEB_THREADS --timeout 60 --graceful-timeout 30 --access-logfile - --error-logfile - darkroom.app:app"]
