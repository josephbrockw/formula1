from __future__ import annotations

import itertools

import pandas as pd
from django.test import SimpleTestCase, override_settings

from predictions.optimizers.base import Lineup, LineupOptimizer
from predictions.optimizers.ilp_v3 import ILPOptimizer


def _make_drivers(n: int = 10) -> pd.DataFrame:
    """n drivers priced $10-$(10+n-1)M, points proportional to price."""
    return pd.DataFrame(
        {
            "driver_id": list(range(1, n + 1)),
            "predicted_fantasy_points": [float(10 + i) for i in range(n)],
            "price": [float(10 + i) for i in range(n)],
        }
    )


def _make_constructors(n: int = 5) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "team_id": list(range(101, 101 + n)),
            "predicted_fantasy_points": [float(20 + i * 2) for i in range(n)],
            "price": [float(15 + i) for i in range(n)],
        }
    )


# ---------------------------------------------------------------------------
# Output shape and types
# ---------------------------------------------------------------------------


class TestILPOptimizerShape(SimpleTestCase):
    def setUp(self) -> None:
        self.optimizer = ILPOptimizer()
        self.drivers = _make_drivers(10)
        self.constructors = _make_constructors(5)

    def test_selects_five_drivers_two_constructors(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        self.assertIsInstance(result, Lineup)
        self.assertEqual(len(result.driver_ids), 5)
        self.assertEqual(len(result.constructor_ids), 2)

    def test_driver_ids_are_unique(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        self.assertEqual(len(set(result.driver_ids)), 5)

    def test_constructor_ids_are_unique(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        self.assertEqual(len(set(result.constructor_ids)), 2)

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
# Budget constraint
# ---------------------------------------------------------------------------


class TestILPOptimizerBudget(SimpleTestCase):
    def test_respects_budget_constraint(self) -> None:
        result = ILPOptimizer().optimize_single_race(_make_drivers(10), _make_constructors(5), budget=100.0)
        self.assertLessEqual(result.total_cost, 100.0)

    def test_total_cost_matches_sum_of_prices(self) -> None:
        drivers = _make_drivers(10)
        constructors = _make_constructors(5)
        result = ILPOptimizer().optimize_single_race(drivers, constructors, budget=100.0)
        d_cost = float(drivers.loc[drivers["driver_id"].isin(result.driver_ids), "price"].sum())
        c_cost = float(constructors.loc[constructors["team_id"].isin(result.constructor_ids), "price"].sum())
        self.assertAlmostEqual(result.total_cost, d_cost + c_cost, places=6)


# ---------------------------------------------------------------------------
# DRS boost constraint
# ---------------------------------------------------------------------------


class TestILPOptimizerDRS(SimpleTestCase):
    def test_drs_driver_is_in_selected_drivers(self) -> None:
        """z[i] ≤ x[i] constraint: DRS driver must also be in the lineup."""
        result = ILPOptimizer().optimize_single_race(_make_drivers(10), _make_constructors(5), budget=100.0)
        self.assertIn(result.drs_boost_driver_id, result.driver_ids)


# ---------------------------------------------------------------------------
# Optimality — brute-force verification
# ---------------------------------------------------------------------------


class TestILPOptimizerOptimality(SimpleTestCase):
    def test_optimal_selects_highest_scoring_within_budget(self) -> None:
        """
        ILP result matches the brute-force maximum over every valid lineup.

        This verifies that no combination of 5 drivers + 2 constructors within
        budget scores more than what the ILP found. Uses a small pool (7 drivers,
        4 constructors) so the brute-force enumeration is fast (~84 combos).
        """
        drivers = pd.DataFrame(
            {
                "driver_id": [1, 2, 3, 4, 5, 6, 7],
                "predicted_fantasy_points": [30.0, 28.0, 25.0, 22.0, 20.0, 18.0, 10.0],
                "price": [20.0, 18.0, 15.0, 15.0, 12.0, 10.0, 10.0],
            }
        )
        constructors = pd.DataFrame(
            {
                "team_id": [101, 102, 103, 104],
                "predicted_fantasy_points": [40.0, 35.0, 25.0, 15.0],
                "price": [20.0, 15.0, 12.0, 10.0],
            }
        )
        budget = 100.0

        result = ILPOptimizer().optimize_single_race(drivers, constructors, budget)

        # Enumerate every valid combination of 5 drivers + 2 constructors
        best_pts = -1.0
        for d_combo in itertools.combinations(range(len(drivers)), 5):
            for c_combo in itertools.combinations(range(len(constructors)), 2):
                d_cost = sum(drivers.iloc[i]["price"] for i in d_combo)
                c_cost = sum(constructors.iloc[j]["price"] for j in c_combo)
                if d_cost + c_cost > budget:
                    continue
                d_pts_list = [drivers.iloc[i]["predicted_fantasy_points"] for i in d_combo]
                c_pts_sum = sum(constructors.iloc[j]["predicted_fantasy_points"] for j in c_combo)
                total = sum(d_pts_list) + c_pts_sum + max(d_pts_list)
                if total > best_pts:
                    best_pts = total

        self.assertAlmostEqual(result.predicted_points, best_pts, places=4)


# ---------------------------------------------------------------------------
# Transfer penalty constraints
# ---------------------------------------------------------------------------


def _current_lineup() -> Lineup:
    """A baseline lineup of drivers 1-5, constructors 101-102, DRS on driver 1."""
    return Lineup(
        driver_ids=[1, 2, 3, 4, 5],
        constructor_ids=[101, 102],
        drs_boost_driver_id=1,
        total_cost=70.0,
        predicted_points=100.0,
    )


def _uniform_pool() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    8 drivers and 4 constructors, all $10, so budget is never the binding constraint.

    Drivers 1-5 are in the current lineup (20pts each).
    Driver 6 scores 24pts, driver 7 scores 50pts, driver 8 scores 20pts.
    Constructors 101-102 are in the current lineup (30pts each).
    Constructors 103-104 score 20pts each.
    """
    drivers = pd.DataFrame(
        {
            "driver_id": [1, 2, 3, 4, 5, 6, 7, 8],
            "predicted_fantasy_points": [20.0, 20.0, 20.0, 20.0, 20.0, 24.0, 50.0, 20.0],
            "price": [10.0] * 8,
        }
    )
    constructors = pd.DataFrame(
        {
            "team_id": [101, 102, 103, 104],
            "predicted_fantasy_points": [30.0, 30.0, 20.0, 20.0],
            "price": [10.0] * 4,
        }
    )
    return drivers, constructors


class TestILPTransferPenalty(SimpleTestCase):
    def setUp(self) -> None:
        self.optimizer = ILPOptimizer()
        self.drivers, self.constructors = _uniform_pool()
        self.current = _current_lineup()

    def test_no_constraints_ignores_transfer_logic(self) -> None:
        """constraints=None produces a valid lineup (same as original behaviour)."""
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        self.assertEqual(len(result.driver_ids), 5)
        self.assertEqual(len(result.constructor_ids), 2)

    def test_keeps_current_driver_when_gain_less_than_penalty(self) -> None:
        """
        Driver 6 scores 24pts vs driver 1's 20pts.
        Gain = (24-20) driver points + (24-20) DRS bonus = 8pts total.
        Penalty = 10pts per extra transfer, with 0 free transfers.
        Net = -2pts  →  ILP keeps driver 1.
        """
        constraints = {"current_lineup": self.current, "free_transfers": 0, "transfer_penalty": 10.0}
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, 100.0, constraints)
        self.assertIn(1, result.driver_ids)
        self.assertNotIn(6, result.driver_ids)

    def test_swaps_driver_when_gain_exceeds_penalty(self) -> None:
        """
        Driver 7 scores 50pts vs any current driver's 20pts.
        Gain = (50-20) driver pts + (50-20) DRS bonus = 60pts.
        Penalty = 10pts with 0 free transfers.
        Net = +50pts  →  ILP includes driver 7 (drops one of the 20pt drivers).
        """
        constraints = {"current_lineup": self.current, "free_transfers": 0, "transfer_penalty": 10.0}
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, 100.0, constraints)
        self.assertIn(7, result.driver_ids)
        # One of the 20pt current drivers must have been dropped to fit driver 7
        self.assertEqual(len(set(result.driver_ids) - {7}), 4)

    def test_free_transfers_allow_changes_at_no_cost(self) -> None:
        """
        With free_transfers=5 all transfers are free, so ILP picks the best possible
        lineup without any constraint — driver 7 (50pts) must be included.
        """
        constraints = {"current_lineup": self.current, "free_transfers": 5, "transfer_penalty": 10.0}
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, 100.0, constraints)
        self.assertIn(7, result.driver_ids)

    def test_drs_driver_still_in_lineup_with_transfer_constraints(self) -> None:
        """z[i] ≤ x[i] must hold even when the e variable is added."""
        constraints = {"current_lineup": self.current, "free_transfers": 2, "transfer_penalty": 10.0}
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, 100.0, constraints)
        self.assertIn(result.drs_boost_driver_id, result.driver_ids)

    def test_budget_respected_with_transfer_constraints(self) -> None:
        constraints = {"current_lineup": self.current, "free_transfers": 2, "transfer_penalty": 10.0}
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, 100.0, constraints)
        self.assertLessEqual(result.total_cost, 100.0)

    @override_settings(ILP_TRANSFER_THRESHOLD=50.0)
    def test_high_threshold_prevents_marginal_transfer(self) -> None:
        """With a very high threshold, a small-gain transfer is blocked."""
        # Driver 6 scores 24pts vs current 20pts — gain ~8pts << threshold 50pts
        constraints = {"current_lineup": self.current, "free_transfers": 2, "transfer_penalty": 10.0}
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, 100.0, constraints)
        self.assertNotIn(6, result.driver_ids)  # small-gain player not brought in

    @override_settings(ILP_TRANSFER_THRESHOLD=0.0)
    def test_zero_threshold_allows_marginal_transfer(self) -> None:
        """With threshold=0, any positive gain triggers a transfer (baseline behaviour)."""
        constraints = {"current_lineup": self.current, "free_transfers": 2, "transfer_penalty": 10.0}
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, 100.0, constraints)
        self.assertIn(7, result.driver_ids)  # high-scoring driver 7 (50pts) is selected


# ---------------------------------------------------------------------------
# Infeasible input
# ---------------------------------------------------------------------------


class TestILPOptimizerInfeasible(SimpleTestCase):
    def test_raises_on_infeasible(self) -> None:
        """
        Raises ValueError when budget is too low to build any valid lineup.

        Minimum lineup from _make_drivers(10) / _make_constructors(5):
          cheapest 5 drivers = $10+$11+$12+$13+$14 = $60
          cheapest 2 constructors = $15+$16 = $31
          minimum total = $91 — so budget=$50 is infeasible.
        """
        with self.assertRaises(ValueError):
            ILPOptimizer().optimize_single_race(_make_drivers(10), _make_constructors(5), budget=50.0)
