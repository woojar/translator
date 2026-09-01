"""Best-effort on-disk cache for translations.

GoldenDict re-runs the program for every lookup, and the same words get looked
up over and over. Caching removes most of the network traffic, which is the
single biggest source of flakiness (rate limiting). Every function here
swallows its own errors: a broken cache must never break a lookup.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from ._providers import Translation

DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days
_TRUTHY = {"1", "true", "yes", "on"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    key         TEXT PRIMARY KEY,
    translation TEXT NOT NULL,
    detected    TEXT,
    provider    TEXT,
    created     REAL NOT NULL
)
"""


def enabled() -> bool:
    """Return False when ``GD_NO_CACHE`` asks us to stay out of the way."""
    return os.environ.get("GD_NO_CACHE", "").strip().lower() not in _TRUTHY


def ttl_seconds() -> int:
    """Entry lifetime; ``GD_CACHE_TTL=0`` means "never expire"."""
    raw = os.environ.get("GD_CACHE_TTL")
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_TTL_SECONDS


def cache_path() -> Path:
    """Location of the SQLite file (``GD_CACHE_DIR`` overrides)."""
    override = os.environ.get("GD_CACHE_DIR")
    if override:
        base = Path(override)
    elif os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        base = Path(root) / "gd-translator"
    else:
        root = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
        base = Path(root) / "gd-translator"
    return base / "translations.sqlite3"


def make_key(text: str, source: str, target: str) -> str:
    """Cache key; the unit separator cannot appear in a looked-up word."""
    return f"{source}\x1f{target}\x1f{text}"


def _connect() -> sqlite3.Connection:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Short busy timeout + WAL so concurrent GoldenDict lookups don't block.
    connection = sqlite3.connect(path, timeout=1.0, isolation_level=None)
    connection.execute("PRAGMA busy_timeout=1000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(_SCHEMA)
    return connection


def get(key: str) -> Translation | None:
    """Return a fresh cached translation, or None."""
    if not enabled():
        return None
    try:
        with closing(_connect()) as connection:
            row = connection.execute(
                "SELECT translation, detected, provider, created "
                "FROM entries WHERE key = ?",
                (key,),
            ).fetchone()
    except (sqlite3.Error, OSError):
        return None
    if row is None:
        return None

    translation, detected, provider, created = row
    ttl = ttl_seconds()
    if ttl and time.time() - created > ttl:
        return None
    return Translation(translation, detected, f"{provider or 'cache'} (cached)")


def put(key: str, result: Translation) -> None:
    """Store a translation, ignoring any storage failure."""
    if not enabled():
        return
    try:
        with closing(_connect()) as connection:
            connection.execute(
                "INSERT INTO entries (key, translation, detected, provider, created) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "translation=excluded.translation, detected=excluded.detected, "
                "provider=excluded.provider, created=excluded.created",
                (key, result.text, result.detected, result.provider, time.time()),
            )
    except (sqlite3.Error, OSError):
        return
