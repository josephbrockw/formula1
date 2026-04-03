"""
Qualifying feature store and training dataset builder for the qualifying_ranker family.

Why a separate module?
  The race pipeline's build_training_dataset targets session_type="R" and uses
  race_total as the fantasy points label. Qualifying prediction needs a different
  target: qualifying *position* and qualifying *fantasy points* (which are only
  awarded to Q3 qualifiers in F1 Fantasy, so the distribution is very different
  from race points).

  All V4 features are computed from pre-qualifying data (practice telemetry,
  recent form, weather) — so QualifyingV1FeatureStore can simply inherit V4
  without overriding anything. The qualifying-specific work is entirely in the
  training dataset builder.

Usage:
  from predictions.features.qualifying.v1_qualify import (
      QualifyingV1FeatureStore,
      build_qualifying_training_dataset,
  )
"""
from __future__ import annotations

import pandas as pd
from django.db.models import Sum

from core.models import Event, SessionResult
from predictions.features.base import FeatureStore
from predictions.features.v4 import V4FeatureStore
from predictions.models import FantasyDriverScore
from predictions.predictors.xgboost.shared import TARGET_POINTS, TARGET_POSITION


class QualifyingV1FeatureStore(V4FeatureStore):
    """
    Feature store for qualifying position prediction.

    Inherits all V4 features without changes. Every V4 feature is derived from
    data available before qualifying starts (practice telemetry, recent race and
    qualifying form, weather forecasts) — none of them "leak" information from
    the qualifying session itself.

    This subclass exists as a named entry point for the qualifying family so that
    a future v2 can add qualifying-specific features (e.g. Q-lap sector times from
    FP3, tyre compound choice) without touching the race pipeline.
    """


# ---------------------------------------------------------------------------
# Qualifying fantasy points fallback
# ---------------------------------------------------------------------------

# In F1 Fantasy, qualifying points are awarded only to the top-10 qualifiers
# (those who reach Q3). The exact scale used here matches the standard scoring:
#   P1 → 10 pts, P2 → 9 pts, ..., P10 → 1 pt
#   P11–P20 → 0 pts (eliminated in Q1 or Q2)
#
# This fallback is used when no FantasyDriverScore qualifying rows have been
# imported yet (e.g., for historical events where only the CSV race data exists).
# Once real data is imported, it takes precedence automatically.
_QUALIFYING_POSITION_BASE_POINTS: dict[int, float] = {
    1: 10.0, 2: 9.0, 3: 8.0, 4: 7.0, 5: 6.0,
    6: 5.0, 7: 4.0, 8: 3.0, 9: 2.0, 10: 1.0,
}


def _estimate_qualifying_fantasy_points(position: int) -> float:
    """Estimate qualifying fantasy points from position alone (Q3 top-10 only)."""
    return _QUALIFYING_POSITION_BASE_POINTS.get(position, 0.0)


# ---------------------------------------------------------------------------
# Dataset builder
# ---------------------------------------------------------------------------


def build_qualifying_training_dataset(
    events: list[Event],
    feature_store: FeatureStore,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Build X (features) and y (targets) for qualifying position prediction.

    Mirrors build_training_dataset from xgboost/shared.py but targets qualifying
    results instead of race results. Three key differences:

    1. Session filter: session_type="Q" (not "R")
    2. Fantasy points source: FantasyDriverScore rows with event_type="qualifying",
       aggregated with Sum("points") — NOT Max("race_total"), which is the full
       weekend total. This matches how _actuals_for_session in backtest_model works.
    3. Fantasy points fallback: _QUALIFYING_POSITION_BASE_POINTS (top-10 get 10–1 pts,
       others get 0) instead of the race position table.

    Events with no qualifying SessionResult rows are skipped (e.g. pre-season tests,
    events where data hasn't been collected yet). Drivers without a recorded
    qualifying position are also skipped (DNS, DSQ).

    Returns:
        X: feature DataFrame including 'driver_id' and 'event_index'
        y: target DataFrame with 'finishing_position' (qualifying) and
           'fantasy_points' (qualifying) columns — same row order as X
    """
    X_rows: list[dict] = []
    y_rows: list[dict] = []

    for i, event in enumerate(events):
        X_event = feature_store.get_all_driver_features(event.id)
        if X_event.empty:
            continue

        # Qualifying positions: who lined up where on the grid.
        # We use position (the actual classified qualifying result) not
        # grid_position (which can differ due to penalties applied after qualifying).
        quali_positions = dict(
            SessionResult.objects.filter(
                session__event=event,
                session__session_type="Q",
            ).values_list("driver_id", "position")
        )

        # No qualifying session data for this event — skip entirely.
        # This is normal for pre-season tests or events where Q data hasn't
        # been collected yet.
        if not quali_positions:
            continue

        # Qualifying fantasy points: only the points earned in the qualifying
        # session itself. Sum individual line items (not race_total, which is
        # the full-weekend aggregate including race + sprint + qualifying).
        quali_fantasy_pts = dict(
            FantasyDriverScore.objects.filter(event=event, event_type="qualifying")
            .values("driver_id")
            .annotate(total=Sum("points"))
            .values_list("driver_id", "total")
        )

        for _, row in X_event.iterrows():
            driver_id = int(row["driver_id"])
            position = quali_positions.get(driver_id)

            # Skip drivers with no qualifying position recorded (DNS, DSQ, etc.)
            if position is None:
                continue

            # Use real qualifying fantasy points if available; otherwise estimate
            # from position. The fallback gives 0 pts for anyone outside Q3 (P11–20),
            # which is correct but coarser than the real scoring breakdown.
            fantasy_pts = quali_fantasy_pts.get(
                driver_id, _estimate_qualifying_fantasy_points(int(position))
            )

            row_dict = row.to_dict()
            row_dict["event_index"] = i
            X_rows.append(row_dict)
            y_rows.append(
                {
                    TARGET_POSITION: float(position),
                    TARGET_POINTS: float(fantasy_pts),
                }
            )

    return pd.DataFrame(X_rows), pd.DataFrame(y_rows)
