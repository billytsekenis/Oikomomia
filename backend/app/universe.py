"""Analysis universe.

A fixed list of large, liquid US equities across all 11 GICS sectors, plus
benchmark/sector ETFs. NOTE ON SURVIVORSHIP BIAS: this list is today's large
caps; free data sources do not provide point-in-time index membership or
delisted securities. Backtests over this universe are therefore biased upward
in absolute terms. The platform mitigates this by (a) reporting skill relative
to a naive baseline on the *same* universe, (b) market-neutral long/short
spreads and (c) the forward-only live prediction track record, which is free
of survivorship and look-ahead bias by construction.
"""
from __future__ import annotations

STOCKS: list[tuple[str, str, str, str]] = [
    # ticker, name, sector, industry
    ("AAPL", "Apple", "Technology", "Hardware"),
    ("MSFT", "Microsoft", "Technology", "Software"),
    ("NVDA", "NVIDIA", "Technology", "Semiconductors"),
    ("AVGO", "Broadcom", "Technology", "Semiconductors"),
    ("ORCL", "Oracle", "Technology", "Software"),
    ("CRM", "Salesforce", "Technology", "Software"),
    ("ADBE", "Adobe", "Technology", "Software"),
    ("AMD", "Advanced Micro Devices", "Technology", "Semiconductors"),
    ("CSCO", "Cisco Systems", "Technology", "Networking"),
    ("ACN", "Accenture", "Technology", "IT Services"),
    ("IBM", "IBM", "Technology", "IT Services"),
    ("INTC", "Intel", "Technology", "Semiconductors"),
    ("QCOM", "Qualcomm", "Technology", "Semiconductors"),
    ("TXN", "Texas Instruments", "Technology", "Semiconductors"),
    ("AMAT", "Applied Materials", "Technology", "Semiconductor Equipment"),
    ("MU", "Micron Technology", "Technology", "Semiconductors"),
    ("NOW", "ServiceNow", "Technology", "Software"),
    ("INTU", "Intuit", "Technology", "Software"),
    ("PANW", "Palo Alto Networks", "Technology", "Software"),
    ("ADI", "Analog Devices", "Technology", "Semiconductors"),
    ("LRCX", "Lam Research", "Technology", "Semiconductor Equipment"),
    ("KLAC", "KLA", "Technology", "Semiconductor Equipment"),
    ("GOOGL", "Alphabet", "Communication Services", "Interactive Media"),
    ("META", "Meta Platforms", "Communication Services", "Interactive Media"),
    ("NFLX", "Netflix", "Communication Services", "Entertainment"),
    ("DIS", "Walt Disney", "Communication Services", "Entertainment"),
    ("TMUS", "T-Mobile US", "Communication Services", "Telecom"),
    ("VZ", "Verizon", "Communication Services", "Telecom"),
    ("T", "AT&T", "Communication Services", "Telecom"),
    ("CMCSA", "Comcast", "Communication Services", "Media"),
    ("AMZN", "Amazon", "Consumer Discretionary", "Internet Retail"),
    ("TSLA", "Tesla", "Consumer Discretionary", "Automobiles"),
    ("HD", "Home Depot", "Consumer Discretionary", "Home Improvement Retail"),
    ("MCD", "McDonald's", "Consumer Discretionary", "Restaurants"),
    ("NKE", "Nike", "Consumer Discretionary", "Apparel"),
    ("LOW", "Lowe's", "Consumer Discretionary", "Home Improvement Retail"),
    ("SBUX", "Starbucks", "Consumer Discretionary", "Restaurants"),
    ("BKNG", "Booking Holdings", "Consumer Discretionary", "Travel"),
    ("TJX", "TJX Companies", "Consumer Discretionary", "Apparel Retail"),
    ("CMG", "Chipotle", "Consumer Discretionary", "Restaurants"),
    ("WMT", "Walmart", "Consumer Staples", "Retail"),
    ("PG", "Procter & Gamble", "Consumer Staples", "Household Products"),
    ("KO", "Coca-Cola", "Consumer Staples", "Beverages"),
    ("PEP", "PepsiCo", "Consumer Staples", "Beverages"),
    ("COST", "Costco", "Consumer Staples", "Retail"),
    ("PM", "Philip Morris", "Consumer Staples", "Tobacco"),
    ("MDLZ", "Mondelez", "Consumer Staples", "Food"),
    ("CL", "Colgate-Palmolive", "Consumer Staples", "Household Products"),
    ("MO", "Altria", "Consumer Staples", "Tobacco"),
    ("LLY", "Eli Lilly", "Health Care", "Pharmaceuticals"),
    ("UNH", "UnitedHealth", "Health Care", "Managed Care"),
    ("JNJ", "Johnson & Johnson", "Health Care", "Pharmaceuticals"),
    ("ABBV", "AbbVie", "Health Care", "Pharmaceuticals"),
    ("MRK", "Merck", "Health Care", "Pharmaceuticals"),
    ("PFE", "Pfizer", "Health Care", "Pharmaceuticals"),
    ("TMO", "Thermo Fisher", "Health Care", "Life Sciences Tools"),
    ("ABT", "Abbott Laboratories", "Health Care", "Medical Devices"),
    ("AMGN", "Amgen", "Health Care", "Biotechnology"),
    ("ISRG", "Intuitive Surgical", "Health Care", "Medical Devices"),
    ("GILD", "Gilead Sciences", "Health Care", "Biotechnology"),
    ("CVS", "CVS Health", "Health Care", "Health Care Services"),
    ("BMY", "Bristol-Myers Squibb", "Health Care", "Pharmaceuticals"),
    ("JPM", "JPMorgan Chase", "Financials", "Banks"),
    ("BAC", "Bank of America", "Financials", "Banks"),
    ("WFC", "Wells Fargo", "Financials", "Banks"),
    ("GS", "Goldman Sachs", "Financials", "Capital Markets"),
    ("MS", "Morgan Stanley", "Financials", "Capital Markets"),
    ("C", "Citigroup", "Financials", "Banks"),
    ("BLK", "BlackRock", "Financials", "Asset Management"),
    ("SCHW", "Charles Schwab", "Financials", "Capital Markets"),
    ("AXP", "American Express", "Financials", "Consumer Finance"),
    ("V", "Visa", "Financials", "Payments"),
    ("MA", "Mastercard", "Financials", "Payments"),
    ("PYPL", "PayPal", "Financials", "Payments"),
    ("SPGI", "S&P Global", "Financials", "Financial Data"),
    ("XOM", "Exxon Mobil", "Energy", "Integrated Oil & Gas"),
    ("CVX", "Chevron", "Energy", "Integrated Oil & Gas"),
    ("COP", "ConocoPhillips", "Energy", "E&P"),
    ("SLB", "SLB", "Energy", "Oil Services"),
    ("EOG", "EOG Resources", "Energy", "E&P"),
    ("OXY", "Occidental Petroleum", "Energy", "E&P"),
    ("MPC", "Marathon Petroleum", "Energy", "Refining"),
    ("CAT", "Caterpillar", "Industrials", "Machinery"),
    ("GE", "GE Aerospace", "Industrials", "Aerospace"),
    ("HON", "Honeywell", "Industrials", "Conglomerates"),
    ("UNP", "Union Pacific", "Industrials", "Railroads"),
    ("UPS", "United Parcel Service", "Industrials", "Logistics"),
    ("BA", "Boeing", "Industrials", "Aerospace"),
    ("LMT", "Lockheed Martin", "Industrials", "Defense"),
    ("RTX", "RTX", "Industrials", "Aerospace"),
    ("DE", "Deere", "Industrials", "Machinery"),
    ("MMM", "3M", "Industrials", "Conglomerates"),
    ("LIN", "Linde", "Materials", "Industrial Gases"),
    ("SHW", "Sherwin-Williams", "Materials", "Chemicals"),
    ("FCX", "Freeport-McMoRan", "Materials", "Copper"),
    ("NEM", "Newmont", "Materials", "Gold"),
    ("DOW", "Dow", "Materials", "Chemicals"),
    ("NEE", "NextEra Energy", "Utilities", "Electric Utilities"),
    ("DUK", "Duke Energy", "Utilities", "Electric Utilities"),
    ("SO", "Southern Company", "Utilities", "Electric Utilities"),
    ("D", "Dominion Energy", "Utilities", "Electric Utilities"),
    ("PLD", "Prologis", "Real Estate", "Industrial REIT"),
    ("AMT", "American Tower", "Real Estate", "Specialty REIT"),
    ("EQIX", "Equinix", "Real Estate", "Data Center REIT"),
    ("O", "Realty Income", "Real Estate", "Retail REIT"),
]

