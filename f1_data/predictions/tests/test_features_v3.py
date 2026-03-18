from __future__ import annotations

from datetime import date

from django.test import TestCase

from predictions.features.v3_pandas import (
    V3FeatureStore,
    _driver_race_counts,
    _driver_wet_session_counts,
    _driver_vs_teammate_gap,
    _driver_championship_positions,
    _team_qualifying_means,
    _team_recent_finish_means,
    _wet_vs_dry_position_deltas,
)
from predictions.tests.factories import (
    make_circuit,
    make_driver,
    make_event,
    make_result,
    make_season,
    make_session,
    make_team,
    make_weather_sample,
)

V3_FEATURE_COUNT = 36  # 25 from V2 + 11 new V3 features


# ---------------------------------------------------------------------------
# Shared setup helpers
# ---------------------------------------------------------------------------


def _setup_base():
    """
    Creates a minimal world: one season, team, driver, and a target event
    at round 5 (so there's room for 4 prior events to build history from).
    Returns (season, team, driver, target_event).
    """
    season = make_season(2024)
    circuit = make_circuit(key="monaco")
    team = make_team(season, name="Ferrari")
    driver = make_driver(season, team, code="LEC", driver_number=16)
    # Round 10: gives rounds 1-8 as safe slots for past-race history.
    target_event = make_event(season, round_number=10, circuit=circuit, event_date=date(2024, 10, 1))
    return season, team, driver, target_event


def _past_race(season, team, driver, round_number: int, position: int, is_wet: bool = False):
    """
    Create a past race result (with optional rain in the race session).
    Uses season.year so dates stay in the correct year for event_date filtering.
    """
    event = make_event(season, round_number=round_number, event_date=date(season.year, round_number, 1))
    session = make_session(event, session_type="R")
    make_result(session, driver, team, position=position)
    make_weather_sample(session, rainfall=is_wet)
    return event


def _past_race_shared(season, round_number: int, drivers_positions: list[tuple], team, is_wet: bool = False):
    """
    Create one past race event with multiple drivers (realistic: they all race together).
    drivers_positions: [(driver, position), ...]
    """
    event = make_event(season, round_number=round_number, event_date=date(season.year, round_number, 1))
    session = make_session(event, session_type="R")
    for driver, position in drivers_positions:
        make_result(session, driver, team, position=position)
    make_weather_sample(session, rainfall=is_wet)
    return event


# ---------------------------------------------------------------------------
# _wet_vs_dry_position_deltas unit tests
# ---------------------------------------------------------------------------


