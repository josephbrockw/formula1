from __future__ import annotations

import math

import pandas as pd
from django.test import SimpleTestCase

from predictions.evaluation.metrics import (
    RankMetrics,
    _ndcg_at_10,
    _spearman_rho,
    _top10_precision,
    _top10_recall,
    compute_rank_metrics,
)


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_predictions(rows: list[dict]) -> pd.DataFrame:
    """Build a predictions DataFrame from a list of dicts.

    Each dict must contain driver_id, predicted_position,
    predicted_fantasy_points.
    """
    return pd.DataFrame(rows)


def _twenty_driver_field() -> tuple[pd.DataFrame, dict]:
    """Return a standard 20-driver field where predictions are perfect.

    Drivers are numbered 1-20.  Driver 1 is predicted (and actually) 1st,
    driver 2 is predicted 2nd, and so on.  Fantasy points decrease linearly
    so driver 1 scores 20 pts, driver 2 scores 19 pts, …, driver 20 scores 1 pt.
    """
    rows = [
        {
            "driver_id": i,
            "predicted_position": i,
            "predicted_fantasy_points": 21 - i,  # driver 1 → 20 pts, driver 20 → 1 pt
        }
        for i in range(1, 21)
    ]
    predictions = _make_predictions(rows)
    actuals = {i: (float(i), float(21 - i)) for i in range(1, 21)}
    return predictions, actuals


def _reversed_twenty_driver_field() -> tuple[pd.DataFrame, dict]:
    """Return a 20-driver field where predictions are completely reversed.

    The driver we predict will finish 1st actually finishes 20th, etc.
    """
    rows = [
        {
            "driver_id": i,
            "predicted_position": 21 - i,   # reversed predicted position
            "predicted_fantasy_points": i,   # reversed predicted fantasy pts
        }
        for i in range(1, 21)
    ]
    predictions = _make_predictions(rows)
    actuals = {i: (float(i), float(21 - i)) for i in range(1, 21)}
    return predictions, actuals


# ---------------------------------------------------------------------------
# _spearman_rho
# ---------------------------------------------------------------------------


class TestSpearmanRho(SimpleTestCase):

    def test_perfect_prediction_returns_one(self) -> None:
        """When predicted_position matches actual finishing order exactly, ρ = 1.0."""
        predictions, actuals = _twenty_driver_field()
        rho = _spearman_rho(predictions, actuals)
        self.assertAlmostEqual(rho, 1.0, places=5)

    def test_reversed_prediction_returns_minus_one(self) -> None:
        """When predicted_position is the exact reverse of actual order, ρ = -1.0."""
        predictions, actuals = _reversed_twenty_driver_field()
        # In the reversed field the predicted_position for driver i is (21 - i),
        # which is the reverse of actuals[i][0] = i.  So ρ should be -1.
        rho = _spearman_rho(predictions, actuals)
        self.assertAlmostEqual(rho, -1.0, places=5)

    def test_fewer_than_two_matched_drivers_returns_zero(self) -> None:
        """With only one driver present in both sets, ρ is undefined — return 0.0."""
        predictions = _make_predictions([
            {"driver_id": 1, "predicted_position": 1, "predicted_fantasy_points": 20},
        ])
        actuals = {1: (1.0, 20.0)}
        self.assertEqual(_spearman_rho(predictions, actuals), 0.0)

    def test_no_matched_drivers_returns_zero(self) -> None:
        """When no predictions match any actual driver, return 0.0."""
        predictions = _make_predictions([
            {"driver_id": 99, "predicted_position": 1, "predicted_fantasy_points": 20},
        ])
        actuals = {1: (1.0, 20.0), 2: (2.0, 15.0)}
        self.assertEqual(_spearman_rho(predictions, actuals), 0.0)

    def test_drivers_in_predictions_but_not_actuals_are_ignored(self) -> None:
        """Extra drivers in predictions that have no actual result are silently dropped."""
        # Drivers 1-20 are a perfect prediction; driver 99 has no actual.
        predictions, actuals = _twenty_driver_field()
        extra_row = pd.DataFrame([{
            "driver_id": 99,
            "predicted_position": 5,
            "predicted_fantasy_points": 50,
        }])
        predictions = pd.concat([predictions, extra_row], ignore_index=True)
        rho = _spearman_rho(predictions, actuals)
        self.assertAlmostEqual(rho, 1.0, places=5)

    def test_two_matched_drivers_perfect_order(self) -> None:
        """Minimum valid case: two perfectly-ordered drivers → ρ = 1.0."""
        predictions = _make_predictions([
            {"driver_id": 1, "predicted_position": 1, "predicted_fantasy_points": 20},
            {"driver_id": 2, "predicted_position": 2, "predicted_fantasy_points": 15},
        ])
        actuals = {1: (1.0, 20.0), 2: (2.0, 15.0)}
        rho = _spearman_rho(predictions, actuals)
        self.assertAlmostEqual(rho, 1.0, places=5)

    def test_two_matched_drivers_reversed_order(self) -> None:
        """Minimum valid case: two reversed drivers → ρ = -1.0."""
        predictions = _make_predictions([
            {"driver_id": 1, "predicted_position": 2, "predicted_fantasy_points": 15},
            {"driver_id": 2, "predicted_position": 1, "predicted_fantasy_points": 20},
        ])
        actuals = {1: (1.0, 20.0), 2: (2.0, 15.0)}
        rho = _spearman_rho(predictions, actuals)
        self.assertAlmostEqual(rho, -1.0, places=5)

    def test_rho_lies_between_minus_one_and_one(self) -> None:
        """For any realistic input, ρ must stay in [-1, 1]."""
        # Partially correct: first 10 right, last 10 random
        rows = [
            {"driver_id": i, "predicted_position": float(i if i <= 10 else 21 - i),
             "predicted_fantasy_points": float(21 - i)}
            for i in range(1, 21)
        ]
        predictions = _make_predictions(rows)
        actuals = {i: (float(i), float(21 - i)) for i in range(1, 21)}
        rho = _spearman_rho(predictions, actuals)
        self.assertGreaterEqual(rho, -1.0)
        self.assertLessEqual(rho, 1.0)


