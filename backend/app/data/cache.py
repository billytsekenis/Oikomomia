"""Two-level cache: in-process TTL dict in front of the `cache_entries` table.

`cached()` returns stale data (flagged) when a refresh fails, so the UI can say
"data as of X, refresh failed" instead of showing nothing or inventing values.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from ..db import SessionLocal
from ..models import CacheEntry, utcnow
from .http import ProviderError

log = logging.getLogger("oik.cache")
_mem: dict[str, tuple[float, dict]] = {}
_mem_lock = threading.Lock()
_key_locks: dict[str, threading.Lock] = {}


def _lock_for(key: str) -> threading.Lock:
    with _mem_lock:
        return _key_locks.setdefault(key, threading.Lock())


def _envelope(value: Any, source: str, fetched_at: datetime, stale: bool = False, error: str = "") -> dict:
    return {"value": value, "source": source, "fetched_at": fetched_at.isoformat() + "Z", "stale": stale, "error": error}


def get_entry(key: str) -> CacheEntry | None:
    with SessionLocal() as db:
        return db.get(CacheEntry, key)


def put(key: str, value: Any, source: str, ttl_s: float) -> dict:
    now = utcnow()
    with SessionLocal() as db:
        row = db.get(CacheEntry, key)
        if row is None:
            row = CacheEntry(key=key)
            db.add(row)
        row.value = value
        row.source = source
        row.fetched_at = now
        row.expires_at = now + timedelta(seconds=ttl_s)
        db.commit()
    env = _envelope(value, source, now)
    with _mem_lock:
        _mem[key] = (time.time() + min(ttl_s, 300), env)
    return env


def invalidate(prefix: str) -> None:
    with _mem_lock:
        for k in [k for k in _mem if k.startswith(prefix)]:
            _mem.pop(k, None)
    with SessionLocal() as db:
        db.query(CacheEntry).filter(CacheEntry.key.like(prefix + "%")).delete(synchronize_session=False)
        db.commit()


def cached(key: str, ttl_s: float, source: str, fetch: Callable[[], Any], *, force: bool = False) -> dict:
    """Return an envelope {value, source, fetched_at, stale, error}."""
    if not force:
        with _mem_lock:
            hit = _mem.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
    with _lock_for(key):
        row = get_entry(key)
        now = utcnow()
        if row is not None and not force and row.expires_at > now:
            env = _envelope(row.value, row.source, row.fetched_at)
            with _mem_lock:
                _mem[key] = (time.time() + min(60.0, (row.expires_at - now).total_seconds()), env)
            return env
        try:
            value = fetch()
        except (ProviderError, ValueError, KeyError, TypeError) as exc:
            log.warning("fetch failed for %s: %s", key, exc)
            if row is not None:
                return _envelope(row.value, row.source, row.fetched_at, stale=True, error=str(exc))
            raise ProviderError(str(exc)) from exc
        return put(key, value, source, ttl_s)
