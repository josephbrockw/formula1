from __future__ import annotations

import pandas as pd
from django.test import SimpleTestCase

from predictions.optimizers.base import Lineup
from predictions.optimizers.greedy_v2 import GreedyOptimizerV2, _apply_transfer_constraints, _upgrade_picks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_drivers(n: int = 10) -> pd.DataFrame:
    """n drivers with increasing points and price, uniform value."""
    return pd.DataFrame({
        "driver_id": list(range(1, n + 1)),
        "predicted_fantasy_points": [float(10 + i) for i in range(n)],
        "price": [float(10 + i) for i in range(n)],
    })


def _make_constructors(n: int = 5) -> pd.DataFrame:
    return pd.DataFrame({
        "team_id": list(range(101, 101 + n)),
        "predicted_fantasy_points": [float(20 + i) for i in range(n)],
        "price": [float(10 + i) for i in range(n)],
    })


# ---------------------------------------------------------------------------
# GreedyOptimizerV2 — shape and budget tests
# ---------------------------------------------------------------------------


class TestGreedyOptimizerV2Shape(SimpleTestCase):
    def test_returns_5_drivers_2_constructors(self) -> None:
        lineup = GreedyOptimizerV2().optimize_single_race(
            _make_drivers(10), _make_constructors(5), budget=100.0
        )
        self.assertEqual(len(lineup.driver_ids), 5)
        self.assertEqual(len(lineup.constructor_ids), 2)

    def test_total_cost_within_budget(self) -> None:
        lineup = GreedyOptimizerV2().optimize_single_race(
            _make_drivers(10), _make_constructors(5), budget=100.0
        )
        self.assertLessEqual(lineup.total_cost, 100.0)

    def test_spends_more_budget_than_v1_when_slack_exists(self) -> None:
        from predictions.optimizers.greedy_v1 import GreedyOptimizer

        drivers = _make_drivers(10)
        constructors = _make_constructors(5)
        budget = 200.0  # very generous — v1 leaves money on table, v2 spends it

        v1 = GreedyOptimizer().optimize_single_race(drivers, constructors, budget)
        v2 = GreedyOptimizerV2().optimize_single_race(drivers, constructors, budget)

        self.assertGreaterEqual(v2.total_cost, v1.total_cost)
        self.assertGreaterEqual(v2.predicted_points, v1.predicted_points)


# ---------------------------------------------------------------------------
# _upgrade_picks unit tests
# ---------------------------------------------------------------------------


class TestUpgradePicks(SimpleTestCase):
    def _make_candidates(self, prices: list[float], points: list[float]) -> pd.DataFrame:
        return pd.DataFrame({
            "id": list(range(1, len(prices) + 1)),
            "price": prices,
            "predicted_fantasy_points": points,
        })

    def test_upgrades_when_better_player_fits(self) -> None:
        # Picked id=1 (10pts, $5). Unpicked id=2 scores 20pts at $5 — same price, more points.
        candidates = self._make_candidates([5.0, 5.0], [10.0, 20.0])
        result = _upgrade_picks([1], candidates, "id", 10.0)
        self.assertIn(2, result)
        self.assertNotIn(1, result)

    def test_no_upgrade_when_better_player_too_expensive(self) -> None:
        # Picked id=1 ($5, 10pts). Better id=2 scores 20pts but costs $20 — over budget.
        candidates = self._make_candidates([5.0, 20.0], [10.0, 20.0])
        result = _upgrade_picks([1], candidates, "id", 5.0)
        self.assertEqual(result, [1])

    def test_no_upgrade_when_no_better_player(self) -> None:
        # id=1 already scores the most — no upgrade possible.
        candidates = self._make_candidates([5.0, 5.0], [30.0, 10.0])
        result = _upgrade_picks([1], candidates, "id", 10.0)
        self.assertEqual(result, [1])

    def test_upgrade_repeats_until_stable(self) -> None:
        # id=1 ($5, 10pts) → id=2 ($8, 20pts) → id=3 ($8, 25pts) once budget allows.
        candidates = self._make_candidates([5.0, 8.0, 8.0], [10.0, 20.0, 25.0])
        result = _upgrade_picks([1], candidates, "id", 10.0)
        self.assertIn(3, result)

    def test_no_change_when_already_optimal(self) -> None:
        # 3 picks are already the 3 highest scorers — no upgrades.
        candidates = self._make_candidates([5.0, 5.0, 5.0, 5.0], [40.0, 30.0, 20.0, 10.0])
        result = _upgrade_picks([1, 2, 3], candidates, "id", 15.0)
        self.assertEqual(sorted(result), [1, 2, 3])


