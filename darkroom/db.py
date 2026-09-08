"""Connection pooling, schema and queries."""

import logging
from datetime import datetime

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config

log = logging.getLogger(__name__)

_pool: ConnectionPool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id            BIGSERIAL PRIMARY KEY,
    public_id     TEXT        NOT NULL UNIQUE,
    description   TEXT        NOT NULL DEFAULT '',
    width         INTEGER     NOT NULL,
    height        INTEGER     NOT NULL,
    size_bytes    BIGINT      NOT NULL,
    manage_hash   TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_vector tsvector GENERATED ALWAYS AS
                  (to_tsvector('english', description)) STORED
);
CREATE INDEX IF NOT EXISTS photos_feed_idx   ON photos (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS photos_search_idx ON photos USING GIN (search_vector);
"""


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=config.database_url(),
            min_size=config.POOL_MIN,
            max_size=config.POOL_MAX,
            timeout=config.CONNECT_TIMEOUT,
            open=True,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )
    return _pool


def parse_cursor(raw: str | None) -> tuple | None:
    """Cursor format is "<iso timestamp>_<row id>". Malformed means page one."""
    if not raw:
        return None
    try:
        timestamp, row_id = raw.rsplit("_", 1)
        return datetime.fromisoformat(timestamp), int(row_id)
    except (ValueError, AttributeError):
        return None


def encode_cursor(cursor: tuple | None) -> str | None:
    return f"{cursor[0].isoformat()}_{cursor[1]}" if cursor else None


def init_schema() -> None:
    with get_pool().connection() as conn:
        conn.execute(SCHEMA)
    log.info("schema ready")


def list_photos(
    cursor: tuple | None = None,
    query: str | None = None,
    limit: int | None = None,
) -> tuple[list[dict], tuple | None]:
    """One page of the feed, newest first, plus the next cursor.

    Keyset, not OFFSET: stays fast at any depth and never skips or repeats a
    row when someone uploads mid-scroll.
    """
    limit = limit or config.PAGE_SIZE
    where: list[str] = []
    params: dict = {"limit": limit + 1}

    if query:
        where.append("search_vector @@ websearch_to_tsquery('english', %(q)s)")
        params["q"] = query
    if cursor:
        where.append("(created_at, id) < (%(cursor_ts)s, %(cursor_id)s)")
        params["cursor_ts"], params["cursor_id"] = cursor

    sql = "SELECT public_id, description, width, height, created_at, id FROM photos"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC LIMIT %(limit)s"

    with get_pool().connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    # The extra row only tells us whether another page exists.
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (rows[-1]["created_at"], rows[-1]["id"]) if has_more else None
    return rows, next_cursor


def count_photos() -> int:
    with get_pool().connection() as conn:
        return conn.execute("SELECT count(*) AS n FROM photos").fetchone()["n"]


def insert_photo(
    public_id: str,
    description: str,
    width: int,
    height: int,
    size_bytes: int,
    manage_hash: str,
) -> dict:
    with get_pool().connection() as conn:
        return conn.execute(
            """
            INSERT INTO photos
                (public_id, description, width, height, size_bytes, manage_hash)
            VALUES
                (%(pid)s, %(desc)s, %(w)s, %(h)s, %(size)s, %(hash)s)
            RETURNING public_id, description, width, height, created_at, id
            """,
            {
                "pid": public_id,
                "desc": description,
                "w": width,
                "h": height,
                "size": size_bytes,
                "hash": manage_hash,
            },
        ).fetchone()


def delete_photo(public_id: str, manage_hash: str) -> bool:
    """Delete only if the caller holds the manage token. True if a row went."""
    with get_pool().connection() as conn:
        row = conn.execute(
            "DELETE FROM photos WHERE public_id = %(pid)s "
            "AND manage_hash = %(hash)s RETURNING id",
            {"pid": public_id, "hash": manage_hash},
        ).fetchone()
    return row is not None
