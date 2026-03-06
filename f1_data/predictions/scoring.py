"""
Post-race scoring functions.

These are pure in the sense that they take explicit inputs rather than
re-running the ML pipeline. Called from:
  - score_lineup management command (explicit)
  - next_race command (auto-scoring the previous round)
"""

from __future__ import annotations

import pandas as pd
from django.db.models import Max

from core.models import Event
from predictions.models import (
    FantasyConstructorPrice,
    FantasyConstructorScore,
    FantasyDriverPrice,
    FantasyDriverScore,
)
from predictions.optimizers.greedy_v2 import GreedyOptimizerV2


def load_actual_driver_pts(event: Event) -> dict[int, float]:
    """One actual fantasy total per driver — race_total is consistent across all line items."""
    return {
        did: float(total)
        for did, total in FantasyDriverScore.objects.filter(event=event)
        .values("driver_id")
        .annotate(total=Max("race_total"))
        .values_list("driver_id", "total")
    }


def load_actual_constructor_pts(event: Event) -> dict[int, float]:
    return {
        tid: float(total)
        for tid, total in FantasyConstructorScore.objects.filter(event=event)
        .values("team_id")
        .annotate(total=Max("race_total"))
        .values_list("team_id", "total")
    }


def score_roster(
    driver_ids: list[int],
    constructor_ids: list[int],
    drs_driver_id: int,
    actual_driver_pts: dict[int, float],
    actual_constructor_pts: dict[int, float],
) -> float:
    """Score a lineup using actual post-race results. DRS driver's points count twice."""
    driver_total = sum(actual_driver_pts.get(did, 0.0) for did in driver_ids)
    constructor_total = sum(actual_constructor_pts.get(cid, 0.0) for cid in constructor_ids)
    drs_bonus = actual_driver_pts.get(drs_driver_id, 0.0)
    return driver_total + constructor_total + drs_bonus


def compute_oracle(
    event: Event,
    actual_driver_pts: dict[int, float],
    actual_constructor_pts: dict[int, float],
    budget: float,
) -> float | None:
    """
    Run the optimizer with actual points as inputs to find the best achievable lineup.

    This is the ceiling — the score you'd get with perfect predictions.
    Returns None if price data is missing for the event.
    """
    driver_prices = dict(
        FantasyDriverPrice.objects.filter(event=event).values_list("driver_id", "price")
    )
    constructor_prices = dict(
        FantasyConstructorPrice.objects.filter(event=event).values_list("team_id", "price")
    )
    if not driver_prices or not constructor_prices:
        return None

    driver_df = pd.DataFrame([
        {"driver_id": did, "predicted_fantasy_points": actual_driver_pts.get(did, 0.0), "price": float(price)}
        for did, price in driver_prices.items()
    ])
    constructor_df = pd.DataFrame([
        {"team_id": tid, "predicted_fantasy_points": actual_constructor_pts.get(tid, 0.0), "price": float(price)}
        for tid, price in constructor_prices.items()
    ])

    oracle_lineup = GreedyOptimizerV2().optimize_single_race(driver_df, constructor_df, budget)
    return score_roster(
        oracle_lineup.driver_ids,
        oracle_lineup.constructor_ids,
        oracle_lineup.drs_boost_driver_id,
        actual_driver_pts,
        actual_constructor_pts,
    )
