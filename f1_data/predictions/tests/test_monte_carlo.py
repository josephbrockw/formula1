from __future__ import annotations

import pandas as pd
from django.test import SimpleTestCase

from predictions.optimizers.base import Lineup
from predictions.optimizers.monte_carlo_v4 import MonteCarloOptimizer, _needs_fallback


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


def _make_drivers(n: int = 8, wide_band: bool = True) -> pd.DataFrame:
    """
    n drivers priced $10 each.

    With wide_band=True: confidence interval spans ±40% of the predicted mean,
    giving MC meaningful variance to explore.
    """
    means = [float(15 + i * 2) for i in range(n)]
    rows = {
        "driver_id": list(range(1, n + 1)),
        "predicted_fantasy_points": means,
        "price": [10.0] * n,
    }
    if wide_band:
        rows["confidence_lower"] = [m * 0.6 for m in means]
        rows["confidence_upper"] = [m * 1.4 for m in means]
    return pd.DataFrame(rows)


def _make_constructors(n: int = 4) -> pd.DataFrame:
    """n constructors priced $10 each with simple confidence bounds."""
    means = [float(20 + i * 5) for i in range(n)]
    return pd.DataFrame({
        "team_id": list(range(101, 101 + n)),
        "predicted_fantasy_points": means,
        "price": [10.0] * n,
        "confidence_lower": [m * 0.7 for m in means],
        "confidence_upper": [m * 1.3 for m in means],
    })


# ---------------------------------------------------------------------------
# 1. Returns a valid Lineup
# ---------------------------------------------------------------------------


