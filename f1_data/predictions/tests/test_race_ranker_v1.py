"""
Tests for RaceRankerV1.

These are pure unit tests — no database required, so we use SimpleTestCase.
The model takes a feature DataFrame (including predicted_quali_position) and
learns to rank drivers by their expected race outcome.

Test structure mirrors test_xgboost_v4.py: helper functions build synthetic
feature and target DataFrames, then each test exercises one behaviour.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from predictions.predictors.race_ranker.v1_race import RaceRankerV1

# Feature columns: all standard V4 features plus the new qualifying position.
# We use a short list of representative columns — the model only cares that
# the same columns appear in fit() and predict(), not that they're real values.
FEATURE_COLS = [
    "position_mean_last3",
    "position_mean_last5",
    "position_std_last5",
    "dnf_rate_last10",
    "qualifying_position_mean_last3",
    "circuit_position_mean_last3",
    "team_position_mean_last5",
    "fantasy_points_mean_last3",
    "practice_best_lap_rank",
    "circuit_length",
    "round_number",
    "predicted_quali_position",   # the new feature added by RaceV1FeatureStore
]

N_RACES = 5
N_DRIVERS = 20


def _make_X(n_races: int = N_RACES, n_drivers: int = N_DRIVERS, seed: int = 0) -> pd.DataFrame:
    """Synthetic feature DataFrame with varied values so the ranker can learn distinctions."""
    n_rows = n_races * n_drivers
    rng = np.random.default_rng(seed)
    data = {col: rng.uniform(1, 20, n_rows) for col in FEATURE_COLS}
    data["driver_id"] = list(range(1, n_drivers + 1)) * n_races
    data["event_index"] = [i // n_drivers for i in range(n_rows)]
    return pd.DataFrame(data)


def _make_y(n_races: int = N_RACES, n_drivers: int = N_DRIVERS, seed: int = 1) -> pd.DataFrame:
    """Synthetic race targets: finishing position (1–20) and fantasy points (0–60)."""
    n_rows = n_races * n_drivers
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "finishing_position": rng.uniform(1, 20, n_rows),
        "fantasy_points": rng.uniform(0, 60, n_rows),
    })


# ---------------------------------------------------------------------------
# Interface contract
# ---------------------------------------------------------------------------


class TestRaceRankerV1Interface(SimpleTestCase):
    """RaceRankerV1 must satisfy the same output contract as other predictors."""

    def test_fit_does_not_raise(self) -> None:
        RaceRankerV1().fit(_make_X(), _make_y())

    def test_predict_before_fit_raises_runtime_error(self) -> None:
        with self.assertRaises(RuntimeError):
            RaceRankerV1().predict(_make_X(n_races=1))

    def test_predict_returns_dataframe(self) -> None:
        m = RaceRankerV1()
        m.fit(_make_X(), _make_y())
        self.assertIsInstance(m.predict(_make_X(n_races=1)), pd.DataFrame)

    def test_predict_returns_one_row_per_driver(self) -> None:
        m = RaceRankerV1()
        m.fit(_make_X(), _make_y())
        result = m.predict(_make_X(n_races=1))
        self.assertEqual(len(result), N_DRIVERS)

    def test_predict_output_has_required_columns(self) -> None:
        m = RaceRankerV1()
        m.fit(_make_X(), _make_y())
        result = m.predict(_make_X(n_races=1))
        for col in ["driver_id", "predicted_position", "predicted_fantasy_points",
                    "confidence_lower", "confidence_upper"]:
            self.assertIn(col, result.columns, f"Missing column: {col}")

    def test_predict_driver_ids_match_input(self) -> None:
        m = RaceRankerV1()
        m.fit(_make_X(), _make_y())
        X = _make_X(n_races=1)
        result = m.predict(X)
        self.assertListEqual(list(result["driver_id"]), list(X["driver_id"]))

    def test_confidence_lower_leq_upper(self) -> None:
        """q10 bound must always be ≤ q90 bound (predict() enforces this with min/max)."""
        m = RaceRankerV1()
        m.fit(_make_X(n_races=10), _make_y(n_races=10))
        result = m.predict(_make_X(n_races=1))
        self.assertTrue((result["confidence_lower"] <= result["confidence_upper"]).all())


# ---------------------------------------------------------------------------
# Ranking behaviour
# ---------------------------------------------------------------------------


class TestRaceRankerV1Ranking(SimpleTestCase):
    """The core output of a ranker is a relative ordering, not absolute values."""

    def test_predicted_positions_span_1_to_n(self) -> None:
        # With varied inputs, predicted positions should span the full 1..N range.
        # (Tied inputs can produce averaged ranks, but min should be 1 and max N.)
        m = RaceRankerV1()
        m.fit(_make_X(), _make_y())
        result = m.predict(_make_X(n_races=1))
        positions = result["predicted_position"].tolist()
        self.assertAlmostEqual(min(positions), 1.0, places=0)
        self.assertAlmostEqual(max(positions), float(N_DRIVERS), places=0)

    def test_position_and_points_order_broadly_consistent(self) -> None:
        # A driver predicted to have more fantasy points should also have a lower
        # (better) predicted position. Test that the top-5 by each metric largely agree.
        m = RaceRankerV1()
        m.fit(_make_X(), _make_y())
        result = m.predict(_make_X(n_races=1))
        top5_by_pts = set(result.nlargest(5, "predicted_fantasy_points")["driver_id"])
        top5_by_pos = set(result.nsmallest(5, "predicted_position")["driver_id"])
        overlap = len(top5_by_pts & top5_by_pos)
        self.assertGreaterEqual(overlap, 3, "Top-5 by pts and position should largely agree")

    def test_predicted_positions_are_unique_with_varied_inputs(self) -> None:
        # With sufficiently varied inputs, no two drivers should share the same rank.
        m = RaceRankerV1()
        m.fit(_make_X(), _make_y())
        result = m.predict(_make_X(n_races=1))
        positions = result["predicted_position"].tolist()
        self.assertEqual(len(positions), len(set(positions)), "Expected no tied ranks")


# ---------------------------------------------------------------------------
# Feature importances
# ---------------------------------------------------------------------------


class TestRaceRankerV1FeatureImportances(SimpleTestCase):

    def test_importances_before_fit_returns_empty(self) -> None:
        self.assertEqual(RaceRankerV1().get_feature_importances(), {})

    def test_importances_after_fit_is_dict(self) -> None:
        m = RaceRankerV1()
        m.fit(_make_X(), _make_y())
        importances = m.get_feature_importances()
        self.assertIsInstance(importances, dict)
        self.assertGreater(len(importances), 0)

    def test_importances_are_non_negative(self) -> None:
        m = RaceRankerV1()
        m.fit(_make_X(), _make_y())
        importances = m.get_feature_importances()
        self.assertTrue(all(v >= 0 for v in importances.values()))

    def test_predicted_quali_position_appears_in_importances(self) -> None:
        """predicted_quali_position must be treated as a feature, not stripped."""
        m = RaceRankerV1()
        m.fit(_make_X(), _make_y())
        importances = m.get_feature_importances()
        self.assertIn("predicted_quali_position", importances)


# ---------------------------------------------------------------------------
# Degenerate training set guard
# ---------------------------------------------------------------------------


class TestRaceRankerV1DegenerateGuard(SimpleTestCase):
    """
    With a tiny training set (one race), XGBRanker may produce identical raw
    scores for all drivers. The calibration guard must prevent np.polyfit from
    failing on zero-variance input.
    """

    def test_single_race_training_does_not_raise(self) -> None:
        # 1 race = minimum possible training data; all ranker scores may be equal.
        m = RaceRankerV1()
        m.fit(_make_X(n_races=1), _make_y(n_races=1))
        # predict() must still work (calibration stays at identity mapping)
        result = m.predict(_make_X(n_races=1))
        self.assertEqual(len(result), N_DRIVERS)
