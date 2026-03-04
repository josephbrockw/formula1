from __future__ import annotations

from datetime import datetime

import pandas as pd
from django.test import SimpleTestCase

from core.models import Driver, Session, Team
from core.tasks.data_mappers import map_laps, map_session_results, map_weather
from core.tests.factories import make_laps_dataframe, make_results_dataframe, make_weather_dataframe


class TestMapLaps(SimpleTestCase):
    def setUp(self) -> None:
        self.session = Session()
        self.driver = Driver(code="VER")
        self.driver_lookup = {"VER": self.driver}

    def test_map_laps_happy_path_returns_correct_count(self) -> None:
        df = make_laps_dataframe(num_drivers=1, num_laps=5)
        laps, skipped = map_laps(df, self.session, self.driver_lookup)
        self.assertEqual(len(laps), 5)

    def test_map_laps_assigns_session_and_driver(self) -> None:
        df = make_laps_dataframe(num_drivers=1, num_laps=1)
        laps, _ = map_laps(df, self.session, self.driver_lookup)
        self.assertIs(laps[0].session, self.session)
        self.assertIs(laps[0].driver, self.driver)

    def test_map_laps_lap_number_set_correctly(self) -> None:
        df = make_laps_dataframe(num_drivers=1, num_laps=3)
        laps, _ = map_laps(df, self.session, self.driver_lookup)
        self.assertEqual([lap.lap_number for lap in laps], [1, 2, 3])

    def test_map_laps_with_nat_laptime_sets_none(self) -> None:
        df = make_laps_dataframe(num_drivers=1, num_laps=1, LapTime=pd.NaT)
        laps, _ = map_laps(df, self.session, self.driver_lookup)
        self.assertIsNone(laps[0].lap_time)

    def test_map_laps_with_nat_sector_times_sets_none(self) -> None:
        df = make_laps_dataframe(
            num_drivers=1, num_laps=1, Sector1Time=pd.NaT, Sector2Time=pd.NaT, Sector3Time=pd.NaT
        )
        laps, _ = map_laps(df, self.session, self.driver_lookup)
        self.assertIsNone(laps[0].sector1_time)
        self.assertIsNone(laps[0].sector2_time)
        self.assertIsNone(laps[0].sector3_time)

    def test_map_laps_valid_laptime_preserved(self) -> None:
        expected = pd.Timedelta(seconds=91)
        df = make_laps_dataframe(num_drivers=1, num_laps=1, LapTime=expected)
        laps, _ = map_laps(df, self.session, self.driver_lookup)
        self.assertEqual(laps[0].lap_time, expected)

    def test_map_laps_pit_in_lap_flagged_correctly(self) -> None:
        pit_time = pd.Timedelta(seconds=50)
        df = make_laps_dataframe(num_drivers=1, num_laps=1, PitInTime=pit_time)
        laps, _ = map_laps(df, self.session, self.driver_lookup)
        self.assertTrue(laps[0].is_pit_in_lap)
        self.assertEqual(laps[0].pit_in_time, pit_time)

    def test_map_laps_pit_out_lap_flagged_correctly(self) -> None:
        pit_time = pd.Timedelta(seconds=50)
        df = make_laps_dataframe(num_drivers=1, num_laps=1, PitOutTime=pit_time)
        laps, _ = map_laps(df, self.session, self.driver_lookup)
        self.assertTrue(laps[0].is_pit_out_lap)
        self.assertEqual(laps[0].pit_out_time, pit_time)

    def test_map_laps_non_pit_lap_flags_false(self) -> None:
        df = make_laps_dataframe(num_drivers=1, num_laps=1)
        laps, _ = map_laps(df, self.session, self.driver_lookup)
        self.assertFalse(laps[0].is_pit_in_lap)
        self.assertFalse(laps[0].is_pit_out_lap)

    def test_map_laps_nan_position_sets_none(self) -> None:
        df = make_laps_dataframe(num_drivers=1, num_laps=1, Position=float("nan"))
        laps, _ = map_laps(df, self.session, self.driver_lookup)
        self.assertIsNone(laps[0].position)

    def test_map_laps_nan_tyre_life_sets_none(self) -> None:
        df = make_laps_dataframe(num_drivers=1, num_laps=1, TyreLife=float("nan"))
        laps, _ = map_laps(df, self.session, self.driver_lookup)
        self.assertIsNone(laps[0].tyre_life)

    def test_map_laps_nan_compound_sets_none(self) -> None:
        df = make_laps_dataframe(num_drivers=1, num_laps=1, Compound=float("nan"))
        laps, _ = map_laps(df, self.session, self.driver_lookup)
        self.assertIsNone(laps[0].compound)

    def test_map_laps_unknown_compound_stored_as_is(self) -> None:
        df = make_laps_dataframe(num_drivers=1, num_laps=1, Compound="UNKNOWN")
        laps, _ = map_laps(df, self.session, self.driver_lookup)
        self.assertEqual(laps[0].compound, "UNKNOWN")

    def test_map_laps_unknown_driver_returns_no_laps(self) -> None:
        df = make_laps_dataframe(num_drivers=1, num_laps=3)
        laps, _ = map_laps(df, self.session, {})
        self.assertEqual(len(laps), 0)

    def test_map_laps_partial_driver_lookup_maps_known_only(self) -> None:
        df = make_laps_dataframe(num_drivers=3, num_laps=2)
        laps, _ = map_laps(df, self.session, {"VER": self.driver})
        self.assertEqual(len(laps), 2)

    def test_map_laps_multiple_drivers_all_mapped(self) -> None:
        df = make_laps_dataframe(num_drivers=3, num_laps=5)
        lookup = {
            "VER": Driver(code="VER"),
            "HAM": Driver(code="HAM"),
            "LEC": Driver(code="LEC"),
        }
        laps, _ = map_laps(df, self.session, lookup)
        self.assertEqual(len(laps), 15)

    def test_map_laps_is_personal_best_set(self) -> None:
        df = make_laps_dataframe(num_drivers=1, num_laps=1, IsPersonalBest=True)
        laps, _ = map_laps(df, self.session, self.driver_lookup)
        self.assertTrue(laps[0].is_personal_best)

    def test_map_laps_is_accurate_set(self) -> None:
        df = make_laps_dataframe(num_drivers=1, num_laps=1, IsAccurate=False)
        laps, _ = map_laps(df, self.session, self.driver_lookup)
        self.assertFalse(laps[0].is_accurate)

    # --- skip reporting ---

    def test_map_laps_no_skips_returns_empty_list(self) -> None:
        df = make_laps_dataframe(num_drivers=1, num_laps=3)
        _, skipped = map_laps(df, self.session, self.driver_lookup)
        self.assertEqual(skipped, [])

    def test_map_laps_unknown_driver_returned_in_skipped(self) -> None:
        df = make_laps_dataframe(num_drivers=1, num_laps=3)
        _, skipped = map_laps(df, self.session, {})
        self.assertEqual(skipped, ["VER"])

    def test_map_laps_skipped_deduped_across_multiple_laps(self) -> None:
        df = make_laps_dataframe(num_drivers=1, num_laps=57)
        _, skipped = map_laps(df, self.session, {})
        self.assertEqual(skipped, ["VER"])  # 57 skipped laps, one unique code

    def test_map_laps_partial_lookup_returns_only_missing_codes(self) -> None:
        df = make_laps_dataframe(num_drivers=3, num_laps=2)
        _, skipped = map_laps(df, self.session, {"VER": self.driver})
        self.assertEqual(skipped, ["HAM", "LEC"])

    def test_map_laps_skipped_codes_sorted(self) -> None:
        df = make_laps_dataframe(num_drivers=3, num_laps=1)
        _, skipped = map_laps(df, self.session, {})
        self.assertEqual(skipped, sorted(skipped))


