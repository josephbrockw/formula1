from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class RankMetrics:
    spearman_rho: float
    top10_precision: float
    top10_recall: float
    ndcg_at_10: float


def compute_rank_metrics(
    predictions: pd.DataFrame,
    actuals: dict[int, tuple[float, float]],
) -> RankMetrics:
    """
    Compute ranking quality metrics for a single race.

    predictions: DataFrame with columns driver_id, predicted_position,
                 predicted_fantasy_points (as returned by any predictor).
    actuals: {driver_id: (actual_position, actual_fantasy_pts)} — the
             ground-truth results for this race.

    Returns RankMetrics with four values, all in [0, 1] except Spearman ρ
    which is in [-1, 1].
    """
    return RankMetrics(
        spearman_rho=_spearman_rho(predictions, actuals),
        top10_precision=_top10_precision(predictions, actuals),
        top10_recall=_top10_recall(predictions, actuals),
        ndcg_at_10=_ndcg_at_10(predictions, actuals),
    )


# ---------------------------------------------------------------------------
# Individual metric implementations
# ---------------------------------------------------------------------------


def _spearman_rho(
    predictions: pd.DataFrame,
    actuals: dict[int, tuple[float, float]],
) -> float:
    """
    Rank correlation between predicted_position and actual finishing position.

    Spearman ρ = 1.0 means we predicted the exact finishing order.
    ρ = 0.0 means our predictions are uncorrelated with reality.
    ρ = -1.0 means we predicted backwards.

    We use numpy's argsort-twice trick: argsort gives the indices that would
    sort the array; argsort-ing those indices gives the rank of each element.
    This avoids any dependency on scipy.
    """
    driver_ids = [int(row["driver_id"]) for _, row in predictions.iterrows() if int(row["driver_id"]) in actuals]
    if len(driver_ids) < 2:
        return 0.0

    pred_pos = np.array([float(predictions.loc[predictions["driver_id"] == did, "predicted_position"].iloc[0]) for did in driver_ids])
    actual_pos = np.array([actuals[did][0] for did in driver_ids])

    # Convert values to ranks (1-based). argsort twice: first gives sorted
    # indices, second gives the rank of each original element.
    pred_ranks = np.argsort(np.argsort(pred_pos)).astype(float)
    actual_ranks = np.argsort(np.argsort(actual_pos)).astype(float)

    # Pearson correlation on the ranks is Spearman correlation by definition.
    correlation_matrix = np.corrcoef(pred_ranks, actual_ranks)
    rho = float(correlation_matrix[0, 1])
    return rho if not math.isnan(rho) else 0.0


def _top10_precision(
    predictions: pd.DataFrame,
    actuals: dict[int, tuple[float, float]],
) -> float:
    """
    Of the 10 drivers we predicted would score most fantasy points,
    how many actually scored in the top 10?

    precision = |predicted_top10 ∩ actual_top10| / 10

    Returns 0.0 if fewer than 10 drivers are available in either set.
    The top 10 corresponds to the F1 points-scoring positions — where
    almost all fantasy value concentrates.
    """
    predicted_top10 = _predicted_top10_set(predictions, actuals)
    actual_top10 = _actual_top10_set(actuals)
    if not predicted_top10 or not actual_top10:
        return 0.0
    return len(predicted_top10 & actual_top10) / 10.0


def _top10_recall(
    predictions: pd.DataFrame,
    actuals: dict[int, tuple[float, float]],
) -> float:
    """
    Of the 10 drivers who actually scored most fantasy points,
    how many did we correctly predict in our top 10?

    recall = |predicted_top10 ∩ actual_top10| / min(10, |actual_top10|)

    The denominator adjusts for races with fewer than 10 drivers (rare but
    possible due to DNFs or missing data).
    """
    predicted_top10 = _predicted_top10_set(predictions, actuals)
    actual_top10 = _actual_top10_set(actuals)
    if not predicted_top10 or not actual_top10:
        return 0.0
    return len(predicted_top10 & actual_top10) / min(10, len(actual_top10))


def _ndcg_at_10(
    predictions: pd.DataFrame,
    actuals: dict[int, tuple[float, float]],
) -> float:
    """
    Normalized Discounted Cumulative Gain at 10.

    Ranks drivers by predicted_fantasy_points. For each of the top 10
    positions in our ranking, the 'gain' is that driver's actual fantasy pts.
    Positions ranked higher get more credit: the discount is 1/log2(rank+1).

    DCG@10  = Σ actual_pts_i / log2(i+2)   for i in 0..9
    IDCG@10 = DCG of the ideal (perfect) ranking
    NDCG@10 = DCG@10 / IDCG@10

    A score of 1.0 means we ranked the highest-scoring drivers perfectly at
    the top. A score near 0.5 is roughly what a random ordering would give.
    """
    # Build matched list of (predicted_pts, actual_pts) for drivers in both sets
    matched: list[tuple[float, float]] = []
    for _, row in predictions.iterrows():
        did = int(row["driver_id"])
        if did in actuals:
            matched.append((float(row["predicted_fantasy_points"]), actuals[did][1]))

    if len(matched) < 2:
        return 0.0

    # Sort by our predicted fantasy points (descending) — this is our ranking
    matched.sort(key=lambda x: x[0], reverse=True)

    k = min(10, len(matched))

    # DCG: sum actual_pts / log2(rank+1), where rank is 1-based
    dcg = sum(actual_pts / math.log2(i + 2) for i, (_, actual_pts) in enumerate(matched[:k]))

    # IDCG: what DCG would be if we ranked by actual fantasy points perfectly
    ideal = sorted(matched, key=lambda x: x[1], reverse=True)
    idcg = sum(actual_pts / math.log2(i + 2) for i, (_, actual_pts) in enumerate(ideal[:k]))

    if idcg == 0.0:
        return 0.0
    return dcg / idcg


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _predicted_top10_set(
    predictions: pd.DataFrame,
    actuals: dict[int, tuple[float, float]],
) -> set[int]:
    """Driver IDs of our top 10 predicted fantasy scorers, intersected with actuals."""
    matched = [(int(row["driver_id"]), float(row["predicted_fantasy_points"]))
               for _, row in predictions.iterrows()
               if int(row["driver_id"]) in actuals]
    if len(matched) < 10:
        return set()
    matched.sort(key=lambda x: x[1], reverse=True)
    return {did for did, _ in matched[:10]}


def _actual_top10_set(actuals: dict[int, tuple[float, float]]) -> set[int]:
    """Driver IDs of the 10 highest actual fantasy scorers."""
    if len(actuals) < 10:
        return set()
    sorted_drivers = sorted(actuals.items(), key=lambda kv: kv[1][1], reverse=True)
    return {did for did, _ in sorted_drivers[:10]}
