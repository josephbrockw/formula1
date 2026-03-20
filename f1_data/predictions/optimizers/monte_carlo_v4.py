from __future__ import annotations

import numpy as np
import pandas as pd
from django.conf import settings

from predictions.optimizers.base import Lineup, LineupOptimizer
from predictions.optimizers.greedy_v2 import GreedyOptimizerV2

# ── swap this to ILPOptimizer() for higher-quality (but slower) MC scenarios ──
_DEFAULT_INNER_OPTIMIZER: LineupOptimizer = GreedyOptimizerV2()


class MonteCarloOptimizer:
    """
    Monte Carlo lineup optimizer (v4).

    For each of N scenarios, samples each driver/constructor's score from a
    Triangular(lower=q10, mode=predicted_mean, upper=q90) distribution, runs an
    inner optimizer (default: GreedyV2) on those sampled points, then evaluates
    all distinct candidate lineups against every scenario. Returns the lineup
    with the highest average score across all scenarios.

    Why Triangular? We know three things per driver: q10 (worst case), predicted
    mean (best guess), q90 (best case). Triangular uses all three, is asymmetric
    (like real F1 scoring), and has hard bounds. Normal has infinite tails; Uniform
    ignores the mean.

    Fallback: when confidence bounds are missing or all identical (e.g. v1 predictor),
    the distribution degenerates to a point mass. We detect this and delegate directly
    to ILP on the mean predictions.
    """

    def __init__(
        self,
        inner_optimizer: LineupOptimizer | None = None,
        n_scenarios: int | None = None,
        seed: int | None = None,
    ) -> None:
        self._inner = inner_optimizer if inner_optimizer is not None else _DEFAULT_INNER_OPTIMIZER
        self._n = n_scenarios if n_scenarios is not None else settings.MC_N_SCENARIOS
        self._rng = np.random.default_rng(seed)

    def optimize_single_race(
        self,
        driver_predictions: pd.DataFrame,
        constructor_predictions: pd.DataFrame,
        budget: float,
        constraints: dict | None = None,
    ) -> Lineup:
        # No confidence bounds → single deterministic solve with ILP
        if _needs_fallback(driver_predictions):
            from predictions.optimizers.ilp_v3 import ILPOptimizer
            return ILPOptimizer().optimize_single_race(
                driver_predictions, constructor_predictions, budget, constraints
            )

        # Extract triangular params as arrays for vectorised sampling
        driver_ids = driver_predictions["driver_id"].astype(int).tolist()
        d_lower = driver_predictions["confidence_lower"].to_numpy(dtype=float)
        d_upper = driver_predictions["confidence_upper"].to_numpy(dtype=float)
        d_mode = np.clip(
            driver_predictions["predicted_fantasy_points"].to_numpy(dtype=float),
            d_lower, d_upper,
        )

        constructor_ids = constructor_predictions["team_id"].astype(int).tolist()
        if (
            "confidence_lower" in constructor_predictions.columns
            and "confidence_upper" in constructor_predictions.columns
        ):
            c_lower = constructor_predictions["confidence_lower"].to_numpy(dtype=float)
            c_upper = constructor_predictions["confidence_upper"].to_numpy(dtype=float)
        else:
            c_lower = constructor_predictions["predicted_fantasy_points"].to_numpy(dtype=float)
            c_upper = c_lower.copy()
        c_mode = np.clip(
            constructor_predictions["predicted_fantasy_points"].to_numpy(dtype=float),
            c_lower, c_upper,
        )

        # Run N scenarios: sample → inner solve → collect unique candidate lineups
        all_d_pts: list[dict[int, float]] = []
        all_c_pts: list[dict[int, float]] = []
        seen: dict[tuple[frozenset, frozenset], Lineup] = {}

        for _ in range(self._n):
            sampled_d = _sample_triangular(self._rng, d_lower, d_mode, d_upper)
            sampled_c = _sample_triangular(self._rng, c_lower, c_mode, c_upper)

            d_pts = dict(zip(driver_ids, sampled_d.tolist()))
            c_pts = dict(zip(constructor_ids, sampled_c.tolist()))
            all_d_pts.append(d_pts)
            all_c_pts.append(c_pts)

            d_df = driver_predictions.assign(
                predicted_fantasy_points=driver_predictions["driver_id"].astype(int).map(d_pts)
            )
            c_df = constructor_predictions.assign(
                predicted_fantasy_points=constructor_predictions["team_id"].astype(int).map(c_pts)
            )

            lineup = self._inner.optimize_single_race(d_df, c_df, budget, constraints)
            key = (frozenset(lineup.driver_ids), frozenset(lineup.constructor_ids))
            seen.setdefault(key, lineup)

        # Score every candidate across all N scenarios; pick highest mean
        best = max(seen.values(), key=lambda lu: _mean_score(lu, all_d_pts, all_c_pts))
        return best


def _needs_fallback(driver_preds: pd.DataFrame) -> bool:
    """Return True when confidence bounds are absent or all-identical (no variance to sample)."""
    if (
        "confidence_lower" not in driver_preds.columns
        or "confidence_upper" not in driver_preds.columns
    ):
        return True
    return bool((driver_preds["confidence_lower"] == driver_preds["confidence_upper"]).all())


def _sample_triangular(
    rng: np.random.Generator,
    lower: np.ndarray,
    mode: np.ndarray,
    upper: np.ndarray,
) -> np.ndarray:
    """
    Vectorised triangular sampling with degenerate-case handling.

    numpy's Generator.triangular requires lower < upper. When lower == upper for
    an element (no uncertainty), we return lower directly without sampling.
    """
    result = lower.copy()
    mask = lower < upper
    if mask.any():
        result[mask] = rng.triangular(lower[mask], mode[mask], upper[mask])
    return result


def _mean_score(
    lineup: Lineup,
    all_d_pts: list[dict[int, float]],
    all_c_pts: list[dict[int, float]],
) -> float:
    """
    Average score of a lineup across all sampled scenarios.

    DRS is re-derived per scenario as the highest-scoring driver in the lineup
    (not fixed from the original Lineup object) — this correctly represents how
    the DRS boost would be assigned given that scenario's actual scores.
    """
    total = 0.0
    for d_pts, c_pts in zip(all_d_pts, all_c_pts):
        driver_scores = [d_pts.get(did, 0.0) for did in lineup.driver_ids]
        constructor_score = sum(c_pts.get(cid, 0.0) for cid in lineup.constructor_ids)
        drs_bonus = max(driver_scores) if driver_scores else 0.0
        total += sum(driver_scores) + constructor_score + drs_bonus
    return total / len(all_d_pts) if all_d_pts else 0.0
