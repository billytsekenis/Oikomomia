"""Volatility modelling: GJR-GARCH(1,1) with Student-t innovations (arch package),
EWMA fallback, horizon variance term structure and filtered historical
simulation (FHS) Monte Carlo."""
from __future__ import annotations

import logging
import warnings

import numpy as np
import pandas as pd

log = logging.getLogger("oik.vol")


class VolModel:
    def __init__(self, omega: float, alpha: float, gamma: float, beta: float, nu: float, last_var: float,
                 std_resid: np.ndarray, kind: str, uncond_var: float):
        self.omega, self.alpha, self.gamma, self.beta, self.nu = omega, alpha, gamma, beta, nu
        self.last_var = last_var  # one-step-ahead variance for t+1 (decimal^2)
        self.std_resid = std_resid
        self.kind = kind
        self.uncond_var = uncond_var

    @property
    def persistence(self) -> float:
        return self.alpha + 0.5 * self.gamma + self.beta

    def term_variance(self, h: int) -> np.ndarray:
        """Expected daily variance for steps 1..h: v_j = vL + phi^(j-1) (v1 - vL)."""
        phi = self.persistence
        j = np.arange(h)
        return self.uncond_var + phi ** j * (self.last_var - self.uncond_var)

    def horizon_sigma(self, h: int) -> float:
        return horizon_sigma_from_var(self, self.last_var, h)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "omega": self.omega, "alpha": self.alpha, "gamma": self.gamma, "beta": self.beta,
                "nu": self.nu, "annualized_vol_now": float(np.sqrt(self.last_var * 252)),
                "annualized_vol_longrun": float(np.sqrt(self.uncond_var * 252)),
                "persistence": self.alpha + 0.5 * self.gamma + self.beta}


def _ewma(r: np.ndarray, lam: float = 0.94) -> VolModel:
    var = np.var(r[-252:]) if len(r) > 30 else 4e-4
    v = var
    vs = []
    for x in r[-500:]:
        vs.append(v)
        v = lam * v + (1 - lam) * x * x
    vs = np.array(vs)
    z = r[-500:] / np.sqrt(vs)
    long = float(np.var(r))
    # blend toward long-run variance (persistence 0.98) so long horizons mean-revert
    return VolModel(omega=long * 0.02, alpha=0.05, gamma=0.0, beta=0.93, nu=8.0, last_var=float(v),
                    std_resid=z[np.isfinite(z)], kind="EWMA(0.94) with mean reversion", uncond_var=long)


def fit(log_returns: pd.Series, max_obs: int = 1500) -> VolModel:
    r = log_returns.dropna().values[-max_obs:]
    if len(r) < 250:
        return _ewma(r)
    try:
        from arch import arch_model
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            am = arch_model(r * 100, mean="Constant", vol="GARCH", p=1, o=1, q=1, dist="t", rescale=False)
            res = am.fit(disp="off", show_warning=False, options={"maxiter": 200})
        p = res.params
        omega, alpha, gamma, beta, nu = (float(p["omega"]) / 1e4, float(p["alpha[1]"]), float(p["gamma[1]"]),
                                         float(p["beta[1]"]), float(p["nu"]))
        persist = alpha + 0.5 * gamma + beta
        if not (0 < persist < 0.9995) or omega <= 0 or not np.isfinite(nu):
            raise ValueError("non-stationary fit")
        fc = res.forecast(horizon=1, reindex=False)
        last_var = float(fc.variance.values[-1, 0]) / 1e4
        std = np.asarray(res.std_resid)
        std = std[np.isfinite(std)]
        return VolModel(omega, alpha, gamma, beta, nu, last_var, std, "GJR-GARCH(1,1)-t", omega / (1 - persist))
    except Exception as exc:  # noqa: BLE001 - fall back to EWMA on any fitting failure
        log.info("GARCH fit failed (%s); EWMA fallback", exc)
        return _ewma(r)


def filtered_variance_path(model: VolModel, log_returns: np.ndarray, init_var: float) -> np.ndarray:
    """One-step-ahead conditional variances for each date using FIXED params
    (used in walk-forward: params estimated on past data only)."""
    v = init_var
    out = np.empty(len(log_returns))
    mu = 0.0
    for i, x in enumerate(log_returns):
        out[i] = v  # variance forecast for day i made at i-1
        e = x - mu
        v = model.omega + (model.alpha + model.gamma * (e < 0)) * e * e + model.beta * v
        if not np.isfinite(v) or v <= 0:
            v = model.uncond_var
    return np.append(out[1:], v)  # out[i] = variance for day i+1 as known at close of i


def horizon_sigma_from_var(model: VolModel, next_var, h: int):
    """Closed-form sum of expected variances over h steps (works on arrays):
    sum_j v_j = h vL + (v1 - vL) (1 - phi^h) / (1 - phi)."""
    phi = model.persistence
    vl = model.uncond_var
    tot = h * vl + (np.asarray(next_var) - vl) * (1 - phi ** h) / (1 - phi)
    return np.sqrt(np.maximum(tot, 1e-12))


def simulate_paths(model: VolModel, n_paths: int, horizon: int, seed: int = 7) -> np.ndarray:
    """FHS Monte Carlo: bootstrap standardized residuals through the GJR-GARCH
    recursion. Returns cumulative log returns (n_paths x horizon), zero drift."""
    rng = np.random.default_rng(seed)
    z_pool = model.std_resid
    if len(z_pool) < 100:
        z_pool = rng.standard_t(max(model.nu, 4.1), 5000) / np.sqrt(max(model.nu, 4.1) / (max(model.nu, 4.1) - 2))
    z_pool = (z_pool - z_pool.mean()) / z_pool.std()
    v = np.full(n_paths, model.last_var)
    cum = np.zeros((n_paths, horizon))
    acc = np.zeros(n_paths)
    for t in range(horizon):
        z = z_pool[rng.integers(0, len(z_pool), n_paths)]
        e = np.sqrt(v) * z
        acc = acc + e
        cum[:, t] = acc
        v = model.omega + (model.alpha + model.gamma * (e < 0)) * e * e + model.beta * v
        v = np.clip(v, model.uncond_var * 0.05, model.uncond_var * 50)
    return cum