class TestMapSessionResults(SimpleTestCase):
    def setUp(self) -> None:
        self.session = Session()
        self.team = Team(name="Red Bull Racing")
        self.driver = Driver(code="VER")
        self.driver_lookup = {"VER": self.driver}
        self.team_lookup = {"Red Bull Racing": self.team}

    def test_map_results_happy_path_returns_result(self) -> None:
        df = make_results_dataframe(num_drivers=1)
        results, _ = map_session_results(df, self.session, self.driver_lookup, self.team_lookup)
        self.assertEqual(len(results), 1)

    def test_map_results_assigns_session_driver_team(self) -> None:
        df = make_results_dataframe(num_drivers=1)
        results, _ = map_session_results(df, self.session, self.driver_lookup, self.team_lookup)
        self.assertIs(results[0].session, self.session)
        self.assertIs(results[0].driver, self.driver)
        self.assertIs(results[0].team, self.team)

    def test_map_results_position_set_correctly(self) -> None:
        df = make_results_dataframe(num_drivers=1)
        results, _ = map_session_results(df, self.session, self.driver_lookup, self.team_lookup)
        self.assertEqual(results[0].position, 1)
        self.assertEqual(results[0].classified_position, "1")

    def test_map_results_dnf_has_null_position(self) -> None:
        df = make_results_dataframe(num_drivers=1, include_dnf=True)
        results, _ = map_session_results(df, self.session, self.driver_lookup, self.team_lookup)
        self.assertIsNone(results[0].position)

    def test_map_results_dnf_classified_position_is_r(self) -> None:
        df = make_results_dataframe(num_drivers=1, include_dnf=True)
        results, _ = map_session_results(df, self.session, self.driver_lookup, self.team_lookup)
        self.assertEqual(results[0].classified_position, "R")

    def test_map_results_dnf_status_set(self) -> None:
        df = make_results_dataframe(num_drivers=1, include_dnf=True)
        results, _ = map_session_results(df, self.session, self.driver_lookup, self.team_lookup)
        self.assertEqual(results[0].status, "Engine")

    def test_map_results_winner_time_is_none(self) -> None:
        df = make_results_dataframe(num_drivers=1)
        results, _ = map_session_results(df, self.session, self.driver_lookup, self.team_lookup)
        self.assertIsNone(results[0].time)

    def test_map_results_non_winner_time_set(self) -> None:
        df = make_results_dataframe(num_drivers=2)
        driver_lookup = {"VER": Driver(code="VER"), "HAM": Driver(code="HAM")}
        team_lookup = {
            "Red Bull Racing": Team(name="Red Bull Racing"),
            "Mercedes": Team(name="Mercedes"),
        }
        results, _ = map_session_results(df, self.session, driver_lookup, team_lookup)
        self.assertIsNotNone(results[1].time)

    def test_map_results_points_set_correctly(self) -> None:
        df = make_results_dataframe(num_drivers=1)
        results, _ = map_session_results(df, self.session, self.driver_lookup, self.team_lookup)
        self.assertEqual(results[0].points, 25.0)

    def test_map_results_nan_points_defaults_to_zero(self) -> None:
        df = make_results_dataframe(num_drivers=1, Points=float("nan"))
        results, _ = map_session_results(df, self.session, self.driver_lookup, self.team_lookup)
        self.assertEqual(results[0].points, 0.0)

    def test_map_results_multiple_drivers_all_mapped(self) -> None:
        df = make_results_dataframe(num_drivers=3)
        driver_lookup = {
            "VER": Driver(code="VER"),
            "HAM": Driver(code="HAM"),
            "LEC": Driver(code="LEC"),
        }
        team_lookup = {
            "Red Bull Racing": Team(name="Red Bull Racing"),
            "Mercedes": Team(name="Mercedes"),
            "Ferrari": Team(name="Ferrari"),
        }
        results, _ = map_session_results(df, self.session, driver_lookup, team_lookup)
        self.assertEqual(len(results), 3)

    # --- skip reporting ---

    def test_map_results_no_skips_returns_empty_list(self) -> None:
        df = make_results_dataframe(num_drivers=1)
        _, skipped = map_session_results(df, self.session, self.driver_lookup, self.team_lookup)
        self.assertEqual(skipped, [])

    def test_map_results_missing_driver_returned_in_skipped(self) -> None:
        df = make_results_dataframe(num_drivers=1)
        _, skipped = map_session_results(df, self.session, {}, self.team_lookup)
        self.assertEqual(skipped, ["VER"])

    def test_map_results_missing_team_returned_in_skipped(self) -> None:
        df = make_results_dataframe(num_drivers=1)
        _, skipped = map_session_results(df, self.session, self.driver_lookup, {})
        self.assertEqual(skipped, ["VER"])

    def test_map_results_partial_lookup_returns_only_missing_codes(self) -> None:
        df = make_results_dataframe(num_drivers=3)
        driver_lookup = {"VER": Driver(code="VER")}
        team_lookup = {"Red Bull Racing": Team(name="Red Bull Racing")}
        results, skipped = map_session_results(df, self.session, driver_lookup, team_lookup)
        self.assertEqual(len(results), 1)
        self.assertIn("HAM", skipped)
        self.assertIn("LEC", skipped)

    def test_map_results_skipped_codes_sorted(self) -> None:
        df = make_results_dataframe(num_drivers=3)
        _, skipped = map_session_results(df, self.session, {}, {})
        self.assertEqual(skipped, sorted(skipped))


