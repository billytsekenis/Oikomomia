"""Live probabilistic forecasts for one ticker across all horizons."""
from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd

from ..config import get_settings
from ..data import fundamentals_store as fs
from ..data import market
from . import model_engine
from . import volatility as volm
from .context import finite, get_market_context, load_model, model_metrics, stable_seed
from .features import ALL_FEATURES, HORIZON_LABELS, HORIZONS, latest_feature_row
from .indicators import support_resistance
from .market_hours import add_trading_days

log = logging.getLogger("oik.forecast")
QUANTILES = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)


def _skill_label(m: dict | None) -> tuple[str, str]:
    if not m:
        return "unvalidated", "No walk-forward validation available yet for this horizon (model not trained)."
    bss = m.get("brier_skill_score") or 0
    ic_t = ((m.get("models") or {}).get("ensemble") or {}).get("ic_tstat_overlap_adj") or 0
    if bss > 0.005 and ic_t > 2:
        return "small-but-detectable", m.get("verdict", "")
    if bss > 0 or ic_t > 2:
        return "weak", m.get("verdict", "")
    return "none-demonstrated", m.get("verdict", "")


def forecast(ticker: str, n_paths: int | None = None, include_paths: bool = True) -> dict:
    ticker = ticker.upper()
    n_paths = n_paths or get_settings().mc_paths
    df = market.bars(ticker)
    prov = market.provenance(df)
    if len(df) < 60:
        return {"ticker": ticker, "available": False, "reason": f"Only {len(df)} sessions of history - insufficient for any forecast.",
                "provenance": prov}
    ctx = get_market_context()
    mf = ctx["mf"]
    cf = fs.company_facts_for(ticker) if not ticker.startswith("^") else None
    lr = np.log(df["adj_close"]).diff().dropna()
    vm = volm.fit(lr)
    art = load_model()
    metrics_all = (model_metrics() or {}).get("metrics", {})
    feat = latest_feature_row(df, mf, cf, art.get("reference") if art else None)
    beta = finite(feat["beta126"].iloc[0]) if "beta126" in feat else None
    # beta126 was replaced by its rank; recompute raw beta for the prior
    m_ret = mf["mkt_ret"].reindex(df.index)
    raw = pd.concat([lr, m_ret], axis=1).dropna().tail(126)
    beta_raw = float(raw.iloc[:, 0].cov(raw.iloc[:, 1]) / raw.iloc[:, 1].var()) if len(raw) > 60 else 1.0
    rf = finite(mf["rf"].dropna().iloc[-1]) if mf["rf"].notna().any() else 0.0
    last_price = float(df["close"].iloc[-1])
    as_of = df.index[-1]
    ml_ok = art is not None and len(df) >= 300 and feat[ALL_FEATURES].notna().sum(axis=1).iloc[0] >= 0.7 * len(ALL_FEATURES)
    max_h = max(HORIZONS.values())
    paths = volm.simulate_paths(vm, n_paths, max_h, seed=stable_seed(ticker, as_of))
    sr = support_resistance(df)
    horizons = []
    mu_curve = {}
    for h, n in HORIZONS.items():
        sigma_h = float(vm.horizon_sigma(n))
        prior = float(model_engine.prior_mu(np.array([rf]), np.array([beta_raw]), np.array([sigma_h]), n)[0])
        comp = {"prior_capm": prior}
        w = 0.0
        mu = prior
        if ml_ok and h in art["final_models"]:
            X = feat[ALL_FEATURES].astype(float).values
            r = float(art["final_models"][h]["ridge"].predict(X)[0])
            g = float(art["final_models"][h]["gbm"].predict(X)[0])
            comp["ridge"] = prior + r
            comp["gbm"] = prior + g
            w = float(art["weights"].get(h, 0.0))
            mu = prior + w * 0.5 * (r + g)
        mu_curve[n] = mu
        x = paths[:, n - 1]
        x = x - x.mean() + mu
        sim = np.exp(x) - 1
        qs = np.quantile(sim, QUANTILES)
        p_up = float(np.mean(x > 0))
        upside = float(np.mean(np.maximum(sim, 0)))
        downside = float(np.mean(np.maximum(-sim, 0)))
        cvar5 = float(sim[sim <= qs[0]].mean())
        path_seg = paths[:, :n] - paths[:, :n].mean(axis=0) + np.linspace(mu / n, mu, n)
        dd = np.exp(path_seg - np.maximum.accumulate(np.concatenate([np.zeros((n_paths, 1)), path_seg], axis=1)[:, 1:], axis=1)) - 1
        p_dd10 = float(np.mean(dd.min(axis=1) <= -0.10))
        m = metrics_all.get(h)
        skill, verdict = _skill_label(m)
        target = add_trading_days(as_of, n)
        comp_vals = [v for v in comp.values()]
        horizons.append({
            "horizon": h, "label": HORIZON_LABELS[h], "sessions": n, "target_date": target.isoformat(),
            "expected_return": float(sim.mean()), "median_return": float(qs[3]),
            "expected_log_return": mu, "sigma": sigma_h,
            "p_up": p_up, "p_down": 1 - p_up,
            "price_range_80": [last_price * (1 + qs[1]), last_price * (1 + qs[5])],
            "price_range_90": [last_price * (1 + qs[0]), last_price * (1 + qs[6])],
            "quantiles": {f"q{int(q * 100):02d}": float(v) for q, v in zip(QUANTILES, qs)},
            "scenarios": {
                "bull": {"return": float(qs[5]), "price": last_price * (1 + qs[5]), "definition": "90th percentile of simulated outcomes"},
                "base": {"return": float(qs[3]), "price": last_price * (1 + qs[3]), "definition": "median simulated outcome"},
                "bear": {"return": float(qs[1]), "price": last_price * (1 + qs[1]), "definition": "10th percentile of simulated outcomes"},
            },
            "potential_downside": {"var95": float(qs[0]), "cvar95": cvar5, "p_drawdown_10pct": p_dd10},
            "risk_reward": {"omega_ratio": upside / downside if downside > 0 else None,
                            "expected_upside": upside, "expected_downside": downside},
            "uncertainty": {"interval_90_width": float(qs[6] - qs[0]),
                            "model_disagreement": float(np.std(comp_vals)) if len(comp_vals) > 1 else None},
            "components": comp, "ml_weight": w,
            "skill": skill, "skill_detail": verdict,
            "validation": {k: m.get(k) for k in ("brier_skill_score", "coverage_80", "coverage_90", "base_rate_up", "n", "start", "end")} if m else None,
            "invalidation": _invalidation(h, n, last_price, qs, sr, vm, sigma_h),
        })
    # fan chart: quantiles of the price path for every 1..max_h step (subsampled)
    fan = []
    if include_paths:
        ns = sorted(mu_curve)
        steps = list(range(1, 6)) + list(range(10, max_h + 1, 5))
        for s in steps:
            mu_s = float(np.interp(s, [0] + ns, [0.0] + [mu_curve[k] for k in ns]))
            x = paths[:, s - 1] - paths[:, s - 1].mean() + mu_s
            q = np.quantile(np.exp(x), (0.05, 0.25, 0.5, 0.75, 0.95)) * last_price
            fan.append({"step": s, "date": add_trading_days(as_of, s).isoformat(), "p05": q[0], "p25": q[1], "p50": q[2],
                        "p75": q[3], "p95": q[4]})
    # histogram for the 30D horizon (probability distribution chart)
    dist = {}
    for h in ("7D", "30D", "90D", "12M"):
        n = HORIZONS[h]
        x = paths[:, n - 1] - paths[:, n - 1].mean() + mu_curve[n]
        sim = np.exp(x) - 1
        counts, edges = np.histogram(sim, bins=40)
        dist[h] = [{"ret": float((edges[i] + edges[i + 1]) / 2), "p": float(counts[i] / n_paths)} for i in range(len(counts))]
    notes = [
        "Probabilities come from a Monte Carlo filtered-historical simulation of the GJR-GARCH volatility model "
        f"({n_paths} paths), centred on the ensemble expected log return.",
        "News sentiment is NOT used in these numbers: no point-in-time news archive exists to validate it.",
        "The CAPM prior assumes a 5% equity risk premium; ML components only get weight in proportion to their "
        "demonstrated out-of-sample skill (forecast-encompassing weight).",
    ]
    if not ml_ok:
        notes.append("ML components unavailable (model not trained yet, too little history, or too many missing "
                     "features); forecasts use the CAPM prior + GARCH volatility only.")
    if prov.get("demo"):
        notes.insert(0, "DEMO MODE: synthetic data - these forecasts are meaningless for real markets.")
    return {"ticker": ticker, "available": True, "as_of": as_of.isoformat(), "price": last_price,
            "model_version": (art or {}).get("version", model_engine.MODEL_VERSION) + ("" if ml_ok else "-prior-only"),
            "model_trained_at": (art or {}).get("trained_at"), "beta": beta_raw, "risk_free": rf,
            "volatility_model": vm.to_dict(), "horizons": horizons, "fan": fan, "distributions": dist,
            "notes": notes, "provenance": prov}


