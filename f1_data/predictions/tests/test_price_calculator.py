from __future__ import annotations

from decimal import Decimal

from django.test import SimpleTestCase

from predictions.price_calculator import (
    classify_performance,
    compute_avg_ppm,
    compute_price_change,
    next_price,
)


class TestClassifyPerformance(SimpleTestCase):
    def test_above_1_2_is_great(self) -> None:
        self.assertEqual(classify_performance(1.3), "great")

    def test_exactly_1_2_is_good(self) -> None:
        # boundary: > 1.2 is great, == 1.2 is good
        self.assertEqual(classify_performance(1.2), "good")

    def test_between_0_9_and_1_2_is_good(self) -> None:
        self.assertEqual(classify_performance(1.0), "good")

    def test_exactly_0_9_is_poor(self) -> None:
        self.assertEqual(classify_performance(0.9), "poor")

    def test_between_0_6_and_0_9_is_poor(self) -> None:
        self.assertEqual(classify_performance(0.75), "poor")

    def test_exactly_0_6_is_terrible(self) -> None:
        self.assertEqual(classify_performance(0.6), "terrible")

    def test_below_0_6_is_terrible(self) -> None:
        self.assertEqual(classify_performance(0.1), "terrible")

    def test_zero_is_terrible(self) -> None:
        self.assertEqual(classify_performance(0.0), "terrible")


class TestComputePriceChange(SimpleTestCase):
    def test_great_a_tier(self) -> None:
        self.assertEqual(compute_price_change(1.5, Decimal("25.0")), Decimal("0.3"))

    def test_great_b_tier(self) -> None:
        self.assertEqual(compute_price_change(1.5, Decimal("15.0")), Decimal("0.6"))

    def test_good_a_tier(self) -> None:
        self.assertEqual(compute_price_change(1.0, Decimal("20.0")), Decimal("0.1"))

    def test_good_b_tier(self) -> None:
        self.assertEqual(compute_price_change(1.0, Decimal("10.0")), Decimal("0.2"))

    def test_poor_a_tier(self) -> None:
        self.assertEqual(compute_price_change(0.75, Decimal("22.0")), Decimal("-0.1"))

    def test_poor_b_tier(self) -> None:
        self.assertEqual(compute_price_change(0.75, Decimal("12.0")), Decimal("-0.2"))

    def test_terrible_a_tier(self) -> None:
        self.assertEqual(compute_price_change(0.3, Decimal("19.0")), Decimal("-0.3"))

    def test_terrible_b_tier(self) -> None:
        self.assertEqual(compute_price_change(0.3, Decimal("8.0")), Decimal("-0.6"))

    def test_exactly_19m_is_a_tier(self) -> None:
        # $19M is A-Tier (threshold is >=19)
        self.assertEqual(compute_price_change(1.5, Decimal("19.0")), Decimal("0.3"))

    def test_just_below_19m_is_b_tier(self) -> None:
        self.assertEqual(compute_price_change(1.5, Decimal("18.9")), Decimal("0.6"))


class TestComputeAvgPpm(SimpleTestCase):
    def test_single_race(self) -> None:
        # 30 pts at $10M = 3.0 PPM
        result = compute_avg_ppm([(30.0, Decimal("10.0"))])
        self.assertAlmostEqual(result, 3.0)

    def test_two_races(self) -> None:
        result = compute_avg_ppm([
            (20.0, Decimal("10.0")),  # 2.0 PPM
            (10.0, Decimal("10.0")),  # 1.0 PPM
        ])
        self.assertAlmostEqual(result, 1.5)

    def test_uses_last_three_only(self) -> None:
        # 4 races — only last 3 should count
        result = compute_avg_ppm([
            (100.0, Decimal("10.0")),  # 10.0 PPM — should be ignored
            (10.0, Decimal("10.0")),   # 1.0 PPM
            (20.0, Decimal("10.0")),   # 2.0 PPM
            (30.0, Decimal("10.0")),   # 3.0 PPM
        ])
        self.assertAlmostEqual(result, 2.0)  # mean of 1.0, 2.0, 3.0

    def test_empty_returns_zero(self) -> None:
        self.assertEqual(compute_avg_ppm([]), 0.0)


class TestNextPrice(SimpleTestCase):
    def test_price_increases_for_great_performance(self) -> None:
        change, new = next_price(Decimal("10.0"), 1.5)  # B-tier, great → +0.6
        self.assertEqual(change, Decimal("0.6"))
        self.assertEqual(new, Decimal("10.6"))

    def test_price_clamped_at_ceiling(self) -> None:
        change, new = next_price(Decimal("33.9"), 2.0)  # A-tier, great → +0.3
        self.assertEqual(new, Decimal("34.0"))

    def test_price_clamped_at_floor(self) -> None:
        change, new = next_price(Decimal("3.1"), 0.0)  # B-tier, terrible → -0.6
        self.assertEqual(new, Decimal("3.0"))
