from __future__ import annotations

from decimal import Decimal

from django.test import SimpleTestCase

from predictions.predictors.price_heuristic import predict_price_trajectory


class TestPredictPriceTrajectory(SimpleTestCase):
    def test_returns_one_price_per_predicted_race(self) -> None:
        prices = predict_price_trajectory(Decimal("10.0"), [], [30.0, 30.0, 30.0])
        self.assertEqual(len(prices), 3)

    def test_empty_predicted_points_returns_empty(self) -> None:
        prices = predict_price_trajectory(Decimal("10.0"), [], [])
        self.assertEqual(prices, [])

    def test_great_recent_history_raises_price(self) -> None:
        # 30 pts at $10M = 3.0 PPM → great → B-tier +0.6 per race
        recent = [(30.0, Decimal("10.0")), (30.0, Decimal("10.0")), (30.0, Decimal("10.0"))]
        prices = predict_price_trajectory(Decimal("10.0"), recent, [30.0, 30.0, 30.0])
        self.assertGreater(prices[0], Decimal("10.0"))
        self.assertGreater(prices[1], prices[0])
        self.assertGreater(prices[2], prices[1])

    def test_terrible_recent_history_drops_price(self) -> None:
        # 1 pt at $10M = 0.1 PPM → terrible → B-tier -0.6 per race
        recent = [(1.0, Decimal("10.0")), (1.0, Decimal("10.0")), (1.0, Decimal("10.0"))]
        prices = predict_price_trajectory(Decimal("10.0"), recent, [1.0, 1.0, 1.0])
        self.assertLess(prices[0], Decimal("10.0"))
        self.assertLess(prices[1], prices[0])
        self.assertLess(prices[2], prices[1])

    def test_actual_history_seeds_rolling_window(self) -> None:
        # Two great actual races give a head start — price drops less on a bad predicted race
        great_history = [(30.0, Decimal("10.0")), (30.0, Decimal("10.0"))]
        prices_with_history = predict_price_trajectory(Decimal("10.0"), great_history, [0.0])
        prices_no_history = predict_price_trajectory(Decimal("10.0"), [], [0.0])
        self.assertGreater(prices_with_history[0], prices_no_history[0])

    def test_rolling_window_shifts_with_predictions(self) -> None:
        # After 3 predicted races, old actual history drops out of the 3-race window.
        # Seed with great history, then predict 3 terrible races — by race 4 the old
        # great history is gone and the price should be falling from its peak.
        great_history = [(30.0, Decimal("10.0")), (30.0, Decimal("10.0")), (30.0, Decimal("10.0"))]
        prices = predict_price_trajectory(Decimal("10.0"), great_history, [0.0, 0.0, 0.0, 0.0])
        # Price should rise while great history is in the window, then fall
        self.assertGreater(prices[0], Decimal("10.0"))   # still rising at race 1 (great history)
        self.assertLess(prices[3], prices[2])            # falling by race 4 (window now all zeros)

    def test_price_clamped_at_ceiling(self) -> None:
        recent = [(100.0, Decimal("33.9")), (100.0, Decimal("33.9")), (100.0, Decimal("33.9"))]
        prices = predict_price_trajectory(Decimal("33.9"), recent, [100.0, 100.0, 100.0])
        for p in prices:
            self.assertLessEqual(p, Decimal("34.0"))

    def test_price_clamped_at_floor(self) -> None:
        recent = [(0.0, Decimal("3.1")), (0.0, Decimal("3.1")), (0.0, Decimal("3.1"))]
        prices = predict_price_trajectory(Decimal("3.1"), recent, [0.0, 0.0, 0.0])
        for p in prices:
            self.assertGreaterEqual(p, Decimal("3.0"))

    def test_a_tier_price_changes_are_smaller(self) -> None:
        # A-Tier (≥$19M): great = +0.3. B-Tier (<$19M): great = +0.6
        recent = [(30.0, Decimal("20.0")), (30.0, Decimal("20.0")), (30.0, Decimal("20.0"))]
        a_tier = predict_price_trajectory(Decimal("20.0"), recent, [30.0])

        recent_b = [(30.0, Decimal("10.0")), (30.0, Decimal("10.0")), (30.0, Decimal("10.0"))]
        b_tier = predict_price_trajectory(Decimal("10.0"), recent_b, [30.0])

        a_change = a_tier[0] - Decimal("20.0")
        b_change = b_tier[0] - Decimal("10.0")
        self.assertLess(a_change, b_change)
