"""Shared HTTP client: retries with exponential backoff, per-host rate limiting
and a simple circuit breaker so that a rate-limited provider (HTTP 429) is
skipped for a cool-down period instead of being hammered."""
from __future__ import annotations

import logging
import random
import threading
import time
from urllib.parse import urlparse

import httpx

from ..config import get_settings

log = logging.getLogger("oik.http")


class ProviderError(RuntimeError):
    """Raised when a provider cannot deliver valid data."""


class _HostState:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self.lock = threading.Lock()
        self.last = 0.0
        self.blocked_until = 0.0
        self.failures = 0


# Minimum seconds between requests per host (polite usage; SEC allows 10 req/s).
_HOST_INTERVALS = {
    "data.sec.gov": 0.15,
    "www.sec.gov": 0.15,
    "api.nasdaq.com": 0.25,
    "query1.finance.yahoo.com": 0.6,
    "query2.finance.yahoo.com": 0.6,
    "fred.stlouisfed.org": 0.5,
}
_hosts: dict[str, _HostState] = {}
_hosts_lock = threading.Lock()
_client: httpx.Client | None = None


def _state(host: str) -> _HostState:
    with _hosts_lock:
        if host not in _hosts:
            _hosts[host] = _HostState(_HOST_INTERVALS.get(host, 0.1))
        return _hosts[host]


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": get_settings().http_user_agent, "Accept-Encoding": "gzip, deflate"},
        )
    return _client


def host_status() -> dict[str, dict]:
    now = time.time()
    return {
        h: {"blocked_for_s": max(0, round(s.blocked_until - now)), "recent_failures": s.failures}
        for h, s in _hosts.items()
    }


def get(url: str, *, params: dict | None = None, headers: dict | None = None, retries: int = 3,
        timeout: float | None = None, cooldown_on_429: float = 600.0) -> httpx.Response:
    host = urlparse(url).netloc
    st = _state(host)
    now = time.time()
    if st.blocked_until > now:
        raise ProviderError(f"{host} is cooling down after rate limiting ({int(st.blocked_until - now)}s left)")
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        with st.lock:
            wait = st.min_interval - (time.time() - st.last)
            if wait > 0:
                time.sleep(wait)
            st.last = time.time()
        try:
            resp = client().get(url, params=params, headers=headers, timeout=timeout or 30.0)
        except httpx.HTTPError as exc:  # network error -> retry
            last_exc = exc
            st.failures += 1
            time.sleep(min(8.0, (2 ** attempt) * 0.5 + random.random() * 0.3))
            continue
        if resp.status_code == 429:
            st.failures += 1
            if attempt >= 1:
                st.blocked_until = time.time() + cooldown_on_429
                raise ProviderError(f"{host} rate limited (429)")
            time.sleep(2.0 + random.random())
            continue
        if resp.status_code >= 500:
            st.failures += 1
            last_exc = ProviderError(f"{host} HTTP {resp.status_code}")
            time.sleep(min(8.0, (2 ** attempt) * 0.5))
            continue
        if resp.status_code >= 400:
            raise ProviderError(f"{host} HTTP {resp.status_code}")
        st.failures = 0
        return resp
    raise ProviderError(f"{host} request failed after {retries + 1} attempts: {last_exc}")


def get_json(url: str, **kw) -> dict | list:
    resp = get(url, **kw)
    try:
        return resp.json()
    except ValueError as exc:
        raise ProviderError(f"Invalid JSON from {urlparse(url).netloc}") from exc
