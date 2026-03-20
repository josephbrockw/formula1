from __future__ import annotations

import numpy as np
import pandas as pd
from django.conf import settings
from xgboost import XGBRanker

from predictions.predictors.xgboost_v1 import TARGET_POINTS, TARGET_POSITION, _NON_FEATURE_COLS
from predictions.predictors.xgboost_v3 import XGBoostPredictorV3


def _compute_group_sizes(X: pd.DataFrame) -> np.ndarray:
    """
    Derive per-race group sizes for XGBRanker from event_index.

    XGBRanker needs an array like [20, 19, 20, ...] — how many rows belong
    to each race. Since build_training_dataset appends events in order,
    value_counts sorted by index gives the correct sequence.
    """
    return (
        X["event_index"]
        .value_counts(sort=False)
        .sort_index()
        .to_numpy(dtype=int)
    )


class XGBoostPredictorV4(XGBoostPredictorV3):
    """
    Pairwise ranking predictor.

    Replaces the MSE points regressor (from V2/V3) with XGBRanker using
    rank:pairwise objective. This expands training data from ~20 rows/race
    to ~190 driver pairs/race and aligns the loss function with the optimizer's
    ranking need.

    Inherits from V3 for: recency decay weights, q10/q90 quantile bounds,
    position MSE model, and _fitted/_feature_cols machinery.

    A linear calibration step maps raw ranker scores → real fantasy point
    units so the optimizer's transfer penalty (-10 pts) remains correctly scaled.
    """

    def __init__(self) -> None:
        super().__init__()  # sets up _position_model, _points_q10/q90, _decay_weights
        _base = dict(
            n_estimators=50,
            max_depth=2,
            learning_rate=0.1,
            min_child_weight=3,
            subsample=0.7,
            colsample_bytree=0.7,
            reg_lambda=1,
            random_state=42,
            verbosity=0,
        )
        self._ranker = XGBRanker(objective="rank:pairwise", **_base)
        # Linear calibration: calibrated_pts = _calib_a + _calib_b * raw_score
        self._calib_a: float = 0.0
        self._calib_b: float = 1.0

    def fit(self, X: pd.DataFrame, y: pd.DataFrame) -> None:
        weights = self._decay_weights(X)
        self._feature_cols = [c for c in X.columns if c not in _NON_FEATURE_COLS]
        X_train = X[self._feature_cols]

        # Keep position model and quantile bounds from V3
        self._position_model.fit(X_train, y[TARGET_POSITION], sample_weight=weights)
        self._points_q10.fit(X_train, y[TARGET_POINTS], sample_weight=weights)
        self._points_q90.fit(X_train, y[TARGET_POINTS], sample_weight=weights)

        # Train ranker — uses fantasy_points as relevance labels (higher = better)
        # XGBRanker with group= expects one sample_weight per group, not per row.
        # All rows in a group share the same event_index → same decay weight,
        # so we take the first weight for each event_index.
        group_sizes = _compute_group_sizes(X)
        group_weights = (
            X["event_index"]
            .drop_duplicates()
            .sort_values()
            .map(dict(zip(X["event_index"], weights)))
            .to_numpy(dtype=float)
        )
        self._ranker.fit(
            X_train,
            y[TARGET_POINTS],
            group=group_sizes,
            sample_weight=group_weights,
        )

        # Calibrate: fit a line from raw ranker scores → actual fantasy points
        raw_scores = self._ranker.predict(X_train)
        coeffs = np.polyfit(raw_scores, y[TARGET_POINTS].to_numpy(), deg=1)
        self._calib_b, self._calib_a = float(coeffs[0]), float(coeffs[1])
        self._fitted = True

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        if not self._fitted:
            raise RuntimeError("Call fit() before predict()")
        X = features[self._feature_cols]

        raw_scores = self._ranker.predict(X)
        calibrated_pts = self._calib_a + self._calib_b * raw_scores

        # Derive predicted_position from ranker order (rank 1 = highest score)
        predicted_positions = (
            pd.Series(raw_scores).rank(ascending=False).to_numpy()
        )

        q10 = self._points_q10.predict(X)
        q90 = self._points_q90.predict(X)
        return pd.DataFrame({
            "driver_id": features["driver_id"].to_numpy(),
            "predicted_position": predicted_positions,
            "predicted_fantasy_points": calibrated_pts,
            "confidence_lower": np.minimum(q10, q90),
            "confidence_upper": np.maximum(q10, q90),
        })

    def get_feature_importances(self) -> dict[str, float]:
        if not self._fitted:
            return {}
        return dict(
            sorted(
                zip(self._feature_cols, self._ranker.feature_importances_),
                key=lambda x: -x[1],
            )
        )
