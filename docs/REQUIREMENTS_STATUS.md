# Requirements status

Status legend: **Tested** = implemented and verified by automated tests and/or the end-to-end run against real data ·
**Implemented** = works in the running app, verified manually or by API smoke tests, no dedicated automated test ·
**Partial** = works with a stated limitation · **Blocked** = not possible in this environment (reason given).

Evidence: `backend/tests` (89 pytest tests, offline), `e2e/flows.js` (22 Playwright journeys, desktop + iPhone 13, real
data, cold start: **22/22 passed**), `e2e/mobile_overflow.js` (10/10 pages without horizontal overflow at 390 px),
screenshots in `docs/screenshots/`. All numbers below are from real data fetched on 2026-11-23.

## 2. Market Intelligence Dashboard
| Requirement | Status | Evidence / notes |
|---|---|---|
| S&P 500, Nasdaq, Dow | Tested | `/api/market/overview`; e2e "dashboard renders". S&P 500 level from Yahoo; its history falls back to a labelled SPY proxy when Yahoo rate-limits. |
| VIX & volatility indices | Tested | Cboe VIX, VIX9D, VIX3M (official CSV); date-aligned term structure in the regime signals. |
| Rates, inflation, economic indicators | Partial | Treasury curve (official) works. FRED (CPI, unemployment, etc.) is flaky without `FRED_API_KEY` from this host; failures are shown as *unavailable*, never estimated. |
| Market sentiment | Tested | Risk Appetite Index (6 components, percentile method); `test_regime.py`. |
| Sector heatmap | Tested | 11 SPDR ETFs × 6 periods + 105-stock heatmap; e2e "sector period filter". |
| Unusual activity | Implemented | Relative volume ≥ 2× or ≥ 3σ moves (`/api/market/movers`). |
| Biggest gainers / losers | Tested | e2e "movers tabs". |
| Economic calendar | Implemented | Nasdaq economic events (US filter). |
| Earnings calendar | Implemented | Nasdaq earnings calendar, 14 days, universe filter. |
| Market state bullish / bearish / neutral | Tested | Weighted signal score; today: *bearish*. |
| Market regime estimate | Tested | Rules + 2-state Markov-switching model (P(high-vol) 99.4% on 11/23); a silent failure was caught and fixed, with a regression test added. |

## 3. Stock Discovery Engine
| Requirement | Status | Evidence / notes |
|---|---|---|
| Undervalued, growth, momentum, breakout, oversold, reversal, earnings, high-risk/high-reward, overvalued/risk | Tested | 9 categories + composite (`scoring.py`); e2e "discover filters". |
| 0-100 score with documented methodology | Implemented | Universe percentiles with fixed weights, shown in the UI ("Methodology" card). |
| Filters: horizon, risk, sector, market cap, expected return | Tested | All five used in the e2e journey and in the API. |
| Forecast factor only when validated | Implemented | The composite gives the forecast weight 0 when walk-forward shows no skill (currently all horizons). |

## 4. Stock Analysis Page
| Requirement | Status | Evidence / notes |
|---|---|---|
| Interactive candlestick chart, history 1M-max | Tested | lightweight-charts; e2e "stock chart tab". |
| Volume analysis | Implemented | Volume pane, 30-day volume bars, relative volume. |
| RSI, MACD, Bollinger, EMA, SMA, ATR | Tested | `test_indicators.py` checks against independent reference implementations (Wilder RSI/ATR loops, definitions). |
| Support / resistance | Tested | Swing-pivot clustering; side test. |
| Revenue, earnings, EBITDA, FCF, debt | Tested | SEC XBRL; YTD differencing, 16-week quarters and point-in-time are tested (`test_fundamentals.py`); verified on Apple (quarters sum to FY revenue 478.834B). |
| P/E, forward P/E, PEG, EV/EBITDA, … | Tested | `snapshot()` ratios tested; forward EPS from Nasdaq consensus. |
| Fundamental & technical analysis | Implemented | Fundamentals tab, technical read card. |
| News sentiment | Partial | Lexicon headline scoring (transparent but simple); excluded from forecasts because it cannot be backtested. |
| Catalysts | Implemented | Next earnings, past earnings reactions (SEC 8-K 2.02), 8-K list, estimate revisions. |
| Competitor analysis | Tested | Same-sector peers + sector medians; e2e "peers tab". |
| Institutional activity | Partial | Nasdaq 13F aggregation + SEC Form 4 counts; Form 4 transactions are not parsed. |
| Expectations priced in | Tested | Reverse DCF (round-trip test) with sensitivity grid and analyst target. |

