from __future__ import annotations

from typing import Protocol

import pandas as pd


class PerformancePredictor(Protocol):
    """
    Interface for predicting driver race performance from pre-race features.

    Input to predict(): a DataFrame from FeatureStore.get_all_driver_features(),
    which has one row per driver and a 'driver_id' column alongside feature columns.

    Output of predict(): a DataFrame with one row per driver containing:
        driver_id               — matches input
        predicted_position      — expected finishing position (1.0–20.0)
        predicted_fantasy_points — expected total fantasy points
        confidence_lower        — 10th percentile fantasy points estimate
        confidence_upper        — 90th percentile fantasy points estimate

    Implementations:
        xgboost_v1.XGBoostPredictor — gradient boosting trees (MVP)
    """

    def fit(self, X: pd.DataFrame, y: pd.DataFrame) -> None:
        """Train on historical (features, targets) data."""
        ...

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        """Return predictions for all drivers in the features DataFrame."""
        ...
