from __future__ import annotations

from datetime import date

from django.conf import settings
from django.test import TestCase

from predictions.features.v1_pandas import V1FeatureStore
from predictions.tests.factories import (
    make_driver,
    make_event,
    make_fantasy_score,
    make_lap,
    make_result,
    make_season,
    make_session,
    make_team,
)


class TestRecentRaceForm(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season)
        self.driver = make_driver(self.season, self.team)
        self.store = V1FeatureStore()

        # The event we want features FOR (the upcoming race)
        self.target_event = make_event(self.season, round_number=5, event_date=date(2024, 5, 1))

    def _past_race(self, round_number: int, position: int | None, grid_position: int = 1, status: str = "Finished") -> None:
        event = make_event(self.season, round_number=round_number, event_date=date(2024, round_number, 1))
        session = make_session(event, session_type="R")
        make_result(session, self.driver, self.team, position=position, grid_position=grid_position, status=status)

    def test_no_past_races_returns_defaults(self) -> None:
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertEqual(features["position_mean_last3"], settings.NEW_ENTRANT_POSITION_DEFAULT)
        self.assertEqual(features["position_mean_last5"], settings.NEW_ENTRANT_POSITION_DEFAULT)
        self.assertEqual(features["dnf_rate_last10"], 0.0)
        self.assertEqual(features["positions_gained_mean_last5"], 0.0)

    def test_position_mean_uses_last_three_races(self) -> None:
        self._past_race(1, position=1)
        self._past_race(2, position=3)
        self._past_race(3, position=5)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertAlmostEqual(features["position_mean_last3"], 3.0)  # (1+3+5)/3

    def test_position_mean_last5_with_fewer_than_five_races(self) -> None:
        self._past_race(1, position=2)
        self._past_race(2, position=4)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        # Only 2 results available, still averages what it has
        self.assertAlmostEqual(features["position_mean_last5"], 3.0)

    def test_dnf_excluded_from_position_mean(self) -> None:
        self._past_race(1, position=None, status="Engine")   # DNF — no position
        self._past_race(2, position=3)
        self._past_race(3, position=5)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        # DNF has no position so doesn't affect position mean
        self.assertAlmostEqual(features["position_mean_last3"], 4.0)  # (3+5)/2

    def test_dnf_rate_counts_dnfs_correctly(self) -> None:
        self._past_race(1, position=None, status="Retired")
        self._past_race(2, position=None, status="Engine")
        self._past_race(3, position=1)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertAlmostEqual(features["dnf_rate_last10"], 2 / 3)

    def test_finished_lapped_not_counted_as_dnf(self) -> None:
        self._past_race(1, position=15, status="+1 Lap")
        self._past_race(2, position=18, status="+2 Laps")
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertEqual(features["dnf_rate_last10"], 0.0)

    def test_positions_gained_positive_when_finishing_ahead_of_grid(self) -> None:
        self._past_race(1, position=3, grid_position=7)  # gained 4
        self._past_race(2, position=5, grid_position=6)  # gained 1
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertAlmostEqual(features["positions_gained_mean_last5"], 2.5)  # (4+1)/2

    def test_position_std_measures_consistency(self) -> None:
        self._past_race(1, position=1)
        self._past_race(2, position=1)
        self._past_race(3, position=1)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertAlmostEqual(features["position_std_last5"], 0.0)  # perfectly consistent

    def test_position_std_nonzero_for_varied_results(self) -> None:
        self._past_race(1, position=1)
        self._past_race(2, position=10)
        self._past_race(3, position=19)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertGreater(features["position_std_last5"], 0.0)

    def test_future_race_results_not_included(self) -> None:
        # A race AFTER the target event should not affect features
        future_event = make_event(self.season, round_number=10, event_date=date(2024, 10, 1))
        future_session = make_session(future_event, session_type="R")
        make_result(future_session, self.driver, self.team, position=1)

        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertEqual(features["position_mean_last3"], settings.NEW_ENTRANT_POSITION_DEFAULT)  # still default — future not counted


