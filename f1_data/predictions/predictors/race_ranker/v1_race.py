"""
Race position ranker — v1.

Uses XGBRanker with a pairwise ranking objective to predict finishing order.
The core architecture mirrors QualifyingRankerV1 — same hyperparameters,
same calibration logic, same guard against degenerate training sets — with
one important addition to the feature matrix: `predicted_quali_position`.

Why `predicted_quali_position` as a feature
--------------------------------------------
Grid position is a strong predictor of race outcome at street circuits (Monaco,
Hungary — very hard to overtake) and a weak predictor at high-overtaking tracks
(Monza, Spa). Rather than hard-coding this correlation, we include it as a
feature and let XGBRanker learn the circuit-specific weighting from V4's
circuit features (circuit_length, total_corners) alongside it.

Why `y[TARGET_POINTS]` (fantasy points) as relevance labels
-------------------------------------------------------------
XGBRanker doesn't predict an absolute number — it learns a *relative ordering*
(driver A should finish ahead of driver B). Fantasy points are ideal relevance
labels because:

  1. Higher points = better result: P1=25 > P2=18 > P3=15 > ... — the ranker
     naturally learns the position ordering without any inversion.
  2. Bonus points (fastest lap, DotD, overtakes) are also captured — the ranker
     learns to identify drivers who earn bonus points, not just who finishes high.

The actual `predicted_position` column returned by predict() is *derived* from
the ranker's raw output scores:
    predicted_positions = raw_scores.rank(ascending=False)
So rank 1 = highest raw score = predicted P1. Position is never regressed
directly — it emerges from the ranking order.

Train vs predict time
---------------------
During walk-forward training, RaceV1FeatureStore.get_all_driver_features()
reads *actual* qualifying positions from DB SessionResult "Q" records.
This avoids compounding the qualifying model's prediction errors into the
race model's training signal. See features/race/v1_race.py for full detail.

No sample decay weights in v1. A v2 can add recency decay (matching
XGBoostPredictorV3/V4's approach) once we have a baseline Spearman ρ.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from xgboost import XGBRanker, XGBRegressor

from predictions.predictors.xgboost.shared import TARGET_POINTS, TARGET_POSITION, _NON_FEATURE_COLS
from predictions.predictors.xgboost.v4 import _compute_group_sizes

# Hyperparameters match the qualifying_ranker v1 baseline (tuned 2026-03-19 via
# tune_hyperparams --seasons 2022 2023 2024 2025 --n-iter 50).
# depth=2 + subsampling prevents overfitting on small per-event training windows.
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


class RaceRankerV1:
    """
    Pairwise ranking predictor for race finishing position.

    fit() takes X (features including predicted_quali_position) and y
    (race finishing_position + race fantasy_points).
    predict() returns predicted finishing position + calibrated fantasy points
    + q10/q90 confidence bounds.

    The ranker uses race fantasy points as relevance labels (higher = should
    rank first). Position is *derived* from the ranker output order — no
    separate position regression model. Linear calibration maps raw ranker
    scores → the race fantasy point scale.
    """

    def __init__(self) -> None:
        self._ranker = XGBRanker(objective="rank:pairwise", **_BASE)
        # q10/q90: quantile regression over race fantasy points.
        # Wide bounds → high-variance driver (risky pick for fantasy).
        # Narrow bounds → consistent finisher (reliable pick).
        self._q10 = XGBRegressor(objective="reg:quantileerror", quantile_alpha=0.1, **_BASE)
        self._q90 = XGBRegressor(objective="reg:quantileerror", quantile_alpha=0.9, **_BASE)
        # Linear calibration: calibrated_pts = _calib_a + _calib_b * raw_score
        self._calib_a: float = 0.0
        self._calib_b: float = 1.0
        self._feature_cols: list[str] = []
        self._fitted = False

    def fit(self, X: pd.DataFrame, y: pd.DataFrame) -> None:
        """
        Train on race (features, targets) data.

        X must include 'driver_id' and 'event_index' (stripped before training,
        not used as features) and 'predicted_quali_position' (used as a feature).
        y must have TARGET_POSITION ('finishing_position') and TARGET_POINTS
        ('fantasy_points') — see build_training_dataset in xgboost/shared.py,
        which targets session_type="R" race results.
        """
        self._feature_cols = [c for c in X.columns if c not in _NON_FEATURE_COLS]
        X_train = X[self._feature_cols]

        # Quantile bounds on race fantasy points.
        self._q10.fit(X_train, y[TARGET_POINTS])
        self._q90.fit(X_train, y[TARGET_POINTS])

        # Ranker: groups = one race per event_index.
        # We use race fantasy points as relevance labels (higher = better finish).
        group_sizes = _compute_group_sizes(X)
        self._ranker.fit(X_train, y[TARGET_POINTS], group=group_sizes)

        # Calibrate raw ranker scores to the race fantasy points scale.
        # This keeps predicted_fantasy_points on a meaningful numeric range so
        # downstream consumers (optimizer, transfer penalty comparisons) work correctly.
        raw_scores = self._ranker.predict(X_train)
        if raw_scores.std() < 1e-9:
            # All scores identical — degenerate training set (e.g. only one race
            # in the window). Skip calibration; identity mapping is safe.
            self._calib_a, self._calib_b = 0.0, 1.0
        else:
            coeffs = np.polyfit(raw_scores, y[TARGET_POINTS].to_numpy(), deg=1)
            self._calib_b, self._calib_a = float(coeffs[0]), float(coeffs[1])

        self._fitted = True

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        """
        Return per-driver race predictions.

        features must include the same columns as X passed to fit(), including
        'driver_id' and 'predicted_quali_position'. 'event_index' is NOT required
        at predict time (only used during training for group size computation).

        Columns returned:
          driver_id                — integer driver identifier
          predicted_position       — predicted finishing rank (1.0 = race winner)
          predicted_fantasy_points — calibrated race fantasy points estimate
          confidence_lower         — q10 estimate (pessimistic bound)
          confidence_upper         — q90 estimate (optimistic bound)
        """
        if not self._fitted:
            raise RuntimeError("Call fit() before predict()")

        X = features[self._feature_cols]
        raw_scores = self._ranker.predict(X)
        calibrated_pts = self._calib_a + self._calib_b * raw_scores

        # Highest raw score → rank 1 → predicted race winner (P1).
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
