"""System status, data-source catalogue and job control."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import jobs
from ..analytics.context import model_metrics
from ..config import get_settings
from ..data import market
from ..data.http import host_status
from ..security import get_current_user

router = APIRouter(prefix="/api/system", tags=["system"])

DATA_SOURCES = [
    {"name": "Nasdaq public API (api.nasdaq.com)", "used_for": "Daily OHLCV (10y), real-time last sale & bid/ask, dividends (Nasdaq-listed), analyst EPS consensus, earnings surprises, institutional ownership, earnings & economic calendars",
     "cost": "Free, keyless", "rate_limits": "Undocumented; client throttles to 4 req/s with retries", "freshness": "Quotes real-time (Nasdaq Last Sale); daily bars typically T+0 evening / T+1",
     "history": "~10 years daily", "licensing": "Nasdaq.com terms - personal, non-commercial use", "realtime": "Real-time last sale (Nasdaq venues only), not consolidated SIP"},
    {"name": "Yahoo Finance chart API", "used_for": "Secondary prices; S&P 500, Dow, Russell index levels; total-return series for NYSE ETFs",
     "cost": "Free, keyless, unofficial", "rate_limits": "Aggressive (HTTP 429 on datacenter IPs); 15-min cool-down on 429", "freshness": "Delayed ~15 min",
     "history": "Decades", "licensing": "Yahoo terms - personal use only", "realtime": "Delayed"},
    {"name": "SEC EDGAR (data.sec.gov)", "used_for": "XBRL financial statements with filing dates (point-in-time), filings list, 8-K earnings dates, Form 4 insider filings, ticker/CIK map",
     "cost": "Free, official", "rate_limits": "10 requests/second, descriptive User-Agent required (SEC_USER_AGENT)", "freshness": "Minutes after filing",
     "history": "2009+ (XBRL)", "licensing": "Public domain (U.S. government)", "realtime": "n/a"},
    {"name": "U.S. Treasury daily par yield curve", "used_for": "1M-30Y Treasury yields, risk-free rate, curve slope",
     "cost": "Free, official", "rate_limits": "None documented", "freshness": "Daily after ~16:00 ET", "history": "1990+", "licensing": "Public domain", "realtime": "End of day"},
    {"name": "Cboe index history (cdn.cboe.com)", "used_for": "VIX, VIX9D, VIX3M (volatility & term structure)",
     "cost": "Free, official", "rate_limits": "None documented", "freshness": "End of day", "history": "1990+", "licensing": "Cboe terms - informational use", "realtime": "End of day"},
    {"name": "FRED (St. Louis Fed)", "used_for": "CPI, core CPI/PCE, unemployment, payrolls, Fed funds, high-yield OAS, sentiment, GDP",
     "cost": "Free (keyless CSV; optional FRED_API_KEY for the JSON API)", "rate_limits": "120 req/min with key", "freshness": "Per release (monthly/quarterly)",
     "history": "Decades", "licensing": "Mostly public domain; some series third-party (e.g. ICE BofA OAS)", "realtime": "n/a"},
    {"name": "ECB reference rates via Frankfurter", "used_for": "EUR/USD for portfolio FX conversion (trade-date fixings)",
     "cost": "Free, keyless", "rate_limits": "Fair use", "freshness": "Daily ~16:00 CET", "history": "1999+", "licensing": "ECB public data", "realtime": "Daily fixing"},
    {"name": "Yahoo Finance RSS & Google News RSS", "used_for": "Headlines for news sentiment and catalysts",
     "cost": "Free", "rate_limits": "Fair use", "freshness": "Minutes", "history": "Recent only (no archive -> sentiment cannot be backtested)",
     "licensing": "Headlines + links only; publisher copyrights apply", "realtime": "Near real-time"},
]

EVALUATED_NOT_USED = [
    {"name": "Polygon.io / Massive", "why": "Best US market data (SIP, options) but paid for real-time; no key available."},
    {"name": "Alpha Vantage", "why": "Free key limited to 25 requests/day - too low for a 100+ stock universe."},
    {"name": "Financial Modeling Prep", "why": "Good fundamentals/estimates but key required; SEC XBRL covers statements for free."},
    {"name": "Finnhub", "why": "Free tier useful for news/sentiment, key required; RSS + SEC used instead."},
    {"name": "Tiingo", "why": "Clean EOD data with adjusted prices, key required; could replace Yahoo as secondary source."},
    {"name": "Stooq", "why": "Free CSV EOD data, but unreliable from this environment (timeouts)."},
]


@router.get("/status")
def status(_=Depends(get_current_user)):
    s = get_settings()
    m = model_metrics()
    return {"demo_mode": s.demo_mode, "environment": s.environment,
            "keys": {"anthropic_api_key": bool(s.anthropic_api_key), "fred_api_key": bool(s.fred_api_key),
                     "sec_user_agent_customised": "set SEC_USER_AGENT" not in s.sec_user_agent},
            "model": {"version": m.get("version"), "trained_at": m.get("trained_at"), "data_end": m.get("data_end"),
                      "n_tickers": m.get("n_tickers")} if m else None,
            "provider_health": host_status(), "jobs": jobs.status()}


@router.get("/data-sources")
def data_sources(_=Depends(get_current_user)):
    return {"sources": DATA_SOURCES, "evaluated_not_used": EVALUATED_NOT_USED, "series": market.last_refresh_times()}


@router.post("/jobs/{name}")
def run_job(name: str, _=Depends(get_current_user)):
    try:
        return jobs.run_async(name)
    except KeyError as exc:
        raise HTTPException(404, f"unknown job {name}; choose from {list(jobs.JOBS)}") from exc