## 5. Prediction Engine
| Requirement | Status | Evidence / notes |
|---|---|---|
| Statistical / time-series / regression / ML / volatility / Monte Carlo / ensemble | Tested | CAPM prior, Ridge, gradient boosting, GJR-GARCH-t, filtered historical simulation, significance-gated encompassing ensemble. |
| Fundamental, macro factors | Implemented | Point-in-time SEC features (filing dates); VIX, curve slope, rate change, market momentum. |
| Sentiment factor | Partial | Deliberately excluded from forecasts: there is no point-in-time news archive to validate it. Stated in the UI. |
| Horizons 1D / 7D / 30D / 90D / 6M / 12M | Tested | API test checks all 6 and range ordering. |
| Expected return, ranges, P(up/down), bull/base/bear, downside, uncertainty, risk/reward, invalidation | Tested | Forecast tab and API test. |
| Fan chart & distributions | Tested | e2e "stock forecast tab". |
| No arbitrary probabilities | Tested | Probabilities come from the validated model; every horizon shows its validated skill ("no demonstrated skill" today), and the ML weight is 0 unless significant. |

## 6. AI Financial Analyst
| Requirement | Status | Evidence / notes |
|---|---|---|
| Conversational analyst on real platform data | Tested | Deterministic evidence engine (EN + EL); e2e "analyst conversation". |
| Example questions (best opportunity, why it moved, recovery, risk-adjusted, pros & cons, change of view, Nasdaq −10% on my portfolio) | Tested | Intents covered in `test_adversarial.py` and e2e (including the Greek portfolio-stress question). |
| No invented numbers | Tested | Evidence-only templates; the LLM layer is rejected on any unsupported number (tests both reject and accept cases). |
| Independent judgement, challenges false premises | Tested | 25 adversarial tests. |
| LLM (Claude) layer | Partial | Implemented with server-side refusal fallbacks and a numeric guard, but no `ANTHROPIC_API_KEY` was available, so it was tested with injected fake model responses only. |

## 7. Portfolio Intelligence
| Requirement | Status | Evidence / notes |
|---|---|---|
| Buys/sells, average price, realized/unrealized P&L | Tested | Hand-computed test incl. fees and FX; also verified on a real EUR portfolio (AAPL partial sale). |
| Allocation, diversification, correlation, beta, Sharpe, max drawdown, VaR, concentration, currency exposure | Tested | `test_portfolio.py` (VaR, effective N, flags, perf stats, TWR). |
| Stress testing | Implemented | 5 historical windows + factor shocks + custom shock with 90% bands. |
| Benchmark comparison | Implemented | TWR vs benchmark, plus counterfactual "same flows into SPY". |
| Decision simulation | Tested | What-if endpoint; e2e "portfolio analytics + what-if". |
| USD & EUR, fees, FX differences | Tested | ECB trade-date fixings; exact price/FX P&L decomposition test. |

### Revolut (added after delivery)
| Requirement | Status | Evidence / notes |
|---|---|---|
| Import Revolut stocks statement (CSV) incl. dividends, fees, splits, FX | Tested | `test_revolut.py` (parser, FX direction, splits, dedupe, API preview/commit); `e2e/revolut.js` on an emulated iPhone: 7 imported, 2 cash rows skipped, NVDA 10 shares after a 10:1 split. **Partial caveat:** the CSV layout is not officially documented; fixtures follow the commonly seen layout and must be confirmed with a real export. |
| Revolut fee model for paper trading | Tested | Allowance → max(0.25%, €1) test; presets per plan; FX fee is a user setting (not publicly confirmed). |

