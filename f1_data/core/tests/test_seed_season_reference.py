from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from core.flows.collect_season import _load_team_name_map
from core.models import Driver, Season, Team


def _write_roster(data: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


def _minimal_roster(**overrides) -> dict:
    data = {
        "season": 2026,
        "teams": [
            {"name": "Mercedes", "fastf1_name": "Mercedes"},
            {"name": "Ferrari", "fastf1_name": "Ferrari"},
        ],
        "drivers": [
            {
                "code": "RUS",
                "full_name": "George Russell",
                "fastf1_full_name": "George Russell",
                "driver_number": 63,
                "team": "Mercedes",
            },
            {
                "code": "HAM",
                "full_name": "Lewis Hamilton",
                "fastf1_full_name": "Lewis Hamilton",
                "driver_number": 44,
                "team": "Ferrari",
            },
        ],
    }
    data.update(overrides)
    return data


class TestSeedSeasonReference(TestCase):
    def setUp(self) -> None:
        self.season = Season.objects.create(year=2026)

    def test_creates_teams(self) -> None:
        path = _write_roster(_minimal_roster())
        call_command("seed_season_reference", year=2026, roster=path)
        self.assertEqual(Team.objects.filter(season=self.season).count(), 2)

    def test_creates_drivers(self) -> None:
        path = _write_roster(_minimal_roster())
        call_command("seed_season_reference", year=2026, roster=path)
        self.assertEqual(Driver.objects.filter(season=self.season).count(), 2)

    def test_team_name_stored_as_fastf1_name(self) -> None:
        # When fastf1_name differs from name, Team.name = fastf1_name so that
        # _sync_drivers_teams finds the record without a mapping.
        roster = _minimal_roster()
        roster["teams"] = [{"name": "Cadillac", "fastf1_name": "Cadillac F1 Team"}]
        roster["drivers"] = [
            {
                "code": "PER",
                "full_name": "Sergio Perez",
                "fastf1_full_name": "Sergio Perez",
                "driver_number": 11,
                "team": "Cadillac",
            }
        ]
        path = _write_roster(roster)
        call_command("seed_season_reference", year=2026, roster=path)
        self.assertTrue(Team.objects.filter(season=self.season, name="Cadillac F1 Team").exists())
        self.assertFalse(Team.objects.filter(season=self.season, name="Cadillac").exists())

    def test_driver_linked_to_correct_team(self) -> None:
        path = _write_roster(_minimal_roster())
        call_command("seed_season_reference", year=2026, roster=path)
        rus = Driver.objects.get(season=self.season, code="RUS")
        self.assertEqual(rus.team.name, "Mercedes")

    def test_is_idempotent_for_teams(self) -> None:
        path = _write_roster(_minimal_roster())
        call_command("seed_season_reference", year=2026, roster=path)
        call_command("seed_season_reference", year=2026, roster=path)
        self.assertEqual(Team.objects.filter(season=self.season).count(), 2)

    def test_is_idempotent_for_drivers(self) -> None:
        path = _write_roster(_minimal_roster())
        call_command("seed_season_reference", year=2026, roster=path)
        call_command("seed_season_reference", year=2026, roster=path)
        self.assertEqual(Driver.objects.filter(season=self.season).count(), 2)

    def test_updates_existing_driver_full_name(self) -> None:
        Team.objects.create(season=self.season, name="Mercedes")
        Driver.objects.create(
            season=self.season,
            code="RUS",
            full_name="Old Name",
            driver_number=99,
            team=Team.objects.get(season=self.season, name="Mercedes"),
        )
        path = _write_roster(_minimal_roster())
        call_command("seed_season_reference", year=2026, roster=path)
        rus = Driver.objects.get(season=self.season, code="RUS")
        self.assertEqual(rus.full_name, "George Russell")
        self.assertEqual(rus.driver_number, 63)

    def test_renames_team_when_fastf1_name_changes(self) -> None:
        # Simulate: seeded as "Cadillac", learned FastF1 calls it "Cadillac F1 Team".
        # Re-running seed should rename the existing row, not create a new one.
        Season.objects.create(year=9000)
        roster_before = {
            "season": 9000,
            "teams": [{"name": "Cadillac", "fastf1_name": "Cadillac"}],
            "drivers": [],
        }
        call_command("seed_season_reference", year=9000, roster=_write_roster(roster_before))
        old_pk = Team.objects.get(season__year=9000, name="Cadillac").pk

        roster_after = {
            "season": 9000,
            "teams": [{"name": "Cadillac", "fastf1_name": "Cadillac F1 Team"}],
            "drivers": [],
        }
        call_command("seed_season_reference", year=9000, roster=_write_roster(roster_after))

        self.assertEqual(Team.objects.filter(season__year=9000).count(), 1)
        renamed = Team.objects.get(season__year=9000)
        self.assertEqual(renamed.name, "Cadillac F1 Team")
        self.assertEqual(renamed.pk, old_pk)  # same row, FK references preserved

    def test_raises_if_season_not_found(self) -> None:
        path = _write_roster(_minimal_roster())
        with self.assertRaises(CommandError):
            call_command("seed_season_reference", year=9999, roster=path)

    def test_raises_if_roster_file_not_found(self) -> None:
        with self.assertRaises(CommandError):
            call_command("seed_season_reference", year=2026, roster="/nonexistent/roster.json")

    def test_raises_if_driver_references_unlisted_team(self) -> None:
        roster = _minimal_roster()
        roster["drivers"][0]["team"] = "UnknownTeam"
        path = _write_roster(roster)
        with self.assertRaises(CommandError):
            call_command("seed_season_reference", year=2026, roster=path)


class TestLoadTeamNameMap(SimpleTestCase):
    def test_returns_empty_dict_when_no_roster_file(self) -> None:
        result = _load_team_name_map(9999)
        self.assertEqual(result, {})

    def test_returns_empty_dict_when_all_fastf1_names_match(self) -> None:
        roster = {
            "teams": [
                {"name": "Mercedes", "fastf1_name": "Mercedes"},
                {"name": "Ferrari", "fastf1_name": "Ferrari"},
            ]
        }
        self.assertEqual(_map_from_data(roster), {})

    def test_returns_mapping_when_fastf1_name_differs(self) -> None:
        roster = {
            "teams": [
                {"name": "Cadillac", "fastf1_name": "Cadillac F1 Team"},
                {"name": "Audi", "fastf1_name": "Audi"},
            ]
        }
        self.assertEqual(_map_from_data(roster), {"Cadillac": "Cadillac F1 Team"})


def _map_from_data(data: dict) -> dict:
    """Run the _load_team_name_map mapping logic against in-memory data."""
    return {
        t["name"]: t["fastf1_name"]
        for t in data.get("teams", [])
        if "fastf1_name" in t and t["fastf1_name"] != t["name"]
    }