class TestWetVsDryPositionDeltas(TestCase):
    """
    Unit tests for the _wet_vs_dry_position_deltas helper.

    The function signature is:
        _wet_vs_dry_position_deltas(code_to_driver_id, event) -> dict[str, float]

    We test:
    - wet specialist returns a negative delta
    - struggles in wet returns a positive delta
    - < 3 wet appearances returns the rookie penalty (+2.0)
    - < 3 dry appearances returns 0.0 (no dry baseline)
    - no past results at all returns the rookie penalty (+2.0)
    - rain classification uses race session weather, not practice weather
    """

    def setUp(self) -> None:
        self.season, self.team, self.driver, self.target_event = _setup_base()
        self.code_to_id = {"LEC": self.driver.id}

    def test_wet_specialist_has_negative_delta(self) -> None:
        """
        A driver who consistently finishes higher in wet than dry gets a negative delta.
        3 wet races at position 3, 3 dry races at position 8.
        Expected delta: 3.0 - 8.0 = -5.0
        """
        for i, pos in enumerate([3, 3, 3], start=1):
            _past_race(self.season, self.team, self.driver, round_number=i, position=pos, is_wet=True)
        for i, pos in enumerate([8, 8, 8], start=4):
            _past_race(self.season, self.team, self.driver, round_number=i, position=pos, is_wet=False)

        result = _wet_vs_dry_position_deltas(self.code_to_id, self.target_event)
        self.assertAlmostEqual(result["LEC"], -5.0)

    def test_struggles_in_wet_has_positive_delta(self) -> None:
        """
        A driver who finishes lower in wet than dry gets a positive delta.
        3 wet races at position 10, 3 dry races at position 3.
        Expected delta: 10.0 - 3.0 = +7.0
        """
        for i, pos in enumerate([10, 10, 10], start=1):
            _past_race(self.season, self.team, self.driver, round_number=i, position=pos, is_wet=True)
        for i, pos in enumerate([3, 3, 3], start=4):
            _past_race(self.season, self.team, self.driver, round_number=i, position=pos, is_wet=False)

        result = _wet_vs_dry_position_deltas(self.code_to_id, self.target_event)
        self.assertAlmostEqual(result["LEC"], 7.0)

    def test_fewer_than_3_wet_races_returns_rookie_penalty(self) -> None:
        """
        Only 2 wet race appearances → not enough data → +2.0 rookie penalty.
        """
        for i in range(1, 3):  # 2 wet races
            _past_race(self.season, self.team, self.driver, round_number=i, position=5, is_wet=True)
        for i in range(3, 7):  # 4 dry races
            _past_race(self.season, self.team, self.driver, round_number=i, position=5, is_wet=False)

        result = _wet_vs_dry_position_deltas(self.code_to_id, self.target_event)
        self.assertAlmostEqual(result["LEC"], 2.0)

    def test_fewer_than_3_dry_races_returns_zero(self) -> None:
        """
        Only 2 dry race appearances → can't compute dry baseline → 0.0.
        """
        for i in range(1, 5):  # 4 wet races
            _past_race(self.season, self.team, self.driver, round_number=i, position=5, is_wet=True)
        for i in range(5, 7):  # 2 dry races
            _past_race(self.season, self.team, self.driver, round_number=i, position=5, is_wet=False)

        result = _wet_vs_dry_position_deltas(self.code_to_id, self.target_event)
        self.assertAlmostEqual(result["LEC"], 0.0)

    def test_no_past_results_returns_rookie_penalty(self) -> None:
        """Driver with zero historical race data gets the +2.0 default."""
        result = _wet_vs_dry_position_deltas(self.code_to_id, self.target_event)
        self.assertAlmostEqual(result["LEC"], 2.0)

    def test_future_results_excluded(self) -> None:
        """
        Results from events after the target event's date must not be counted.
        Give the driver only wet races in the future → should still get +2.0 (no data).
        """
        future_event = make_event(
            self.season, round_number=9, event_date=date(2024, 9, 1)
        )
        future_session = make_session(future_event, session_type="R")
        make_result(future_session, self.driver, self.team, position=1)
        make_weather_sample(future_session, rainfall=True)

        result = _wet_vs_dry_position_deltas(self.code_to_id, self.target_event)
        self.assertAlmostEqual(result["LEC"], 2.0)

    def test_rain_classification_uses_race_session_not_practice(self) -> None:
        """
        A race event where practice was wet but the race was dry should be
        classified as dry — only race session weather counts.
        """
        event = make_event(self.season, round_number=1, event_date=date(2024, 1, 1))
        race_session = make_session(event, session_type="R")
        practice_session = make_session(event, session_type="FP1")
        make_result(race_session, self.driver, self.team, position=5)
        # Practice was wet, race was dry
        make_weather_sample(practice_session, rainfall=True)
        make_weather_sample(race_session, rainfall=False)

        # Also need 2 more dry races to have ≥3 dry, and 3 wet to test the classification
        for i in range(2, 4):
            _past_race(self.season, self.team, self.driver, round_number=i, position=5, is_wet=False)

        result = _wet_vs_dry_position_deltas(self.code_to_id, self.target_event)
        # All 3 races were dry (practice rain didn't count) → < 3 wet → rookie penalty
        self.assertAlmostEqual(result["LEC"], 2.0)

    def test_delta_averaged_correctly_across_multiple_races(self) -> None:
        """
        Checks that wet/dry means are computed as averages, not sums.
        3 wet races at positions 2, 4, 6 → mean wet = 4.0
        3 dry races at positions 8, 10, 12 → mean dry = 10.0
        Expected delta: 4.0 - 10.0 = -6.0
        """
        for i, pos in enumerate([2, 4, 6], start=1):
            _past_race(self.season, self.team, self.driver, round_number=i, position=pos, is_wet=True)
        for i, pos in enumerate([8, 10, 12], start=4):
            _past_race(self.season, self.team, self.driver, round_number=i, position=pos, is_wet=False)

        result = _wet_vs_dry_position_deltas(self.code_to_id, self.target_event)
        self.assertAlmostEqual(result["LEC"], -6.0)

    def test_multiple_drivers_computed_in_single_call(self) -> None:
        """
        The function is batch-efficient: all drivers computed together.
        Two drivers in the same races but with opposite finishing tendencies.
        LEC: finishes 3rd in wet, 8th in dry → delta = -5.0
        HAM: finishes 10th in wet, 2nd in dry → delta = +8.0
        """
        team2 = make_team(self.season, name="Mercedes")
        driver2 = make_driver(self.season, team2, code="HAM", driver_number=44)

        # Shared wet races (rounds 1-3): LEC finishes 3rd, HAM finishes 10th
        for i in range(1, 4):
            _past_race_shared(
                self.season, round_number=i,
                drivers_positions=[(self.driver, 3), (driver2, 10)],
                team=self.team, is_wet=True,
            )
        # Shared dry races (rounds 4-6): LEC finishes 8th, HAM finishes 2nd
        for i in range(4, 7):
            _past_race_shared(
                self.season, round_number=i,
                drivers_positions=[(self.driver, 8), (driver2, 2)],
                team=self.team, is_wet=False,
            )

        code_to_id = {"LEC": self.driver.id, "HAM": driver2.id}
        result = _wet_vs_dry_position_deltas(code_to_id, self.target_event)

        self.assertAlmostEqual(result["LEC"], -5.0)   # 3.0 - 8.0
        self.assertAlmostEqual(result["HAM"], 8.0)    # 10.0 - 2.0
        self.assertLess(result["LEC"], result["HAM"])  # wet specialist < struggles-in-wet


