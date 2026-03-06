from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from predictions.models import MyLineup
from predictions.tests.factories import make_driver, make_event, make_season, make_team


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