# ---------------------------------------------------------------------------
# _top10_precision
# ---------------------------------------------------------------------------


class TestTop10Precision(SimpleTestCase):

    def test_perfect_overlap_returns_one(self) -> None:
        """Our predicted top 10 by fantasy pts == actual top 10 → precision 1.0."""
        predictions, actuals = _twenty_driver_field()
        self.assertAlmostEqual(_top10_precision(predictions, actuals), 1.0, places=5)

    def test_no_overlap_returns_zero(self) -> None:
        """Our predicted top 10 are drivers 11-20 in reality → precision 0.0."""
        # Reverse fantasy points in predictions only so our top 10 becomes the
        # actual bottom 10.
        rows = [
            {
                "driver_id": i,
                "predicted_position": i,
                "predicted_fantasy_points": float(i),  # driver 1 gets 1 pt (lowest)
            }
            for i in range(1, 21)
        ]
        predictions = _make_predictions(rows)
        # actuals: driver 1 scores 20 pts (top), driver 20 scores 1 pt (bottom)
        actuals = {i: (float(i), float(21 - i)) for i in range(1, 21)}
        # Our top 10 predicted (by predicted_fantasy_points) = drivers 11-20
        # Actual top 10 = drivers 1-10
        self.assertAlmostEqual(_top10_precision(predictions, actuals), 0.0, places=5)

    def test_partial_overlap_returns_correct_fraction(self) -> None:
        """6 of 10 predicted drivers are in the actual top 10 → precision 0.6."""
        # Drivers 1-10 are the actual top scorers (20, 19, … 11 pts).
        # We correctly predict 6 of them (drivers 1-6) and wrongly put 4 others
        # (drivers 11-14) in our predicted top 10.
        rows = []
        # Correctly predicted top 6: drivers 1-6, high fantasy pts
        for i in range(1, 7):
            rows.append({"driver_id": i, "predicted_position": i, "predicted_fantasy_points": 50.0 - i})
        # Wrong picks: drivers 11-14 falsely given high predicted pts
        for i in range(11, 15):
            rows.append({"driver_id": i, "predicted_position": i, "predicted_fantasy_points": 40.0 - i})
        # Remaining 10 drivers get low predicted pts (outside our predicted top 10)
        for i in list(range(7, 11)) + list(range(15, 21)):
            rows.append({"driver_id": i, "predicted_position": i, "predicted_fantasy_points": float(i)})
        predictions = _make_predictions(rows)
        actuals = {i: (float(i), float(21 - i)) for i in range(1, 21)}
        self.assertAlmostEqual(_top10_precision(predictions, actuals), 0.6, places=5)

    def test_fewer_than_ten_matched_drivers_returns_zero(self) -> None:
        """With only 9 matched drivers, precision is undefined — return 0.0."""
        rows = [
            {"driver_id": i, "predicted_position": i, "predicted_fantasy_points": float(10 - i)}
            for i in range(1, 10)  # only 9 drivers
        ]
        predictions = _make_predictions(rows)
        actuals = {i: (float(i), float(10 - i)) for i in range(1, 10)}
        self.assertEqual(_top10_precision(predictions, actuals), 0.0)

    def test_fewer_than_ten_actuals_returns_zero(self) -> None:
        """With fewer than 10 actual drivers, actual_top10 is empty — return 0.0."""
        rows = [
            {"driver_id": i, "predicted_position": i, "predicted_fantasy_points": float(20 - i)}
            for i in range(1, 21)
        ]
        predictions = _make_predictions(rows)
        # Only 9 actuals
        actuals = {i: (float(i), float(20 - i)) for i in range(1, 10)}
        self.assertEqual(_top10_precision(predictions, actuals), 0.0)


