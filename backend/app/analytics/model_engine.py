"""Prediction engine training & walk-forward validation.

Model stack per horizon h (log-return forecasts):
  1. PRIOR  : CAPM drift  mu = h/252 (rf + beta * ERP) - sigma_h^2 / 2
  2. RIDGE  : ridge regression on rank-transformed features -> residual over prior
  3. GBM    : histogram gradient boosting (shallow, heavily regularised) -> residual
  4. ML     : average of RIDGE and GBM residual forecasts
  5. ENSEMBLE: mu = prior + w * ML, where w in [0, 1] is the forecast-encompassing
     slope estimated ONLY on earlier out-of-sample predictions. If ML has shown
     no out-of-sample skill, w -> 0 and the system falls back to the prior.
  sigma_h from walk-forward GJR-GARCH (parameters re-estimated yearly on past data).

Leakage controls:
  * expanding-window walk-forward, refit every 26 weekly steps (~6 months)
  * purging: a training row at t is used only if t + h + embargo < test start
  * winsorisation bounds computed on the training fold only
  * hyper-parameters fixed a priori (no tuning on test data)
  * fundamentals enter only after their SEC filing date
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import pickle
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
from scipy.stats import norm, spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import volatility as volm
from .features import ALL_FEATURES, HORIZONS, STOCK_FEATURES, build_panel, cross_sectional_rank

log = logging.getLogger("oik.model")

MODEL_VERSION = "ens-1.1"  # 1.1 = significance-gated ensemble weights (see encompassing_weight)
ERP = 0.05
EMBARGO = 5
REFIT_EVERY = 26          # weekly steps (~6 months)
MIN_TRAIN_YEARS = 3
MIN_W_ROWS = 300
HYPERPARAMS = {
    "ridge": {"alpha": 50.0},
    "gbm": {"max_iter": 150, "learning_rate": 0.05, "max_depth": 3, "min_samples_leaf": 300,
            "l2_regularization": 1.0, "random_state": 0},
    "step_sessions": 5, "refit_every_steps": REFIT_EVERY, "embargo_sessions": EMBARGO, "erp": ERP,
    "winsor": [0.01, 0.99],
}


MIN_CAL_ROWS = 1000
# Calibration needs enough *independent* outcomes: the calibration history must span at least
# 8 non-overlapping horizon periods (e.g. 8 years for 12M). NOTE (disclosed): this rule was added
# after a first run showed unstable 12M calibration fitted on ~3 years of overlapping outcomes.
MIN_CAL_PERIODS = 8


def fit_platt(p: np.ndarray, up: np.ndarray) -> tuple[float, float]:
    """Platt scaling on the logit of the raw probability: P = sigmoid(a + b*logit(p))."""
    from sklearn.linear_model import LogisticRegression
    x = np.log(np.clip(p, 1e-4, 1 - 1e-4) / (1 - np.clip(p, 1e-4, 1 - 1e-4))).reshape(-1, 1)
    lr = LogisticRegression(C=1e4).fit(x, up)
    return float(lr.intercept_[0]), float(lr.coef_[0, 0])


def apply_platt(cal: tuple[float, float] | None, p):
    if cal is None:
        return p
    a, b = cal
    p = np.clip(np.asarray(p, dtype=float), 1e-4, 1 - 1e-4)
    z = a + b * np.log(p / (1 - p))
    return 1 / (1 + np.exp(-z))


MIN_W_TSTAT = 2.0


def encompassing_weight(x: np.ndarray, e: np.ndarray, dates: np.ndarray, h_sessions: int) -> float:
    """Forecast-encompassing weight of the ML residual forecast x for the prior's error e:
    slope of e on x (no intercept), clipped to [0, 1]. The weight is set to 0 unless the slope
    is statistically significant: standard errors are clustered by prediction date (stocks on
    the same date are correlated) and inflated for overlapping horizons (h / 5-session step).
    NOTE (disclosed): the significance requirement was added after inspecting a 12M forecast
    in which an insignificant positive slope (IC t = 1.3) received weight 0.69."""
    ok = np.isfinite(x) & np.isfinite(e)
    x, e, dates = x[ok], e[ok], dates[ok]
    if len(x) < MIN_W_ROWS:
        return 0.0
    sxx = float(np.dot(x, x))
    if sxx <= 0:
        return 0.0
    b = float(np.dot(x, e)) / sxx
    resid = e - b * x
    g = pd.DataFrame({"d": dates, "s": x * resid}).groupby("d")["s"].sum().values
    var = float(np.sum(g ** 2)) / sxx ** 2 * max(1.0, h_sessions / 5.0)
    t = b / np.sqrt(var) if var > 0 else 0.0
    if t < MIN_W_TSTAT:
        return 0.0
    return float(np.clip(b, 0.0, 1.0))


def prepare_X(X: np.ndarray, dead: list[int]) -> np.ndarray:
    """Features that were entirely unavailable in training (e.g. VIX feed down) are set to a
    constant 0 - identically at training and inference - so they carry no information."""
    X = np.array(X, dtype=float, copy=True)
    if dead:
        X[:, dead] = 0.0
    return X


def make_ridge():
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=HYPERPARAMS["ridge"]["alpha"]))


def make_gbm():
    return HistGradientBoostingRegressor(**HYPERPARAMS["gbm"])


def prior_mu(rf: np.ndarray, beta: np.ndarray, sigma_h: np.ndarray, h: int) -> np.ndarray:
    rf = np.nan_to_num(rf, nan=0.0)
    b = np.clip(np.nan_to_num(beta, nan=1.0), 0.2, 2.5)
    return h / 252 * (rf + b * ERP) - 0.5 * sigma_h ** 2


# --------------------------------------------------------------- volatility WF
def walkforward_sigma(bars: dict[str, pd.DataFrame], panel: pd.DataFrame) -> pd.DataFrame:
    """For each panel row, horizon sigmas from GARCH params estimated on data
    strictly before the start of that calendar year."""
    out = []
    for t, rows in panel.groupby("ticker"):
        df = bars[t]
        lr = np.log(df["adj_close"]).diff().dropna()
        years = sorted({d.year for d in rows["date"]})
        sig = {h: np.full(len(rows), np.nan) for h in HORIZONS}
        rows = rows.sort_values("date")
        for y in years:
            hist = lr[[d.year < y for d in lr.index]]
            if len(hist) < 250:
                continue
            model = volm.fit(hist)
            seg = lr[[d.year >= y - 1 for d in lr.index]]
            init_idx = np.searchsorted(np.array(seg.index), date(y, 1, 1))
            path = volm.filtered_variance_path(model, seg.values, float(np.var(hist.values[-63:])))
            var_by_date = pd.Series(path, index=seg.index)
            mask = np.array([d.year == y for d in rows["date"]])
            dates = rows["date"].values[mask]
            nv = var_by_date.reindex(dates).values
            for h, n in HORIZONS.items():
                sig[h][mask] = volm.horizon_sigma_from_var(model, nv, n)
            _ = init_idx
        frame = pd.DataFrame({"date": rows["date"].values, "ticker": t, **{f"sigma_{h}": sig[h] for h in HORIZONS}})
        out.append(frame)
    return pd.concat(out) if out else pd.DataFrame()


# ----------------------------------------------------------------- evaluation
def _calibration(p: np.ndarray, y_up: np.ndarray, bins: int = 10) -> list[dict]:
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    edges = np.unique(edges)
    out = []
    for lo, hi in zip(edges, edges[1:]):
        m = (p >= lo) & (p <= hi) if hi == edges[-1] else (p >= lo) & (p < hi)
        if m.sum() < 20:
            continue
        out.append({"p_lo": float(lo), "p_hi": float(hi), "mean_p": float(p[m].mean()),
                    "realized": float(y_up[m].mean()), "n": int(m.sum())})
    return out


def evaluate(df: pd.DataFrame, h: str, n_days: int) -> dict:
    """Out-of-sample metrics for every model column in df."""
    y = df["y"].values
    up = (y > 0).astype(float)
    res: dict = {"n": int(len(df)), "n_dates": int(df["date"].nunique()),
                 "start": str(df["date"].min()), "end": str(df["date"].max()), "base_rate_up": float(up.mean())}
    always_up_acc = float(max(up.mean(), 1 - up.mean()))
    res["always_majority_accuracy"] = always_up_acc
    sse_zero = float(np.sum(y ** 2))
    sse_prior = float(np.sum((y - df["prior"].values) ** 2))
    models = {}
    for col in ("prior", "ridge", "gbm", "ml", "ensemble"):
        pred = df[col].values
        sse = float(np.sum((y - pred) ** 2))
        ic_vals = []
        for _, g in df.groupby("date"):
            if len(g) >= 10 and g[col].std() > 0:
                ic_vals.append(spearmanr(g[col], g["y"]).statistic)
        ic_vals = np.array([v for v in ic_vals if np.isfinite(v)])
        overlap = max(1.0, n_days / 5.0)
        ic_t = float(ic_vals.mean() / ic_vals.std() * np.sqrt(len(ic_vals) / overlap)) if len(ic_vals) > 2 and ic_vals.std() > 0 else None
        models[col] = {
            "directional_accuracy": float(np.mean(np.sign(pred) == np.sign(y))),
            "mae": float(np.mean(np.abs(y - pred))), "rmse": float(np.sqrt(sse / len(y))),
            "r2_vs_zero": 1 - sse / sse_zero if sse_zero > 0 else None,
            "r2_vs_prior": 1 - sse / sse_prior if sse_prior > 0 else None,
            "ic_mean": float(ic_vals.mean()) if len(ic_vals) else None, "ic_tstat_overlap_adj": ic_t,
        }
    # probabilistic evaluation of the ensemble distribution
    p = df["p_up"].values
    brier = float(np.mean((p - up) ** 2))
    clim = df["clim_up"].values
    brier_clim = float(np.mean((clim - up) ** 2))
    mu, sg = df["ensemble"].values, df["sigma"].values
    z = (y - mu) / sg
    res.update({
        "models": models,
        "brier": brier, "brier_climatology": brier_clim,
        "brier_skill_score": 1 - brier / brier_clim if brier_clim > 0 else None,
        "calibration": _calibration(p, up),
        "coverage_80": float(np.mean(np.abs(z) <= norm.ppf(0.9))),
        "coverage_90": float(np.mean(np.abs(z) <= norm.ppf(0.95))),
        "mean_abs_return": float(np.mean(np.abs(y))),
    })
    # long-short top-minus-bottom quintile of ensemble forecast (per date, h-day return)
    spreads = []
    for _, g in df.groupby("date"):
        if len(g) >= 20:
            q = g["ensemble"].rank(pct=True)
            spreads.append(g.loc[q >= 0.8, "y"].mean() - g.loc[q <= 0.2, "y"].mean())
    spreads = np.array(spreads)
    res["quintile_spread_mean"] = float(spreads.mean()) if len(spreads) else None
    res["quintile_spread_hit_rate"] = float((spreads > 0).mean()) if len(spreads) else None
    # per-year stability (no cherry-picking: every year is reported)
    yearly = []
    df_y = df.assign(year=[d.year for d in df["date"]])
    for yr, g in df_y.groupby("year"):
        acc = float(np.mean(np.sign(g["ensemble"]) == np.sign(g["y"])))
        icv = [spearmanr(gg["ensemble"], gg["y"]).statistic for _, gg in g.groupby("date") if len(gg) >= 10 and gg["ensemble"].std() > 0]
        icv = [v for v in icv if np.isfinite(v)]
        yearly.append({"year": int(yr), "n": int(len(g)), "accuracy": acc, "base_rate_up": float((g["y"] > 0).mean()),
                       "ic_mean": float(np.mean(icv)) if icv else None,
                       "brier": float(np.mean((g["p_up"] - (g["y"] > 0)) ** 2)),
                       "mean_weight_ml": float(g["w"].mean())})
    res["by_year"] = yearly
    res["ml_weight_mean"] = float(df["w"].mean())
    # verdict
    bss = res["brier_skill_score"] or 0
    ic_t = models["ensemble"]["ic_tstat_overlap_adj"] or 0
    if bss > 0.005 and ic_t > 2:
        verdict = "Statistically detectable out-of-sample skill (small)."
    elif ic_t > 2 or bss > 0:
        verdict = "Weak / inconsistent out-of-sample skill - treat forecasts as low-confidence."
    else:
        verdict = "No demonstrated out-of-sample skill beyond base rates - forecasts are essentially volatility-scaled priors."
    res["verdict"] = verdict
    return res


# -------------------------------------------------------------- walk-forward
@dataclass
class TrainResult:
    version: str
    trained_at: str
    data_end: str
    tickers: list[str]
    hyperparams: dict
    metrics: dict = field(default_factory=dict)
    weights: dict = field(default_factory=dict)
    oos: pd.DataFrame | None = None
    final_models: dict = field(default_factory=dict)
    calibrators: dict = field(default_factory=dict)
    dead_features: list = field(default_factory=list)
    reference: pd.DataFrame | None = None
    seconds: float = 0.0


def _winsor(y: np.ndarray) -> tuple[float, float]:
    lo, hi = HYPERPARAMS["winsor"]
    return float(np.nanquantile(y, lo)), float(np.nanquantile(y, hi))


def run_walkforward(bars: dict[str, pd.DataFrame], mf: pd.DataFrame, facts: dict[str, dict | None],
                    progress=None) -> TrainResult:
    t0 = time.time()
    step = HYPERPARAMS["step_sessions"]
    panel = build_panel(bars, mf, facts, step=step)
    if panel.empty:
        raise ValueError("not enough data to train")
    raw_beta = panel["beta126"].values.copy()
    panel["beta_raw"] = raw_beta
    ranked = cross_sectional_rank(panel, STOCK_FEATURES)
    sig = walkforward_sigma(bars, panel)
    ranked = ranked.merge(sig, on=["date", "ticker"], how="left")
    tpos = {d: i for i, d in enumerate(mf.index)}
    ranked["tidx"] = ranked["date"].map(tpos)
    ranked = ranked.dropna(subset=["tidx"]).sort_values(["date", "ticker"]).reset_index(drop=True)
    dates = np.array(sorted(ranked["date"].unique()))
    first_test = mf.index[0].replace(year=mf.index[0].year + MIN_TRAIN_YEARS)
    test_dates = dates[dates >= first_test]
    fold_starts = test_dates[::REFIT_EVERY]
    X_all = ranked[ALL_FEATURES].astype(float).values
    dead = [i for i in range(X_all.shape[1]) if not np.isfinite(X_all[:, i]).any()]
    X_all = prepare_X(X_all, dead)
    result = TrainResult(MODEL_VERSION, datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
                         str(mf.index[-1]), sorted(bars), HYPERPARAMS)
    result.dead_features = [ALL_FEATURES[i] for i in dead]
    oos_frames = []
    n_steps = len(HORIZONS) * (len(fold_starts) + 1)
    done = 0
    last_tidx = len(mf.index) - 1
    for h, n in HORIZONS.items():
        y_all = ranked[f"y_{h}"].values
        sigma = ranked[f"sigma_{h}"].values
        sigma = np.where(np.isfinite(sigma), sigma, np.nanmedian(sigma))
        prior = prior_mu(ranked["rf"].values, ranked["beta_raw"].values, sigma, n)
        resid = y_all - prior
        tidx = ranked["tidx"].values
        preds = []
        for k, fs in enumerate(fold_starts):
            fe = fold_starts[k + 1] if k + 1 < len(fold_starts) else None
            fs_idx = tpos[fs]
            train = (tidx + n + EMBARGO < fs_idx) & np.isfinite(resid)
            test = (ranked["date"].values >= fs) & ((ranked["date"].values < fe) if fe is not None else True)
            if train.sum() < 2000 or test.sum() == 0:
                done += 1
                continue
            lo, hi = _winsor(resid[train])
            yt = np.clip(resid[train], lo, hi)
            r = make_ridge().fit(X_all[train], yt)
            g = make_gbm().fit(X_all[train], yt)
            pr, pg = r.predict(X_all[test]), g.predict(X_all[test])
            # encompassing weight from EARLIER oos rows whose targets were realised before fs
            w = 0.0
            if preds:
                prev = pd.concat(preds)
                prev = prev[(prev["tidx"] + n < fs_idx) & np.isfinite(prev["y"])]
                w = encompassing_weight(prev["ml"].values, (prev["y"] - prev["prior"]).values, prev["date"].values, n)
            clim = float(np.mean(y_all[train] > 0))
            ens_t = prior[test] + w * 0.5 * (pr + pg)
            p_raw_t = 1 - norm.cdf(-ens_t / sigma[test])
            cal = None
            if preds:
                prev = pd.concat(preds)
                prev = prev[(prev["tidx"] + n < fs_idx) & np.isfinite(prev["y"])]
                span = fs_idx - (prev["tidx"].min() if len(prev) else fs_idx)
                if len(prev) >= MIN_CAL_ROWS and span >= MIN_CAL_PERIODS * n:
                    cal = fit_platt(prev["p_raw"].values, (prev["y"].values > 0).astype(int))
            frame = pd.DataFrame({
                "date": ranked["date"].values[test], "ticker": ranked["ticker"].values[test], "tidx": tidx[test],
                "y": y_all[test], "prior": prior[test], "ridge_r": pr, "gbm_r": pg, "sigma": sigma[test],
                "w": w, "clim_up": clim, "p_raw": p_raw_t,
                "p_cal": apply_platt(cal, p_raw_t) if cal is not None else p_raw_t,
            })
            frame["ml"] = 0.5 * (frame["ridge_r"] + frame["gbm_r"])
            preds.append(frame)
            done += 1
            if progress:
                progress(done / n_steps, f"{h}: fold {k + 1}/{len(fold_starts)}")
        if not preds:
            continue
        oos = pd.concat(preds)
        oos["ridge"] = oos["prior"] + oos["ridge_r"]
        oos["gbm"] = oos["prior"] + oos["gbm_r"]
        oos["ml"] = oos["prior"] + oos["ml"]
        oos["ensemble"] = oos["prior"] + oos["w"] * (oos["ml"] - oos["prior"])
        oos["p_up"] = oos["p_cal"]
        oos["horizon"] = h
        realized = oos[np.isfinite(oos["y"])]
        ml_ic_t = None
        if len(realized) > 100:
            result.metrics[h] = evaluate(realized, h, n)
            ml_ic_t = result.metrics[h]["models"]["ml"]["ic_tstat_overlap_adj"]
            up = (realized["y"].values > 0).astype(float)
            b_raw = float(np.mean((realized["p_raw"].values - up) ** 2))
            result.metrics[h]["brier_uncalibrated"] = b_raw
            result.metrics[h]["brier_skill_uncalibrated"] = 1 - b_raw / result.metrics[h]["brier_climatology"]
            result.metrics[h]["calibration_uncalibrated"] = _calibration(realized["p_raw"].values, up)
        span_all = (realized["tidx"].max() - realized["tidx"].min()) if len(realized) else 0
        if len(realized) >= MIN_CAL_ROWS and span_all >= MIN_CAL_PERIODS * n:
            result.calibrators[h] = fit_platt(realized["p_raw"].values, (realized["y"].values > 0).astype(int))
        # final weight on all realised OOS rows
        w_final = encompassing_weight((realized["ml"] - realized["prior"]).values,
                                      (realized["y"] - realized["prior"]).values, realized["date"].values, n)
        # Live weight additionally requires the ML component's own out-of-sample rank IC to be
        # significant (overlap-adjusted t > 2), consistent with the published skill verdicts.
        result.weights[h] = w_final if (ml_ic_t is not None and ml_ic_t > MIN_W_TSTAT) else 0.0
        oos_frames.append(oos.drop(columns=["ridge_r", "gbm_r", "p_cal"]))
        # final models on all rows with realised targets (purged at the end of data)
        train = (tidx + n + EMBARGO <= last_tidx) & np.isfinite(resid)
        lo, hi = _winsor(resid[train])
        yt = np.clip(resid[train], lo, hi)
        result.final_models[h] = {"ridge": make_ridge().fit(X_all[train], yt), "gbm": make_gbm().fit(X_all[train], yt)}
        done += 1
        if progress:
            progress(done / n_steps, f"{h}: final fit")
    result.oos = pd.concat(oos_frames) if oos_frames else None
    # reference cross-section (raw features at the last date of each ticker) for live ranking
    from .features import stock_features
    ref_rows = []
    for t, df in bars.items():
        if len(df) >= 260:
            ref_rows.append(stock_features(df.tail(400), mf, facts.get(t)).iloc[-1])
    result.reference = pd.DataFrame(ref_rows)[STOCK_FEATURES] if ref_rows else None
    result.seconds = time.time() - t0
    return result


# ---------------------------------------------------------- serialisation
def pack(obj) -> str:
    return base64.b64encode(gzip.compress(pickle.dumps(obj, protocol=4))).decode()


def unpack(s: str):
    return pickle.loads(gzip.decompress(base64.b64decode(s)))  # noqa: S301 - our own artefacts only


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()
