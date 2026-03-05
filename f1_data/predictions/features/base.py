from __future__ import annotations

from typing import Protocol

import pandas as pd


class FeatureStore(Protocol):
    """
    Interface for computing ML-ready feature vectors from raw race data.

    A feature vector is a flat dict of floats representing everything we know
    about a driver BEFORE a race starts. The ML model learns which of these
    numbers are predictive of fantasy points.

    Implementations:
        v1_pandas.V1FeatureStore — ORM queries + pandas rolling calculations (MVP)
    """

    def get_driver_features(self, driver_id: int, event_id: int) -> dict[str, float]:
        """Return a flat dict of float features for one driver at one event."""
        ...

    def get_all_driver_features(self, event_id: int) -> pd.DataFrame:
        """Return a DataFrame with one row per driver in the season for this event.
        Always includes a 'driver_id' column alongside the feature columns."""
        ...
