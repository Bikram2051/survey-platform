"""
Survey statistics core: raking and weighted estimation.

This module is why the platform exists. Free collection tools stop at raw
counts; the claims a serious buyer pays for are (a) weights that calibrate
the sample to known population margins and (b) uncertainty that does not
pretend the design was simple random sampling.

Raking (iterative proportional fitting): starting from design weights,
repeatedly rescale so that weighted category shares match target population
margins (e.g. census age x region), cycling over margin variables until
convergence. Weights start at the design weight (inverse inclusion
probability) when present, else 1.

Variance (MVP honesty note): standard errors use the Kish effective sample
size, n_eff = (sum w)^2 / sum w^2, i.e. SE = sqrt(p(1-p)/n_eff), with
DEFF = n / n_eff reported alongside. This correctly penalizes weight
variability but does NOT capture stratification gains or clustering losses;
full Taylor linearization over (stratum, cluster) is the documented next
step (architecture section 6). The API labels the method explicitly so no
consumer can mistake the approximation for the full design-based variance.
"""
from __future__ import annotations

import math


class RakingError(ValueError):
    pass


def rake(
    rows: list[dict],
    margins: dict[str, dict[str, float]],
    start_weights: list[float],
    max_iter: int = 100,
    tol: float = 1e-6,
) -> tuple[list[float], dict]:
    """
    rows: one dict per respondent mapping margin-variable -> category.
    margins: {"q_age": {"18_29": 0.32, ...}, "q_region": {...}} as
             population PROPORTIONS (each variable must sum to ~1).
    Returns (weights, diagnostics). Raises RakingError on impossible input.
    """
    n = len(rows)
    if n == 0:
        raise RakingError("no rows to calibrate")
    if len(start_weights) != n:
        raise RakingError("start_weights length mismatch")

    for var, targets in margins.items():
        s = sum(targets.values())
        if abs(s - 1.0) > 1e-6:
            raise RakingError(f"margins for {var!r} sum to {s}, expected 1.0")
        sample_cats = {r[var] for r in rows}
        for cat, share in targets.items():
            if share > 0 and cat not in sample_cats:
                raise RakingError(
                    f"target category {var}={cat!r} has population share {share} "
                    "but zero sample observations: cannot rake"
                )
        extra = sample_cats - set(targets)
        if extra:
            raise RakingError(f"sample has categories missing from margins for {var!r}: {sorted(extra)}")

    weights = list(start_weights)
    total = sum(weights)
    max_dev = float("inf")
    iterations = 0

    for iterations in range(1, max_iter + 1):
        for var, targets in margins.items():
            cat_weight: dict[str, float] = {}
            for r, w in zip(rows, weights):
                cat_weight[r[var]] = cat_weight.get(r[var], 0.0) + w
            factors = {cat: (targets[cat] * total) / cat_weight[cat] for cat in cat_weight}
            weights = [w * factors[r[var]] for r, w in zip(rows, weights)]

        # convergence: worst deviation of achieved share from target, over all margins
        max_dev = 0.0
        current_total = sum(weights)
        for var, targets in margins.items():
            cat_weight = {}
            for r, w in zip(rows, weights):
                cat_weight[r[var]] = cat_weight.get(r[var], 0.0) + w
            for cat, share in targets.items():
                achieved = cat_weight.get(cat, 0.0) / current_total
                max_dev = max(max_dev, abs(achieved - share))
        if max_dev < tol:
            break

    wmin, wmax = min(weights), max(weights)
    return weights, {
        "iterations": iterations,
        "converged": max_dev < tol,
        "max_margin_deviation": max_dev,
        "n": n,
        "weight_min": wmin,
        "weight_max": wmax,
        "weight_ratio": (wmax / wmin) if wmin > 0 else None,
    }


def kish_neff(weights: list[float]) -> float:
    sw = sum(weights)
    sw2 = sum(w * w for w in weights)
    return (sw * sw) / sw2 if sw2 > 0 else 0.0


def weighted_proportions(values: list, weights: list[float]) -> dict:
    """
    Weighted category proportions with Kish-approximate SEs and 95% CIs.
    Returns {"estimates": {...}, "n": ..., "n_eff": ..., "deff": ...}.
    """
    n = len(values)
    if n == 0:
        return {"estimates": {}, "n": 0, "n_eff": 0.0, "deff": None}
    total = sum(weights)
    n_eff = kish_neff(weights)
    by_cat: dict = {}
    for v, w in zip(values, weights):
        by_cat[v] = by_cat.get(v, 0.0) + w

    estimates = {}
    for cat, wsum in sorted(by_cat.items(), key=lambda kv: str(kv[0])):
        p = wsum / total
        se = math.sqrt(p * (1 - p) / n_eff) if n_eff > 0 else None
        ci = [max(0.0, p - 1.96 * se), min(1.0, p + 1.96 * se)] if se is not None else None
        estimates[str(cat)] = {"proportion": p, "se": se, "ci95": ci}

    return {
        "estimates": estimates,
        "n": n,
        "n_eff": n_eff,
        "deff": (n / n_eff) if n_eff > 0 else None,
        "se_method": "kish_neff_approx",
    }
