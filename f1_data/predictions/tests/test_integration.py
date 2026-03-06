from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
from django.core.management import call_command
from django.test import TestCase

from core.models import Driver, Event, Season, Session, SessionResult
from predictions.models import FantasyDriverPrice, FantasyDriverScore, MyLineup

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_ROSTER_PATH = _PROJECT_ROOT / "data" / "2026_roster.json"
_DRIVER_PRICES_PATH = _PROJECT_ROOT / "data" / "starting_prices" / "2026_drivers.csv"
_CONSTRUCTOR_PRICES_PATH = _PROJECT_ROOT / "data" / "starting_prices" / "2026_constructors.csv"

# 22-driver 2026 grid: (code, full_name, driver_number, fastf1_team_name, starting_price)
# Prices match data/starting_prices/2026_drivers.csv exactly.
_2026_DRIVERS = [
    ("RUS", "George Russell", 63, "Mercedes", 27.4),
    ("ANT", "Andrea Kimi Antonelli", 12, "Mercedes", 23.2),
    ("LEC", "Charles Leclerc", 16, "Ferrari", 22.8),
    ("HAM", "Lewis Hamilton", 44, "Ferrari", 22.5),
    ("NOR", "Lando Norris", 4, "McLaren", 27.2),
    ("PIA", "Oscar Piastri", 81, "McLaren", 25.5),
    ("VER", "Max Verstappen", 1, "Red Bull Racing", 27.7),
    ("HAD", "Isack Hadjar", 6, "Red Bull Racing", 15.1),
    ("STR", "Lance Stroll", 18, "Aston Martin", 8.0),
    ("ALO", "Fernando Alonso", 14, "Aston Martin", 10.0),
    ("ALB", "Alexander Albon", 23, "Williams", 11.6),
    ("SAI", "Carlos Sainz", 55, "Williams", 11.8),
    ("GAS", "Pierre Gasly", 10, "Alpine", 12.0),
    ("COL", "Franco Colapinto", 43, "Alpine", 6.2),
    ("OCO", "Esteban Ocon", 31, "Haas F1 Team", 7.3),
    ("BEA", "Oliver Bearman", 87, "Haas F1 Team", 7.4),
    ("LAW", "Liam Lawson", 30, "Racing Bulls", 6.5),
    ("LIN", "Arvid Lindblad", 41, "Racing Bulls", 6.2),
    ("HUL", "Nico Hulkenberg", 27, "Audi", 6.8),
    ("BOR", "Gabriel Bortoleto", 5, "Audi", 6.4),
    ("PER", "Sergio Perez", 11, "Cadillac", 6.0),
    ("BOT", "Valtteri Bottas", 77, "Cadillac", 5.9),
]

# 11 constructors with starting prices from data/starting_prices/2026_constructors.csv
_2026_CONSTRUCTORS = [
    ("Red Bull Racing", 28.2),
    ("Ferrari", 23.3),
    ("McLaren", 28.9),
    ("Mercedes", 29.3),
    ("Aston Martin", 10.3),
    ("Alpine", 12.5),
    ("Williams", 12.0),
    ("Racing Bulls", 6.3),
    ("Haas F1 Team", 7.4),
    ("Cadillac", 6.0),
    ("Audi", 6.6),
]

# Cheapest valid lineup: BOT(5.9)+PER(6.0)+COL(6.2)+LIN(6.2)+BOR(6.4)+Cadillac(6.0)+RacingBulls(6.3) = $43.0M
_LINEUP_DRIVERS = ["BOT", "PER", "COL", "LIN", "BOR"]
_LINEUP_DRS = "BOT"
_LINEUP_CONSTRUCTORS = ["Cadillac", "Racing Bulls"]

_ROUND_DATES = {
    1: date(2026, 3, 16),
    2: date(2026, 4, 6),
    3: date(2026, 4, 27),
    4: date(2026, 5, 18),
    5: date(2026, 6, 8),
    6: date(2026, 6, 29),
}
_ROUND_NAMES = {
    1: "Australian Grand Prix",
    2: "Bahrain Grand Prix",
    3: "Chinese Grand Prix",
    4: "Japanese Grand Prix",
    5: "Saudi Arabian Grand Prix",
    6: "Miami Grand Prix",
}
# Short keywords for _event_by_race_name icontains matching
_RACE_KEYWORDS = {1: "Australia", 2: "Bahrain", 3: "Chinese", 4: "Japan", 5: "Saudi"}
# CSV snapshot dates: 4 days before each event
_CSV_DATES = {
    1: "2026-03-12",
    2: "2026-04-02",
    3: "2026-04-23",
    4: "2026-05-14",
    5: "2026-06-04",
    6: "2026-06-25",
}

_FLOW = "core.flows.collect_season"


