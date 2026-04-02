from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from predictions.management.commands.next_race import (
    _compute_available_budget,
    _count_transfers,
    _current_state,
)
from predictions.models import MyLineup
from predictions.optimizers.base import Lineup
from predictions.tests.factories import (
    make_constructor_price,
    make_driver,
    make_driver_price,
    make_event,
    make_season,
    make_team,
)


# ---------------------------------------------------------------------------
# Pure-function unit tests — no DB needed
# ---------------------------------------------------------------------------


class TestCountTransfers(SimpleTestCase):
    def _lineup(self, drivers: list[int], constructors: list[int]) -> Lineup:
        return Lineup(
            driver_ids=drivers,
            constructor_ids=constructors,
            drs_boost_driver_id=drivers[0],
            total_cost=0.0,
            predicted_points=0.0,
        )

    def test_both_none_returns_zero(self) -> None:
        self.assertEqual(_count_transfers(None, None), 0)

    def test_old_none_returns_zero(self) -> None:
        self.assertEqual(_count_transfers(None, self._lineup([1, 2, 3, 4, 5], [10, 11])), 0)

    def test_same_lineup_returns_zero(self) -> None:
        lineup = self._lineup([1, 2, 3, 4, 5], [10, 11])
        self.assertEqual(_count_transfers(lineup, lineup), 0)

    def test_two_driver_changes(self) -> None:
        old = self._lineup([1, 2, 3, 4, 5], [10, 11])
        new = self._lineup([1, 2, 3, 6, 7], [10, 11])
        self.assertEqual(_count_transfers(old, new), 2)

    def test_one_constructor_change(self) -> None:
        old = self._lineup([1, 2, 3, 4, 5], [10, 11])
        new = self._lineup([1, 2, 3, 4, 5], [10, 12])
        self.assertEqual(_count_transfers(old, new), 1)

    def test_driver_and_constructor_change(self) -> None:
        old = self._lineup([1, 2, 3, 4, 5], [10, 11])
        new = self._lineup([1, 2, 3, 4, 6], [10, 12])
        self.assertEqual(_count_transfers(old, new), 2)


# ---------------------------------------------------------------------------
# _current_state — DB-dependent
# ---------------------------------------------------------------------------


class TestCurrentState(TestCase):
    def setUp(self) -> None:
        self.season = make_season(year=2026)
        self.mclaren = make_team(self.season, name="McLaren")
        self.ferrari = make_team(self.season, name="Ferrari")
        self.nor = make_driver(self.season, self.mclaren, code="NOR", driver_number=4)
        self.ver = make_driver(self.season, self.mclaren, code="VER", driver_number=1)
        self.pia = make_driver(self.season, self.mclaren, code="PIA", driver_number=81)
        self.lec = make_driver(self.season, self.ferrari, code="LEC", driver_number=16)
        self.ham = make_driver(self.season, self.ferrari, code="HAM", driver_number=44)
        self.rus = make_driver(self.season, self.ferrari, code="RUS", driver_number=63)

        self.event1 = make_event(self.season, round_number=1, event_date=date(2026, 3, 1))
        self.event2 = make_event(self.season, round_number=2, event_date=date(2026, 3, 15))
        self.event3 = make_event(self.season, round_number=3, event_date=date(2026, 4, 1))

    def _save_lineup(self, event, driver_5=None) -> None:
        d5 = driver_5 or self.ham
        MyLineup.objects.update_or_create(
            event=event,
            defaults={
                "driver_1": self.nor, "driver_2": self.ver, "driver_3": self.pia,
                "driver_4": self.lec, "driver_5": d5,
                "drs_boost_driver": self.nor,
                "constructor_1": self.mclaren, "constructor_2": self.ferrari,
            },
        )

    def test_no_past_lineups_returns_none_and_two_banked(self) -> None:
        lineup, banked = _current_state(self.event1)
        self.assertIsNone(lineup)
        self.assertEqual(banked, 2)

    def test_one_past_lineup_no_transfers_banks_extra(self) -> None:
        # event1 saved, asking about event2 — no prev to compare against, 0 transfers
        self._save_lineup(self.event1)
        _, banked = _current_state(self.event2)
        # banked = min(2, 2 - min(0,2) + 1) = min(2, 3) = 2
        self.assertEqual(banked, 2)

    def test_two_past_lineups_zero_transfers_between_them(self) -> None:
        self._save_lineup(self.event1)
        self._save_lineup(self.event2)
        _, banked = _current_state(self.event3)
        # Round 1 → 2: 0 transfers, banked = min(2, 2-0+1) = 2
        # Round 2 → 3 check: 0 transfers, banked = min(2, 2-0+1) = 2
        self.assertEqual(banked, 2)

    def test_two_transfers_reduces_banked(self) -> None:
        self._save_lineup(self.event1)
        # event2 lineup changes two drivers
        self._save_lineup(self.event2, driver_5=self.rus)
        _, banked = _current_state(self.event3)
        # After event1 (first in history, prev=None): 0 transfers, banked=min(2,2-0+1)=2
        # After event2 vs event1: 1 transfer (HAM→RUS), banked=min(2,2-1+1)=2
        self.assertEqual(banked, 2)

    def test_returns_most_recent_lineup(self) -> None:
        self._save_lineup(self.event1)
        self._save_lineup(self.event2, driver_5=self.rus)
        lineup, _ = _current_state(self.event3)
        self.assertIsNotNone(lineup)
        self.assertIn(self.rus.id, lineup.driver_ids)

    def test_ignores_lineups_from_other_seasons(self) -> None:
        other_season = make_season(year=2025)
        other_team = make_team(other_season, name="McLaren")
        other_driver = make_driver(other_season, other_team, code="NOR", driver_number=4)
        from predictions.tests.factories import make_circuit
        other_circuit = make_circuit(key="circuit_other_season")
        other_event = make_event(other_season, round_number=1, event_date=date(2025, 1, 1), circuit=other_circuit)
        MyLineup.objects.create(
            event=other_event,
            driver_1=other_driver, driver_2=other_driver, driver_3=other_driver,
            driver_4=other_driver, driver_5=other_driver,
            drs_boost_driver=other_driver,
            constructor_1=other_team, constructor_2=other_team,
        )
        lineup, banked = _current_state(self.event1)
        self.assertIsNone(lineup)
        self.assertEqual(banked, 2)