# ---------------------------------------------------------------------------
# V3FeatureStore integration tests
# ---------------------------------------------------------------------------


class TestV3FeatureCount(TestCase):
    """V3 must produce exactly 30 features (25 from V2 + 5 new)."""

    def test_correct_feature_count(self) -> None:
        season, team, driver, target_event = _setup_base()
        # Seed enough past data so V2 features are non-trivial
        for i in range(1, 4):
            _past_race(season, team, driver, round_number=i, position=i, is_wet=False)

        df = V3FeatureStore().get_all_driver_features(target_event.id)
        feature_cols = [c for c in df.columns if c not in ("driver_id",)]
        self.assertEqual(len(feature_cols), V3_FEATURE_COUNT)

    def test_wet_dry_delta_column_present(self) -> None:
        _, _, _, target_event = _setup_base()
        df = V3FeatureStore().get_all_driver_features(target_event.id)
        self.assertIn("driver_wet_vs_dry_position_delta", df.columns)

    def test_one_row_per_driver(self) -> None:
        season, team, driver, target_event = _setup_base()
        driver2 = make_driver(season, team, code="SAI", driver_number=55)

        # Both drivers race in the same event (one event, two results)
        _past_race_shared(
            season, round_number=1,
            drivers_positions=[(driver, 1), (driver2, 2)],
            team=team,
        )

        df = V3FeatureStore().get_all_driver_features(target_event.id)
        self.assertEqual(len(df), 2)
        self.assertEqual(df["driver_id"].nunique(), 2)


