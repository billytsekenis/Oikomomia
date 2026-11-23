"""News headlines from public RSS feeds (Yahoo Finance per-ticker RSS, Google News).

All external text is untrusted: it is sanitised, scored, and screened for
prompt-injection patterns before it can reach the AI analyst. Flagged items are
still shown (transparency) but marked and excluded from analyst context.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

from ..analytics.text_sentiment import injection_flags, sanitize_text, score_text
from .http import ProviderError, get

MAX_XML_BYTES = 2_000_000


def _parse_rss(xml_text: str, source: str) -> list[dict]:
    if len(xml_text) > MAX_XML_BYTES or "<!ENTITY" in xml_text[:5000]:
        raise ProviderError(f"{source}: refused suspicious/oversized XML")
    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter("item"):
        title = sanitize_text(it.findtext("title") or "", 300)
        desc = sanitize_text(it.findtext("description") or "", 400)
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        try:
            ts = parsedate_to_datetime(pub).astimezone(timezone.utc) if pub else None
        except (TypeError, ValueError):
            ts = None
        publisher = sanitize_text(it.findtext("source") or "", 80) or source
        if not title:
            continue
        items.append({"title": title, "summary": desc, "url": link if link.startswith("http") else "",
                      "published_at": ts.isoformat() if ts else None, "publisher": publisher, "feed": source})
    return items


def fetch_news(ticker: str, company: str | None = None, limit: int = 40) -> list[dict]:
    items: list[dict] = []
    errors = []
    try:
        r = get("https://feeds.finance.yahoo.com/rss/2.0/headline",
                params={"s": ticker, "region": "US", "lang": "en-US"}, retries=1)
        items += _parse_rss(r.text, "Yahoo Finance RSS")
    except (ProviderError, ET.ParseError) as exc:
        errors.append(str(exc))
    try:
        q = quote_plus(f"{ticker} stock" + (f" {company}" if company and company != ticker else ""))
        r = get(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en", retries=1)
        items += _parse_rss(r.text, "Google News RSS")
    except (ProviderError, ET.ParseError) as exc:
        errors.append(str(exc))
    if not items and errors:
        raise ProviderError("; ".join(errors))
    seen = set()
    out = []
    for it in items:
        key = it["title"].lower()[:90]
        if key in seen:
            continue
        seen.add(key)
        text = it["title"] + ". " + it["summary"]
        s = score_text(text)
        flags = injection_flags(text)
        it.update({"sentiment": s["score"], "sentiment_hits": s["hits"], "injection_flags": flags,
                   "suspicious": bool(flags)})
        out.append(it)
    out.sort(key=lambda x: x["published_at"] or "", reverse=True)
    return out[:limit]


def aggregate_sentiment(items: list[dict], half_life_days: float = 3.0) -> dict:
    """Recency-weighted mean headline sentiment (suspicious items excluded)."""
    now = datetime.now(timezone.utc)
    num = den = 0.0
    n = 0
    for it in items:
        if it.get("suspicious") or not it.get("published_at"):
            continue
        age = (now - datetime.fromisoformat(it["published_at"])).total_seconds() / 86400
        if age > 30:
            continue
        w = 0.5 ** (max(age, 0) / half_life_days)
        num += w * it["sentiment"]
        den += w
        n += 1
    if n == 0:
        return {"score": None, "n": 0, "label": "insufficient data"}
    sc = num / den
    label = "positive" if sc > 0.15 else "negative" if sc < -0.15 else "neutral"
    return {"score": round(sc, 3), "n": n, "label": label}
