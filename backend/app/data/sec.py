"""SEC EDGAR (official, free, 10 req/s, requires a descriptive User-Agent).

* company_tickers.json  -> ticker/CIK map (also powers global search)
* companyfacts (XBRL)   -> financial statements, *with filing dates*, so every
                           derived value can be used point-in-time (no look-ahead).
* submissions           -> SIC code, filings list, 8-K item 2.02 (earnings
                           release) dates, Form 4 (insider) filing counts.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from ..config import get_settings
from .http import ProviderError, get_json

SOURCE = "SEC EDGAR"


def _headers() -> dict:
    return {"User-Agent": get_settings().sec_user_agent, "Accept-Encoding": "gzip, deflate"}


def ticker_map() -> dict[str, dict]:
    raw = get_json("https://www.sec.gov/files/company_tickers.json", headers=_headers())
    out = {}
    for row in (raw.values() if isinstance(raw, dict) else raw):
        t = str(row["ticker"]).upper().replace(".", "-")
        out[t] = {"cik": int(row["cik_str"]), "name": row["title"]}
    if not out:
        raise ProviderError("SEC: empty ticker map")
    return out


# ------------------------------------------------------------ XBRL extraction
DURATION_CONCEPTS: dict[str, list[str]] = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "RevenuesNetOfInterestExpense",
                "SalesRevenueGoodsNet"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
    "dna": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
            "DepreciationAmortizationAndAccretionNet", "Depreciation"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "rnd": ["ResearchAndDevelopmentExpense"],
    "sbc": ["ShareBasedCompensation"],
    "dividends_paid": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
}
PER_SHARE_CONCEPTS = {"eps_diluted": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
                      "dps": ["CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"]}
SHARE_DURATION = {"diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"]}
INSTANT_CONCEPTS: dict[str, list[str]] = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"],
    "short_term_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "long_term_debt": ["LongTermDebt"],
    "long_term_debt_noncurrent": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"],
    "long_term_debt_current": ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"],
    "short_term_debt": ["ShortTermBorrowings", "CommercialPaper", "DebtCurrent"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
}


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _facts(cf: dict, concept: str, unit_pref: tuple[str, ...]) -> list[dict]:
    for ns in ("us-gaap", "ifrs-full"):
        node = (cf.get("facts") or {}).get(ns, {}).get(concept)
        if not node:
            continue
        units = node.get("units") or {}
        for u in unit_pref:
            if u in units:
                return [f for f in units[u] if str(f.get("form", "")).startswith(("10-Q", "10-K", "20-F", "40-F"))]
    return []


def _quarterize(facts: list[dict]) -> dict[date, tuple[float, date]]:
    """Convert duration facts (quarterly and year-to-date) to discrete quarters.

    Returns {quarter_end: (value, filed_date)}. Values are *as first reported*
    (earliest filing), making them usable point-in-time.
    """
    by_span: dict[tuple[date, date], tuple[float, date]] = {}
    for f in facts:
        if "start" not in f or "end" not in f or f.get("val") is None:
            continue
        key = (_d(f["start"]), _d(f["end"]))
        filed = _d(f["filed"])
        if key not in by_span or filed < by_span[key][1]:
            by_span[key] = (float(f["val"]), filed)
    quarters: dict[date, tuple[float, date]] = {}
    by_start: dict[date, list[tuple[date, float, date]]] = defaultdict(list)
    for (s, e), (v, filed) in by_span.items():
        dur = (e - s).days
        if 75 <= dur <= 120:  # 12- and 16-week retail fiscal quarters included
            quarters[e] = (v, filed)
        by_start[s].append((e, v, filed))
    # year-to-date differencing: (start, e2) - (start, e1) where e2 - e1 ~ one quarter
    for s, items in by_start.items():
        items.sort()
        for i in range(1, len(items)):
            e2, v2, f2 = items[i]
            for j in range(i - 1, -1, -1):
                e1, v1, f1 = items[j]
                gap = (e2 - e1).days
                if 75 <= gap <= 120:
                    if e2 not in quarters and (e1 - s).days >= 75:
                        quarters[e2] = (v2 - v1, max(f1, f2))
                    break
    return dict(sorted(quarters.items()))


def _annualize(facts: list[dict]) -> dict[date, tuple[float, date]]:
    out: dict[date, tuple[float, date]] = {}
    for f in facts:
        if "start" not in f or f.get("val") is None:
            continue
        s, e = _d(f["start"]), _d(f["end"])
        if 350 <= (e - s).days <= 380:
            filed = _d(f["filed"])
            if e not in out or filed < out[e][1]:
                out[e] = (float(f["val"]), filed)
    return dict(sorted(out.items()))


def _instants(facts: list[dict]) -> dict[date, tuple[float, date]]:
    out: dict[date, tuple[float, date]] = {}
    for f in facts:
        if "start" in f or f.get("val") is None:
            continue
        e, filed = _d(f["end"]), _d(f["filed"])
        if e not in out or filed < out[e][1]:
            out[e] = (float(f["val"]), filed)
    return dict(sorted(out.items()))


def _merge(cf: dict, concepts: list[str], fn, units=("USD",)) -> dict[date, tuple[float, date]]:
    """Merge alternative concept names (companies switch tags over time); the
    earlier concept in the list wins for overlapping periods."""
    merged: dict[date, tuple[float, date]] = {}
    for c in reversed(concepts):
        merged.update(fn(_facts(cf, c, units)))
    return dict(sorted(merged.items()))


def _ser(d: dict[date, tuple[float, date]]) -> list[list]:
    return [[k.isoformat(), v, f.isoformat()] for k, (v, f) in d.items()]


def extract_company_facts(cf: dict) -> dict:
    out: dict = {"entity": cf.get("entityName"), "quarterly": {}, "annual": {}, "instant": {}}
    for name, concepts in DURATION_CONCEPTS.items():
        out["quarterly"][name] = _ser(_merge(cf, concepts, _quarterize))
        out["annual"][name] = _ser(_merge(cf, concepts, _annualize))
    for name, concepts in PER_SHARE_CONCEPTS.items():
        out["quarterly"][name] = _ser(_merge(cf, concepts, _quarterize, units=("USD/shares",)))
        out["annual"][name] = _ser(_merge(cf, concepts, _annualize, units=("USD/shares",)))
    for name, concepts in SHARE_DURATION.items():
        q: dict[date, tuple[float, date]] = {}
        for c in reversed(concepts):
            facts = _facts(cf, c, ("shares",))
            for f in facts:  # share counts are averages, not additive: keep ~quarterly spans only
                if "start" in f and 75 <= (_d(f["end"]) - _d(f["start"])).days <= 120:
                    e, filed = _d(f["end"]), _d(f["filed"])
                    if e not in q or filed < q[e][1]:
                        q[e] = (float(f["val"]), filed)
        out["quarterly"][name] = _ser(dict(sorted(q.items())))
    for name, concepts in INSTANT_CONCEPTS.items():
        out["instant"][name] = _ser(_merge(cf, concepts, _instants))
    # dei shares outstanding (cover page). Multi-class issuers report one fact per class -> sum per filing.
    dei = ((cf.get("facts") or {}).get("dei") or {}).get("EntityCommonStockSharesOutstanding", {})
    per_accn: dict[str, list] = {}
    for f in (dei.get("units") or {}).get("shares", []):
        acc = f.get("accn")
        if acc not in per_accn:
            per_accn[acc] = [_d(f["end"]), 0.0, _d(f["filed"]), set()]
        rec = per_accn[acc]
        key = (f["end"], f["val"], f.get("frame"))
        if key not in rec[3]:
            rec[3].add(key)
            rec[1] += float(f["val"])
    shares = {}
    for e, v, filed, _ in per_accn.values():
        if e not in shares or filed < shares[e][1]:
            shares[e] = (v, filed)
    out["instant"]["shares_outstanding"] = _ser(dict(sorted(shares.items())))
    return out


def company_facts(cik: int) -> dict:
    cf = get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", headers=_headers(), timeout=60)
    return extract_company_facts(cf)


def submissions(cik: int) -> dict:
    raw = get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=_headers())
    rec = (raw.get("filings") or {}).get("recent") or {}
    n = len(rec.get("accessionNumber", []))
    filings = []
    for i in range(n):
        form = rec["form"][i]
        acc = rec["accessionNumber"][i]
        doc = rec.get("primaryDocument", [""] * n)[i]
        filings.append({
            "form": form, "filing_date": rec["filingDate"][i], "report_date": rec.get("reportDate", [""] * n)[i],
            "items": rec.get("items", [""] * n)[i], "description": rec.get("primaryDocDescription", [""] * n)[i],
            "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}" if doc else
                   f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}",
        })
    today = date.today()
    cutoff = (today - timedelta(days=90)).isoformat()
    form4 = [f for f in filings if f["form"] == "4" and f["filing_date"] >= cutoff]
    earnings_dates = sorted({f["filing_date"] for f in filings if f["form"].startswith("8-K") and "2.02" in (f["items"] or "")})
    important = [f for f in filings if f["form"] in ("10-K", "10-Q", "8-K", "SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A", "DEF 14A", "S-1", "S-3", "424B5")]
    return {
        "name": raw.get("name"), "sic": raw.get("sic"), "sic_description": raw.get("sicDescription"),
        "fiscal_year_end": raw.get("fiscalYearEnd"), "exchanges": raw.get("exchanges"),
        "insider_form4_count_90d": len(form4),
        "insider_form4_recent": form4[:15],
        "earnings_release_dates": earnings_dates,
        "recent_filings": important[:40],
    }
