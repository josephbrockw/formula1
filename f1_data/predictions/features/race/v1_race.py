"""
Race feature store for race_ranker models (v1+).

The key difference from the base V4 store is the addition of
`predicted_quali_position` — the qualifying grid position for this event.

Why qualifying position is a useful feature
--------------------------------------------
Grid position is strongly correlated with race outcome at some circuits
(Monaco, Hungary — hard to overtake) and weakly correlated at others
(Monza, Spa — high overtaking rate). Rather than hard-coding this relationship,
we let XGBRanker learn the circuit-specific weighting from V4's circuit features
(circuit_length, total_corners, etc.) combined with predicted_quali_position.

Train vs predict time
---------------------
During walk-forward backtesting (training AND testing), we use the *actual*
qualifying position from the DB (SessionResult session_type="Q"). This is the
ground truth — no compounding error from a noisy quali model tainting the race
model's training signal.

At real prediction time (next_race command, before the race), qualifying has
already happened by race day, so we *still* read from SessionResult — the same
code path. The only time predicted_quali_position reflects a *model prediction*
rather than a recorded result is when a future inference command invokes the
qualifying model first and inserts its output into the DB (or passes it to the
feature store via some other mechanism). This store does not know or care which
path the caller took; it reads whatever SessionResult Q rows exist.

Fallback (10.5)
---------------
10.5 is used when:
  - The event has no qualifying SessionResult rows yet (data not collected).
  - A specific driver has no qualifying position (DNS in qualifying — position IS
    in the DB but as NULL, so we must check `is not None`, not just .get() with
    a default, since dict.get() only fires on missing keys, not None values).
10.5 = midfield average for a 20-driver grid (positions 1–20 average to 10.5).
"""
from __future__ import annotations

import pandas as pd

from core.models import SessionResult
from predictions.features.v4 import V4FeatureStore

_QUALI_POSITION_FALLBACK = 10.5  # midfield average for 20-driver grid


class RaceV1FeatureStore(V4FeatureStore):
    """
    Feature store for race position prediction (race_ranker family).

    Extends V4FeatureStore with one additional column:
      predicted_quali_position — the driver's qualifying grid position for
        this event (actual SessionResult Q result, or 10.5 as midfield fallback).

    See module docstring for the train-vs-predict-time explanation.
    """

    def get_all_driver_features(self, event_id: int) -> pd.DataFrame:
        df = super().get_all_driver_features(event_id)
        if df.empty:
            return df

        # Fetch actual qualifying positions for this event.
        # session__event_id traverses the FK from SessionResult → Session → Event.
        # session__session_type="Q" filters to qualifying sessions only.
        # values_list gives (driver_id, position) pairs — position is nullable
        # (NULL for DNS drivers), so the dict may contain {driver_id: None}.
        quali_positions: dict[int, int | None] = dict(
            SessionResult.objects.filter(
                session__event_id=event_id,
                session__session_type="Q",
            ).values_list("driver_id", "position")
        )

        # Map each driver to their qualifying position. We must use `is not None`
        # (not a .get() default) because dict.get(key, default) only uses the
        # default when the key is absent — if the key is present with value None
        # (DNS driver), .get() returns None and float(None) would raise TypeError.
        df["predicted_quali_position"] = df["driver_id"].astype(int).map(
            lambda did: (
                float(quali_positions[did])
                if quali_positions.get(did) is not None
                else _QUALI_POSITION_FALLBACK
            )
        )

        return df