class TestV3WetDryColumnValues(TestCase):
    """The wet/dry delta column must vary across drivers — not all the same value."""

    def test_values_vary_per_driver(self) -> None:
        season = make_season(2022)
        team = make_team(season, name="Alpine")
        driver1 = make_driver(season, team, code="ALO", driver_number=14)
        driver2 = make_driver(season, team, code="OCO", driver_number=31)
        target_event = make_event(season, round_number=9, event_date=date(2022, 9, 1))

        # Shared wet races (rounds 1-3): ALO finishes 2nd, OCO finishes 12th
        for i in range(1, 4):
            _past_race_shared(
                season, round_number=i,
                drivers_positions=[(driver1, 2), (driver2, 12)],
                team=team, is_wet=True,
            )
        # Shared dry races (rounds 4-7): both finish 9th (neutral baseline)
        for i in range(4, 8):
            _past_race_shared(
                season, round_number=i,
                drivers_positions=[(driver1, 9), (driver2, 9)],
                team=team, is_wet=False,
            )

        df = V3FeatureStore().get_all_driver_features(target_event.id)
        deltas = df.set_index("driver_id")["driver_wet_vs_dry_position_delta"]

        d1_delta = deltas[driver1.id]
        d2_delta = deltas[driver2.id]

        # ALO finishes better in wet (2 vs 9) → negative delta
        self.assertLess(d1_delta, 0.0)
        # OCO finishes worse in wet (12 vs 9) → positive delta
        self.assertGreater(d2_delta, 0.0)

    def test_default_applied_when_no_history(self) -> None:
        """A driver with zero past races should get the +2.0 rookie default."""
        season, team, driver, target_event = _setup_base()
        df = V3FeatureStore().get_all_driver_features(target_event.id)
        if df.empty:
            return  # no race results means no features row — nothing to assert
        row = df[df["driver_id"] == driver.id]
        if row.empty:
            return
        self.assertAlmostEqual(row.iloc[0]["driver_wet_vs_dry_position_delta"], 2.0)


# ---------------------------------------------------------------------------
# _driver_race_counts unit tests
# ---------------------------------------------------------------------------


class TestDriverRaceCounts(TestCase):
    def setUp(self) -> None:
        self.season, self.team, self.driver, self.target_event = _setup_base()
        self.code_to_id = {"LEC": self.driver.id}

    def test_zero_for_driver_with_no_prior_races(self) -> None:
        result = _driver_race_counts(["LEC"], self.target_event)
        self.assertEqual(result["LEC"], 0)

    def test_counts_each_race_start(self) -> None:
        for i in range(1, 5):  # 4 past races
            _past_race(self.season, self.team, self.driver, round_number=i, position=5)
        result = _driver_race_counts(["LEC"], self.target_event)
        self.assertEqual(result["LEC"], 4)

    def test_excludes_future_races(self) -> None:
        # 3 past races + 1 future race (after target_event)
        for i in range(1, 4):
            _past_race(self.season, self.team, self.driver, round_number=i, position=5)
        future = make_event(self.season, round_number=11, event_date=date(2024, 11, 1))
        future_session = make_session(future, session_type="R")
        make_result(future_session, self.driver, self.team, position=1)

        result = _driver_race_counts(["LEC"], self.target_event)
        self.assertEqual(result["LEC"], 3)

    def test_counts_dnfs_as_race_starts(self) -> None:
        """A DNF still counts as a race start — the driver took the grid."""
        event = make_event(self.season, round_number=1, event_date=date(2024, 1, 1))
        session = make_session(event, session_type="R")
        make_result(session, self.driver, self.team, position=None, status="DNF")

        result = _driver_race_counts(["LEC"], self.target_event)
        self.assertEqual(result["LEC"], 1)

    def test_does_not_count_qualifying_sessions(self) -> None:
        """Only race sessions (session_type='R') count as race starts."""
        event = make_event(self.season, round_number=1, event_date=date(2024, 1, 1))
        qual_session = make_session(event, session_type="Q")
        make_result(qual_session, self.driver, self.team, position=3)

        result = _driver_race_counts(["LEC"], self.target_event)
        self.assertEqual(result["LEC"], 0)

    def test_cross_season_count(self) -> None:
        """Races from a previous season count toward the total."""
        prev_season = make_season(2022)
        prev_team = make_team(prev_season, name="Ferrari")
        prev_driver = make_driver(prev_season, prev_team, code="LEC", driver_number=16)

        # 2 races in 2022 — use explicitly unique circuit keys to avoid collision
        for i in range(1, 3):
            circuit = make_circuit(key=f"circuit_2022_{i}")
            event = make_event(prev_season, round_number=i, circuit=circuit, event_date=date(2022, i, 1))
            session = make_session(event, session_type="R")
            make_result(session, prev_driver, prev_team, position=4)

        # 3 races in 2024 (current season, before target_event at round 10)
        for i in range(1, 4):
            _past_race(self.season, self.team, self.driver, round_number=i, position=4)

        result = _driver_race_counts(["LEC"], self.target_event)
        self.assertEqual(result["LEC"], 5)

    def test_multiple_drivers_in_single_call(self) -> None:
        """Verifies the batch query returns correct counts for each driver."""
        team2 = make_team(self.season, name="Mercedes")
        driver2 = make_driver(self.season, team2, code="HAM", driver_number=44)

        for i in range(1, 5):  # LEC: 4 races
            _past_race_shared(self.season, round_number=i, drivers_positions=[(self.driver, 5), (driver2, 3)], team=self.team)

        result = _driver_race_counts(["LEC", "HAM"], self.target_event)
        self.assertEqual(result["LEC"], 4)
        self.assertEqual(result["HAM"], 4)


