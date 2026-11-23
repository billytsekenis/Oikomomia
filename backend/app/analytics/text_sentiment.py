"""Deterministic financial-news sentiment and prompt-injection screening.

Sentiment uses a compact finance-specific lexicon (in the spirit of
Loughran & McDonald, 2011) with negation handling and phrase rules. It is
transparent and reproducible, but it is a *headline* scorer: it does not read
full articles and cannot detect sarcasm or context. Because there is no
point-in-time news archive available for free, news sentiment is displayed but
deliberately NOT fed into the numeric forecast (it could not be backtested).
"""
from __future__ import annotations

import html
import re
import unicodedata

POSITIVE = {
    "beat", "beats", "beating", "exceeds", "exceeded", "surpass", "surpasses", "record", "records", "upgrade",
    "upgraded", "upgrades", "outperform", "outperforms", "raises", "raised", "raise", "boost", "boosts",
    "boosted", "growth", "grows", "grew", "surge", "surges", "surged", "soar", "soars", "soared", "jump",
    "jumps", "jumped", "rally", "rallies", "rallied", "gain", "gains", "gained", "rebound", "rebounds",
    "strong", "stronger", "strength", "robust", "profit", "profitable", "profits", "approval", "approved",
    "approves", "wins", "win", "won", "award", "awarded", "partnership", "expands", "expansion", "buyback",
    "repurchase", "dividend", "bullish", "optimistic", "upbeat", "accelerate", "accelerates", "breakthrough",
    "innovative", "launch", "launches", "climbs", "climb", "higher", "tops", "topped", "positive", "momentum",
    "overweight", "buy", "rises", "rise", "rose", "recovery", "recovers", "upside", "lifts", "lifted",
}
NEGATIVE = {
    "miss", "misses", "missed", "downgrade", "downgraded", "downgrades", "underperform", "cut", "cuts",
    "cutting", "lower", "lowers", "lowered", "weak", "weaker", "weakness", "decline", "declines", "declined",
    "drop", "drops", "dropped", "fall", "falls", "fell", "plunge", "plunges", "plunged", "slump", "slumps",
    "tumble", "tumbles", "tumbled", "sink", "sinks", "sank", "loss", "losses", "lawsuit", "sued", "sues",
    "probe", "investigation", "investigates", "fraud", "recall", "recalls", "warning", "warns", "warned",
    "bearish", "pessimistic", "layoff", "layoffs", "bankruptcy", "default", "defaults", "fine", "fined",
    "penalty", "delay", "delayed", "delays", "halt", "halts", "halted", "slowdown", "slows", "concern",
    "concerns", "risk", "risks", "selloff", "sell-off", "crash", "crashes", "volatile", "uncertainty",
    "underweight", "sell", "negative", "downside", "headwind", "headwinds", "shortfall", "disappoint",
    "disappoints", "disappointing", "resigns", "resignation", "antitrust", "tariff", "tariffs", "dilution",
    "slashes", "slashed", "subpoena", "breach", "hack", "outage",
}
NEGATIONS = {"not", "no", "never", "without", "fails", "failed", "fail", "despite", "lack", "barely"}
PHRASES = {
    "beats estimates": 2.0, "tops estimates": 2.0, "raises guidance": 2.5, "raised guidance": 2.5,
    "cuts guidance": -2.5, "lowers guidance": -2.5, "misses estimates": -2.0, "price target raised": 1.5,
    "price target cut": -1.5, "all-time high": 1.5, "record high": 1.5, "52-week low": -1.5,
    "guidance cut": -2.5, "profit warning": -2.5, "going concern": -3.0, "share buyback": 1.0,
}

_TOKEN = re.compile(r"[a-z][a-z\-']*")

INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts|messages)",
    r"disregard (all |any |the )?(previous|prior|above) ",
    r"\bsystem prompt\b", r"\byou are now\b", r"\bact as\b", r"\bnew instructions?\b",
    r"\bassistant:\s", r"\buser:\s", r"<\s*/?\s*(system|instructions?|prompt)\s*>",
    r"\b(tell|instruct) (the )?(user|reader|ai|model) to (buy|sell)",
    r"\bguaranteed (returns?|profit|gains?)\b", r"\brisk[- ]free (profit|returns?)\b",
    r"\boverride\b.*\b(rules|instructions|safety)\b", r"\bjailbreak\b",
    r"\bdo not (mention|disclose|reveal)\b",
]
_INJ = [re.compile(p, re.I) for p in INJECTION_PATTERNS]


def sanitize_text(text: str, max_len: int = 400) -> str:
    """Strip HTML/markup and control characters; collapse whitespace; truncate."""
    t = html.unescape(text or "")
    t = re.sub(r"<[^>]{0,500}>", " ", t)
    t = "".join(ch for ch in t if unicodedata.category(ch)[0] != "C" or ch in " \n")
    t = re.sub(r"\s+", " ", t).strip()
    return t[:max_len]


def injection_flags(text: str) -> list[str]:
    return [p.pattern for p in _INJ if p.search(text or "")]


def score_text(text: str) -> dict:
    """Return {'score' in [-1,1], 'pos', 'neg', 'hits'}."""
    low = (text or "").lower()
    total = 0.0
    hits: list[str] = []
    for phrase, w in PHRASES.items():
        if phrase in low:
            total += w
            hits.append(phrase)
    toks = _TOKEN.findall(low)
    pos = neg = 0
    for i, tok in enumerate(toks):
        val = 1.0 if tok in POSITIVE else -1.0 if tok in NEGATIVE else 0.0
        if not val:
            continue
        window = toks[max(0, i - 3):i]
        if any(w in NEGATIONS for w in window):
            val = -val
        total += val
        hits.append(tok)
        if val > 0:
            pos += 1
        else:
            neg += 1
    # squash to [-1, 1]
    score = total / (abs(total) + 2.0) if total else 0.0
    return {"score": round(score, 3), "pos": pos, "neg": neg, "hits": hits[:12]}