# ---------------------------------------------------------------------------
# _apply_transfer_constraints unit tests
#
# Setup: drivers 1-8 with pts [10,11,12,13,14,15,16,17] and price $10 each.
# constructors 101-105 with pts [20,21,22,23,24] and price $10 each.
# ---------------------------------------------------------------------------


def _make_full_drivers() -> pd.DataFrame:
    return pd.DataFrame({
        "driver_id": list(range(1, 9)),
        "predicted_fantasy_points": [float(9 + i) for i in range(1, 9)],
        "price": [10.0] * 8,
    })


def _make_full_constructors() -> pd.DataFrame:
    return pd.DataFrame({
        "team_id": list(range(101, 106)),
        "predicted_fantasy_points": [float(19 + i) for i in range(1, 6)],
        "price": [10.0] * 5,
    })


def _lineup(driver_ids: list[int], constructor_ids: list[int]) -> Lineup:
    return Lineup(
        driver_ids=driver_ids,
        constructor_ids=constructor_ids,
        drs_boost_driver_id=driver_ids[0],
        total_cost=float((len(driver_ids) + len(constructor_ids)) * 10),
        predicted_points=0.0,
    )


class TestApplyTransferConstraints(SimpleTestCase):
    def setUp(self) -> None:
        self.drivers = _make_full_drivers()
        self.constructors = _make_full_constructors()

    def _apply(self, ideal_d, current_d, free_transfers, transfer_penalty=10.0):
        ideal = _lineup(ideal_d, [101, 102])
        current = _lineup(current_d, [101, 102])
        return _apply_transfer_constraints(
            ideal=ideal,
            current=current,
            free_transfers=free_transfers,
            transfer_penalty=transfer_penalty,
            drivers_df=self.drivers,
            constructors_df=self.constructors,
            budget=100.0,
        )

    def test_no_changes_needed_returns_current(self) -> None:
        result = self._apply([4, 5, 6, 7, 8], [4, 5, 6, 7, 8], free_transfers=2)
        self.assertEqual(sorted(result.driver_ids), [4, 5, 6, 7, 8])

    def test_free_transfers_allow_changes(self) -> None:
        # current=[1,2,3,4,5], ideal=[4,5,6,7,8] → 3 changes needed
        # with free_transfers=3 all should be made
        result = self._apply([4, 5, 6, 7, 8], [1, 2, 3, 4, 5], free_transfers=3)
        self.assertEqual(sorted(result.driver_ids), [4, 5, 6, 7, 8])

    def test_extra_transfer_blocked_when_gain_below_penalty(self) -> None:
        # current=[1,2,3,4,5], ideal=[4,5,6,7,8]: 3 changes
        # Gains: drop1(10)→bring8(17)=7, drop2(11)→bring7(16)=5, drop3(12)→bring6(15)=3
        # free_transfers=2, penalty=10: 3rd gain=3 < 10 → blocked
        result = self._apply([4, 5, 6, 7, 8], [1, 2, 3, 4, 5], free_transfers=2, transfer_penalty=10.0)
        # Changes 1&2 made: drop 1→8, drop 2→7. Driver 3 stays.
        self.assertIn(3, result.driver_ids)
        self.assertNotIn(1, result.driver_ids)
        self.assertNotIn(2, result.driver_ids)

    def test_extra_transfer_allowed_when_gain_exceeds_penalty(self) -> None:
        # Same setup but penalty=2: gain=3 > 2 → all 3 changes made
        result = self._apply([4, 5, 6, 7, 8], [1, 2, 3, 4, 5], free_transfers=2, transfer_penalty=2.0)
        self.assertEqual(sorted(result.driver_ids), [4, 5, 6, 7, 8])

    def test_result_contains_five_drivers(self) -> None:
        result = self._apply([4, 5, 6, 7, 8], [1, 2, 3, 4, 5], free_transfers=2)
        self.assertEqual(len(result.driver_ids), 5)