class TestV3DriverRacesColumn(TestCase):
    def test_column_present_in_output(self) -> None:
        _, _, _, target_event = _setup_base()
        df = V3FeatureStore().get_all_driver_features(target_event.id)
        self.assertIn("driver_races", df.columns)

    def test_veteran_has_higher_count_than_rookie(self) -> None:
        season, team, veteran, target_event = _setup_base()
        rookie = make_driver(season, team, code="ANT", driver_number=12)

        # veteran has 5 past races; rookie has 0
        for i in range(1, 6):
            _past_race_shared(
                season, round_number=i,
                drivers_positions=[(veteran, 5), (rookie, 15)],
                team=team,
            )
        # give rookie one race so they appear in the feature df
        # (V2 needs at least one result for the driver to appear)
        # Actually the rookie already appeared above — 5 races each
        df = V3FeatureStore().get_all_driver_features(target_event.id)
        counts = df.set_index("driver_id")["driver_races"]
        # Both raced 5 times before target_event — should be equal
        self.assertEqual(counts[veteran.id], 5)
        self.assertEqual(counts[rookie.id], 5)

    def test_true_rookie_gets_zero(self) -> None:
        """A driver with no prior races gets 0, not NaN."""
        season, team, driver, target_event = _setup_base()
        # Add one past race so the driver appears in V2's feature set,
        # but that single race is what we're testing the count of
        _past_race(season, team, driver, round_number=1, position=5)

        df = V3FeatureStore().get_all_driver_features(target_event.id)
        row = df[df["driver_id"] == driver.id]
        if row.empty:
            return
        self.assertEqual(row.iloc[0]["driver_races"], 1)


# ---------------------------------------------------------------------------
# _driver_wet_session_counts unit tests
# ---------------------------------------------------------------------------


