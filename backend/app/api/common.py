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


def memo(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
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
