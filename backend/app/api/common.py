"""Shared helpers for API modules: JSON-safe conversion and memo caching."""
from __future__ import annotations

import math
import threading
import time
from datetime import date, datetime
from typing import Any, Callable

import numpy as np

_memo: dict[str, tuple[float, Any]] = {}
_memo_lock = threading.Lock()


def clean(o: Any) -> Any:
    """Recursively convert numpy/pandas/NaN values to JSON-safe python values."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    return o


_refreshing: set[str] = set()
_key_locks: dict[str, threading.Lock] = {}


def memo(key: str, ttl: float, fn: Callable[[], Any], swr: bool = True) -> Any:
    """TTL memo with stale-while-revalidate: an expired value is returned immediately while
    a background thread recomputes it, so users never wait on a cold recomputation twice."""
    with _memo_lock:
        hit = _memo.get(key)
    now = time.time()
    if hit and hit[0] > now:
        return hit[1]
    if hit and swr:
        with _memo_lock:
            start = key not in _refreshing
            _refreshing.add(key)
        if start:
            def work():
                try:
                    v = fn()
                    with _memo_lock:
                        _memo[key] = (time.time() + ttl, v)
                except Exception:  # noqa: BLE001 - keep serving the stale value
                    pass
                finally:
                    with _memo_lock:
                        _refreshing.discard(key)
            threading.Thread(target=work, daemon=True).start()
        return hit[1]
    with _memo_lock:
        klock = _key_locks.setdefault(key, threading.Lock())
    with klock:  # single-flight: concurrent cold callers wait for one computation
        with _memo_lock:
            hit = _memo.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        val = fn()
        with _memo_lock:
            _memo[key] = (time.time() + ttl, val)
        return val


def clear_memo(prefix: str = "") -> None:
    with _memo_lock:
        for k in [k for k in _memo if k.startswith(prefix)]:
            _memo.pop(k, None)
