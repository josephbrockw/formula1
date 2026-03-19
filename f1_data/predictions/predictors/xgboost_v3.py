from __future__ import annotations

import math

import numpy as np
import pandas as pd
from django.conf import settings

from predictions.predictors.xgboost_v1 import (
    TARGET_POINTS,
    TARGET_POSITION,
    _NON_FEATURE_COLS,
)
from predictions.predictors.xgboost_v2 import XGBoostPredictorV2


class XGBoostPredictorV3(XGBoostPredictorV2):
    """
    V2 + exponential decay sample weights.

    All four models (position MSE, points MSE, q10, q90) are trained with
    the same weight vector — more recent races count more, older races fade.

    half_life controls how many events back a row's weight halves.
    Configured via settings.ML_PREDICTOR_V3_HALF_LIFE (default 10 ≈ half a season).
    """

    def __init__(self) -> None:
        super().__init__()
        self._half_life: int = settings.ML_PREDICTOR_V3_HALF_LIFE

    def fit(self, X: pd.DataFrame, y: pd.DataFrame) -> None:
        weights = self._decay_weights(X)
        self._feature_cols = [c for c in X.columns if c not in _NON_FEATURE_COLS]
        X_train = X[self._feature_cols]
        self._position_model.fit(X_train, y[TARGET_POSITION], sample_weight=weights)
        self._points_mean_model.fit(X_train, y[TARGET_POINTS], sample_weight=weights)
        self._points_q10.fit(X_train, y[TARGET_POINTS], sample_weight=weights)
        self._points_q90.fit(X_train, y[TARGET_POINTS], sample_weight=weights)
        self._fitted = True

    def _decay_weights(self, X: pd.DataFrame) -> np.ndarray:
        """Exponential decay: weight = exp(-λ * (max_idx - event_idx))."""
        if "event_index" not in X.columns:
            return np.ones(len(X))
        event_idx = X["event_index"].to_numpy(dtype=float)
        lam = math.log(2) / self._half_life
        return np.exp(-lam * (event_idx.max() - event_idx))