## 8. Backtesting Engine
| Requirement | Status | Evidence / notes |
|---|---|---|
| Historical, walk-forward, out-of-sample | Tested | Expanding walk-forward, ~7 years OOS, ~210k predictions. |
| Costs, slippage, benchmarks | Tested | Exact cost-drag test; equal-weight universe + SPY benchmarks. |
| Directional accuracy, error, calibration, profit factor, Sharpe, max DD, cumulative returns | Tested | Model-validation and strategy views; e2e "backtest". |
| No look-ahead / leakage | Tested | Feature-future-mutation test; purge/embargo; target definition; one-day execution lag test. |
| Survivorship bias | Partial | Cannot be removed with free data (no point-in-time constituents). Disclosed everywhere; mitigated with the same-universe benchmark and the forward-only track record. |
| No overfitting / cherry-picking | Tested | Fixed hyper-parameters; noise-data test (no spurious ML weight); immutable run log; every year reported. Two post-hoc rules are disclosed in the README. |
| Prediction archive & evaluation at horizon end | Tested | `test_tracking.py`; 636 live predictions logged on 2026-11-23. |
| Track Record page, failures kept | Tested | e2e "track record page"; no delete path exists. |

## 9. Paper Trading
| Requirement | Status | Evidence / notes |
|---|---|---|
| Market, limit, stop, stop-limit; SL/TP; pending orders | Tested | `test_paper.py` (fills, queueing, limit improvement, bracket OCO with gap-through stop, DAY expiry). |
| Market hours | Tested | NYSE calendar test (2026 holidays, early closes). |
| Bid/ask spread, execution simulation | Tested | Live Nasdaq bid/ask or a Corwin-Schultz spread; square-root slippage. |
| History & performance analytics | Implemented | Trades, equity snapshots, win rate, profit factor. |
| System strategy self-evaluation | Implemented | Weekly system paper account; runs only in market hours, so not yet observed filling during this session. |

## 10. Alerts & Watchlists
| Requirement | Status | Evidence / notes |
|---|---|---|
| Watchlists | Tested | API + e2e. |
| Price / technical / earnings / volume / news / prediction-change / risk alerts | Tested (price) / Implemented (others) | 12 alert kinds; price alerts tested end-to-end; WebSocket push to the bell menu. |

## 11. Data Architecture
| Requirement | Status | Evidence / notes |
|---|---|---|
| Source research (accuracy, cost, limits, freshness, history, licensing, real-time) | Implemented | `Data & System` page, README table. |
| Caching, retries, error handling, validation | Tested | DB + memory cache with stale fallback, backoff, 429 circuit breaker, OHLC validation test. |
| Secure API keys | Implemented | Env-only; the UI sees booleans. |
| Source + timestamp on every price | Implemented | Quotes, series, fundamentals and news all carry provenance. |
| Demo mode, clearly labelled | Tested | All API tests run in demo mode; banner + `DEMO` source labels. |

## 12-13. UI/UX & technical implementation
| Requirement | Status | Evidence / notes |
|---|---|---|
| Dark/light, sidebar, command palette, global search, skeletons, tooltips, heatmaps, responsive | Tested | e2e (palette search, theme toggle, mobile drawer); overflow check 10/10. |
| Every control works | Tested (main journeys) | 22 journeys; not every single button is individually automated. |
| PWA (Add to Home Screen) | Implemented | Manifest + icons; not tested on a physical iPhone. |
| FastAPI, React/TS, PostgreSQL-ready, migrations, background jobs, WebSockets, auth | Tested | Alembic migration at start-up; user isolation test; clean-room install from `requirements.txt` passed. |
| Deployment | Partial | Dockerfile + Render Blueprint written; **the Docker image could not be built here (no Docker daemon in this environment)**. Its steps were reproduced in a clean room (fresh venv + `vite build` + start) successfully. |

## 14. Adversarial Intelligence
| Requirement | Status | Evidence / notes |
|---|---|---|
| Confirmation bias, authority bias, fabricated data, misleading assumptions, guaranteed returns, false precision, contradictory evidence, hallucinations, prompt injection via news | Tested | `test_adversarial.py` (25 tests). It found and fixed two real bugs: a too-loose numeric guard, and a leak of injected headline text into the LLM prompt. |
| Revises on new verifiable data, not insistence | Tested | `test_view_changes_when_verifiable_data_changes`, `test_insistence_without_new_data_does_not_change_view`. |

## Blocked / outside this environment
- **Live Claude narration**: no API key was available. The code path is implemented and tested with fakes.
- **Docker build**: no Docker daemon here. Equivalent clean-room steps pass.
- **Hosting**: deploying needs your Render (or other host) account, so it can't be done from this session. See the README steps.
- **Long-run track-record statistics**: these need weeks of real time. Logging and evaluation are tested; the record starts 2026-11-23.