def _invalidation(h: str, n: int, price: float, qs: np.ndarray, sr: dict, vm: volm.VolModel, sigma_h: float) -> list[str]:
    out = []
    lo = price * (1 + qs[0])
    out.append(f"Close below ${lo:,.2f} (5th percentile of the {h} distribution) - outcome outside the modelled range; "
               "the volatility/drift assumptions should be considered broken.")
    daily = sigma_h / np.sqrt(n)
    out.append(f"Realised 10-session volatility above {2 * daily * np.sqrt(252) * 100:.0f}% annualised (2x the model's "
               "forecast) - regime shift; re-run the forecast.")
    if sr.get("support"):
        s = sr["support"][0]["price"]
        out.append(f"Decisive close below nearest support ${s:,.2f} - technical structure assumed by the momentum "
                   "features no longer holds.")
    out.append("A scheduled earnings release, guidance change or macro shock inside the horizon introduces information "
               "the model has not seen; treat the forecast as expired after such an event.")
    return out


def compact(fc: dict) -> dict:
    """Small summary used by discovery & the analyst."""
    if not fc.get("available"):
        return {"available": False, "reason": fc.get("reason")}
    return {"available": True, "as_of": fc["as_of"], "price": fc["price"], "model_version": fc["model_version"],
            "horizons": {h["horizon"]: {k: h[k] for k in ("expected_return", "median_return", "p_up", "sigma", "skill",
                                                             "target_date", "ml_weight")}
                         | {"range_80": h["price_range_80"], "var95": h["potential_downside"]["var95"],
                            "omega": h["risk_reward"]["omega_ratio"]} for h in fc["horizons"]}}
