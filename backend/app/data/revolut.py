"""Revolut trading statement import (CSV exported from the Revolut app).

The exact column layout of Revolut's "Account statement" export is not publicly
documented and has changed over time, so the parser is deliberately tolerant:
it locates columns by name (several aliases), strips currency prefixes such as
"USD 150.25" or "$150.25", and classifies the free-text "Type" column. Every
import is previewed first; rows that cannot be parsed are listed with a reason
and never guessed.

Supported row types: buys, sells, dividends, fees (custody / commission rows),
stock splits. Cash top-ups/withdrawals and transfers are cash movements and are
reported as skipped (the portfolio tracks securities, not the cash balance).
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

COLUMN_ALIASES = {
    "date": ["date", "date time", "datetime", "time", "completed date", "trade date"],
    "ticker": ["ticker", "symbol", "instrument"],
    "type": ["type", "transaction type", "description", "action"],
    "quantity": ["quantity", "qty", "shares", "units"],
    "price": ["price per share", "price", "share price", "unit price"],
    "total": ["total amount", "total", "amount", "value"],
    "currency": ["currency", "ccy"],
    "fx": ["fx rate", "fx", "exchange rate", "rate"],
}
_CCY = re.compile(r"\b(USD|EUR|GBP|CHF|PLN|SEK|NOK|DKK|CZK|HUF|RON|BGN)\b")
_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}


@dataclass
class ParsedRow:
    line: int
    raw_type: str
    kind: str | None = None          # BUY / SELL / DIV / FEE / SPLIT / SKIP
    ticker: str | None = None
    trade_date: date | None = None
    quantity: float | None = None
    price: float | None = None
    total: float | None = None
    currency: str | None = None
    fx_raw: float | None = None
    fee: float = 0.0
    issues: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return self.kind not in (None, "SKIP") and not any(i.startswith("ERROR") for i in self.issues)


def parse_number(v: str | None) -> tuple[float | None, str | None]:
    """'USD 1,234.50' -> (1234.5, 'USD'); '-€10' -> (-10.0, 'EUR'); '1.234,56' -> 1234.56; '' -> (None, None)."""
    if v is None:
        return None, None
    s = str(v).strip().replace("\u00a0", " ")
    if not s or s in ("-", "--"):
        return None, None
    ccy = None
    m = _CCY.search(s.upper())
    if m:
        ccy = m.group(1)
    for sym, c in _SYMBOLS.items():
        if sym in s and not ccy:
            ccy = c
    digits_at = re.search(r"\d", s)
    neg = (digits_at is not None and "-" in s[:digits_at.start()]) or (s.startswith("(") and s.endswith(")"))
    core = re.sub(r"[^\d,.]", "", s)
    if not core or not re.search(r"\d", core):
        return None, ccy
    if "," in core and "." in core:
        core = core.replace(".", "").replace(",", ".") if core.rfind(",") > core.rfind(".") else core.replace(",", "")
    elif "," in core:
        core = core.replace(",", ".") if (core.count(",") == 1 and re.search(r",\d{1,2}$", core)) else core.replace(",", "")
    try:
        val = float(core)
    except ValueError:
        return None, ccy
    return (-val if neg else val), ccy


def parse_date(v: str) -> date | None:
    s = (v or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def classify(t: str) -> str:
    u = (t or "").upper()
    if "SPLIT" in u:
        return "SPLIT"
    if "DIVIDEND" in u and "TAX" not in u:
        return "DIV"
    if "BUY" in u:
        return "BUY"
    if "SELL" in u:
        return "SELL"
    if "FEE" in u or "COMMISSION" in u or "WITHHOLDING" in u or ("DIVIDEND" in u and "TAX" in u):
        return "FEE"
    return "SKIP"


def _header_map(header: list[str]) -> dict[str, int]:
    norm = [re.sub(r"\s+", " ", h.strip().lower().replace("_", " ")) for h in header]
    out = {}
    for key, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            if a in norm:
                out[key] = norm.index(a)
                break
    return out


def parse_csv(text: str) -> tuple[list[ParsedRow], dict]:
    text = text.lstrip("﻿")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    # header = first row that contains a date column and a type column
    hidx, cmap = None, {}
    for i, r in enumerate(rows[:20]):
        m = _header_map(r)
        if "date" in m and "type" in m:
            hidx, cmap = i, m
            break
    info = {"columns_found": sorted(cmap), "header_line": (hidx + 1) if hidx is not None else None}
    if hidx is None:
        return [], {**info, "error": "No header row with 'Date' and 'Type' columns found. Is this the Revolut stocks account statement (CSV)?"}
    out: list[ParsedRow] = []
    for ln, r in enumerate(rows[hidx + 1:], start=hidx + 2):
        if not any(c.strip() for c in r):
            continue
        get = lambda k: r[cmap[k]] if k in cmap and cmap[k] < len(r) else None  # noqa: E731
        pr = ParsedRow(line=ln, raw_type=(get("type") or "").strip())
        pr.kind = classify(pr.raw_type)
        pr.trade_date = parse_date(get("date") or "")
        pr.ticker = (get("ticker") or "").strip().upper().replace(".", "-") or None
        q, _ = parse_number(get("quantity"))
        p, pc = parse_number(get("price"))
        tot, tc = parse_number(get("total"))
        ccy = (get("currency") or "").strip().upper() or pc or tc
        fx, _ = parse_number(get("fx"))
        pr.quantity, pr.price, pr.total, pr.currency, pr.fx_raw = q, p, tot, ccy, fx
        if pr.kind == "SKIP":
            pr.issues.append(f"skipped: '{pr.raw_type}' is a cash movement or unsupported type")
            out.append(pr)
            continue
        if pr.trade_date is None:
            pr.issues.append("ERROR: unreadable date")
        if pr.kind in ("BUY", "SELL", "SPLIT", "DIV") and not pr.ticker:
            pr.issues.append("ERROR: missing ticker")
        if pr.currency not in ("USD", "EUR"):
            pr.issues.append(f"ERROR: currency '{pr.currency}' not supported (USD/EUR only)")
        if pr.kind in ("BUY", "SELL"):
            if not q or q <= 0:
                pr.issues.append("ERROR: missing quantity")
            if (not p or p <= 0) and tot and q:
                pr.price = abs(tot) / abs(q)
                pr.issues.append("price derived from total / quantity")
            if not pr.price or pr.price <= 0:
                pr.issues.append("ERROR: missing price")
            pr.quantity = abs(q) if q else q
            if pr.quantity and pr.price and tot:
                gross = pr.quantity * pr.price
                diff = abs(tot) - gross if pr.kind == "BUY" else gross - abs(tot)
                # a positive gap between cash amount and shares x price is the commission
                if diff > 0.005 and diff < 0.05 * gross:
                    pr.fee = round(diff, 4)
        elif pr.kind == "DIV":
            if tot is None:
                pr.issues.append("ERROR: missing dividend amount")
            pr.total = abs(tot) if tot is not None else None
        elif pr.kind == "FEE":
            if tot is None:
                pr.issues.append("ERROR: missing fee amount")
            pr.total = abs(tot) if tot is not None else None
        elif pr.kind == "SPLIT":
            if q is None or q == 0:
                pr.issues.append("ERROR: split row without share quantity change")
        out.append(pr)
    return out, info


def resolve_fx(raw: float | None, ecb_eurusd: float | None) -> tuple[float | None, str]:
    """Interpret a statement FX value as USD-per-EUR. Revolut statements may quote either
    direction; the one closest to the ECB fixing of the trade date is chosen. Values more than
    5% away from the ECB fixing in both directions are rejected."""
    if not raw or raw <= 0 or not ecb_eurusd:
        return None, "ECB fixing" if ecb_eurusd else "unavailable"
    cands = [(abs(raw / ecb_eurusd - 1), raw), (abs((1 / raw) / ecb_eurusd - 1), 1 / raw)]
    err, val = min(cands)
    if err > 0.05:
        return None, f"statement FX {raw} inconsistent with ECB {ecb_eurusd:.4f}; ECB used"
    return val, "Revolut statement"
