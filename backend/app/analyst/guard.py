"""Adversarial guard: detects manipulation patterns in the user's message,
verifies numeric claims against platform data, and checks that LLM output
contains no numbers absent from the evidence pack (hallucination guard)."""
from __future__ import annotations

import re

from ..analytics.text_sentiment import injection_flags

GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]")


def language(text: str) -> str:
    return "el" if len(GREEK.findall(text or "")) >= 3 else "en"


PATTERNS: dict[str, list[str]] = {
    "guaranteed_returns": [
        r"\bguarantee[ds]?\b", r"\bsure thing\b", r"\b100\s*%\s*(sure|certain|safe)\b", r"\brisk[- ]free\b",
        r"\bcan'?t (lose|go down|fail)\b", r"\bwill definitely\b", r"\bdefinitely (go|rise|win)\b", r"\bno risk\b",
        r"εγγυημ", r"σίγουρ[ηαοε]", r"χωρίς ρίσκο", r"δεν μπορεί να πέσει", r"εξασφαλισμέν",
    ],
    "authority_bias": [
        r"\b(goldman|morgan stanley|jp ?morgan|buffett|cathie|cramer|analysts?|experts?|insiders?|my (friend|broker|uncle|advisor))\b.{0,40}\b(say|says|said|told|predict|recommend|think|agree)",
        r"\beveryone (says|knows)\b", r"\b(famous|top) (investor|analyst)\b",
        r"(ο|η) (buffett|goldman|αναλυτ\w+|ειδικ\w+|φίλος μου|μεσίτης μου).{0,40}(λέει|είπε|προβλέπει|συστήνει)",
        r"όλοι (λένε|ξέρουν)",
    ],
    "confirmation_bias": [
        r"\bconfirm (that|my|it)\b", r"\b(just )?(tell|show) me why .{0,40}(will|is going to) (go up|rise|win|double)",
        r"\bi (already )?know .{0,40}(will|is going to)\b", r"\bagree (with me )?that\b", r"\bright\?\s*$",
        r"επιβεβαίωσε", r"συμφωνείς ότι", r"ξέρω (ήδη )?ότι .{0,40}θα",
    ],
    "false_precision": [
        r"\bexact (price|value|number|date)\b", r"\bprecise (price|target)\b", r"\bto the (cent|penny|dollar)\b",
        r"\bexactly (how much|what price|where)\b", r"ακριβή τιμή", r"ακριβώς (πόσο|πού|ποια)",
    ],
    "misleading_assumption": [
        r"\b(stocks?|markets?|it|they|prices?) (always|never) (go|goes|went|recover|recovers|fall|falls|drop|drops)\b",
        r"\bmust (recover|go up|bounce|rebound)\b", r"\bcan only go (up|down)\b", r"\bhas to (go up|recover|bounce)\b",
        r"\bbottomed\b.{0,20}\b(for sure|definitely)\b", r"\b(always|never) (loses?|wins?)\b",
        r"πάντα (ανεβαίν|ανακάμπτ|επιστρέφ)", r"πρέπει να ανακάμψει", r"μόνο (πάνω|ανοδικ)",
    ],
    "pressure_without_evidence": [
        r"\bi insist\b", r"\bjust say (yes|it)\b", r"\bstop (hedging|being negative)\b", r"\bjust answer (yes|buy)\b",
        r"\bwhy (won'?t|don'?t) you (agree|just say)\b", r"\byou'?re wrong\b(?!.*\b(because|data|reported|filing)\b)",
        r"επιμένω", r"απλά πες (ναι|ότι)", r"σταμάτα να",
    ],
}
_COMPILED = {k: [re.compile(p, re.I) for p in v] for k, v in PATTERNS.items()}

CLAIM_METRICS = {
    "pe": (r"\bp\s*/?\s*e\b(?: ratio)?|\bprice[- ]to[- ]earnings\b", "pe_ttm", "x"),
    "forward_pe": (r"\bforward p\s*/?\s*e\b", "forward_pe", "x"),
    "price": (r"\b(price|trades? at|trading at|stock is at|τιμή)\b", "price", "$"),
    "revenue_growth": (r"\brevenue (growth|grew)\b|\bέσοδα .{0,10}αυξήθηκαν", "revenue_growth_yoy", "%"),
    "dividend_yield": (r"\bdividend yield\b|\bμερισματική απόδοση", "dividend_yield", "%"),
    "market_cap": (r"\bmarket cap\b|\bκεφαλαιοποίηση", "market_cap", "$"),
}
NUM = re.compile(r"(?<![\w.])[-+]?\$?\d[\d,]*(?:\.\d+)?\s*(?:%|x|bn|b|billion|trillion|t|m|million)?", re.I)


