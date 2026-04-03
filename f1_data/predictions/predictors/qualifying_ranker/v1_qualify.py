"""
Qualifying position ranker — v1.

Uses XGBRanker with a pairwise ranking objective to predict qualifying order.
The ranker learns "which driver will qualify ahead of which other driver at this
circuit" rather than predicting an absolute position number. This is better
aligned with the structure of qualifying (all 20 drivers rank relative to each
other) than a regression approach.

How this differs from XGBoostPredictorV4 (race ranker):
  - Standalone class — no inheritance from the race predictor chain, which
    avoids dragging in recency-decay weights, the position MSE model, and
    other race-specific machinery that isn't needed for v1 qualifying.
  - Trained on qualifying positions + qualifying fantasy points via
    build_qualifying_training_dataset (not the race dataset builder).
  - No sample weights in v1. Recency decay can be added in v2 once we
    establish a baseline.
  - Calibration guard for sparse data: if all ranker scores are identical
    (can happen with tiny training sets early in the season), np.polyfit
    would be degenerate — we skip it and keep the identity calibration.

Training target note:
  The ranker uses y[TARGET_POINTS] (qualifying fantasy points) as relevance
  labels, with fallback estimates for events where FantasyDriverScore qualifying
  rows haven't been imported. The fallback is 10–1 pts for Q3 (top-10), 0 for
  the rest. This means in early training the ranker mostly learns to separate
  "made it to Q3" vs "didn't" — a coarser but still useful signal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from xgboost import XGBRanker, XGBRegressor

from predictions.predictors.xgboost.shared import TARGET_POINTS, TARGET_POSITION, _NON_FEATURE_COLS
from predictions.predictors.xgboost.v4 import _compute_group_sizes

# Hyperparameters match V2–V4 race predictors (tuned 2026-03-19 via
# tune_hyperparams --seasons 2022 2023 2024 2025 --n-iter 50).
# depth=2 + subsampling prevents overfitting on the small per-event
# training windows (~15–20 qualifying rows/race).
_BASE = dict(
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


class QualifyingRankerV1:
    """
    Pairwise ranking predictor for qualifying position.

    fit() takes X (features) and y (qualifying position + qualifying fantasy pts).
    predict() returns predicted qualifying position + calibrated fantasy points
    + q10/q90 confidence bounds.

    The ranker is trained on qualifying fantasy points as relevance labels
    (higher points = better qualifier = should rank first). Position is *derived*
    from the ranker's output order — we don't train a separate position model.

    Linear calibration maps raw ranker scores (arbitrary real-valued) → estimated
    qualifying fantasy points, keeping the output on a meaningful scale.
    """

    def __init__(self) -> None:
        self._ranker = XGBRanker(objective="rank:pairwise", **_BASE)
        # q10/q90: quantile regression over qualifying fantasy points.
        # Wide bounds → high-variance qualifier (risky pick for fantasy).
        # Narrow bounds → consistent qualifier (reliable top-10 lock).
        self._q10 = XGBRegressor(objective="reg:quantileerror", quantile_alpha=0.1, **_BASE)
        self._q90 = XGBRegressor(objective="reg:quantileerror", quantile_alpha=0.9, **_BASE)
        # Linear calibration: calibrated_pts = _calib_a + _calib_b * raw_score
        self._calib_a: float = 0.0
        self._calib_b: float = 1.0
        self._feature_cols: list[str] = []
        self._fitted = False

    def fit(self, X: pd.DataFrame, y: pd.DataFrame) -> None:
        """
        Train on qualifying (features, targets) data.

        X must include 'driver_id' and 'event_index' columns (not used as features,
        but needed for group size computation and are in _NON_FEATURE_COLS).
        y must have TARGET_POSITION ('finishing_position') and TARGET_POINTS
        ('fantasy_points') columns — see build_qualifying_training_dataset.
        """
        self._feature_cols = [c for c in X.columns if c not in _NON_FEATURE_COLS]
        X_train = X[self._feature_cols]

        # Quantile bounds on qualifying fantasy points.
        self._q10.fit(X_train, y[TARGET_POINTS])
        self._q90.fit(X_train, y[TARGET_POINTS])

        # Ranker: groups = one qualifying session per event.
        # We use qualifying fantasy points as relevance labels (higher = ranked higher).
        # No per-group sample weights in v1.
        group_sizes = _compute_group_sizes(X)
        self._ranker.fit(X_train, y[TARGET_POINTS], group=group_sizes)

        # Calibrate raw ranker scores to the qualifying fantasy points scale.
        # This keeps predicted_fantasy_points on a sensible numeric range so
        # any downstream consumers (e.g. a future optimizer) can compare
        # qualifying picks against the transfer penalty threshold.
        raw_scores = self._ranker.predict(X_train)
        if raw_scores.std() < 1e-9:
            # All scores are identical — degenerate training set (e.g. only one
            # qualifying event in the window). Skip calibration; identity is safe.
            self._calib_a, self._calib_b = 0.0, 1.0
        else:
            coeffs = np.polyfit(raw_scores, y[TARGET_POINTS].to_numpy(), deg=1)
            self._calib_b, self._calib_a = float(coeffs[0]), float(coeffs[1])

        self._fitted = True

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        """
        Return per-driver qualifying predictions.

        features must have the same columns as X passed to fit(), including
        'driver_id'. The 'event_index' column is NOT required at predict time
        (it's only used during training for group size computation).

        Columns returned:
          driver_id             — integer driver identifier
          predicted_position    — predicted qualifying rank (1.0 = fastest)
          predicted_fantasy_pts — calibrated qualifying fantasy points estimate
          confidence_lower      — q10 estimate (pessimistic bound)
          confidence_upper      — q90 estimate (optimistic bound)
        """
        if not self._fitted:
            raise RuntimeError("Call fit() before predict()")

        X = features[self._feature_cols]
        raw_scores = self._ranker.predict(X)
        calibrated_pts = self._calib_a + self._calib_b * raw_scores

        # Highest raw score → lowest predicted position number (P1).
        predicted_positions = pd.Series(raw_scores).rank(ascending=False).to_numpy()

        q10 = self._q10.predict(X)
        q90 = self._q90.predict(X)
        return pd.DataFrame(
            {
                "driver_id": features["driver_id"].to_numpy(),
                "predicted_position": predicted_positions,
                "predicted_fantasy_points": calibrated_pts,
                "confidence_lower": np.minimum(q10, q90),
                "confidence_upper": np.maximum(q10, q90),
            }
        )

    def get_feature_importances(self) -> dict[str, float]:
        """Return {feature_name: importance} from the ranker, sorted descending."""
        if not self._fitted:
            return {}
        return dict(
            sorted(
                zip(self._feature_cols, self._ranker.feature_importances_),
                key=lambda x: -x[1],
            )
        )