# ---------------------------------------------------------------------------
# _top10_recall
# ---------------------------------------------------------------------------


class TestTop10Recall(SimpleTestCase):

    def test_perfect_recall_returns_one(self) -> None:
        """Perfect prediction — we recall all 10 actual top scorers → 1.0."""
        predictions, actuals = _twenty_driver_field()
        self.assertAlmostEqual(_top10_recall(predictions, actuals), 1.0, places=5)

    def test_no_overlap_returns_zero(self) -> None:
        """No actual top-10 scorer in our predicted top 10 → recall 0.0."""
        rows = [
            {
                "driver_id": i,
                "predicted_position": i,
                "predicted_fantasy_points": float(i),  # driver 20 gets highest predicted pts
            }
            for i in range(1, 21)
        ]
        predictions = _make_predictions(rows)
        actuals = {i: (float(i), float(21 - i)) for i in range(1, 21)}
        self.assertAlmostEqual(_top10_recall(predictions, actuals), 0.0, places=5)

    def test_partial_recall_returns_correct_fraction(self) -> None:
        """6 actual top-10 drivers correctly recalled → 0.6."""
        rows = []
        for i in range(1, 7):
            rows.append({"driver_id": i, "predicted_position": i, "predicted_fantasy_points": 50.0 - i})
        for i in range(11, 15):
            rows.append({"driver_id": i, "predicted_position": i, "predicted_fantasy_points": 40.0 - i})
        for i in list(range(7, 11)) + list(range(15, 21)):
            rows.append({"driver_id": i, "predicted_position": i, "predicted_fantasy_points": float(i)})
        predictions = _make_predictions(rows)
        actuals = {i: (float(i), float(21 - i)) for i in range(1, 21)}
        self.assertAlmostEqual(_top10_recall(predictions, actuals), 0.6, places=5)

    def test_fewer_than_ten_matched_drivers_returns_zero(self) -> None:
        """With only 9 matched drivers, recall is undefined — return 0.0."""
        rows = [
            {"driver_id": i, "predicted_position": i, "predicted_fantasy_points": float(10 - i)}
            for i in range(1, 10)
        ]
        predictions = _make_predictions(rows)
        actuals = {i: (float(i), float(10 - i)) for i in range(1, 10)}
        self.assertEqual(_top10_recall(predictions, actuals), 0.0)

    def test_fewer_than_ten_actuals_returns_zero(self) -> None:
        """With fewer than 10 actual drivers, actual_top10 is empty — return 0.0."""
        rows = [
            {"driver_id": i, "predicted_position": i, "predicted_fantasy_points": float(20 - i)}
            for i in range(1, 21)
        ]
        predictions = _make_predictions(rows)
        actuals = {i: (float(i), float(20 - i)) for i in range(1, 10)}
        self.assertEqual(_top10_recall(predictions, actuals), 0.0)

    def test_precision_and_recall_agree_on_perfect_prediction(self) -> None:
        """On a perfect 20-driver field precision and recall must both be 1.0."""
        predictions, actuals = _twenty_driver_field()
        self.assertAlmostEqual(_top10_precision(predictions, actuals), 1.0, places=5)
        self.assertAlmostEqual(_top10_recall(predictions, actuals), 1.0, places=5)