def _make_schedule_df() -> pd.DataFrame:
    sessions = ["Practice 1", "Practice 2", "Practice 3", "Qualifying", "Race"]
    rows = []
    for rnd in range(1, 7):
        event_date = _ROUND_DATES[rnd]
        row: dict = {
            "RoundNumber": rnd,
            "EventName": _ROUND_NAMES[rnd],
            "Country": "Country",
            "Location": f"City{rnd}",
            "EventDate": pd.Timestamp(event_date),
            "EventFormat": "conventional",
        }
        for slot, sname in enumerate(sessions, 1):
            row[f"Session{slot}"] = sname
            row[f"Session{slot}Date"] = pd.Timestamp(f"{event_date} 12:00:00")
        rows.append(row)
    return pd.DataFrame(rows)


def _make_session_mock() -> MagicMock:
    """22-driver results + 3 laps per driver + 5 weather samples."""
    from core.tests.factories import make_weather_dataframe

    results_rows = []
    laps_rows = []
    for i, (code, full_name, number, team, _price) in enumerate(_2026_DRIVERS):
        pos = i + 1
        results_rows.append(
            {
                "DriverNumber": str(number),
                "Abbreviation": code,
                "FullName": full_name,
                "TeamName": team,
                "Position": float(pos),
                "ClassifiedPosition": str(pos),
                "GridPosition": pos,
                "Status": "Finished",
                "Points": max(0.0, 25.0 - i * 2),
                "Time": pd.NaT if i == 0 else pd.Timedelta(seconds=5 * i),
                "FastestLapTime": pd.NaT,
                "FastestLapRank": float("nan"),
            }
        )
        for lap in range(1, 4):
            laps_rows.append(
                {
                    "Driver": code,
                    "DriverNumber": str(number),
                    "LapNumber": lap,
                    "LapTime": pd.Timedelta(seconds=90 + lap * 0.1 + i * 0.05),
                    "Sector1Time": pd.Timedelta(seconds=28),
                    "Sector2Time": pd.Timedelta(seconds=32),
                    "Sector3Time": pd.Timedelta(seconds=30),
                    "PitInTime": pd.NaT,
                    "PitOutTime": pd.NaT,
                    "Stint": 1,
                    "Compound": "SOFT",
                    "TyreLife": float(lap),
                    "Position": float(pos),
                    "TrackStatus": "1",
                    "IsPersonalBest": lap == 1,
                    "IsAccurate": True,
                }
            )
    mock = MagicMock()
    mock.results = pd.DataFrame(results_rows)
    mock.laps = pd.DataFrame(laps_rows)
    mock.weather_data = make_weather_dataframe()
    mock.date = datetime(2026, 3, 16, 15, 0)
    return mock


def _write_driver_price_csv(path: Path) -> None:
    lines = ["Driver Name,Current Value,Price Change,% Picked,Season Points"]
    for _code, full_name, _num, _team, price in _2026_DRIVERS:
        lines.append(f'"{full_name}","${price:.1f}M","$0.0M",10.0,0')
    path.write_text("\n".join(lines))


def _write_constructor_price_csv(path: Path) -> None:
    lines = ["Constructor Name,Current Value,Price Change,% Picked,Season Points"]
    for name, price in _2026_CONSTRUCTORS:
        lines.append(f'"{name}","${price:.1f}M","$0.0M",10.0,0')
    path.write_text("\n".join(lines))


def _write_driver_score_csv(path: Path, race_keyword: str) -> None:
    lines = ["Race,Driver Name,Event Type,Scoring Item,Frequency,Position,Points,Race Total,Season Total"]
    for i, (_code, full_name, _num, _team, _price) in enumerate(_2026_DRIVERS):
        pos = i + 1
        race_total = max(0, 50 - pos * 2)
        points = max(0, 25 - pos)
        lines.append(
            f'"{race_keyword}","{full_name}","race","Race Position",,{pos},{points},{race_total},{race_total}'
        )
    path.write_text("\n".join(lines))


def _write_constructor_score_csv(path: Path, race_keyword: str) -> None:
    lines = ["Race,Constructor Name,Event Type,Scoring Item,Frequency,Position,Points,Race Total,Season Total"]
    for i, (name, _price) in enumerate(_2026_CONSTRUCTORS):
        race_total = max(0, 80 - i * 5)
        points = max(0, 25 - (i + 1))
        lines.append(
            f'"{race_keyword}","{name}","race","Race Position",,{i + 1},{points},{race_total},{race_total}'
        )
    path.write_text("\n".join(lines))