class TestRecentQualifyingForm(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season)
        self.driver = make_driver(self.season, self.team)
        self.target_event = make_event(self.season, round_number=5, event_date=date(2024, 5, 1))
        self.store = V1FeatureStore()

    def _past_qualifying(self, round_number: int, position: int) -> None:
        event = make_event(self.season, round_number=round_number, event_date=date(2024, round_number, 1))
        session = make_session(event, session_type="Q")
        make_result(session, self.driver, self.team, position=position)

    def test_averages_last_three_qualifying_positions(self) -> None:
        self._past_qualifying(1, position=1)
        self._past_qualifying(2, position=3)
        self._past_qualifying(3, position=5)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertAlmostEqual(features["qualifying_position_mean_last3"], 3.0)

    def test_no_qualifying_history_returns_default(self) -> None:
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertEqual(features["qualifying_position_mean_last3"], settings.NEW_ENTRANT_POSITION_DEFAULT)

    def test_current_event_qualifying_not_included(self) -> None:
        # Even if qualifying data exists for the target event, it must not be used
        session = make_session(self.target_event, session_type="Q")
        make_result(session, self.driver, self.team, position=1)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        # No past qualifying → should still be the default, not 1.0
        self.assertEqual(features["qualifying_position_mean_last3"], settings.NEW_ENTRANT_POSITION_DEFAULT)


class TestEventContext(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season)
        self.driver = make_driver(self.season, self.team)
        self.store = V1FeatureStore()

    def test_round_number_in_features(self) -> None:
        event = make_event(self.season, round_number=7)
        features = self.store.get_driver_features(self.driver.id, event.id)
        self.assertEqual(features["round_number"], 7.0)

    def test_conventional_weekend_is_not_sprint(self) -> None:
        event = make_event(self.season, round_number=1, event_format="conventional")
        features = self.store.get_driver_features(self.driver.id, event.id)
        self.assertEqual(features["is_sprint_weekend"], 0.0)

    def test_sprint_weekend_flag(self) -> None:
        event = make_event(self.season, round_number=1, event_format="sprint")
        features = self.store.get_driver_features(self.driver.id, event.id)
        self.assertEqual(features["is_sprint_weekend"], 1.0)


class TestGetAllDriverFeatures(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season)
        self.event = make_event(self.season, round_number=1)
        self.store = V1FeatureStore()

    def test_returns_one_row_per_driver_in_season(self) -> None:
        make_driver(self.season, self.team, code="VER", driver_number=1)
        make_driver(self.season, self.team, code="HAM", driver_number=44)
        make_driver(self.season, self.team, code="LEC", driver_number=16)
        df = self.store.get_all_driver_features(self.event.id)
        self.assertEqual(len(df), 3)

    def test_dataframe_has_driver_id_column(self) -> None:
        make_driver(self.season, self.team, code="VER", driver_number=1)
        df = self.store.get_all_driver_features(self.event.id)
        self.assertIn("driver_id", df.columns)

    def test_dataframe_has_all_feature_columns(self) -> None:
        make_driver(self.season, self.team, code="VER", driver_number=1)
        df = self.store.get_all_driver_features(self.event.id)
        expected_features = [
            "position_mean_last3",
            "position_mean_last5",
            "position_std_last5",
            "dnf_rate_last10",
            "positions_gained_mean_last5",
            "qualifying_position_mean_last3",
            "circuit_position_mean_last3",
            "team_position_mean_last5",
            "fantasy_points_mean_last3",
            "practice_best_lap_rank",
            "practice_avg_best_5_rank",
            "circuit_length",
            "total_corners",
            "round_number",
            "is_sprint_weekend",
        ]
        for feature in expected_features:
            self.assertIn(feature, df.columns, f"Missing feature: {feature}")


