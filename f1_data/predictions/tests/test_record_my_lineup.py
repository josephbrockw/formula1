from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from predictions.models import MyLineup
from predictions.tests.factories import (
    make_constructor_price,
    make_driver,
    make_driver_price,
    make_event,
    make_season,
    make_team,
)


class TestRecordMyLineup(TestCase):
    def setUp(self) -> None:
        self.season = make_season(year=2026)
        self.event = make_event(self.season, round_number=1)
        self.mclaren = make_team(self.season, name="McLaren")
        self.ferrari = make_team(self.season, name="Ferrari")
        self.nor = make_driver(self.season, self.mclaren, code="NOR", driver_number=4)
        self.ver = make_driver(self.season, self.mclaren, code="VER", driver_number=1)
        self.pia = make_driver(self.season, self.mclaren, code="PIA", driver_number=81)
        self.lec = make_driver(self.season, self.ferrari, code="LEC", driver_number=16)
        self.ham = make_driver(self.season, self.ferrari, code="HAM", driver_number=44)

    def _call(self, **kwargs) -> None:
        defaults = dict(
            year=2026, round=1,
            drivers=["NOR", "VER", "PIA", "LEC", "HAM"],
            drs="NOR",
            constructors=["McLaren", "Ferrari"],
            actual_points=None,
        )
        defaults.update(kwargs)
        call_command("record_my_lineup", **defaults)

    def test_creates_mylineup_record(self) -> None:
        self._call()
        self.assertEqual(MyLineup.objects.count(), 1)

    def test_saves_correct_drivers(self) -> None:
        self._call()
        lineup = MyLineup.objects.get()
        saved = {lineup.driver_1, lineup.driver_2, lineup.driver_3, lineup.driver_4, lineup.driver_5}
        self.assertEqual(saved, {self.nor, self.ver, self.pia, self.lec, self.ham})

    def test_saves_correct_drs_driver(self) -> None:
        self._call()
        self.assertEqual(MyLineup.objects.get().drs_boost_driver, self.nor)

    def test_saves_correct_constructors(self) -> None:
        self._call()
        lineup = MyLineup.objects.get()
        saved = {lineup.constructor_1, lineup.constructor_2}
        self.assertEqual(saved, {self.mclaren, self.ferrari})

    def test_actual_points_defaults_to_none(self) -> None:
        self._call()
        self.assertIsNone(MyLineup.objects.get().actual_points)

    def test_saves_actual_points_when_provided(self) -> None:
        self._call(actual_points=187.5)
        self.assertEqual(MyLineup.objects.get().actual_points, 187.5)

    def test_is_idempotent(self) -> None:
        self._call()
        self._call()
        self.assertEqual(MyLineup.objects.count(), 1)

    def test_update_actual_points_on_rerun(self) -> None:
        self._call()
        self._call(actual_points=142.0)
        self.assertEqual(MyLineup.objects.get().actual_points, 142.0)

    def test_constructor_name_is_case_insensitive(self) -> None:
        self._call(constructors=["mclaren", "ferrari"])
        self.assertEqual(MyLineup.objects.count(), 1)

    def test_driver_code_is_case_insensitive(self) -> None:
        self._call(drivers=["nor", "ver", "pia", "lec", "ham"], drs="nor")
        self.assertEqual(MyLineup.objects.get().drs_boost_driver, self.nor)

    def test_raises_if_event_not_found(self) -> None:
        with self.assertRaises(CommandError):
            self._call(round=99)

    def test_raises_if_driver_not_found(self) -> None:
        with self.assertRaises(CommandError):
            self._call(drivers=["NOR", "VER", "PIA", "LEC", "XXX"])

    def test_raises_if_drs_not_in_selected_drivers(self) -> None:
        with self.assertRaises(CommandError):
            self._call(drs="RUS")

    def test_raises_if_constructor_not_found(self) -> None:
        with self.assertRaises(CommandError):
            self._call(constructors=["McLaren", "UnknownTeam"])