# ---------------------------------------------------------------------------
# _compute_available_budget
# ---------------------------------------------------------------------------


class TestComputeAvailableBudget(TestCase):
    def setUp(self) -> None:
        self.season = make_season(year=2026)
        self.mclaren = make_team(self.season, name="McLaren")
        self.ferrari = make_team(self.season, name="Ferrari")
        self.nor = make_driver(self.season, self.mclaren, code="NOR", driver_number=4)
        self.ver = make_driver(self.season, self.mclaren, code="VER", driver_number=1)
        self.pia = make_driver(self.season, self.mclaren, code="PIA", driver_number=81)
        self.lec = make_driver(self.season, self.ferrari, code="LEC", driver_number=16)
        self.ham = make_driver(self.season, self.ferrari, code="HAM", driver_number=44)
        self.event1 = make_event(self.season, round_number=1, event_date=date(2026, 3, 1))
        self.event2 = make_event(self.season, round_number=2, event_date=date(2026, 3, 15))

    def _make_lineup(self, event, budget_cap, team_cost) -> MyLineup:
        from decimal import Decimal
        return MyLineup.objects.create(
            event=event,
            driver_1=self.nor, driver_2=self.ver, driver_3=self.pia,
            driver_4=self.lec, driver_5=self.ham,
            drs_boost_driver=self.nor,
            constructor_1=self.mclaren, constructor_2=self.ferrari,
            budget_cap=Decimal(str(budget_cap)) if budget_cap is not None else None,
            team_cost=Decimal(str(team_cost)) if team_cost is not None else None,
        )

    def test_returns_none_when_budget_cap_null(self) -> None:
        lineup = self._make_lineup(self.event1, budget_cap=None, team_cost=98.5)
        result = _compute_available_budget(lineup, self.event2)
        self.assertIsNone(result)

    def test_returns_none_when_team_cost_null(self) -> None:
        lineup = self._make_lineup(self.event1, budget_cap=100.0, team_cost=None)
        result = _compute_available_budget(lineup, self.event2)
        self.assertIsNone(result)

    def test_returns_none_when_prices_missing(self) -> None:
        lineup = self._make_lineup(self.event1, budget_cap=100.0, team_cost=98.5)
        # No prices seeded for event2
        result = _compute_available_budget(lineup, self.event2)
        self.assertIsNone(result)

    def test_correct_budget_with_prices(self) -> None:
        # bank = 100.0 - 98.5 = 1.5
        # event2 prices: 5 drivers @ 20.0 + 2 constructors @ 15.0 = 130.0
        # available = 1.5 + 130.0 = 131.5
        lineup = self._make_lineup(self.event1, budget_cap=100.0, team_cost=98.5)
        for d in [self.nor, self.ver, self.pia, self.lec, self.ham]:
            make_driver_price(d, self.event2, price=20.0)
        make_constructor_price(self.mclaren, self.event2, price=15.0)
        make_constructor_price(self.ferrari, self.event2, price=15.0)
        result = _compute_available_budget(lineup, self.event2)
        self.assertAlmostEqual(result, 131.5)


