from __future__ import annotations

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from predictions.predictors.xgboost.shared import (
    TARGET_POINTS,
    TARGET_POSITION,
    _NON_FEATURE_COLS,
)


class XGBoostPredictor:
    """
    Gradient boosting predictor for driver finishing position and fantasy points.

    Trains two separate XGBRegressor models — one per target. Separate models
    are simpler than multi-output and work well when targets are not tightly
    correlated (fantasy points include bonuses like fastest lap that don't
    directly relate to finishing position).

    Confidence bounds in v1 are approximate: ±1 std dev of training residuals.
    v2 will use proper quantile regression (objective='reg:quantileerror').
    """

    def __init__(self) -> None:
        self._position_model = XGBRegressor(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            random_state=42,
            verbosity=0,
        )
        self._points_model = XGBRegressor(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            random_state=42,
            verbosity=0,
        )
        self._feature_cols: list[str] = []
        self._points_residual_std: float = 10.0  # fallback until fit() is called
        self._fitted = False

    def fit(self, X: pd.DataFrame, y: pd.DataFrame) -> None:
        """
        Train on historical (features, targets) data.

        X must contain a 'driver_id' column (it's stripped before training).
        y must contain 'finishing_position' and 'fantasy_points' columns.
        """
        self._feature_cols = [c for c in X.columns if c not in _NON_FEATURE_COLS]
        X_train = X[self._feature_cols]

        self._position_model.fit(X_train, y[TARGET_POSITION])
        self._points_model.fit(X_train, y[TARGET_POINTS])

        # Approximate confidence interval width from training residuals.
        # This measures how wrong the model typically is on data it has seen,
        # which is a lower bound on real-world error — but useful for v1.
        points_pred = self._points_model.predict(X_train)
        residuals = y[TARGET_POINTS].to_numpy() - points_pred
        self._points_residual_std = float(np.std(residuals))
        self._fitted = True

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        """
        Return predictions for all drivers in the features DataFrame.

        features must have the same columns as the X passed to fit(),
        including 'driver_id'.
        """
        if not self._fitted:
            raise RuntimeError("Call fit() before predict()")

        X = features[self._feature_cols]
        predicted_positions = self._position_model.predict(X)
        predicted_points = self._points_model.predict(X)

        margin = self._points_residual_std
        return pd.DataFrame(
            {
                "driver_id": features["driver_id"].to_numpy(),
                "predicted_position": self._position_model.predict(X),
                "predicted_fantasy_points": predicted_points,
                "confidence_lower": predicted_points - margin,
                "confidence_upper": predicted_points + margin,
            }
        )
