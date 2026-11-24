# Published live forecasts (pre-registered)

`forecasts_2026-11-23.csv` holds the 636 forecasts the system logged at 23:42 UTC on 2026-11-23: 106 tickers × 6
horizons, based on the close of 2026-11-23. They come from model `ens-1.1` (logged then under the label `ens-1.0`),
trained 2026-11-23 23:38 UTC, with all ML weights 0. That makes them CAPM drift + GJR-GARCH Monte Carlo forecasts.

The file was committed to git before any of its target dates, so the commit timestamp shows the forecasts could not
have been adjusted after the outcomes were known. They are graded with the fixed procedure in
`scripts/score_forecasts.py`:

    python scripts/score_forecasts.py docs/live_forecasts/forecasts_2026-11-23.csv

Results are written next to this file as `score_2026-11-23_on_<date>.md`. Target dates: 1D → 2026-11-24, 7D → 2026-12-01,
30D → 2026-12-23, 90D → 2027-02-25, 6M → 2027-05-26, 12M → 2027-11-24.