class TestRecordMyLineupBudget(TestCase):
    def setUp(self) -> None:
        self.season = make_season(year=2026)
        self.event1 = make_event(self.season, round_number=1, event_date=date(2026, 3, 1))
        self.event2 = make_event(self.season, round_number=2, event_date=date(2026, 3, 15))
        self.mclaren = make_team(self.season, name="McLaren")
        self.ferrari = make_team(self.season, name="Ferrari")
        self.nor = make_driver(self.season, self.mclaren, code="NOR", driver_number=4)
        self.ver = make_driver(self.season, self.mclaren, code="VER", driver_number=1)
        self.pia = make_driver(self.season, self.mclaren, code="PIA", driver_number=81)
        self.lec = make_driver(self.season, self.ferrari, code="LEC", driver_number=16)
        self.ham = make_driver(self.season, self.ferrari, code="HAM", driver_number=44)

    def _add_prices(self, event, driver_price=10.0, constructor_price=10.0) -> None:
        for d in [self.nor, self.ver, self.pia, self.lec, self.ham]:
            make_driver_price(d, event, price=driver_price)
        make_constructor_price(self.mclaren, event, price=constructor_price)
        make_constructor_price(self.ferrari, event, price=constructor_price)

    def _call(self, round: int = 1) -> None:
        call_command(
            "record_my_lineup",
            year=2026, round=round,
            drivers=["NOR", "VER", "PIA", "LEC", "HAM"],
            drs="NOR",
            constructors=["McLaren", "Ferrari"],
            actual_points=None,
        )

    def test_first_race_budget_cap_is_100(self) -> None:
        self._add_prices(self.event1)
        self._call(round=1)
        lineup = MyLineup.objects.get(event=self.event1)
        self.assertEqual(lineup.budget_cap, Decimal("100.0"))

    def test_team_cost_is_sum_of_prices(self) -> None:
        # 5 drivers @ 10.0 + 2 constructors @ 10.0 = 70.0
        self._add_prices(self.event1, driver_price=10.0, constructor_price=10.0)
        self._call(round=1)
        lineup = MyLineup.objects.get(event=self.event1)
        self.assertEqual(lineup.team_cost, Decimal("70.0"))

    def test_team_cost_and_budget_cap_null_when_prices_missing(self) -> None:
        # No prices seeded — both fields should be None
        self._call(round=1)
        lineup = MyLineup.objects.get(event=self.event1)
        self.assertIsNone(lineup.team_cost)
        self.assertIsNone(lineup.budget_cap)

    def test_second_race_budget_cap_uses_prev_bank_plus_current_values(self) -> None:
        # Round 1: 5 drivers @ 10 + 2 constructors @ 10 = 70. budget_cap=100 → bank=30
        # Round 2: same players still at 10 → current_team_value=70
        # budget_cap = 30 + 70 = 100.0
        self._add_prices(self.event1, driver_price=10.0, constructor_price=10.0)
        self._call(round=1)
        self._add_prices(self.event2, driver_price=10.0, constructor_price=10.0)
        self._call(round=2)
        lineup2 = MyLineup.objects.get(event=self.event2)
        self.assertEqual(lineup2.budget_cap, Decimal("100.0"))

    def test_second_race_budget_cap_reflects_price_increases(self) -> None:
        # Round 1: drivers @ 10, constructors @ 10 → team_cost=70, bank=30
        # Round 2: drivers @ 11, constructors @ 11 → current_team_value=77
        # budget_cap = 30 + 77 = 107.0
        self._add_prices(self.event1, driver_price=10.0, constructor_price=10.0)
        self._call(round=1)
        self._add_prices(self.event2, driver_price=11.0, constructor_price=11.0)
        self._call(round=2)
        lineup2 = MyLineup.objects.get(event=self.event2)
        self.assertEqual(lineup2.budget_cap, Decimal("107.0"))

    def test_raises_when_team_cost_exceeds_budget_cap(self) -> None:
        # 5 drivers @ 25.0 + 2 constructors @ 20.0 = 165.0 > 100.0
        self._add_prices(self.event1, driver_price=25.0, constructor_price=20.0)
        with self.assertRaises(CommandError):
            self._call(round=1)

    def test_second_race_budget_cap_null_when_prev_has_null_fields(self) -> None:
        # Round 1 recorded with no prices → team_cost/budget_cap null
        # Round 2 cannot compute budget_cap from nulls
        self._call(round=1)
        self._add_prices(self.event2)
        self._call(round=2)
        lineup2 = MyLineup.objects.get(event=self.event2)
        self.assertIsNone(lineup2.budget_cap)