SECTOR_ETFS: dict[str, str] = {
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
}

BENCHMARK_ETFS: dict[str, str] = {
    "SPY": "S&P 500 ETF",
    "QQQ": "Nasdaq-100 ETF",
    "DIA": "Dow Jones ETF",
    "IWM": "Russell 2000 ETF",
    "TLT": "20+Y Treasury ETF",
    "HYG": "High Yield Bond ETF",
    "GLD": "Gold ETF",
    "USO": "Oil ETF",
    "UUP": "US Dollar ETF",
}

UNIVERSE: dict[str, dict] = {
    t: {"ticker": t, "name": n, "sector": s, "industry": i, "type": "stock"} for t, n, s, i in STOCKS
}
for _sec, _etf in SECTOR_ETFS.items():
    UNIVERSE.setdefault(_etf, {"ticker": _etf, "name": f"{_sec} Select Sector ETF", "sector": _sec, "industry": "ETF", "type": "etf"})
for _t, _n in BENCHMARK_ETFS.items():
    UNIVERSE.setdefault(_t, {"ticker": _t, "name": _n, "sector": "ETF", "industry": "ETF", "type": "etf"})

STOCK_TICKERS: list[str] = [t for t, *_ in STOCKS]


def info(ticker: str) -> dict:
    return UNIVERSE.get(ticker.upper(), {"ticker": ticker.upper(), "name": ticker.upper(), "sector": "Unknown", "industry": "Unknown", "type": "stock"})


def is_etf(ticker: str) -> bool:
    return UNIVERSE.get(ticker.upper(), {}).get("type") == "etf"
