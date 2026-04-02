from __future__ import annotations

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from predictions.predictors.xgboost.v2 import XGBoostPredictorV2


FEATURE_COLS = [
    "position_mean_last3",
    "position_mean_last5",
    "position_std_last5",
    "dnf_rate_last10",
    "positions_gained_mean_last5",
    "qualifying_position_mean_last3",
    "circuit_position_mean_last3",
    "team_position_mean_last5",
    "fantasy_points_mean_last3",
    "practice_best_lap_rank",
    "practice_avg_best_5_rank",
    "circuit_length",
    "total_corners",
    "round_number",
    "is_sprint_weekend",
]


def _make_X(n_rows: int = 20, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    data = {col: rng.uniform(1, 20, n_rows) for col in FEATURE_COLS}
    data["driver_id"] = list(range(1, n_rows + 1))
    return pd.DataFrame(data)


def _make_y(n_rows: int = 20, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "finishing_position": rng.uniform(1, 20, n_rows),
            "fantasy_points": rng.uniform(0, 60, n_rows),
        }
    )


class TestXGBoostPredictorV2Interface(SimpleTestCase):
    def test_fit_does_not_raise(self) -> None:
        predictor = XGBoostPredictorV2()
        predictor.fit(_make_X(100), _make_y(100))

    def test_predict_before_fit_raises_runtime_error(self) -> None:
        predictor = XGBoostPredictorV2()
        with self.assertRaises(RuntimeError):
            predictor.predict(_make_X())

    def test_predict_returns_dataframe(self) -> None:
        predictor = XGBoostPredictorV2()
        predictor.fit(_make_X(100), _make_y(100))
        result = predictor.predict(_make_X(20))
        self.assertIsInstance(result, pd.DataFrame)

    def test_predict_returns_one_row_per_driver(self) -> None:
        predictor = XGBoostPredictorV2()
        predictor.fit(_make_X(100), _make_y(100))
        result = predictor.predict(_make_X(20))
        self.assertEqual(len(result), 20)

    def test_predict_output_has_required_columns(self) -> None:
        predictor = XGBoostPredictorV2()
        predictor.fit(_make_X(100), _make_y(100))
        result = predictor.predict(_make_X(20))
        for col in ["driver_id", "predicted_position", "predicted_fantasy_points",
                    "confidence_lower", "confidence_upper"]:
            self.assertIn(col, result.columns, f"Missing column: {col}")

    def test_predict_driver_ids_match_input(self) -> None:
        predictor = XGBoostPredictorV2()
        predictor.fit(_make_X(100), _make_y(100))
        X = _make_X(20)
        result = predictor.predict(X)
        self.assertListEqual(list(result["driver_id"]), list(X["driver_id"]))


class TestXGBoostPredictorV2QuantileBounds(SimpleTestCase):
    def setUp(self) -> None:
        self.predictor = XGBoostPredictorV2()
        self.predictor.fit(_make_X(200), _make_y(200))
        self.result = self.predictor.predict(_make_X(50))

    def test_confidence_lower_strictly_less_than_upper(self) -> None:
        self.assertTrue((self.result["confidence_lower"] < self.result["confidence_upper"]).all())

    def test_confidence_interval_width_varies_across_drivers(self) -> None:
        # V1 used ±1 std dev — every driver got the same interval width.
        # Quantile regression learns heteroskedasticity: high-variance drivers get wider
        # bands, consistent drivers get narrower ones. Width must not be constant.
        widths = self.result["confidence_upper"] - self.result["confidence_lower"]
        self.assertGreater(float(widths.std()), 0.01)

    def test_predicted_fantasy_points_is_independent_of_bounds(self) -> None:
        # predicted_fantasy_points is the MSE mean (expected value), not the q50 median.
        # It is NOT required to sit inside [confidence_lower, confidence_upper] — for
        # right-skewed drivers the mean can exceed the 90th percentile on some inputs.
        # This test documents that intentional design: we verify the mean comes from a
        # separate model by checking it differs from (lower + upper) / 2 on at least one row.
        midpoint = (self.result["confidence_lower"] + self.result["confidence_upper"]) / 2
        diffs = (self.result["predicted_fantasy_points"] - midpoint).abs()
        self.assertTrue((diffs > 1e-6).any())
