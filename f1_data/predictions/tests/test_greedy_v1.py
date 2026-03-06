from __future__ import annotations

import pandas as pd
from django.test import SimpleTestCase

from predictions.optimizers.greedy_v1 import GreedyOptimizer, _pick_greedily
from predictions.optimizers.base import Lineup


# ---------------------------------------------------------------------------
# Helpers — synthetic prediction DataFrames
# ---------------------------------------------------------------------------


def _make_drivers(n: int = 10) -> pd.DataFrame:
    """n drivers priced $10-19M, points proportional to price (value is uniform)."""
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
# GreedyOptimizer.optimize_single_race — output shape and types
# ---------------------------------------------------------------------------


class TestGreedyOptimizerShape(SimpleTestCase):
    def setUp(self) -> None:
        self.optimizer = GreedyOptimizer()
        self.drivers = _make_drivers(10)
        self.constructors = _make_constructors(5)

    def test_returns_lineup_instance(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        self.assertIsInstance(result, Lineup)

    def test_lineup_has_five_drivers(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        self.assertEqual(len(result.driver_ids), 5)

    def test_lineup_has_two_constructors(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        self.assertEqual(len(result.constructor_ids), 2)

    def test_driver_ids_are_unique(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        self.assertEqual(len(set(result.driver_ids)), 5)

    def test_constructor_ids_are_unique(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        self.assertEqual(len(set(result.constructor_ids)), 2)

    def test_driver_ids_come_from_input(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        valid_ids = set(self.drivers["driver_id"])
        for did in result.driver_ids:
            self.assertIn(did, valid_ids)

    def test_constructor_ids_come_from_input(self) -> None:
        result = self.optimizer.optimize_single_race(self.drivers, self.constructors, budget=100.0)
        valid_ids = set(self.constructors["team_id"])
        for cid in result.constructor_ids:
            self.assertIn(cid, valid_ids)


# ---------------------------------------------------------------------------
# Budget constraint
# ---------------------------------------------------------------------------


class TestGreedyOptimizerBudget(SimpleTestCase):
    def setUp(self) -> None:
        self.optimizer = GreedyOptimizer()

    def test_total_cost_within_budget(self) -> None:
        drivers = _make_drivers(10)
        constructors = _make_constructors(5)
        result = self.optimizer.optimize_single_race(drivers, constructors, budget=100.0)
        self.assertLessEqual(result.total_cost, 100.0)

    def test_tight_budget_still_finds_lineup(self) -> None:
        # Minimum possible spend: 5 cheapest drivers ($10-14M = $60) + 2 cheapest constructors ($15+$16 = $31)
        # Total minimum = $91M — use $92M budget to allow just slightly above minimum
        drivers = _make_drivers(10)
        constructors = _make_constructors(5)
        result = self.optimizer.optimize_single_race(drivers, constructors, budget=92.0)
        self.assertEqual(len(result.driver_ids), 5)
        self.assertEqual(len(result.constructor_ids), 2)
        self.assertLessEqual(result.total_cost, 92.0)

    def test_total_cost_matches_sum_of_prices(self) -> None:
        drivers = _make_drivers(10)
        constructors = _make_constructors(5)
        result = self.optimizer.optimize_single_race(drivers, constructors, budget=100.0)

        driver_cost = float(
            drivers.loc[drivers["driver_id"].isin(result.driver_ids), "price"].sum()
        )
        constructor_cost = float(
            constructors.loc[constructors["team_id"].isin(result.constructor_ids), "price"].sum()
        )
        self.assertAlmostEqual(result.total_cost, driver_cost + constructor_cost, places=6)


# ---------------------------------------------------------------------------
# DRS Boost selection
# ---------------------------------------------------------------------------


class TestGreedyOptimizerDRS(SimpleTestCase):
    def setUp(self) -> None:
        self.optimizer = GreedyOptimizer()

    def test_drs_driver_is_in_lineup(self) -> None:
        drivers = _make_drivers(10)
        constructors = _make_constructors(5)
        result = self.optimizer.optimize_single_race(drivers, constructors, budget=100.0)
        self.assertIn(result.drs_boost_driver_id, result.driver_ids)

    def test_drs_driver_has_highest_points_in_lineup(self) -> None:
        drivers = _make_drivers(10)
        constructors = _make_constructors(5)
        result = self.optimizer.optimize_single_race(drivers, constructors, budget=100.0)

        picked_pts = drivers.loc[
            drivers["driver_id"].isin(result.driver_ids),
            ["driver_id", "predicted_fantasy_points"],
        ]
        best_id = int(
            picked_pts.sort_values("predicted_fantasy_points", ascending=False).iloc[0]["driver_id"]
        )
        self.assertEqual(result.drs_boost_driver_id, best_id)


# ---------------------------------------------------------------------------
# Points formula
# ---------------------------------------------------------------------------


class TestGreedyOptimizerPoints(SimpleTestCase):
    def setUp(self) -> None:
        self.optimizer = GreedyOptimizer()

    def test_predicted_points_formula(self) -> None:
        """predicted_points = sum(driver pts) + sum(constructor pts) + drs_driver_pts"""
        drivers = _make_drivers(10)
        constructors = _make_constructors(5)
        result = self.optimizer.optimize_single_race(drivers, constructors, budget=100.0)

        driver_pts = float(
            drivers.loc[drivers["driver_id"].isin(result.driver_ids), "predicted_fantasy_points"].sum()
        )
        constructor_pts = float(
            constructors.loc[
                constructors["team_id"].isin(result.constructor_ids), "predicted_fantasy_points"
            ].sum()
        )
        drs_pts = float(
            drivers.loc[
                drivers["driver_id"] == result.drs_boost_driver_id, "predicted_fantasy_points"
            ].iloc[0]
        )
        expected = driver_pts + constructor_pts + drs_pts
        self.assertAlmostEqual(result.predicted_points, expected, places=6)


# ---------------------------------------------------------------------------
# _pick_greedily — unit tests for the core algorithm
# ---------------------------------------------------------------------------


class TestPickGreedily(SimpleTestCase):
    def _make_candidates(self, prices: list[float], points: list[float], id_start: int = 1) -> pd.DataFrame:
        n = len(prices)
        df = pd.DataFrame(
            {
                "id": list(range(id_start, id_start + n)),
                "price": prices,
                "predicted_fantasy_points": points,
            }
        )
        df["value"] = df["predicted_fantasy_points"] / df["price"]
        return df.sort_values("value", ascending=False).reset_index(drop=True)

    def test_picks_n_candidates(self) -> None:
        candidates = self._make_candidates([10.0] * 6, [20.0] * 6)
        result = _pick_greedily(candidates, "id", 3, 100.0)
        self.assertEqual(len(result), 3)

    def test_respects_budget(self) -> None:
        # 4 candidates at $20 each — budget only allows 2
        candidates = self._make_candidates([20.0] * 4, [30.0] * 4)
        result = _pick_greedily(candidates, "id", 3, 45.0)
        self.assertLessEqual(len(result), 3)

    def test_prefers_higher_value_candidates(self) -> None:
        # candidate A: 30pts/$10 = 3.0 value; candidate B: 20pts/$10 = 2.0 value
        candidates = self._make_candidates([10.0, 10.0], [30.0, 20.0])
        result = _pick_greedily(candidates, "id", 1, 100.0)
        # Higher value candidate has id=1
        self.assertEqual(result[0], candidates.iloc[0]["id"])

    def test_budget_lookahead_skips_expensive_pick(self) -> None:
        # 3 candidates: A($50, 100pts), B($10, 50pts), C($10, 30pts)
        # Need to pick 2 within $60 budget.
        # Greedy by value: A(2.0), B(5.0), C(3.0) → sorted: B, C, A
        # B costs $10, then C costs $10: total $20 — fits.
        candidates = self._make_candidates([50.0, 10.0, 10.0], [100.0, 50.0, 30.0])
        result = _pick_greedily(candidates, "id", 2, 60.0)
        self.assertEqual(len(result), 2)
        total_price = sum(
            candidates.loc[candidates["id"].isin(result), "price"]
        )
        self.assertLessEqual(total_price, 60.0)

