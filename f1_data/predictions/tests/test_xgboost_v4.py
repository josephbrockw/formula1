from __future__ import annotations

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from predictions.predictors.xgboost_v4 import XGBoostPredictorV4, _compute_group_sizes


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

# Simulate 5 races of 20 drivers each
N_RACES = 5
N_DRIVERS = 20
N_ROWS = N_RACES * N_DRIVERS


def _make_X(n_races: int = N_RACES, n_drivers: int = N_DRIVERS, seed: int = 0) -> pd.DataFrame:
    n_rows = n_races * n_drivers
    rng = np.random.default_rng(seed)
    data = {col: rng.uniform(1, 20, n_rows) for col in FEATURE_COLS}
    data["driver_id"] = list(range(1, n_drivers + 1)) * n_races
    data["event_index"] = [i // n_drivers for i in range(n_rows)]
    return pd.DataFrame(data)


def _make_y(n_races: int = N_RACES, n_drivers: int = N_DRIVERS, seed: int = 1) -> pd.DataFrame:
    n_rows = n_races * n_drivers
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "finishing_position": rng.uniform(1, 20, n_rows),
        "fantasy_points": rng.uniform(0, 60, n_rows),
    })


# ---------------------------------------------------------------------------
# _compute_group_sizes helper
# ---------------------------------------------------------------------------


class TestComputeGroupSizes(SimpleTestCase):
    def test_uniform_groups(self) -> None:
        # 3 races of 4 drivers each
        X = pd.DataFrame({"event_index": [0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2]})
        sizes = _compute_group_sizes(X)
        np.testing.assert_array_equal(sizes, [4, 4, 4])

    def test_variable_group_sizes(self) -> None:
        # Race 0 has 3 drivers, race 1 has 5 drivers
        X = pd.DataFrame({"event_index": [0, 0, 0, 1, 1, 1, 1, 1]})
        sizes = _compute_group_sizes(X)
        np.testing.assert_array_equal(sizes, [3, 5])

    def test_single_group(self) -> None:
        X = pd.DataFrame({"event_index": [0] * 10})
        sizes = _compute_group_sizes(X)
        np.testing.assert_array_equal(sizes, [10])


# ---------------------------------------------------------------------------
# Interface — same contract as V1/V2/V3
# ---------------------------------------------------------------------------


class TestXGBoostPredictorV4Interface(SimpleTestCase):
    def test_fit_does_not_raise(self) -> None:
        XGBoostPredictorV4().fit(_make_X(), _make_y())

    def test_predict_before_fit_raises_runtime_error(self) -> None:
        with self.assertRaises(RuntimeError):
            XGBoostPredictorV4().predict(_make_X(n_races=1))

    def test_predict_returns_dataframe(self) -> None:
        p = XGBoostPredictorV4()
        p.fit(_make_X(), _make_y())
        self.assertIsInstance(p.predict(_make_X(n_races=1)), pd.DataFrame)

    def test_predict_returns_one_row_per_driver(self) -> None:
        p = XGBoostPredictorV4()
        p.fit(_make_X(), _make_y())
        result = p.predict(_make_X(n_races=1))
        self.assertEqual(len(result), N_DRIVERS)

    def test_predict_output_has_required_columns(self) -> None:
        p = XGBoostPredictorV4()
        p.fit(_make_X(), _make_y())
        result = p.predict(_make_X(n_races=1))
        for col in ["driver_id", "predicted_position", "predicted_fantasy_points",
                    "confidence_lower", "confidence_upper"]:
            self.assertIn(col, result.columns, f"Missing column: {col}")

    def test_predict_driver_ids_match_input(self) -> None:
        p = XGBoostPredictorV4()
        p.fit(_make_X(), _make_y())
        X = _make_X(n_races=1)
        result = p.predict(X)
        self.assertListEqual(list(result["driver_id"]), list(X["driver_id"]))

    def test_confidence_lower_less_than_upper(self) -> None:
        p = XGBoostPredictorV4()
        p.fit(_make_X(n_races=10), _make_y(n_races=10))
        result = p.predict(_make_X(n_races=1))
        self.assertTrue((result["confidence_lower"] < result["confidence_upper"]).all())


# ---------------------------------------------------------------------------
# Ranking behaviour
# ---------------------------------------------------------------------------


class TestXGBoostPredictorV4Ranking(SimpleTestCase):
    """V4's key innovation: a ranker model drives position prediction."""

    def test_predicted_positions_are_1_to_n(self) -> None:
        # Predicted positions should be a ranking of 1..N (possibly with ties on
        # identical inputs, but with varied inputs they should span the range).
        p = XGBoostPredictorV4()
        p.fit(_make_X(), _make_y())
        result = p.predict(_make_X(n_races=1))
        positions = sorted(result["predicted_position"].tolist())
        # Positions should be the integers 1..N (rank method="average" may produce
        # non-integer tied ranks, but min and max should be 1 and N).
        self.assertAlmostEqual(min(positions), 1.0, places=0)
        self.assertAlmostEqual(max(positions), float(N_DRIVERS), places=0)

    def test_ranking_order_consistent_with_scores(self) -> None:
        # A driver with better predicted_fantasy_points should have lower (better) position.
        p = XGBoostPredictorV4()
        p.fit(_make_X(), _make_y())
        result = p.predict(_make_X(n_races=1))
        # Rank by predicted_fantasy_points descending, check positions ascending
        sorted_by_pts = result.sort_values("predicted_fantasy_points", ascending=False)
        sorted_by_pos = result.sort_values("predicted_position", ascending=True)
        # The top-5 by pts should all appear in the top-5 by position
        top5_pts = set(sorted_by_pts["driver_id"].iloc[:5])
        top5_pos = set(sorted_by_pos["driver_id"].iloc[:5])
        overlap = len(top5_pts & top5_pos)
        self.assertGreaterEqual(overlap, 3, "Top-5 by pts and position should largely agree")

    def test_get_feature_importances_after_fit(self) -> None:
        p = XGBoostPredictorV4()
        p.fit(_make_X(), _make_y())
        importances = p.get_feature_importances()
        self.assertIsInstance(importances, dict)
        self.assertGreater(len(importances), 0)
        # All importance values are non-negative
        self.assertTrue(all(v >= 0 for v in importances.values()))

    def test_get_feature_importances_before_fit_returns_empty(self) -> None:
        self.assertEqual(XGBoostPredictorV4().get_feature_importances(), {})

    def test_fit_requires_event_index(self) -> None:
        # Without event_index, _compute_group_sizes will raise a KeyError
        X_no_idx = _make_X()
        X_no_idx = X_no_idx.drop(columns=["event_index"])
        with self.assertRaises(KeyError):
            XGBoostPredictorV4().fit(X_no_idx, _make_y())