# ---------------------------------------------------------------------------
# _ndcg_at_10
# ---------------------------------------------------------------------------


class TestNdcgAt10(SimpleTestCase):

    def test_perfect_ranking_returns_one(self) -> None:
        """When we rank drivers in exactly the right order by fantasy pts, NDCG = 1.0."""
        predictions, actuals = _twenty_driver_field()
        # _twenty_driver_field has predicted_fantasy_points = 21 - i, matching actuals.
        self.assertAlmostEqual(_ndcg_at_10(predictions, actuals), 1.0, places=5)

    def test_fewer_than_two_matched_drivers_returns_zero(self) -> None:
        """NDCG is undefined with a single driver — return 0.0."""
        predictions = _make_predictions([
            {"driver_id": 1, "predicted_position": 1, "predicted_fantasy_points": 20.0},
        ])
        actuals = {1: (1.0, 20.0)}
        self.assertEqual(_ndcg_at_10(predictions, actuals), 0.0)

    def test_no_matched_drivers_returns_zero(self) -> None:
        """No intersection between predictions and actuals — return 0.0."""
        predictions = _make_predictions([
            {"driver_id": 99, "predicted_position": 1, "predicted_fantasy_points": 20.0},
        ])
        actuals = {1: (1.0, 20.0), 2: (2.0, 15.0)}
        self.assertEqual(_ndcg_at_10(predictions, actuals), 0.0)

    def test_all_actual_points_zero_returns_zero(self) -> None:
        """When IDCG = 0 (all actual pts are 0), return 0.0 to avoid division by zero."""
        rows = [
            {"driver_id": i, "predicted_position": i, "predicted_fantasy_points": float(20 - i)}
            for i in range(1, 21)
        ]
        predictions = _make_predictions(rows)
        actuals = {i: (float(i), 0.0) for i in range(1, 21)}
        self.assertEqual(_ndcg_at_10(predictions, actuals), 0.0)

    def test_reversed_ranking_is_less_than_perfect(self) -> None:
        """A completely reversed ranking should score strictly below 1.0."""
        predictions, actuals = _reversed_twenty_driver_field()
        # Reversed: driver 20 gets highest predicted_fantasy_points = 20, but
        # driver 20 actually scores 1 pt — the worst pick is ranked first.
        ndcg = _ndcg_at_10(predictions, actuals)
        self.assertGreater(ndcg, 0.0)   # not zero — some points still accrue
        self.assertLess(ndcg, 1.0)      # definitely not perfect

    def test_ndcg_is_bounded_between_zero_and_one(self) -> None:
        """NDCG@10 must always be in [0, 1]."""
        predictions, actuals = _twenty_driver_field()
        ndcg = _ndcg_at_10(predictions, actuals)
        self.assertGreaterEqual(ndcg, 0.0)
        self.assertLessEqual(ndcg, 1.0)

    def test_ndcg_manually_computed_three_drivers(self) -> None:
        """Verify the DCG/IDCG arithmetic against a hand-calculated example.

        Three drivers, k=3:
          Our ranking (by predicted_fantasy_points desc): driver 1 (10 pts), driver 2 (6 pts), driver 3 (2 pts)
          Actual fantasy pts:                            driver 1 →  10,      driver 2 →  6,      driver 3 →  2

        This is a perfect ranking, so NDCG should be 1.0 by definition.  We
        verify by also computing it from first principles so we know the
        arithmetic is right.

          DCG  = 10/log2(2) + 6/log2(3) + 2/log2(4) = 10/1 + 6/1.585 + 2/2
               = 10.0 + 3.785 + 1.0 = 14.785
          IDCG = same (perfect order) = 14.785
          NDCG = 1.0
        """
        predictions = _make_predictions([
            {"driver_id": 1, "predicted_position": 1, "predicted_fantasy_points": 10.0},
            {"driver_id": 2, "predicted_position": 2, "predicted_fantasy_points": 6.0},
            {"driver_id": 3, "predicted_position": 3, "predicted_fantasy_points": 2.0},
        ])
        actuals = {1: (1.0, 10.0), 2: (2.0, 6.0), 3: (3.0, 2.0)}
        self.assertAlmostEqual(_ndcg_at_10(predictions, actuals), 1.0, places=5)

    def test_ndcg_manually_computed_imperfect_ranking(self) -> None:
        """Verify imperfect ranking arithmetic against a hand-calculated value.

        Three drivers, k=3.  Actual fantasy pts: driver 1=10, driver 2=6, driver 3=2.
        Our ranking puts driver 3 first, then driver 1, then driver 2.

          Our DCG  = 2/log2(2) + 10/log2(3) + 6/log2(4)
                   = 2/1 + 10/1.58496 + 6/2
                   = 2.0 + 6.30930 + 3.0 = 11.30930
          Ideal    = 10/log2(2) + 6/log2(3) + 2/log2(4)
                   = 10.0 + 3.78512 + 1.0 = 14.78512
          NDCG     = 11.30930 / 14.78512 ≈ 0.76494
        """
        predictions = _make_predictions([
            {"driver_id": 3, "predicted_position": 1, "predicted_fantasy_points": 100.0},  # wrong pick #1
            {"driver_id": 1, "predicted_position": 2, "predicted_fantasy_points": 50.0},   # correct pick #2
            {"driver_id": 2, "predicted_position": 3, "predicted_fantasy_points": 10.0},   # wrong pick #3
        ])
        actuals = {1: (1.0, 10.0), 2: (2.0, 6.0), 3: (3.0, 2.0)}
        expected_ndcg = (
            (2.0 / math.log2(2) + 10.0 / math.log2(3) + 6.0 / math.log2(4))
            / (10.0 / math.log2(2) + 6.0 / math.log2(3) + 2.0 / math.log2(4))
        )
        self.assertAlmostEqual(_ndcg_at_10(predictions, actuals), expected_ndcg, places=5)