@unittest.skipUnless(os.getenv("RUN_INTEGRATION_TESTS"), "Set RUN_INTEGRATION_TESTS=1 to run")
class FullWorkflowIntegrationTest(TestCase):
    def test_full_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)

            # ---------------------------------------------------------------
            # Phase A — New Season Initialization
            # ---------------------------------------------------------------

            # Steps 1+4: collect_data --year 2026 --round 1
            # Syncs the full 6-event 2026 schedule (creating all Events and Sessions)
            # then collects only Round 1 session data via the FastF1 mock.
            with (
                patch(f"{_FLOW}.get_event_schedule", return_value=_make_schedule_df()),
                patch(f"{_FLOW}.load_session", side_effect=lambda y, r, s: _make_session_mock()),
                patch(f"{_FLOW}.send_slack_notification"),
            ):
                call_command("collect_data", year=2026, round_number=1, stdout=StringIO())

            self.assertEqual(Event.objects.filter(season__year=2026).count(), 6)
            self.assertEqual(Session.objects.filter(event__season__year=2026).count(), 30)

            # Step 2: seed 22 drivers and 11 teams from the 2026 roster JSON
            call_command(
                "seed_season_reference",
                year=2026,
                roster=str(_ROSTER_PATH),
                stdout=StringIO(),
            )
            self.assertEqual(Driver.objects.filter(season__year=2026).count(), 22)

            # Step 3: compute starting prices for all 6 events (no scores exist yet,
            # so all prices equal the starting prices CSV values)
            call_command(
                "compute_fantasy_prices",
                year=2026,
                driver_prices=str(_DRIVER_PRICES_PATH),
                constructor_prices=str(_CONSTRUCTOR_PRICES_PATH),
                stdout=StringIO(),
            )
            self.assertEqual(
                FantasyDriverPrice.objects.filter(event__season__year=2026).count(),
                22 * 6,
            )

            # ---------------------------------------------------------------
            # Phase B — Seed Past Rounds
            # ---------------------------------------------------------------

            # Round 1 race results were collected by collect_data above
            self.assertGreater(
                SessionResult.objects.filter(
                    session__event__round_number=1, session__session_type="R"
                ).count(),
                0,
            )

            # Step 5: seed rounds 2–5 with direct SessionResult records (faster than
            # 4×5 mock collect_data calls; avoids hitting the FastF1 mock boundary)
            season = Season.objects.get(year=2026)
            drivers = list(Driver.objects.filter(season=season).order_by("id"))
            for round_num in range(2, 6):
                race_session = Session.objects.get(
                    event__season=season,
                    event__round_number=round_num,
                    session_type="R",
                )
                SessionResult.objects.bulk_create(
                    [
                        SessionResult(
                            session=race_session,
                            driver=drivers[i],
                            team=drivers[i].team,
                            position=i + 1,
                            classified_position=str(i + 1),
                            grid_position=i + 1,
                            status="Finished",
                            points=max(0.0, 25.0 - i * 2),
                        )
                        for i in range(len(drivers))
                    ]
                )

            # Step 6: import price + score CSVs for rounds 1–5
            scores_dir = tmpdir / "scores"
            scores_dir.mkdir()
            for round_num in range(1, 6):
                csv_date = _CSV_DATES[round_num]
                race_kw = _RACE_KEYWORDS[round_num]
                _write_driver_price_csv(scores_dir / f"{csv_date}-drivers.csv")
                _write_constructor_price_csv(scores_dir / f"{csv_date}-constructors.csv")
                _write_driver_score_csv(
                    scores_dir / f"{csv_date}-all-drivers-performance.csv", race_kw
                )
                _write_constructor_score_csv(
                    scores_dir / f"{csv_date}-all-constructors-performance.csv", race_kw
                )
            call_command("import_fantasy_csv", dir=str(scores_dir), stdout=StringIO())
            # One score row per driver per round (scoring_item="Race Position")
            self.assertEqual(
                FantasyDriverScore.objects.filter(event__season__year=2026).count(),
                22 * 5,
            )

            # Step 7: import price-only CSVs for round 6 (upcoming — no results yet)
            r6_dir = tmpdir / "r6"
            r6_dir.mkdir()
            r6_csv_date = _CSV_DATES[6]
            _write_driver_price_csv(r6_dir / f"{r6_csv_date}-drivers.csv")
            _write_constructor_price_csv(r6_dir / f"{r6_csv_date}-constructors.csv")
            call_command("import_fantasy_csv", dir=str(r6_dir), stdout=StringIO())

            # Step 8: record round 1 lineup with the 5 cheapest drivers + 2 cheapest constructors
            # BOT(5.9)+PER(6.0)+COL(6.2)+LIN(6.2)+BOR(6.4)+Cadillac(6.0)+RacingBulls(6.3) = $43.0M
            call_command(
                "record_my_lineup",
                year=2026,
                round=1,
                drivers=_LINEUP_DRIVERS,
                drs=_LINEUP_DRS,
                constructors=_LINEUP_CONSTRUCTORS,
                stdout=StringIO(),
            )
            lineup = MyLineup.objects.get(event__season__year=2026, event__round_number=1)
            self.assertEqual(lineup.budget_cap, Decimal("100.0"))
            self.assertIsNotNone(lineup.team_cost)
            self.assertLessEqual(lineup.team_cost, lineup.budget_cap)

            # ---------------------------------------------------------------
            # Phase C — Round 6 Pre-Race Recommendation
            # ---------------------------------------------------------------

            # Step 9: next_race for round 6 — runs the real ML pipeline end-to-end.
            # Training data: rounds 1–5 (session results + fantasy scores).
            # Budget auto-detected from round 1 MyLineup (bank + round-6 team value).
            out = StringIO()
            call_command("next_race", year=2026, round=6, stdout=out)
            output = out.getvalue()

            self.assertIn("auto-detected", output)
            self.assertIn("Budget: $", output)
            self.assertIn("RECOMMENDED LINEUP", output)
            self.assertIn("PREDICTIONS", output)
