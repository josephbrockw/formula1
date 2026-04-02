from __future__ import annotations

import numpy as np
import pandas as pd
from django.conf import settings
from django.test import SimpleTestCase

from predictions.predictors.xgboost_v3 import XGBoostPredictorV3


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


def _make_X(n_rows: int = 20, seed: int = 0, with_event_index: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    data = {col: rng.uniform(1, 20, n_rows) for col in FEATURE_COLS}
    data["driver_id"] = list(range(1, n_rows + 1))
    if with_event_index:
        # Simulate 4 races of ~5 drivers each
        data["event_index"] = [i // 5 for i in range(n_rows)]
    return pd.DataFrame(data)


def _make_y(n_rows: int = 20, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "finishing_position": rng.uniform(1, 20, n_rows),
        "fantasy_points": rng.uniform(0, 60, n_rows),
    })


# ---------------------------------------------------------------------------
# Interface — same contract as V1/V2
# ---------------------------------------------------------------------------


class TestXGBoostPredictorV3Interface(SimpleTestCase):
    def test_fit_does_not_raise(self) -> None:
        XGBoostPredictorV3().fit(_make_X(100), _make_y(100))

    def test_predict_before_fit_raises_runtime_error(self) -> None:
        with self.assertRaises(RuntimeError):
            XGBoostPredictorV3().predict(_make_X())

    def test_predict_returns_dataframe(self) -> None:
        p = XGBoostPredictorV3()
        p.fit(_make_X(100), _make_y(100))
        self.assertIsInstance(p.predict(_make_X(20)), pd.DataFrame)

    def test_predict_returns_one_row_per_driver(self) -> None:
        p = XGBoostPredictorV3()
        p.fit(_make_X(100), _make_y(100))
        self.assertEqual(len(p.predict(_make_X(20))), 20)

    def test_predict_output_has_required_columns(self) -> None:
        p = XGBoostPredictorV3()
        p.fit(_make_X(100), _make_y(100))
        result = p.predict(_make_X(20))
        for col in ["driver_id", "predicted_position", "predicted_fantasy_points",
                    "confidence_lower", "confidence_upper"]:
            self.assertIn(col, result.columns, f"Missing column: {col}")

    def test_predict_driver_ids_match_input(self) -> None:
        p = XGBoostPredictorV3()
        p.fit(_make_X(100), _make_y(100))
        X = _make_X(20)
        result = p.predict(X)
        self.assertListEqual(list(result["driver_id"]), list(X["driver_id"]))

    def test_confidence_lower_less_than_upper(self) -> None:
        p = XGBoostPredictorV3()
        p.fit(_make_X(200), _make_y(200))
        result = p.predict(_make_X(50))
        self.assertTrue((result["confidence_lower"] < result["confidence_upper"]).all())


# ---------------------------------------------------------------------------
# Decay weights
# ---------------------------------------------------------------------------


class TestDecayWeights(SimpleTestCase):
    """V3's key innovation: older events get lower sample weights."""

    def test_decay_weights_shape_matches_rows(self) -> None:
        p = XGBoostPredictorV3()
        X = _make_X(20)
        weights = p._decay_weights(X)
        self.assertEqual(len(weights), 20)

    def test_most_recent_event_gets_weight_1(self) -> None:
        # The most recent event_index has max weight = exp(0) = 1.0
        p = XGBoostPredictorV3()
        X = _make_X(20)
        weights = p._decay_weights(X)
        max_idx = X["event_index"].max()
        recent_weights = weights[X["event_index"] == max_idx]
        self.assertTrue((np.abs(recent_weights - 1.0) < 1e-9).all())

    def test_older_events_get_lower_weights(self) -> None:
        p = XGBoostPredictorV3()
        X = _make_X(20)
        weights = p._decay_weights(X)
        # The oldest event should have lower weight than the most recent
        oldest_weight = weights[X["event_index"] == 0].mean()
        newest_weight = weights[X["event_index"] == X["event_index"].max()].mean()
        self.assertLess(oldest_weight, newest_weight)

    def test_missing_event_index_returns_uniform_weights(self) -> None:
        # V3 gracefully handles feature stores that don't produce event_index
        p = XGBoostPredictorV3()
        X = _make_X(20, with_event_index=False)
        weights = p._decay_weights(X)
        self.assertTrue((weights == 1.0).all())

    def test_half_life_controls_decay_rate(self) -> None:
        # With half_life=1, weight at 1 event back = 0.5; with half_life=100 it's near 1.0
        fast = XGBoostPredictorV3()
        fast._half_life = 1
        slow = XGBoostPredictorV3()
        slow._half_life = 100

        X = pd.DataFrame({
            "event_index": [0, 1],  # 2 events: old (0) and recent (1)
            **{col: [1.0, 1.0] for col in FEATURE_COLS},
            "driver_id": [1, 2],
        })
        w_fast = fast._decay_weights(X)
        w_slow = slow._decay_weights(X)
        # Fast decay: old event gets much smaller weight
        self.assertLess(w_fast[0], w_slow[0])
