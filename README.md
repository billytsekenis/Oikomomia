# Oikonomia Terminal

A personal stock-market intelligence terminal: market dashboard, multi-factor stock discovery, per-stock deep analysis,
a probabilistic prediction engine that is **validated out-of-sample**, an evidence-bound AI analyst, portfolio
intelligence (EUR/USD), paper trading, alerts and a live prediction track record.

> **Honest headline result.** On ~7 years of walk-forward, out-of-sample testing over 105 US large caps, the prediction
> engine shows **no demonstrated directional skill** at any horizon (1 day - 12 months). Its uncertainty bands are well
> calibrated (80%/90% intervals contain 81%/90% of outcomes). Its top-10 ranking strategy did **not** beat simply holding
> the equal-weighted universe. The app shows this on every forecast, and the composite discovery score gives the forecast
> factor zero weight until validation shows skill. See [Validation results](#validation-results).

Nothing here is investment advice, and no real orders are ever placed.

---

## Deploy from an iPhone (no computer needed)

The app is one Docker service (FastAPI API + background scheduler + the built React app). The simplest host you can drive
from Safari is **Render**:

1. Open <https://dashboard.render.com> in Safari and sign in with GitHub.
2. Tap **New → Blueprint**, pick this repository (`billytsekenis/oikomomia`) and the branch you want to deploy.
3. Render reads `render.yaml`. Fill in the prompted values:
   - `SEC_USER_AGENT`: **required by the SEC**, e.g. `Vasilis Example vasilis@example.com`.
   - `ANTHROPIC_API_KEY` (optional): enables Claude narration in the AI analyst.
   - `FRED_API_KEY` (optional, free at fred.stlouisfed.org): makes the macro panel reliable.
4. Tap **Apply**. The first build takes ~5-8 minutes. Open the `*.onrender.com` URL.
5. The **first account you register becomes yours**. Afterwards set `ALLOW_REGISTRATION=false` in the service's
   *Environment* tab so nobody else can sign up.
6. In Safari tap **Share → Add to Home Screen**: the app installs as a full-screen PWA.

On first start the server downloads ~10 years of prices for ~125 symbols plus SEC filings, then trains and
walk-forward-validates the model. This takes about 3-5 min on a normal CPU (longer on the smallest plans). The dashboard
works immediately; the Discover and Forecast ML components appear when training finishes (see *Data & System → Background jobs*).

**Plans.** `render.yaml` uses the *standard* plan (2 GB RAM) with a 1 GB persistent disk, so SQLite and model artefacts
survive restarts. Training was measured at a ~450 MB peak, which is too close to the 512 MB *starter* limit. For *starter*,
set `TRAIN_MAX_TICKERS=50`.
The *free* plan also works, but it has no persistent disk and sleeps after 15 minutes idle. In that case, attach a free
PostgreSQL database (e.g. Neon or Supabase) by setting `DATABASE_URL=postgresql://…` and removing the `disk:` block. The
jobs are catch-up jobs, so a sleeping host simply catches up when it wakes.

Any Docker host works the same way (Fly.io, Railway, Koyeb, a VPS): build the root `Dockerfile` and expose port `8000`
(or `$PORT`).

## Run locally

```bash
# backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
cd backend && uvicorn app.main:app --port 8000        # migrations run automatically at start-up

# frontend (dev server with hot reload, proxies /api to :8000)
cd frontend && npm ci && npm run dev                   # http://localhost:5173
# or build the SPA into backend/static and use http://localhost:8000
cd frontend && npm run build
```

Demo mode without any network access: `DEMO_MODE=true uvicorn app.main:app` (every value is synthetic and labelled, and a
banner is shown on every page).

Tests: `cd backend && python -m pytest -q` (81 tests, offline).

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | SQLite in `DATA_DIR` | PostgreSQL URL for production (`postgresql://…` accepted) |
| `DATA_DIR` | `backend/data` | SQLite DB, model artefacts, generated secret |
| `SECRET_KEY` | generated & persisted | JWT signing key |
| `SEC_USER_AGENT` | placeholder | **Required by SEC EDGAR**: "Name email" |
| `ALLOW_REGISTRATION` | `true` | Set `false` after creating your account |
| `COOKIE_SECURE` | `false` | `true` behind HTTPS |
| `DEMO_MODE` | `false` | Synthetic, clearly labelled data |
| `ANTHROPIC_API_KEY` | – | Optional Claude narration for the analyst (`ANTHROPIC_MODEL`, default `claude-opus-5-5`) |
| `FRED_API_KEY` | – | Optional official FRED API (the keyless CSV endpoint is unreliable from some hosts) |
| `ENABLE_SCHEDULER` | `true` | Background jobs |
| `TRAIN_MAX_TICKERS` | `120` | Universe size for training (lower = faster on tiny hosts) |
| `TRACK_RECORD_TICKERS` | universe | Comma list of tickers to log daily forecasts for |
| `MC_PATHS` | `4000` | Monte Carlo paths per forecast |

API keys are only read server-side. The UI only ever sees whether each one is set.

## Architecture

```
frontend/ (React 18 + TypeScript + Vite + Tailwind)          backend/ (Python 3.13, FastAPI)
  pages: Dashboard, Discover, Stock, Analyst, Portfolio,        app/data/       providers, cache, validation, provenance
         Paper, Backtest, Track Record, Watchlists, System      app/analytics/  indicators, fundamentals, valuation, scoring,
  charts: lightweight-charts (candles), Recharts                                regime, features, volatility, model_engine,
  command palette (cmdk, ⌘K or /), dark/light, PWA                              forecast, backtest, portfolio, paper, alerts,
                                                                                tracking, market_hours
           ── REST /api/* + WebSocket /ws ──►                    app/analyst/    deterministic engine, guard, optional LLM
                                                                 app/api/        routers;  app/jobs.py  APScheduler
                                                                 alembic/        migrations;  tests/  81 tests
```

Design decisions:
- **One service, no extra infrastructure.** SQLite by default (WAL mode), PostgreSQL via `DATABASE_URL`. Daily bars live in
  a `price_bars` table keyed by (ticker, date), which is ample for ~300k rows and needs no separate time-series DB.
- **In-process scheduler** instead of Celery/Redis: jobs are idempotent catch-up jobs (log forecasts, evaluate due
  predictions, process paper orders every minute in market hours, check alerts every 5 min, rebuild the scan, retrain weekly).
- **WebSocket** pushes triggered alerts to the browser; everything else is plain REST with client-side caching.
- **Auth**: scrypt password hashes and JWT in an httpOnly cookie (plus a bearer token). All data endpoints require
  login, and every portfolio, paper account, watchlist and alert is scoped to its user.

## Data sources

Every price shows its source and timestamp. Every series records its adjustment method. Simulated data appears only in
demo mode, with a banner.

| Source | Used for | Cost / limits | Notes |
|---|---|---|---|
| Nasdaq public API | Daily OHLCV (10y), real-time last sale + bid/ask, dividends, analyst EPS consensus, earnings surprises, institutional ownership, earnings & economic calendars | Free, keyless, unofficial; throttled to 4 req/s | Personal use. Daily history lags one session, so the app builds a **provisional** bar for the last completed session from the quote and intraday data, flags it, and replaces it with the official bar later. |
| Yahoo Finance chart API | S&P 500 / Dow / Russell index levels; total-return series for NYSE ETFs; fallback prices | Free, unofficial, aggressive 429s on datacenter IPs | 15-min cool-down after a 429. If unavailable, index levels fall back to **labelled ETF proxies** (SPY/DIA/IWM). |
| SEC EDGAR | XBRL statements with **filing dates** (point-in-time), filings, 8-K item 2.02 earnings dates, Form 4 counts, ticker/CIK map | Free, official, 10 req/s, needs `SEC_USER_AGENT` | Year-to-date cash-flow facts are differenced into quarters; 12/16-week retail quarters are handled; values are "as first reported". |
| U.S. Treasury | Daily par yield curve 1M-30Y | Free, official | Risk-free rate, curve slope |
| Cboe | VIX, VIX9D, VIX3M history | Free, official | Term structure |
| FRED | CPI, core CPI/PCE, unemployment, payrolls, Fed funds, HY OAS, sentiment, GDP | Free | Keyless CSV is flaky from some hosts. Missing series are shown as *unavailable*, never estimated. |
| ECB (via Frankfurter) | EUR/USD fixings | Free, official | Trade-date FX for portfolio accounting |
| Yahoo / Google News RSS | Headlines | Free | Sanitised and screened for prompt injection |

Evaluated but not used (need paid keys or too limited for 100+ tickers): Polygon/Massive, Alpha Vantage, FMP, Finnhub,
Tiingo, Stooq (timeouts).

**Dividend adjustment.** Nasdaq prices are split-adjusted, but its dividends are not, and it only has dividend history for
Nasdaq-listed symbols. The platform therefore:
- infers splits from integer drops in the dividend series, accepted only when SEC share counts confirm them;
- uses SEC dividends-per-share for NYSE stocks (ex-dates estimated);
- labels any series where dividends could not be included (e.g. NYSE ETFs when Yahoo is unavailable).

## Methodology

**Prediction engine** (`app/analytics/model_engine.py`, `forecast.py`), horizons 1D / 7D (5 sessions) / 30D (21) / 90D (63) / 6M (126) / 12M (252):
1. **Prior**: CAPM drift `h/252·(rf + β·5%) − σ²/2`, with rf the 3-month T-bill rate.
2. **Volatility**: GJR-GARCH(1,1) with Student-t innovations (`arch`), with an EWMA fallback and closed-form horizon variance.
3. **ML**: Ridge regression and histogram gradient boosting on 27 point-in-time features: momentum, volatility, RSI,
   distance to SMAs, Bollinger, volume, 52-week range, beta, idiosyncratic volatility, SEC-filed fundamentals (revenue
   growth, margin, ROE, used only after their filing date), and market features (SPY momentum/volatility, VIX level and
   z-score, curve slope, rate change). Stock features are ranked cross-sectionally; hyper-parameters are fixed a priori.
4. **Ensemble**: `prior + w·(ML − prior)`. The weight `w ∈ [0,1]` is a forecast-encompassing slope estimated **only on
   earlier out-of-sample predictions**. It is set to 0 unless the slope is significant: date-clustered standard errors,
   deflated for overlapping horizons, with t > 2. The live weight additionally requires the ML component's own
   out-of-sample rank IC to be significant. *(Disclosure: both significance gates were added after inspecting a 12M NVDA
   forecast in which an insignificant slope (IC t 1.3) had received weight 0.69.)*
5. **Distribution**: Monte Carlo filtered historical simulation (bootstrapped GARCH residuals). This produces expected and
   median return, P(up/down), 80/90% price ranges, bull/base/bear cases (P90/P50/P10), VaR/CVaR, drawdown probability,
   Omega ratio, model-disagreement uncertainty and concrete invalidation conditions.
6. **Calibration**: Platt scaling fitted on earlier out-of-sample results. It is applied only when that history spans ≥ 8
   non-overlapping horizon periods. *(Disclosure: this rule was added after the first run showed unstable 12M calibration.)*

**Walk-forward protocol**:
- expanding window, re-fit every ~6 months;
- purging + 5-session embargo, so a training row is used only if its target ended before the fold start;
- winsorisation fitted on training data only;
- every horizon and every year is reported.

News sentiment is shown but **not** used in forecasts, because there is no point-in-time archive to validate it.

**Discovery scores** (`scoring.py`): factor scores are universe percentiles (value, growth, quality, momentum, low-risk,
technical, forecast). Category scores (undervalued, growth, momentum, breakout, oversold, reversal, earnings,
high-risk/high-reward, overvalued/risk) use fixed weights, documented in the UI. Missing inputs are re-weighted away,
never imputed.

**Backtesting** (`backtest.py`):
- Strategies: top-N long, long/short quintiles, or P(up) threshold, built on out-of-sample predictions only.
- Execution at the next session's close, with costs and slippage on every weight change and cash earning the T-bill rate.
- Benchmarks: the equal-weighted universe (same data and adjustment basis) and SPY.
- Metrics: CAGR, Sharpe, Sortino, max drawdown, profit factor, hit rate, information ratio, beta, turnover, cost drag.
- Every run is stored immutably and listed, so results cannot be cherry-picked.

**Track record** (`tracking.py`): forecasts for the universe are logged after each close, with timestamp, last bar used,
target date and model version, unique per (ticker, as_of, horizon, version). When the horizon ends they are scored on
direction, interval coverage, Brier score and error. Failures are never removed.

**Portfolio** (`portfolio.py`):
- Accounting: weighted-average cost; fees capitalised on buys and deducted on sells. P&L is split exactly into a price
  effect and an FX effect: `q(P1X1−P0X0) = q(P1−P0)X0 + qP1(X1−X0)`. FX uses the ECB fixing on the trade date.
- Performance: time-weighted return, benchmark comparison, Sharpe/Sortino, max drawdown.
- Risk: historical and parametric VaR/CVaR, beta, correlation matrix, HHI / effective N, diversification ratio,
  concentration flags.
- Stress tests: historical windows (2018 Q4, 2020, 2022, 2024, 2025) and factor shocks with 90% bands.
- Scenarios: a what-if simulator, and a counterfactual "same cash flows into the benchmark".

**Paper trading** (`paper.py`):
- Order types: market, limit, stop and stop-limit; DAY/GTC; bracket stop-loss/take-profit as an OCO pair.
- Market hours: the NYSE calendar, including holidays and early closes.
- Pricing: live bid/ask or a modelled Corwin-Schultz spread, square-root-impact slippage, per-share commissions.
- Constraints: long-only with a cash check.
- A **system account** paper-trades the platform's own weekly top-10 rule, evaluating it forward.

**AI analyst** (`app/analyst/`):
- **Deterministic evidence engine.** Every number comes from the platform's engines. Intents are recognised in English
  and Greek.
- **Bias detectors.** Flags confirmation and authority bias, guarantees, false precision, absolute assumptions and
  insistence without new data.
- **Claim checking.** Verifies numeric claims (P/E, price, growth, market cap, yield) against SEC and market data.
- **Prompt-injection screening.** Flagged news never reaches the analyst or the LLM prompt.
- **Optional Claude layer.** It rephrases the answer, and any reply containing a number that is not in the evidence is
  rejected.

## Validation results

From the walk-forward run on real data, 105 stocks, test window 2019-11 → 2026-11:

| Horizon | OOS predictions | Base rate up | Ensemble accuracy | Ensemble IC (t) | ML-only IC (t) | Brier skill | 80% / 90% coverage | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1D | 36,729 | 53.2% | 51.8% | −0.016 (−1.4) | +0.036 (2.6) | +0.000 | 82.6% / 90.0% | no demonstrated skill |
| 7D | 36,624 | 54.3% | 51.8% | −0.011 (−1.0) | −0.002 (−0.1) | −0.001 | 81.0% / 89.0% | no demonstrated skill |
| 30D | 36,309 | 57.2% | 52.6% | −0.024 (−1.3) | +0.017 (0.7) | −0.017 | 80.9% / 89.6% | no demonstrated skill |
| 90D | 35,469 | 59.5% | 54.2% | −0.043 (−1.4) | +0.019 (0.4) | −0.045 | 82.4% / 90.8% | no demonstrated skill |
| 6M | 34,104 | 65.3% | 59.1% | −0.016 (−0.4) | +0.050 (0.9) | −0.071 | 81.7% / 90.5% | no demonstrated skill |
| 12M | 31,479 | 69.8% | 64.5% | +0.042 (0.6) | +0.063 (0.6) | −0.092 | 80.5% / 90.3% | no demonstrated skill |

The ML component's 1-day cross-sectional ranking (IC 0.036, t 2.6) is the only statistically detectable signal, but its
encompassing slope is not significant, so it gets no weight. Every **live** ML weight is currently 0, which means live
forecasts are the CAPM drift plus the (well-calibrated) GARCH distribution. Directional accuracy below the base rate at long
horizons reflects the conservative CAPM drift in an unusually strong 2019-2026 market for this survivorship-biased universe.

The strategy backtest (top-10 by 30D expected return/σ, monthly rebalance, 10 bp costs + slippage) returned 12.4% CAGR
(Sharpe 0.53), against 17.6% (Sharpe 0.78) for the equal-weighted universe and 12.9% for SPY (price-only). A long/short
quintile spread lost 10.5% a year. The numbers update
whenever the model is retrained (weekly), and the live track record accumulates forward-only evidence.

**Known limitations**:
- **Survivorship bias**: the universe is today's large caps.
- **Unofficial sources**: Nasdaq and Yahoo are personal-use sources.
- **Free-data gaps**: there is no consolidated (SIP) real-time feed and no options data, and some issuers' XBRL is
  incomplete (e.g. XOM's latest annual revenue facts are missing from SEC companyfacts, so its TTM metrics show as n/a).
- **FRED access**: the keyless FRED endpoint is flaky without a key.
- **Institutional data**: holdings are Nasdaq's aggregation of 13F filings; Form 4 is counted but not parsed per
  transaction.
- **Sentiment**: headline lexicon scoring only.

## Repository layout

```
backend/app/        FastAPI app (data, analytics, analyst, api, jobs)
backend/alembic/    database migrations (applied automatically at start-up)
backend/tests/      pytest suite, 81 tests (calculations, leakage, paper engine, backtest, tracking, API, adversarial evals)
frontend/           React/TypeScript SPA (built into backend/static)
Dockerfile          multi-stage build (Node -> Python)
render.yaml         one-tap Render Blueprint
docs/               screenshots and the requirements status report
```