class TestMonteCarloReturnsValidLineup(SimpleTestCase):
    def setUp(self) -> None:
        self.drivers = _make_drivers(8)
        self.constructors = _make_constructors(4)
        self.optimizer = MonteCarloOptimizer(n_scenarios=50, seed=42)

    def test_returns_lineup_instance(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        self.assertIsInstance(result, Lineup)

    def test_selects_five_drivers(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        self.assertEqual(len(result.driver_ids), 5)

    def test_selects_two_constructors(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        self.assertEqual(len(result.constructor_ids), 2)

    def test_driver_ids_are_unique(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        self.assertEqual(len(set(result.driver_ids)), 5)

    def test_constructor_ids_are_unique(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        self.assertEqual(len(set(result.constructor_ids)), 2)

    def test_drs_driver_is_in_lineup(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        self.assertIn(result.drs_boost_driver_id, result.driver_ids)

    def test_driver_ids_come_from_input(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        valid = set(self.drivers["driver_id"])
        for did in result.driver_ids:
            self.assertIn(did, valid)

    def test_constructor_ids_come_from_input(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        valid = set(self.constructors["team_id"])
        for cid in result.constructor_ids:
            self.assertIn(cid, valid)


# ---------------------------------------------------------------------------
# 2. Respects budget
# ---------------------------------------------------------------------------


class TestMonteCarloRespectsBudget(SimpleTestCase):
    def test_total_cost_within_budget(self) -> None:
        drivers = _make_drivers(8)
        constructors = _make_constructors(4)
        result = MonteCarloOptimizer(n_scenarios=50, seed=0).optimize_single_race(
            drivers, constructors, budget=100.0
        )
        self.assertLessEqual(result.total_cost, 100.0)

    def test_total_cost_within_tight_budget(self) -> None:
        """With a tight budget (exactly covers cheapest lineup), still valid."""
        # 5 drivers at $10 + 2 constructors at $10 = $70
        drivers = _make_drivers(8)
        constructors = _make_constructors(4)
        result = MonteCarloOptimizer(n_scenarios=50, seed=0).optimize_single_race(
            drivers, constructors, budget=70.0
        )
        self.assertLessEqual(result.total_cost, 70.0)
        self.assertEqual(len(result.driver_ids), 5)
        self.assertEqual(len(result.constructor_ids), 2)


# ---------------------------------------------------------------------------
# 3. Fallback when confidence bounds are absent or flat
# ---------------------------------------------------------------------------


class TestMonteCarloFallback(SimpleTestCase):
    def test_needs_fallback_when_columns_missing(self) -> None:
        df = _make_drivers(8, wide_band=False)
        self.assertTrue(_needs_fallback(df))

    def test_needs_fallback_when_lower_equals_upper(self) -> None:
        df = _make_drivers(8, wide_band=False)
        df["confidence_lower"] = df["predicted_fantasy_points"]
        df["confidence_upper"] = df["predicted_fantasy_points"]
        self.assertTrue(_needs_fallback(df))

    def test_no_fallback_when_bounds_present(self) -> None:
        df = _make_drivers(8, wide_band=True)
        self.assertFalse(_needs_fallback(df))

    def test_fallback_produces_same_lineup_as_ilp(self) -> None:
        """
        When lower == upper for all drivers, MC delegates to ILP on the mean.
        The result must match ILP's lineup exactly.
        """
        from predictions.optimizers.ilp_v3 import ILPOptimizer

        # Flat confidence bounds: zero variance, MC must fall back to ILP
        drivers = _make_drivers(8, wide_band=False)
        drivers["confidence_lower"] = drivers["predicted_fantasy_points"]
        drivers["confidence_upper"] = drivers["predicted_fantasy_points"]
        constructors = _make_constructors(4)

        mc_lineup = MonteCarloOptimizer(n_scenarios=50, seed=42).optimize_single_race(
            drivers, constructors, budget=100.0
        )
        ilp_lineup = ILPOptimizer().optimize_single_race(drivers, constructors, budget=100.0)

        self.assertEqual(sorted(mc_lineup.driver_ids), sorted(ilp_lineup.driver_ids))
        self.assertEqual(sorted(mc_lineup.constructor_ids), sorted(ilp_lineup.constructor_ids))


# ---------------------------------------------------------------------------
# 4. MC exploits asymmetric upside that ILP cannot see
# ---------------------------------------------------------------------------


class TestMonteCarloExploitsUpside(SimpleTestCase):
    """
    ILP uses predicted_fantasy_points (the mode). When a driver's mode is much
    lower than their triangular mean (q10+mode+q90)/3 — i.e. the distribution
    has heavy upside — ILP undervalues that driver and excludes them.

    MC samples from the full distribution and discovers the driver's high expected
    value, including them in the lineup. When actual points reflect that upside,
    MC wins.

    Driver 1 setup:
        predicted_fantasy_points = 15  ← ILP uses this; ranks them below others
        confidence_lower (q10)   =  0
        confidence_upper (q90)   = 81
        triangular mean          = (0 + 15 + 81) / 3 ≈ 32  ← MC sees this

    Drivers 2-8:
        predicted = 22, q10 = 20, q90 = 24, triangular mean ≈ 22

    ILP picks drivers 2-6 (five highest predicted).
    MC sees driver 1's triangular mean ≈ 32 and picks them instead of a 22-pt driver.
    Actual outcome: driver 1 scores 32. MC wins.
    """

    def _make_asymmetric_drivers(self) -> pd.DataFrame:
        return pd.DataFrame({
            "driver_id":                  [1,    2,    3,    4,    5,    6,    7,    8],
            "predicted_fantasy_points":   [15.0, 22.0, 22.0, 22.0, 22.0, 22.0, 22.0, 22.0],
            "price":                      [10.0] * 8,
            "confidence_lower":           [0.0,  20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0],
            "confidence_upper":           [81.0, 24.0, 24.0, 24.0, 24.0, 24.0, 24.0, 24.0],
        })

    def _score_lineup(
        self,
        lineup: Lineup,
        driver_pts: dict[int, float],
        constructor_pts: dict[int, float],
    ) -> float:
        """Score a lineup against actual points, choosing DRS optimally."""
        d_scores = [driver_pts.get(did, 0.0) for did in lineup.driver_ids]
        return sum(d_scores) + max(d_scores) + sum(
            constructor_pts.get(cid, 0.0) for cid in lineup.constructor_ids
        )

    def test_mc_includes_asymmetric_upside_driver(self) -> None:
        """MC should discover driver 1 (triangular mean ≈ 32) and include them."""
        drivers = self._make_asymmetric_drivers()
        constructors = _make_constructors(4)
        mc_lineup = MonteCarloOptimizer(n_scenarios=200, seed=42).optimize_single_race(
            drivers, constructors, budget=100.0
        )
        self.assertIn(1, mc_lineup.driver_ids)

    def test_ilp_excludes_asymmetric_upside_driver(self) -> None:
        """ILP uses predicted_fantasy_points=15 and correctly excludes driver 1."""
        from predictions.optimizers.ilp_v3 import ILPOptimizer
        drivers = self._make_asymmetric_drivers()
        constructors = _make_constructors(4)
        ilp_lineup = ILPOptimizer().optimize_single_race(drivers, constructors, budget=100.0)
        self.assertNotIn(1, ilp_lineup.driver_ids)

    def test_mc_scores_higher_when_upside_driver_delivers(self) -> None:
        """
        When driver 1 delivers their triangular-mean score (32), the MC lineup
        beats the ILP lineup.
        """
        from predictions.optimizers.ilp_v3 import ILPOptimizer

        drivers = self._make_asymmetric_drivers()
        constructors = _make_constructors(4)

        mc_lineup = MonteCarloOptimizer(n_scenarios=200, seed=42).optimize_single_race(
            drivers, constructors, budget=100.0
        )
        ilp_lineup = ILPOptimizer().optimize_single_race(drivers, constructors, budget=100.0)

        # Actual: driver 1 delivers their triangular-mean value; others score at mean
        actual_driver_pts = {i: (32.0 if i == 1 else 22.0) for i in range(1, 9)}
        actual_constructor_pts = {101: 20.0, 102: 25.0, 103: 30.0, 104: 35.0}

        mc_score = self._score_lineup(mc_lineup, actual_driver_pts, actual_constructor_pts)
        ilp_score = self._score_lineup(ilp_lineup, actual_driver_pts, actual_constructor_pts)

        self.assertGreaterEqual(mc_score, ilp_score)
