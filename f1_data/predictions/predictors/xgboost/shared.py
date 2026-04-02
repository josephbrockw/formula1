from __future__ import annotations

from typing import Iterator

import pandas as pd
from django.db.models import Max

from core.models import Event, SessionResult
from predictions.features.base import FeatureStore
from predictions.models import FantasyDriverScore

TARGET_POSITION = "finishing_position"
TARGET_POINTS = "fantasy_points"

# Columns that are not features — stripped before training/predicting
_NON_FEATURE_COLS = {"driver_id", "event_index"}

# Base race points by finishing position (no bonuses).
# Used as a fallback when FantasyDriverScore data hasn't been imported yet.
# A driver's actual fantasy total also includes qualifying points, overtake
# bonuses, fastest lap, and Driver of the Day — so these estimates are a
# lower bound. Once real data is imported via import_fantasy_csv, those
# records take precedence.
_RACE_POSITION_BASE_POINTS: dict[int, float] = {
    1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1
}


def _estimate_fantasy_points(position: int) -> float:
    """Estimate fantasy points from race position alone (no bonuses)."""
    return _RACE_POSITION_BASE_POINTS.get(position, 0.0)


def build_training_dataset(
    events: list[Event],
    feature_store: FeatureStore,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Build X (features) and y (targets) DataFrames from a list of historical events.

    For each event, computes features for all drivers and pairs them with their
    actual finishing position and fantasy points. Rows are skipped only when
    finishing position is missing (driver didn't start or result not collected).

    Fantasy points source (in priority order):
      1. FantasyDriverScore records (imported from Chrome extension CSVs)
      2. Estimated from race position using the standard scoring table

    The fallback estimate covers base race points only — no qualifying, overtake
    bonuses, fastest lap, or DotD. It unblocks training when real fantasy data
    hasn't been imported yet and automatically gives way to real data once available.

    Returns:
        X: feature DataFrame including 'driver_id'
        y: target DataFrame with 'finishing_position' and 'fantasy_points' columns
           (same row order as X)
    """
    X_rows: list[dict] = []
    y_rows: list[dict] = []

    for i, event in enumerate(events):
        X_event = feature_store.get_all_driver_features(event.id)
        if X_event.empty:
            continue

        race_positions = dict(
            SessionResult.objects.filter(
                session__event=event,
                session__session_type="R",
            ).values_list("driver_id", "position")
        )

        fantasy_totals = dict(
            FantasyDriverScore.objects.filter(event=event)
            .values("driver_id")
            .annotate(total=Max("race_total"))
            .values_list("driver_id", "total")
        )

        for _, row in X_event.iterrows():
            driver_id = int(row["driver_id"])
            position = race_positions.get(driver_id)

            if position is None:
                continue

            fantasy_pts = fantasy_totals.get(driver_id, _estimate_fantasy_points(int(position)))

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


def walk_forward_splits(
    events: list[Event],
    min_train: int = 5,
) -> Iterator[tuple[list[Event], Event]]:
    """
    Yield (train_events, test_event) pairs for walk-forward evaluation.

    Each split trains on all events up to the test event, never including
    any future data. min_train sets the minimum number of training events
    before the first prediction is made.

    Example with min_train=3 and events [R1, R2, R3, R4, R5]:
        train=[R1,R2,R3], test=R4
        train=[R1,R2,R3,R4], test=R5
    """
    for i in range(min_train, len(events)):
        yield events[:i], events[i]