class TestCircuitHistory(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season)
        self.driver = make_driver(self.season, self.team)
        self.store = V1FeatureStore()

    def test_averages_positions_at_same_circuit(self) -> None:
        from predictions.tests.factories import make_circuit
        circuit = make_circuit(key="monaco")
        target_event = make_event(self.season, round_number=5, circuit=circuit, event_date=date(2024, 5, 1))

        # Two past races at the same circuit
        past1 = make_event(make_season(2022), round_number=1, circuit=circuit, event_date=date(2022, 5, 1))
        past2 = make_event(make_season(2023), round_number=1, circuit=circuit, event_date=date(2023, 5, 1))
        for past_event in [past1, past2]:
            past_season = past_event.season
            past_team = make_team(past_season, name=f"Team {past_season.year}")
            past_driver = make_driver(past_season, past_team, code="VER", driver_number=1)
            session = make_session(past_event, session_type="R")
            make_result(session, past_driver, past_team, position=2)

        features = self.store.get_driver_features(self.driver.id, target_event.id)
        self.assertAlmostEqual(features["circuit_position_mean_last3"], 2.0)

    def test_different_circuit_results_not_counted(self) -> None:
        from predictions.tests.factories import make_circuit
        monaco = make_circuit(key="monaco")
        silverstone = make_circuit(key="silverstone")
        target_event = make_event(self.season, round_number=5, circuit=monaco, event_date=date(2024, 5, 1))

        # A past race at a DIFFERENT circuit
        other_event = make_event(self.season, round_number=1, circuit=silverstone, event_date=date(2024, 1, 1))
        session = make_session(other_event, session_type="R")
        make_result(session, self.driver, self.team, position=1)

        features = self.store.get_driver_features(self.driver.id, target_event.id)
        self.assertEqual(features["circuit_position_mean_last3"], 10.0)  # default — no Monaco history


class TestTeamRecentForm(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season)
        self.driver = make_driver(self.season, self.team, code="VER", driver_number=1)
        self.teammate = make_driver(self.season, self.team, code="PER", driver_number=11)
        self.target_event = make_event(self.season, round_number=5, event_date=date(2024, 5, 1))
        self.store = V1FeatureStore()

    def test_includes_both_drivers_on_team(self) -> None:
        past_event = make_event(self.season, round_number=1, event_date=date(2024, 1, 1))
        session = make_session(past_event, session_type="R")
        make_result(session, self.driver, self.team, position=1)
        make_result(session, self.teammate, self.team, position=3)

        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertAlmostEqual(features["team_position_mean_last5"], 2.0)  # (1+3)/2

    def test_no_team_history_returns_default(self) -> None:
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertEqual(features["team_position_mean_last5"], 10.0)


class TestCircuitFeatures(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season)
        self.driver = make_driver(self.season, self.team)
        self.store = V1FeatureStore()

    def test_circuit_length_and_corners_included(self) -> None:
        from predictions.tests.factories import make_circuit
        circuit = make_circuit(key="silverstone")
        event = make_event(self.season, round_number=1, circuit=circuit)
        features = self.store.get_driver_features(self.driver.id, event.id)
        self.assertEqual(features["circuit_length"], 5.891)
        self.assertEqual(features["total_corners"], 18.0)

    def test_null_circuit_fields_default_to_zero(self) -> None:
        from core.models import Circuit
        circuit = Circuit.objects.create(
            circuit_key="unknown", name="Unknown", country="X", city="X",
            circuit_length=None, total_corners=None,
        )
        event = make_event(self.season, round_number=1, circuit=circuit)
        features = self.store.get_driver_features(self.driver.id, event.id)
        self.assertEqual(features["circuit_length"], 0.0)
        self.assertEqual(features["total_corners"], 0.0)


class TestFantasyPointsHistory(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season)
        self.driver = make_driver(self.season, self.team)
        self.target_event = make_event(self.season, round_number=5, event_date=date(2024, 5, 1))
        self.store = V1FeatureStore()

    def test_averages_race_totals_from_past_events(self) -> None:
        e1 = make_event(self.season, round_number=1, event_date=date(2024, 1, 1))
        e2 = make_event(self.season, round_number=2, event_date=date(2024, 2, 1))
        e3 = make_event(self.season, round_number=3, event_date=date(2024, 3, 1))
        make_fantasy_score(self.driver, e1, race_total=40)
        make_fantasy_score(self.driver, e2, race_total=50)
        make_fantasy_score(self.driver, e3, race_total=60)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertAlmostEqual(features["fantasy_points_mean_last3"], 50.0)

    def test_no_fantasy_data_returns_zero(self) -> None:
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertEqual(features["fantasy_points_mean_last3"], 0.0)

    def test_current_event_fantasy_score_not_included(self) -> None:
        make_fantasy_score(self.driver, self.target_event, race_total=99)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertEqual(features["fantasy_points_mean_last3"], 0.0)