def detect(text: str) -> list[dict]:
    flags = []
    for kind, pats in _COMPILED.items():
        for p in pats:
            m = p.search(text or "")
            if m:
                flags.append({"type": kind, "match": m.group(0)[:80]})
                break
    inj = injection_flags(text or "")
    if inj:
        flags.append({"type": "prompt_injection", "match": inj[0][:80]})
    return flags


def _parse_num(s: str) -> float | None:
    s0 = s.lower().replace(",", "").replace("$", "").strip()
    mult = 1.0
    for suf, m in (("trillion", 1e12), ("billion", 1e9), ("million", 1e6), ("bn", 1e9), ("t", 1e12), ("b", 1e9), ("m", 1e6)):
        if s0.endswith(suf):
            s0, mult = s0[: -len(suf)].strip(), m
            break
    pct = s0.endswith("%")
    s0 = s0.rstrip("%x").strip()
    try:
        v = float(s0) * mult
    except ValueError:
        return None
    return v / 100 if pct else v


def extract_claims(text: str) -> list[dict]:
    """Find '<metric> ... <number>' claims within a sentence."""
    claims = []
    for sent in re.split(r"[.;!?\n]", text or ""):
        for key, (pat, field, unit) in CLAIM_METRICS.items():
            m = re.search(pat, sent, re.I)
            if not m:
                continue
            tail = sent[m.end(): m.end() + 40]
            n = NUM.search(tail)
            if n:
                v = _parse_num(n.group(0))
                if v is not None:
                    if unit == "%" and "%" not in n.group(0) and v > 1:
                        v = v / 100
                    claims.append({"metric": key, "field": field, "claimed": v, "text": (m.group(0) + tail[: n.end()]).strip()})
    # 'forward_pe' also matches 'pe' - keep the more specific one
    if any(c["metric"] == "forward_pe" for c in claims):
        claims = [c for c in claims if c["metric"] != "pe"]
    return claims


def verify_claims(claims: list[dict], facts: dict) -> list[dict]:
    out = []
    for c in claims:
        actual = facts.get(c["field"])
        if actual is None:
            out.append({**c, "status": "unverifiable", "actual": None})
            continue
        rel = abs(c["claimed"] - actual) / max(abs(actual), 1e-9)
        out.append({**c, "actual": actual, "status": "consistent" if rel <= 0.10 else "contradicted", "rel_error": rel})
    return out


# ------------------------------------------------------------ hallucination guard
SAFE_SMALL = {0, 1, 2, 3, 4, 5, 6, 7, 10, 12, 21, 30, 50, 63, 90, 100, 126, 200, 252}


def _variants(v: float) -> set[float]:
    out = set()
    for x in (v, v * 100):
        for d in (0, 1, 2):
            out.add(round(x, d))
    return out


def evidence_numbers(obj) -> set[float]:
    nums: set[float] = set()

    def walk(o):
        if isinstance(o, bool) or o is None:
            return
        if isinstance(o, (int, float)):
            if o == o:  # not NaN
                nums.update(_variants(float(o)))
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            for m in NUM.finditer(o):
                p = _parse_num(m.group(0))
                if p is not None:
                    nums.update(_variants(p))
                    nums.update(_variants(p * 100 if "%" in m.group(0) else p))
    walk(obj)
    return nums


def unsupported_numbers(answer: str, evidence, extra_allowed: set[float] | None = None) -> list[str]:
    allowed = evidence_numbers(evidence) | (extra_allowed or set())
    bad = []
    for m in NUM.finditer(answer or ""):
        raw = m.group(0).strip()
        v = _parse_num(raw)
        if v is None:
            continue
        candidates = {v, v * 100} if "%" in raw else {v}
        if any(abs(c) in SAFE_SMALL for c in candidates):
            continue
        if 1900 <= v <= 2100 and float(v).is_integer():  # years
            continue
        # `allowed` already holds each evidence value rounded to 0/1/2 decimals (and x100 for
        # fractions), so a correctly rounded quote of the evidence matches exactly; only a tiny
        # relative tolerance is allowed for float noise. The candidate itself is never rounded.
        ok = any(c in allowed or any(abs(c - a) <= 0.005 * abs(a) for a in allowed if a) for c in candidates)
        if not ok:
            bad.append(raw)
    return bad