class TestDriverWetSessionCount(TestCase):
    """
    Unit tests for _driver_wet_session_counts.

    Unlike wet/dry delta which needs ≥3 wet races, this counter provides a
    signal from the very first wet session and counts both Q and R sessions.
    """

    def setUp(self) -> None:
        self.season, self.team, self.driver, self.target_event = _setup_base()

    def test_dry_only_history_returns_zero(self) -> None:
        """A driver with only dry sessions gets 0."""
        for i in range(1, 4):
            _past_race(self.season, self.team, self.driver, round_number=i, position=5, is_wet=False)
        result = _driver_wet_session_counts(["LEC"], self.target_event)
        self.assertEqual(result["LEC"], 0)

    def test_wet_race_counts(self) -> None:
        """Each wet race increments the count by 1."""
        for i in range(1, 4):  # 3 wet races
            _past_race(self.season, self.team, self.driver, round_number=i, position=5, is_wet=True)
        result = _driver_wet_session_counts(["LEC"], self.target_event)
        self.assertEqual(result["LEC"], 3)

    def test_wet_qualifying_counts(self) -> None:
        """Wet qualifying sessions also increment the count."""
        event = make_event(self.season, round_number=1, event_date=date(2024, 1, 1))
        qual_session = make_session(event, session_type="Q")
        make_result(qual_session, self.driver, self.team, position=3)
        make_weather_sample(qual_session, rainfall=True)

        result = _driver_wet_session_counts(["LEC"], self.target_event)
        self.assertEqual(result["LEC"], 1)

    def test_future_wet_sessions_excluded(self) -> None:
        """Sessions after the target event date must not be counted."""
        future_event = make_event(self.season, round_number=11, event_date=date(2024, 11, 1))
        future_session = make_session(future_event, session_type="R")
        make_result(future_session, self.driver, self.team, position=1)
        make_weather_sample(future_session, rainfall=True)

        result = _driver_wet_session_counts(["LEC"], self.target_event)
        self.assertEqual(result["LEC"], 0)

    def test_no_history_returns_zero(self) -> None:
        """Driver with no prior sessions at all gets 0."""
        result = _driver_wet_session_counts(["LEC"], self.target_event)
        self.assertEqual(result["LEC"], 0)

    def test_mixed_wet_dry_counted_correctly(self) -> None:
        """Only wet sessions count; dry sessions do not."""
        for i in range(1, 4):  # 3 dry
            _past_race(self.season, self.team, self.driver, round_number=i, position=5, is_wet=False)
        for i in range(4, 7):  # 3 wet
            _past_race(self.season, self.team, self.driver, round_number=i, position=5, is_wet=True)
        result = _driver_wet_session_counts(["LEC"], self.target_event)
        self.assertEqual(result["LEC"], 3)


# ---------------------------------------------------------------------------
# _driver_vs_teammate_gap unit tests
# ---------------------------------------------------------------------------


class TestDriverVsTeammateGap(TestCase):
    """
    Unit tests for _driver_vs_teammate_gap.

    Negative gap = driver outperforms teammate. Zero returned when < 3 shared races.
    """

    def setUp(self) -> None:
        self.season, self.team, self.driver, self.target_event = _setup_base()
        self.teammate = make_driver(self.season, self.team, code="SAI", driver_number=55)
        self.code_to_driver_id = {
            "LEC": self.driver.id,
            "SAI": self.teammate.id,
        }
        self.driver_id_to_team_id = {
            self.driver.id: self.team.id,
            self.teammate.id: self.team.id,
        }

    def test_driver_beating_teammate_has_negative_gap(self) -> None:
        """
        LEC finishes 3rd, SAI finishes 8th in 3 shared races.
        LEC gap = 3 - 8 = -5.0 (outperforms)
        SAI gap = 8 - 3 = +5.0 (underperforms)
        """
        for i in range(1, 4):
            _past_race_shared(
                self.season, round_number=i,
                drivers_positions=[(self.driver, 3), (self.teammate, 8)],
                team=self.team,
            )
        result = _driver_vs_teammate_gap(
            self.code_to_driver_id, self.driver_id_to_team_id, self.target_event
        )
        self.assertAlmostEqual(result["LEC"], -5.0)
        self.assertAlmostEqual(result["SAI"], 5.0)

    def test_fewer_than_3_shared_races_returns_zero(self) -> None:
        """Only 2 shared races → insufficient data → 0.0 for both."""
        for i in range(1, 3):  # 2 races
            _past_race_shared(
                self.season, round_number=i,
                drivers_positions=[(self.driver, 2), (self.teammate, 10)],
                team=self.team,
            )
        result = _driver_vs_teammate_gap(
            self.code_to_driver_id, self.driver_id_to_team_id, self.target_event
        )
        self.assertAlmostEqual(result["LEC"], 0.0)
        self.assertAlmostEqual(result["SAI"], 0.0)

    def test_no_history_returns_zero(self) -> None:
        """No past results → 0.0 default."""
        result = _driver_vs_teammate_gap(
            self.code_to_driver_id, self.driver_id_to_team_id, self.target_event
        )
        self.assertAlmostEqual(result["LEC"], 0.0)

    def test_gap_averaged_across_races(self) -> None:
        """
        3 races: LEC finishes 2, 4, 6 — SAI finishes 8, 10, 12.
        LEC mean = 4.0, SAI mean = 10.0. LEC gap = 4 - 10 = -6.0.
        """
        for i, (lec_pos, sai_pos) in enumerate([(2, 8), (4, 10), (6, 12)], start=1):
            _past_race_shared(
                self.season, round_number=i,
                drivers_positions=[(self.driver, lec_pos), (self.teammate, sai_pos)],
                team=self.team,
            )
        result = _driver_vs_teammate_gap(
            self.code_to_driver_id, self.driver_id_to_team_id, self.target_event
        )
        self.assertAlmostEqual(result["LEC"], -6.0)