class TestPracticePace(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season)
        self.event = make_event(self.season, round_number=1)
        self.store = V1FeatureStore()

    def _make_drivers_and_fp_laps(self, lap_times: list[float]) -> list:
        """Create N drivers with one FP1 lap each. lap_times[0] is fastest."""
        fp1 = make_session(self.event, session_type="FP1")
        drivers = []
        for i, lap_time in enumerate(lap_times):
            driver = make_driver(self.season, self.team, code=f"D{i:02d}", driver_number=i + 1)
            make_lap(fp1, driver, lap_number=1, lap_time_seconds=lap_time)
            drivers.append(driver)
        return drivers

    def test_fastest_driver_is_rank_1(self) -> None:
        drivers = self._make_drivers_and_fp_laps([88.0, 90.0, 92.0])
        features = self.store.get_driver_features(drivers[0].id, self.event.id)
        self.assertEqual(features["practice_best_lap_rank"], 1.0)

    def test_slowest_driver_gets_last_rank(self) -> None:
        drivers = self._make_drivers_and_fp_laps([88.0, 90.0, 92.0])
        features = self.store.get_driver_features(drivers[2].id, self.event.id)
        self.assertEqual(features["practice_best_lap_rank"], 3.0)

    def test_inaccurate_laps_excluded(self) -> None:
        fp1 = make_session(self.event, session_type="FP1")
        driver_a = make_driver(self.season, self.team, code="AAA", driver_number=1)
        driver_b = make_driver(self.season, self.team, code="BBB", driver_number=2)
        # driver_a has a fast but inaccurate lap, and a slow accurate lap
        make_lap(fp1, driver_a, lap_number=1, lap_time_seconds=80.0, is_accurate=False)
        make_lap(fp1, driver_a, lap_number=2, lap_time_seconds=95.0, is_accurate=True)
        # driver_b has one accurate lap
        make_lap(fp1, driver_b, lap_number=1, lap_time_seconds=90.0, is_accurate=True)

        features = self.store.get_driver_features(driver_a.id, self.event.id)
        # driver_a's best accurate lap is 95s (slower than driver_b's 90s) → rank 2
        self.assertEqual(features["practice_best_lap_rank"], 2.0)

    def test_avg_best_5_rank_uses_multiple_laps(self) -> None:
        fp1 = make_session(self.event, session_type="FP1")
        driver_a = make_driver(self.season, self.team, code="AAA", driver_number=1)
        driver_b = make_driver(self.season, self.team, code="BBB", driver_number=2)
        # driver_a: one fast lap but inconsistent (avg best 5 will be higher)
        for i, t in enumerate([88.0, 95.0, 96.0, 97.0, 98.0]):
            make_lap(fp1, driver_a, lap_number=i + 1, lap_time_seconds=t)
        # driver_b: consistently fast across all 5 laps
        for i, t in enumerate([89.0, 89.5, 90.0, 90.5, 91.0]):
            make_lap(fp1, driver_b, lap_number=i + 1, lap_time_seconds=t)

        features_a = self.store.get_driver_features(driver_a.id, self.event.id)
        features_b = self.store.get_driver_features(driver_b.id, self.event.id)
        # driver_a is faster in single best lap
        self.assertLess(features_a["practice_best_lap_rank"], features_b["practice_best_lap_rank"])
        # driver_b is better over 5 laps (race pace)
        self.assertLess(features_b["practice_avg_best_5_rank"], features_a["practice_avg_best_5_rank"])

    def test_no_practice_data_returns_midfield_default(self) -> None:
        driver = make_driver(self.season, self.team, code="NEW", driver_number=99)
        features = self.store.get_driver_features(driver.id, self.event.id)
        self.assertEqual(features["practice_best_lap_rank"], 10.0)
        self.assertEqual(features["practice_avg_best_5_rank"], 10.0)