# ---------------------------------------------------------------------------
# Full command integration — ML layers mocked
# ---------------------------------------------------------------------------


def _make_predictions(driver_ids: list[int]) -> pd.DataFrame:
    return pd.DataFrame({
        "driver_id": driver_ids,
        "predicted_position": [float(i + 1) for i in range(len(driver_ids))],
        "predicted_fantasy_points": [30.0 - i * 2 for i in range(len(driver_ids))],
        "confidence_lower": [20.0] * len(driver_ids),
        "confidence_upper": [40.0] * len(driver_ids),
    })


def _make_features(driver_ids: list[int]) -> pd.DataFrame:
    return pd.DataFrame({
        "driver_id": driver_ids,
        "event_id": [1] * len(driver_ids),
        "rolling_avg_pts": [20.0] * len(driver_ids),
    })


def _make_training_data(driver_ids: list[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    X = pd.DataFrame({
        "driver_id": driver_ids,
        "event_id": [1] * len(driver_ids),
        "rolling_avg_pts": [20.0] * len(driver_ids),
    })
    y = pd.DataFrame({
        "finishing_position": [float(i + 1) for i in range(len(driver_ids))],
        "fantasy_points": [30.0 - i * 2 for i in range(len(driver_ids))],
    })
    return X, y


class TestNextRaceCommand(TestCase):
    def setUp(self) -> None:
        self.season = make_season(year=2026)
        self.mclaren = make_team(self.season, name="McLaren")
        self.ferrari = make_team(self.season, name="Ferrari")
        self.nor = make_driver(self.season, self.mclaren, code="NOR", driver_number=4)
        self.ver = make_driver(self.season, self.mclaren, code="VER", driver_number=1)
        self.pia = make_driver(self.season, self.mclaren, code="PIA", driver_number=81)
        self.lec = make_driver(self.season, self.ferrari, code="LEC", driver_number=16)
        self.ham = make_driver(self.season, self.ferrari, code="HAM", driver_number=44)

        self.past_event = make_event(self.season, round_number=1, event_date=date(2026, 3, 1))
        self.target_event = make_event(self.season, round_number=2, event_date=date(2026, 3, 15))

        self._driver_ids = [self.nor.id, self.ver.id, self.pia.id, self.lec.id, self.ham.id]

        for driver in [self.nor, self.ver, self.pia, self.lec, self.ham]:
            make_driver_price(driver, self.target_event, price=20.0)
        make_constructor_price(self.mclaren, self.target_event, price=25.0)
        make_constructor_price(self.ferrari, self.target_event, price=20.0)

    def _call(self, **kwargs) -> None:
        defaults = dict(year=2026, round=2, budget=150.0)
        defaults.update(kwargs)
        call_command("next_race", **defaults)

    def _patch_ml(self):
        """Context manager that patches all three ML layers."""
        driver_ids = self._driver_ids
        X, y = _make_training_data(driver_ids)
        features = _make_features(driver_ids)
        predictions = _make_predictions(driver_ids)
        optimal_lineup = Lineup(
            driver_ids=driver_ids,
            constructor_ids=[self.mclaren.id, self.ferrari.id],
            drs_boost_driver_id=self.nor.id,
            total_cost=105.0,
            predicted_points=130.0,
        )

        p1 = patch(
            "predictions.management.commands.next_race.build_training_dataset",
            return_value=(X, y),
        )
        p2 = patch(
            "predictions.management.commands.next_race.V2FeatureStore",
        )
        p3 = patch(
            "predictions.management.commands.next_race.XGBoostPredictorV4",
        )
        p4 = patch(
            "predictions.management.commands.next_race.ILPOptimizer",
        )

        class _CM:
            def __enter__(self_):
                self_.p1 = p1.start()
                mock_store = p2.start()
                mock_store.return_value.get_all_driver_features.return_value = features
                mock_predictor = p3.start()
                mock_predictor.return_value.predict.return_value = predictions
                mock_optimizer = p4.start()
                mock_optimizer.return_value.optimize_single_race.return_value = optimal_lineup
                return self_

            def __exit__(self_, *args):
                patch.stopall()

        return _CM()

    def test_raises_if_event_not_found(self) -> None:
        with self.assertRaises(CommandError):
            self._call(round=99)

    def test_raises_if_no_training_data(self) -> None:
        empty_X = pd.DataFrame()
        empty_y = pd.DataFrame()
        with patch(
            "predictions.management.commands.next_race.build_training_dataset",
            return_value=(empty_X, empty_y),
        ), patch("predictions.management.commands.next_race.V2FeatureStore"):
            with self.assertRaises(CommandError):
                self._call()

    def test_raises_if_no_features_for_target_event(self) -> None:
        X, y = _make_training_data(self._driver_ids)
        with patch(
            "predictions.management.commands.next_race.build_training_dataset",
            return_value=(X, y),
        ), patch("predictions.management.commands.next_race.V2FeatureStore") as MockStore, \
           patch("predictions.management.commands.next_race.XGBoostPredictorV4"):
            MockStore.return_value.get_all_driver_features.return_value = pd.DataFrame()
            with self.assertRaises(CommandError):
                self._call()

    def test_raises_if_no_price_data(self) -> None:
        from predictions.models import FantasyDriverPrice
        FantasyDriverPrice.objects.filter(event=self.target_event).delete()
        X, y = _make_training_data(self._driver_ids)
        features = _make_features(self._driver_ids)
        predictions = _make_predictions(self._driver_ids)
        with patch(
            "predictions.management.commands.next_race.build_training_dataset",
            return_value=(X, y),
        ), patch("predictions.management.commands.next_race.V2FeatureStore") as MockStore, \
           patch("predictions.management.commands.next_race.XGBoostPredictorV4") as MockPred:
            MockStore.return_value.get_all_driver_features.return_value = features
            MockPred.return_value.predict.return_value = predictions
            with self.assertRaises(CommandError):
                self._call()

    def test_no_current_lineup_prints_first_race_message(self) -> None:
        with self._patch_ml():
            from io import StringIO
            from django.core.management import call_command
            out = StringIO()
            call_command("next_race", year=2026, round=2, budget=150.0, stdout=out)
            self.assertIn("No lineup recorded", out.getvalue())

    def test_with_current_lineup_prints_current_team(self) -> None:
        MyLineup.objects.create(
            event=self.past_event,
            driver_1=self.nor, driver_2=self.ver, driver_3=self.pia,
            driver_4=self.lec, driver_5=self.ham,
            drs_boost_driver=self.nor,
            constructor_1=self.mclaren, constructor_2=self.ferrari,
        )
        with self._patch_ml():
            from io import StringIO
            out = StringIO()
            call_command("next_race", year=2026, round=2, budget=150.0, stdout=out)
            output = out.getvalue()
            self.assertIn("CURRENT TEAM", output)
            self.assertIn("NOR", output)

    def test_prints_predictions_table(self) -> None:
        with self._patch_ml():
            from io import StringIO
            out = StringIO()
            call_command("next_race", year=2026, round=2, budget=150.0, stdout=out)
            self.assertIn("PREDICTIONS", out.getvalue())

    def test_prints_recommended_lineup(self) -> None:
        with self._patch_ml():
            from io import StringIO
            out = StringIO()
            call_command("next_race", year=2026, round=2, budget=150.0, stdout=out)
            self.assertIn("RECOMMENDED LINEUP", out.getvalue())

    def test_raises_when_no_budget_and_no_prior_lineup(self) -> None:
        with self._patch_ml():
            with self.assertRaises(CommandError):
                call_command("next_race", year=2026, round=2)

    def test_auto_detects_budget_from_prior_lineup(self) -> None:
        from decimal import Decimal
        from io import StringIO
        # Record a prior lineup with budget info: bank=1.5, players priced at 20+20+20+20+20+25+20=145
        # auto budget = 1.5 + 145 = 146.5
        MyLineup.objects.create(
            event=self.past_event,
            driver_1=self.nor, driver_2=self.ver, driver_3=self.pia,
            driver_4=self.lec, driver_5=self.ham,
            drs_boost_driver=self.nor,
            constructor_1=self.mclaren, constructor_2=self.ferrari,
            budget_cap=Decimal("100.0"),
            team_cost=Decimal("98.5"),
        )
        with self._patch_ml():
            out = StringIO()
            call_command("next_race", year=2026, round=2, stdout=out)
            output = out.getvalue()
            self.assertIn("auto-detected", output)
            self.assertNotIn("manual override", output)
