"""Pre-registered scoring of published live forecasts.

Usage:  python scripts/score_forecasts.py docs/live_forecasts/forecasts_2026-11-23.csv

The forecast file was committed to git BEFORE its outcomes were known (the commit
timestamp is the proof). This script is the fixed grading procedure; it is not
changed after seeing results. For every forecast whose target session has closed it
fetches the official closing price from Nasdaq and computes:

  * direction hit rate   - predicted up if P(up) > 0.5; compared with the naive
                           "always up" rule and with a coin flip (50%)
  * Brier score          - mean (P(up) - outcome)^2; a coin flip (P = 0.5) scores 0.25
  * band coverage        - share of outcomes inside the 80% (q10-q90) and 90% (q05-q95)
                           ranges; well-calibrated bands should hit about 80% / 90%
  * mean absolute error  - of the median forecast

Returns are price returns (close / close - 1). A dividend going ex inside the window
lowers the measured return by the dividend; with ~0.5% quarterly yields this is small.
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.analytics.market_hours import last_completed_session  # noqa: E402
from app.data import nasdaq  # noqa: E402
from app.data.http import ProviderError  # noqa: E402


def official_close(ticker: str, d: date) -> float | None:
    try:
        df = nasdaq.historical(ticker, d - timedelta(days=7), d + timedelta(days=1))
        if d in df.index:
            return float(df.loc[d, "close"])
    except ProviderError:
        pass
    try:  # Nasdaq history publishes the latest session late: fall back to the session's official close
        b = nasdaq.session_bar(ticker)
        if b["date"] == d:
            return float(b["close"])
    except ProviderError:
        pass
    return None


def main(path: str) -> None:
    rows = list(csv.DictReader(open(path)))
    last = last_completed_session()
    due = [r for r in rows if date.fromisoformat(r["target_date"]) <= last]
    closes: dict[tuple[str, date], float | None] = {}
    by_h: dict[str, list[dict]] = defaultdict(list)
    missing = []
    for r in due:
        key = (r["ticker"], date.fromisoformat(r["target_date"]))
        if key not in closes:
            closes[key] = official_close(*key)
        c = closes[key]
        if c is None:
            missing.append(key)
            continue
        ret = c / float(r["price_at_prediction"]) - 1
        p = float(r["p_up"])
        by_h[r["horizon"]].append({
            "ticker": r["ticker"], "ret": ret, "p": p, "up": ret > 0,
            "hit": (ret > 0) == (p > 0.5), "in80": float(r["q10"]) <= ret <= float(r["q90"]),
            "in90": float(r["q05"]) <= ret <= float(r["q95"]), "err": abs(ret - float(r["median_return"])),
            "brier": (p - (1.0 if ret > 0 else 0.0)) ** 2})
    out = [f"# Live forecast score - {Path(path).name}", "",
           f"Scored on {date.today()} (last completed session {last}). Forecasts due: {len(due)} of {len(rows)}; "
           f"missing closes: {len(missing)}.", ""]
    if not by_h:
        out.append("No forecast has reached its target date yet.")
    for h, xs in sorted(by_h.items(), key=lambda kv: len(kv[0])):
        n = len(xs)
        up_rate = sum(x["up"] for x in xs) / n
        out += [f"## {h} (n = {n})", "",
                "| Metric | Model | Baseline |", "|---|---|---|",
                f"| Direction hit rate | {sum(x['hit'] for x in xs) / n:.1%} | always-up {up_rate:.1%}, coin flip 50.0% |",
                f"| Brier score (lower is better) | {sum(x['brier'] for x in xs) / n:.4f} | coin flip 0.2500 |",
                f"| Inside 80% range | {sum(x['in80'] for x in xs) / n:.1%} | target 80% |",
                f"| Inside 90% range | {sum(x['in90'] for x in xs) / n:.1%} | target 90% |",
                f"| Mean abs. error of median | {sum(x['err'] for x in xs) / n:.2%} | - |", ""]
        worst = sorted(xs, key=lambda x: -x["err"])[:5]
        out.append("Largest misses: " + ", ".join(f"{x['ticker']} {x['ret']:+.1%}" for x in worst))
        out.append("")
        if n < 100:
            out.append(f"Note: {n} forecasts made on a single day share one market move, so this is ONE observation "
                       "of the market, not {n} independent tests. Conclusions need many days.".replace("{n}", str(n)))
            out.append("")
    text = "\n".join(out)
    print(text)
    dest = Path(path).with_name(f"score_{Path(path).stem.replace('forecasts_', '')}_on_{last}.md")
    dest.write_text(text)
    print(f"\nwritten to {dest}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs/live_forecasts/forecasts_2026-11-23.csv")