# ---------------------------------------------------------------------------
# compute_rank_metrics (integration — tests that all four metrics assemble)
# ---------------------------------------------------------------------------


class TestComputeRankMetrics(SimpleTestCase):

    def test_returns_rank_metrics_dataclass(self) -> None:
        """compute_rank_metrics must return a RankMetrics instance."""
        predictions, actuals = _twenty_driver_field()
        result = compute_rank_metrics(predictions, actuals)
        self.assertIsInstance(result, RankMetrics)

    def test_perfect_prediction_all_metrics_at_maximum(self) -> None:
        """On a perfect 20-driver prediction every metric hits its maximum value."""
        predictions, actuals = _twenty_driver_field()
        result = compute_rank_metrics(predictions, actuals)
        self.assertAlmostEqual(result.spearman_rho, 1.0, places=5)
        self.assertAlmostEqual(result.top10_precision, 1.0, places=5)
        self.assertAlmostEqual(result.top10_recall, 1.0, places=5)
        self.assertAlmostEqual(result.ndcg_at_10, 1.0, places=5)

    def test_all_metrics_are_floats(self) -> None:
        """Every field of RankMetrics must be a float (not int or None)."""
        predictions, actuals = _twenty_driver_field()
        result = compute_rank_metrics(predictions, actuals)
        self.assertIsInstance(result.spearman_rho, float)
        self.assertIsInstance(result.top10_precision, float)
        self.assertIsInstance(result.top10_recall, float)
        self.assertIsInstance(result.ndcg_at_10, float)

    def test_spearman_rho_in_valid_range(self) -> None:
        """spearman_rho must always be in [-1, 1]."""
        predictions, actuals = _twenty_driver_field()
        result = compute_rank_metrics(predictions, actuals)
        self.assertGreaterEqual(result.spearman_rho, -1.0)
        self.assertLessEqual(result.spearman_rho, 1.0)

    def test_precision_recall_ndcg_in_unit_interval(self) -> None:
        """top10_precision, top10_recall, ndcg_at_10 must all be in [0, 1]."""
        predictions, actuals = _twenty_driver_field()
        result = compute_rank_metrics(predictions, actuals)
        for name, value in [
            ("top10_precision", result.top10_precision),
            ("top10_recall", result.top10_recall),
            ("ndcg_at_10", result.ndcg_at_10),
        ]:
            with self.subTest(metric=name):
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)