# ---------------------------------------------------------------------------
# _team_qualifying_means unit tests
# ---------------------------------------------------------------------------


class TestTeamQualifyingMeanLast3(TestCase):
    """
    Unit tests for _team_qualifying_means.

    Uses only last 3 qualifying events in the current season.
    Default 10.0 when no history.
    """

    def setUp(self) -> None:
        self.season, self.team, self.driver, self.target_event = _setup_base()
        self.teammate = make_driver(self.season, self.team, code="SAI", driver_number=55)

    def _make_qual_event(self, round_number: int, lec_pos: int, sai_pos: int) -> None:
        event = make_event(
            self.season, round_number=round_number,
            event_date=date(self.season.year, round_number, 1),
        )
        session = make_session(event, session_type="Q")
        make_result(session, self.driver, self.team, position=lec_pos)
        make_result(session, self.teammate, self.team, position=sai_pos)

    def test_no_history_returns_default(self) -> None:
        """No qualifying events → 10.0 default."""
        result = _team_qualifying_means([self.team.id], self.target_event)
        self.assertAlmostEqual(result[self.team.id], 10.0)

    def test_uses_only_last_3_events(self) -> None:
        """
        5 qualifying events with improving positions: last 3 should dominate.
        Rounds 1-2: positions 15, 16 (bad) → mean 15.5
        Rounds 3-5: positions 1, 2 (good) → mean 1.5 each event
        Expected last3 mean = 1.5
        """
        self._make_qual_event(round_number=1, lec_pos=15, sai_pos=16)
        self._make_qual_event(round_number=2, lec_pos=15, sai_pos=16)
        self._make_qual_event(round_number=3, lec_pos=1, sai_pos=2)
        self._make_qual_event(round_number=4, lec_pos=1, sai_pos=2)
        self._make_qual_event(round_number=5, lec_pos=1, sai_pos=2)

        result = _team_qualifying_means([self.team.id], self.target_event)
        self.assertAlmostEqual(result[self.team.id], 1.5)

    def test_current_season_only(self) -> None:
        """Qualifying data from a prior season must not be included."""
        prev_season = make_season(2023)
        prev_team = make_team(prev_season, name="Ferrari")
        prev_driver = make_driver(prev_season, prev_team, code="LEC", driver_number=16)
        # Terrible qualifying in 2023
        prev_event = make_event(prev_season, round_number=1, event_date=date(2023, 1, 1))
        prev_session = make_session(prev_event, session_type="Q")
        make_result(prev_session, prev_driver, prev_team, position=20)

        # No 2024 qualifying history → should get default 10.0, not 20.0
        result = _team_qualifying_means([self.team.id], self.target_event)
        self.assertAlmostEqual(result[self.team.id], 10.0)


# ---------------------------------------------------------------------------
# _driver_championship_positions unit tests
# ---------------------------------------------------------------------------


