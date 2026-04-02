from __future__ import annotations

import numpy as np
import pandas as pd
from django.test import SimpleTestCase, TestCase

from predictions.predictors.xgboost.shared import build_training_dataset, walk_forward_splits
from predictions.predictors.xgboost.v1 import XGBoostPredictor
from predictions.tests.factories import (
    make_driver,
    make_event,
    make_fantasy_score,
    make_result,
    make_season,
    make_session,
    make_team,
)


# ---------------------------------------------------------------------------
# Helpers — synthetic DataFrames (no DB needed for fit/predict tests)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# XGBoostPredictor — fit / predict interface
# ---------------------------------------------------------------------------


class TestXGBoostPredictorFit(SimpleTestCase):
    def test_fit_does_not_raise(self) -> None:
        predictor = XGBoostPredictor()
        predictor.fit(_make_X(100), _make_y(100))

    def test_predict_before_fit_raises(self) -> None:
        predictor = XGBoostPredictor()
        with self.assertRaises(RuntimeError):
            predictor.predict(_make_X())

    def test_fit_then_predict_returns_dataframe(self) -> None:
        predictor = XGBoostPredictor()
        predictor.fit(_make_X(100), _make_y(100))
        result = predictor.predict(_make_X(20))
        self.assertIsInstance(result, pd.DataFrame)

    def test_predict_returns_one_row_per_driver(self) -> None:
        predictor = XGBoostPredictor()
        predictor.fit(_make_X(100), _make_y(100))
        result = predictor.predict(_make_X(20))
        self.assertEqual(len(result), 20)

    def test_predict_output_has_required_columns(self) -> None:
        predictor = XGBoostPredictor()
        predictor.fit(_make_X(100), _make_y(100))
        result = predictor.predict(_make_X(20))
        for col in ["driver_id", "predicted_position", "predicted_fantasy_points",
                    "confidence_lower", "confidence_upper"]:
            self.assertIn(col, result.columns, f"Missing column: {col}")

    def test_predict_driver_ids_match_input(self) -> None:
        predictor = XGBoostPredictor()
        predictor.fit(_make_X(100), _make_y(100))
        X = _make_X(20)
        result = predictor.predict(X)
        self.assertListEqual(list(result["driver_id"]), list(X["driver_id"]))

    def test_confidence_lower_always_below_upper(self) -> None:
        predictor = XGBoostPredictor()
        predictor.fit(_make_X(100), _make_y(100))
        result = predictor.predict(_make_X(20))
        self.assertTrue((result["confidence_lower"] < result["confidence_upper"]).all())

    def test_confidence_interval_brackets_prediction(self) -> None:
        predictor = XGBoostPredictor()
        predictor.fit(_make_X(100), _make_y(100))
        result = predictor.predict(_make_X(20))
        self.assertTrue((result["confidence_lower"] <= result["predicted_fantasy_points"]).all())
        self.assertTrue((result["predicted_fantasy_points"] <= result["confidence_upper"]).all())


# ---------------------------------------------------------------------------
# walk_forward_splits utility
# ---------------------------------------------------------------------------


class TestWalkForwardSplits(SimpleTestCase):
    def _events(self, n: int) -> list:
        return list(range(n))  # dummy objects — function only uses list slicing

    def test_yields_correct_number_of_splits(self) -> None:
        splits = list(walk_forward_splits(self._events(10), min_train=5))
        # 10 events, min_train=5 → test events are indices 5..9 → 5 splits
        self.assertEqual(len(splits), 5)

    def test_first_split_has_min_train_events(self) -> None:
        splits = list(walk_forward_splits(self._events(10), min_train=5))
        train, test = splits[0]
        self.assertEqual(len(train), 5)

    def test_train_grows_by_one_each_split(self) -> None:
        splits = list(walk_forward_splits(self._events(8), min_train=3))
        for i, (train, _) in enumerate(splits):
            self.assertEqual(len(train), 3 + i)

    def test_test_event_is_never_in_train(self) -> None:
        splits = list(walk_forward_splits(self._events(8), min_train=3))
        for train, test in splits:
            self.assertNotIn(test, train)

    def test_no_splits_when_not_enough_events(self) -> None:
        splits = list(walk_forward_splits(self._events(4), min_train=5))
        self.assertEqual(len(splits), 0)


# ---------------------------------------------------------------------------
# build_training_dataset — DB integration
# ---------------------------------------------------------------------------


class TestBuildTrainingDataset(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season)
        self.driver = make_driver(self.season, self.team, code="VER", driver_number=1)

    def _make_event_with_results(self, round_number: int, position: int, fantasy_pts: int) -> object:
        from datetime import date
        event = make_event(self.season, round_number=round_number, event_date=date(2024, round_number, 1))
        session = make_session(event, session_type="R")
        make_result(session, self.driver, self.team, position=position)
        make_fantasy_score(self.driver, event, race_total=fantasy_pts)
        return event

    def test_returns_X_and_y_with_matching_row_counts(self) -> None:
        from predictions.features.v1_pandas import V1FeatureStore
        events = [self._make_event_with_results(i, position=i, fantasy_pts=50 - i * 2) for i in range(1, 4)]
        X, y = build_training_dataset(events, V1FeatureStore())
        self.assertEqual(len(X), len(y))

    def test_y_has_required_target_columns(self) -> None:
        from predictions.features.v1_pandas import V1FeatureStore
        events = [self._make_event_with_results(1, position=1, fantasy_pts=45)]
        _, y = build_training_dataset(events, V1FeatureStore())
        self.assertIn("finishing_position", y.columns)
        self.assertIn("fantasy_points", y.columns)

    def test_events_with_no_fantasy_data_use_position_estimate(self) -> None:
        from datetime import date
        from predictions.features.v1_pandas import V1FeatureStore
        # Event with race result but NO fantasy score — still produces a row,
        # using the position-based fallback estimate (P1 = 25 base points).
        event = make_event(self.season, round_number=1, event_date=date(2024, 1, 1))
        session = make_session(event, session_type="R")
        make_result(session, self.driver, self.team, position=1)
        X, y = build_training_dataset([event], V1FeatureStore())
        self.assertEqual(len(X), 1)
        self.assertEqual(float(y.iloc[0]["fantasy_points"]), 25.0)
