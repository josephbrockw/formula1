from __future__ import annotations

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from predictions.predictors.xgboost_v1 import (
    TARGET_POINTS,
    TARGET_POSITION,
    _NON_FEATURE_COLS,
)


class XGBoostPredictorV2:
    """
    Hybrid predictor: MSE mean for point estimates, quantile regression for confidence bounds.

    Trains four models:
      - _position_model: standard MSE regression for finishing position
      - _points_mean_model: standard MSE regression for fantasy points (expected value)
      - _points_q10: quantile regression at the 10th percentile (pessimistic bound)
      - _points_q90: quantile regression at the 90th percentile (optimistic bound)

    Semantics of output columns:
      - predicted_fantasy_points: expected value (mean) from the MSE model — the right
        target for a greedy expected-value optimizer ranking drivers by pts/price.
      - confidence_lower / confidence_upper: calibrated 80% prediction interval from
        q10/q90 quantile models. These are NOT symmetric around predicted_fantasy_points
        (unlike V1's ±std hack); they reflect the actual shape of the outcome distribution.

    Why mean for the point estimate, not median?
      F1 fantasy points are right-skewed. A driver who usually scores 8pts but occasionally
      hits 50pts (fastest lap + DotD + overtakes) has mean ~15pts but median ~8pts. The
      greedy optimizer maximises expected total points, so the mean is the correct signal.
      Using the median (pure quantile approach) undervalues high-upside drivers and produces
      worse lineups (verified in backtest: median-based V2 scored 7771 vs mean-based 8374
      total lineup points over 43 races in 2024–2025).

    Why quantile bounds, not ±std?
      V1's ±std bounds are symmetric and measure in-sample training error — always an
      underestimate of real uncertainty. Quantile regression learns where actual outcomes
      fall at the 10th/90th percentile, giving properly calibrated, asymmetric bounds.
      Wide bounds → high-variance driver (risky pick). Narrow bounds → consistent driver.
      A future risk-aware optimizer can exploit this: score = mean - λ·(upper - lower).
    """

    def __init__(self) -> None:
        # Hyperparameters tuned via tune_hyperparams --seasons 2022 2023 2024 2025
        # --feature-store v3 --n-iter 50 (2026-03-19).
        # CV MAE improved from 3.779 (depth=4 defaults) → 3.517 (Δ -0.262).
        # Key insight: depth=2 + column/row subsampling prevents overfitting on the
        # small training windows (100–400 rows) seen in early backtest splits.
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
        self._position_model = XGBRegressor(**_base)
        self._points_mean_model = XGBRegressor(**_base)
        self._points_q10 = XGBRegressor(
            objective="reg:quantileerror", quantile_alpha=0.1, **_base
        )
        self._points_q90 = XGBRegressor(
            objective="reg:quantileerror", quantile_alpha=0.9, **_base
        )
        self._feature_cols: list[str] = []
        self._fitted = False

    def fit(self, X: pd.DataFrame, y: pd.DataFrame) -> None:
        self._feature_cols = [c for c in X.columns if c not in _NON_FEATURE_COLS]
        X_train = X[self._feature_cols]
        self._position_model.fit(X_train, y[TARGET_POSITION])
        self._points_mean_model.fit(X_train, y[TARGET_POINTS])
        self._points_q10.fit(X_train, y[TARGET_POINTS])
        self._points_q90.fit(X_train, y[TARGET_POINTS])
        self._fitted = True

    def get_feature_importances(self) -> dict[str, float]:
        """Return {feature_name: importance} from the fantasy-points model, sorted descending."""
        if not self._fitted:
            return {}
        return dict(
            sorted(
                zip(self._feature_cols, self._points_mean_model.feature_importances_),
                key=lambda x: -x[1],
            )
        )

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        if not self._fitted:
            raise RuntimeError("Call fit() before predict()")
        X = features[self._feature_cols]
        # Enforce q10 ≤ q90 — independent models can cross on out-of-sample data.
        q10 = self._points_q10.predict(X)
        q90 = self._points_q90.predict(X)
        return pd.DataFrame(
            {
                "driver_id": features["driver_id"].to_numpy(),
                "predicted_position": self._position_model.predict(X),
                "predicted_fantasy_points": self._points_mean_model.predict(X),
                "confidence_lower": np.minimum(q10, q90),
                "confidence_upper": np.maximum(q10, q90),
            }
        )