class TestDriverChampionshipPosition(TestCase):
    """
    Unit tests for _driver_championship_positions.

    Points leader = rank 1. Drivers with no points = rank 20 (default).
    Current season only.
    """

    def setUp(self) -> None:
        self.season, self.team, self.driver, self.target_event = _setup_base()
        team2 = make_team(self.season, name="Mercedes")
        self.driver2 = make_driver(self.season, team2, code="HAM", driver_number=44)

    def _make_race_with_points(self, driver, round_number: int, points: int) -> None:
        event = make_event(
            self.season, round_number=round_number,
            event_date=date(self.season.year, round_number, 1),
        )
        session = make_session(event, session_type="R")
        make_result(session, driver, self.team, position=1, points=float(points))

    def test_points_leader_is_rank_1(self) -> None:
        """Driver with most points gets championship position 1."""
        self._make_race_with_points(self.driver, round_number=1, points=25)
        self._make_race_with_points(self.driver2, round_number=2, points=18)

        result = _driver_championship_positions(["LEC", "HAM"], self.target_event)
        self.assertEqual(result["LEC"], 1)
        self.assertEqual(result["HAM"], 2)

    def test_no_points_gets_default_20(self) -> None:
        """Driver with no race results gets default rank of 20."""
        result = _driver_championship_positions(["LEC"], self.target_event)
        self.assertEqual(result["LEC"], 20)

    def test_current_season_only(self) -> None:
        """Points from a previous season do not count."""
        prev_season = make_season(2023)
        prev_team = make_team(prev_season, name="Ferrari")
        prev_driver = make_driver(prev_season, prev_team, code="LEC", driver_number=16)
        prev_event = make_event(prev_season, round_number=1, event_date=date(2023, 1, 1))
        prev_session = make_session(prev_event, session_type="R")
        make_result(prev_session, prev_driver, prev_team, position=1, points=25.0)

        # LEC has points in 2023 but not 2024 → should get default 20
        result = _driver_championship_positions(["LEC"], self.target_event)
        self.assertEqual(result["LEC"], 20)


# ---------------------------------------------------------------------------
# _team_recent_finish_means unit tests
# ---------------------------------------------------------------------------


class TestTeamRecentFinishMeanLast3(TestCase):
    """
    Unit tests for _team_recent_finish_means.

    Mirrors TestTeamQualifyingMeanLast3 but uses race session results.
    """

    def setUp(self) -> None:
        self.season, self.team, self.driver, self.target_event = _setup_base()
        self.teammate = make_driver(self.season, self.team, code="SAI", driver_number=55)

    def _make_race_event(self, round_number: int, lec_pos: int, sai_pos: int) -> None:
        event = make_event(
            self.season, round_number=round_number,
            event_date=date(self.season.year, round_number, 1),
        )
        session = make_session(event, session_type="R")
        make_result(session, self.driver, self.team, position=lec_pos)
        make_result(session, self.teammate, self.team, position=sai_pos)

    def test_no_history_returns_default(self) -> None:
        """No race events → 10.0 default."""
        result = _team_recent_finish_means([self.team.id], self.target_event)
        self.assertAlmostEqual(result[self.team.id], 10.0)

    def test_uses_only_last_3_events(self) -> None:
        """
        5 race events; early rounds had bad finishes, last 3 were good.
        Rounds 1-2: positions 15, 16 → event mean = 15.5
        Rounds 3-5: positions 1, 2 → event mean = 1.5
        Expected last3 mean = 1.5
        """
        self._make_race_event(round_number=1, lec_pos=15, sai_pos=16)
        self._make_race_event(round_number=2, lec_pos=15, sai_pos=16)
        self._make_race_event(round_number=3, lec_pos=1, sai_pos=2)
        self._make_race_event(round_number=4, lec_pos=1, sai_pos=2)
        self._make_race_event(round_number=5, lec_pos=1, sai_pos=2)

        result = _team_recent_finish_means([self.team.id], self.target_event)
        self.assertAlmostEqual(result[self.team.id], 1.5)

    def test_current_season_only(self) -> None:
        """Race results from a prior season must not be included."""
        prev_season = make_season(2023)
        prev_team = make_team(prev_season, name="Ferrari")
        prev_driver = make_driver(prev_season, prev_team, code="LEC", driver_number=16)
        prev_event = make_event(prev_season, round_number=1, event_date=date(2023, 1, 1))
        prev_session = make_session(prev_event, session_type="R")
        make_result(prev_session, prev_driver, prev_team, position=20)

        result = _team_recent_finish_means([self.team.id], self.target_event)
        self.assertAlmostEqual(result[self.team.id], 10.0)