class TestMapWeather(SimpleTestCase):
    def setUp(self) -> None:
        self.session = Session()
        self.session_date = datetime(2024, 3, 2, 14, 0, 0)

    def test_map_weather_happy_path_returns_samples(self) -> None:
        df = make_weather_dataframe(num_samples=5)
        result = map_weather(df, self.session, self.session_date)
        self.assertEqual(len(result), 5)

    def test_map_weather_assigns_session(self) -> None:
        df = make_weather_dataframe(num_samples=1)
        result = map_weather(df, self.session, self.session_date)
        self.assertIs(result[0].session, self.session)

    def test_map_weather_timestamp_is_absolute(self) -> None:
        df = make_weather_dataframe(num_samples=1)
        result = map_weather(df, self.session, self.session_date)
        self.assertEqual(result[0].timestamp, self.session_date + pd.Timedelta(minutes=0))

    def test_map_weather_timestamp_offset_applied(self) -> None:
        df = make_weather_dataframe(num_samples=2)
        result = map_weather(df, self.session, self.session_date)
        self.assertEqual(result[1].timestamp, self.session_date + pd.Timedelta(minutes=5))

    def test_map_weather_empty_dataframe_returns_empty_list(self) -> None:
        df = make_weather_dataframe(num_samples=0)
        result = map_weather(df, self.session, self.session_date)
        self.assertEqual(result, [])

    def test_map_weather_none_returns_empty_list(self) -> None:
        result = map_weather(None, self.session, self.session_date)
        self.assertEqual(result, [])

    def test_map_weather_rainfall_flag_set(self) -> None:
        df = make_weather_dataframe(num_samples=5, include_rain=True)
        result = map_weather(df, self.session, self.session_date)
        self.assertEqual(len([s for s in result if s.rainfall]), 2)

    def test_map_weather_no_rain_by_default(self) -> None:
        df = make_weather_dataframe(num_samples=5)
        result = map_weather(df, self.session, self.session_date)
        self.assertFalse(any(s.rainfall for s in result))

    def test_map_weather_fields_mapped_correctly(self) -> None:
        df = make_weather_dataframe(num_samples=1)
        result = map_weather(df, self.session, self.session_date)
        s = result[0]
        self.assertEqual(s.air_temp, 25.0)
        self.assertEqual(s.track_temp, 35.0)
        self.assertEqual(s.humidity, 55.0)
        self.assertEqual(s.pressure, 1013.0)
        self.assertEqual(s.wind_speed, 2.5)
        self.assertEqual(s.wind_direction, 180)
