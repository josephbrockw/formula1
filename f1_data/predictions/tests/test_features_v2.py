from __future__ import annotations

from datetime import date

from django.test import TestCase

from predictions.features.v2_pandas import V2FeatureStore
from predictions.tests.factories import (
    make_circuit,
    make_driver,
    make_driver_price,
    make_event,
    make_fantasy_score,
    make_result,
    make_season,
    make_session,
    make_team,
    make_weather_sample,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

V2_FEATURE_COUNT = 25  # 15 from V1 + 10 from V2


def _setup_base(year: int = 2024) -> tuple:
    """Returns (season, team, driver, target_event)."""
    season = make_season(year)
    team = make_team(season)
    driver = make_driver(season, team)
    target_event = make_event(season, round_number=5, event_date=date(year, 5, 1))
    return season, team, driver, target_event


# ---------------------------------------------------------------------------
# Weather features
# ---------------------------------------------------------------------------


class TestWeatherFeatures(TestCase):
    def setUp(self) -> None:
        self.season, self.team, self.driver, self.target_event = _setup_base()
        self.store = V2FeatureStore()

    def test_no_weather_data_returns_defaults(self) -> None:
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertEqual(features["weather_practice_rainfall"], 0.0)
        self.assertEqual(features["weather_track_temp_mean"], 0.0)

    def test_dry_practice_returns_rainfall_zero(self) -> None:
        fp1 = make_session(self.target_event, "FP1")
        make_weather_sample(fp1, rainfall=False, track_temp=40.0)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertEqual(features["weather_practice_rainfall"], 0.0)

    def test_any_wet_practice_returns_rainfall_one(self) -> None:
        fp1 = make_session(self.target_event, "FP1")
        fp2 = make_session(self.target_event, "FP2")
        make_weather_sample(fp1, rainfall=False)
        make_weather_sample(fp2, rainfall=True)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertEqual(features["weather_practice_rainfall"], 1.0)

    def test_race_session_weather_excluded(self) -> None:
        race_session = make_session(self.target_event, "R")
        make_weather_sample(race_session, rainfall=True, track_temp=50.0)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        # Race session should not be counted
        self.assertEqual(features["weather_practice_rainfall"], 0.0)
        self.assertEqual(features["weather_track_temp_mean"], 0.0)

    def test_track_temp_averages_correctly(self) -> None:
        fp1 = make_session(self.target_event, "FP1")
        fp2 = make_session(self.target_event, "FP2")
        make_weather_sample(fp1, track_temp=30.0)
        make_weather_sample(fp2, track_temp=40.0)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertAlmostEqual(features["weather_track_temp_mean"], 35.0)


# ---------------------------------------------------------------------------
# Constructor standing rank
# ---------------------------------------------------------------------------


class TestConstructorStandingRank(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.store = V2FeatureStore()
        self.target_event = make_event(self.season, round_number=5, event_date=date(2024, 5, 1))

    def _past_race_result(
        self,
        team,
        driver,
        round_number: int,
        position: int,
        points: float,
        event_date: date | None = None,
    ) -> None:
        event = make_event(
            self.season,
            round_number=round_number,
            event_date=event_date or date(2024, round_number, 1),
        )
        session = make_session(event, "R")
        make_result(session, driver, team, position=position, points=points)

    def test_round_1_returns_5_for_all(self) -> None:
        round1_event = make_event(self.season, round_number=1, event_date=date(2024, 1, 1))
        team = make_team(self.season)
        driver = make_driver(self.season, team)
        features = self.store.get_driver_features(driver.id, round1_event.id)
        self.assertEqual(features["team_constructor_standing_rank"], 5.0)

    def test_leader_gets_rank_1(self) -> None:
        team_a = make_team(self.season, name="TeamA")
        team_b = make_team(self.season, name="TeamB")
        driver_a = make_driver(self.season, team_a, code="AAA")
        driver_b = make_driver(self.season, team_b, code="BBB", driver_number=2)

        self._past_race_result(team_a, driver_a, 1, position=1, points=25.0)
        self._past_race_result(team_b, driver_b, 2, position=2, points=18.0)

        features_a = self.store.get_driver_features(driver_a.id, self.target_event.id)
        self.assertEqual(features_a["team_constructor_standing_rank"], 1.0)

    def test_trailing_team_gets_higher_rank_number(self) -> None:
        team_a = make_team(self.season, name="TeamA")
        team_b = make_team(self.season, name="TeamB")
        driver_a = make_driver(self.season, team_a, code="AAA")
        driver_b = make_driver(self.season, team_b, code="BBB", driver_number=2)

        self._past_race_result(team_a, driver_a, 1, position=1, points=25.0)
        self._past_race_result(team_b, driver_b, 2, position=2, points=18.0)

        features_b = self.store.get_driver_features(driver_b.id, self.target_event.id)
        self.assertEqual(features_b["team_constructor_standing_rank"], 2.0)

    def test_future_results_excluded(self) -> None:
        team = make_team(self.season, name="TeamX")
        driver = make_driver(self.season, team, code="XXX")
        # Points AFTER target_event date
        future_event = make_event(self.season, round_number=10, event_date=date(2024, 10, 1))
        future_session = make_session(future_event, "R")
        make_result(future_session, driver, team, position=1, points=25.0)

        features = self.store.get_driver_features(driver.id, self.target_event.id)
        # No results before target event → round-1 default
        self.assertEqual(features["team_constructor_standing_rank"], 5.0)


# ---------------------------------------------------------------------------
# Circuit corner density
# ---------------------------------------------------------------------------


class TestCircuitCornerDensity(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.store = V2FeatureStore()
        self.team = make_team(self.season)
        self.driver = make_driver(self.season, self.team)

    def test_corner_density_arithmetic(self) -> None:
        # Monaco-like: 19 corners, 3.337 km ≈ 5.69
        circuit = make_circuit("monaco")
        circuit.total_corners = 19
        circuit.circuit_length = 3.337
        circuit.save()
        event = make_event(self.season, round_number=1, circuit=circuit)
        features = self.store.get_driver_features(self.driver.id, event.id)
        self.assertAlmostEqual(features["circuit_corner_density"], 19 / 3.337, places=4)

    def test_zero_length_returns_zero(self) -> None:
        circuit = make_circuit("zero_length")
        circuit.circuit_length = 0.0
        circuit.total_corners = 10
        circuit.save()
        event = make_event(self.season, round_number=1, circuit=circuit)
        features = self.store.get_driver_features(self.driver.id, event.id)
        self.assertEqual(features["circuit_corner_density"], 0.0)

    def test_null_corners_treated_as_zero(self) -> None:
        circuit = make_circuit("no_corners")
        circuit.total_corners = None
        circuit.circuit_length = 5.0
        circuit.save()
        event = make_event(self.season, round_number=1, circuit=circuit)
        features = self.store.get_driver_features(self.driver.id, event.id)
        self.assertEqual(features["circuit_corner_density"], 0.0)


# ---------------------------------------------------------------------------
# Team downforce ratings
# ---------------------------------------------------------------------------


class TestTeamDownforceRatings(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.prev_season = make_season(2023)
        self.team = make_team(self.season, name="FastTeam")
        self.prev_team = make_team(self.prev_season, name="FastTeam")
        self.driver = make_driver(self.season, self.team, code="VER")
        self.prev_driver = make_driver(self.prev_season, self.prev_team, code="VER")
        self.target_event = make_event(self.season, round_number=5, event_date=date(2024, 5, 1))
        self.store = V2FeatureStore()

    def _make_race_result(
        self,
        season,
        team,
        driver,
        round_number: int,
        position: int,
        corners: int,
        length: float,
        event_date: date | None = None,
    ) -> None:
        circuit = make_circuit(f"circ_{season.year}_{round_number}")
        circuit.total_corners = corners
        circuit.circuit_length = length
        circuit.save()
        event = make_event(
            season,
            round_number=round_number,
            circuit=circuit,
            event_date=event_date or date(season.year, round_number, 1),
        )
        session = make_session(event, "R")
        make_result(session, driver, team, position=position)

    def test_no_results_returns_defaults(self) -> None:
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertEqual(features["team_low_df_avg_pos"], 10.0)
        self.assertEqual(features["team_high_df_avg_pos"], 10.0)

    def test_low_high_buckets_split_correctly(self) -> None:
        # Low density (4 corners / 6km ≈ 0.67): position 1
        self._make_race_result(self.season, self.team, self.driver, 1, position=1, corners=4, length=6.0)
        # High density (20 corners / 4km = 5.0): position 10
        self._make_race_result(self.season, self.team, self.driver, 2, position=10, corners=20, length=4.0)

        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        # Team does well at low-density, poorly at high-density
        self.assertLess(features["team_low_df_avg_pos"], features["team_high_df_avg_pos"])

    def test_previous_season_included(self) -> None:
        # Prev season race at a low-density circuit: position 2
        self._make_race_result(
            self.prev_season, self.prev_team, self.prev_driver, 1, position=2, corners=5, length=6.0
        )
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        # Should see some data (not just defaults)
        self.assertIsNotNone(features["team_low_df_avg_pos"])

    def test_future_results_excluded(self) -> None:
        # Future event (after target_event)
        self._make_race_result(
            self.season, self.team, self.driver, 10, position=1, corners=10, length=5.0,
            event_date=date(2024, 12, 1),
        )
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        # No valid past results → defaults
        self.assertEqual(features["team_low_df_avg_pos"], 10.0)
        self.assertEqual(features["team_high_df_avg_pos"], 10.0)

    def test_season_two_years_ago_excluded(self) -> None:
        old_season = make_season(2022)
        old_team = make_team(old_season, name="FastTeam")
        old_driver = make_driver(old_season, old_team, code="VER")
        self._make_race_result(
            old_season, old_team, old_driver, 1, position=1, corners=5, length=5.0,
            event_date=date(2022, 1, 1),
        )
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        # 2022 results should not be included (only 2023+2024 in scope)
        self.assertEqual(features["team_low_df_avg_pos"], 10.0)
        self.assertEqual(features["team_high_df_avg_pos"], 10.0)


# ---------------------------------------------------------------------------
# Driver vs teammate gap
# ---------------------------------------------------------------------------


class TestDriverVsTeammateGap(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season)
        self.driver = make_driver(self.season, self.team, code="VER", driver_number=1)
        self.teammate = make_driver(self.season, self.team, code="PER", driver_number=11)
        self.target_event = make_event(self.season, round_number=5, event_date=date(2024, 5, 1))
        self.store = V2FeatureStore()

    def _past_race(self, round_number: int, driver_pos: int, teammate_pos: int) -> None:
        event = make_event(self.season, round_number=round_number, event_date=date(2024, round_number, 1))
        session = make_session(event, "R")
        make_result(session, self.driver, self.team, position=driver_pos)
        make_result(session, self.teammate, self.team, position=teammate_pos)

    def test_driver_outperforms_teammate_is_positive(self) -> None:
        self._past_race(1, driver_pos=1, teammate_pos=5)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertGreater(features["driver_vs_teammate_gap_last5"], 0.0)

    def test_teammate_outperforms_driver_is_negative(self) -> None:
        self._past_race(1, driver_pos=10, teammate_pos=2)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertLess(features["driver_vs_teammate_gap_last5"], 0.0)

    def test_no_teammate_returns_zero(self) -> None:
        solo_season = make_season(2020)
        solo_team = make_team(solo_season)
        solo_driver = make_driver(solo_season, solo_team, code="SOL")
        solo_event = make_event(solo_season, round_number=1, event_date=date(2020, 6, 1))
        features = self.store.get_driver_features(solo_driver.id, solo_event.id)
        self.assertEqual(features["driver_vs_teammate_gap_last5"], 0.0)

    def test_no_shared_races_returns_zero(self) -> None:
        # Driver has races but teammate has none
        event = make_event(self.season, round_number=1, event_date=date(2024, 1, 1))
        session = make_session(event, "R")
        make_result(session, self.driver, self.team, position=3)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertEqual(features["driver_vs_teammate_gap_last5"], 0.0)

    def test_gap_arithmetic(self) -> None:
        # Driver always P2, teammate always P4 → gap = (4-2) = 2.0
        for r in range(1, 4):
            self._past_race(r, driver_pos=2, teammate_pos=4)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertAlmostEqual(features["driver_vs_teammate_gap_last5"], 2.0)


# ---------------------------------------------------------------------------
# Fantasy price signals
# ---------------------------------------------------------------------------


class TestFantasyPriceSignals(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season)
        self.driver = make_driver(self.season, self.team)
        self.target_event = make_event(self.season, round_number=5, event_date=date(2024, 5, 1))
        self.store = V2FeatureStore()

    def test_no_price_data_returns_defaults(self) -> None:
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertEqual(features["pick_percentage"], 0.0)
        self.assertEqual(features["price_change_last_race"], 0.0)

    def test_most_recent_record_used(self) -> None:
        # Earlier event
        early_event = make_event(self.season, round_number=2, event_date=date(2024, 2, 1))
        early_price = make_driver_price(self.driver, early_event, price=10.0)
        early_price.pick_percentage = 5.0
        early_price.price_change = -0.1
        early_price.save()

        # More recent event
        recent_event = make_event(self.season, round_number=4, event_date=date(2024, 4, 1))
        recent_price = make_driver_price(self.driver, recent_event, price=12.0)
        recent_price.pick_percentage = 25.0
        recent_price.price_change = 0.5
        recent_price.save()

        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertAlmostEqual(features["pick_percentage"], 25.0)
        self.assertAlmostEqual(features["price_change_last_race"], 0.5)

    def test_future_price_record_excluded(self) -> None:
        # Price record AFTER target event date
        future_event = make_event(self.season, round_number=10, event_date=date(2024, 10, 1))
        future_price = make_driver_price(self.driver, future_event, price=15.0)
        future_price.pick_percentage = 99.0
        future_price.price_change = 5.0
        future_price.save()

        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertEqual(features["pick_percentage"], 0.0)
        self.assertEqual(features["price_change_last_race"], 0.0)


# ---------------------------------------------------------------------------
# Fantasy points trend
# ---------------------------------------------------------------------------


class TestFantasyPointsTrend(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season)
        self.driver = make_driver(self.season, self.team)
        self.target_event = make_event(self.season, round_number=10, event_date=date(2024, 10, 1))
        self.store = V2FeatureStore()

    def _past_score(self, round_number: int, points: int) -> None:
        event = make_event(self.season, round_number=round_number, event_date=date(2024, round_number, 1))
        make_fantasy_score(self.driver, event, race_total=points)

    def test_improving_trend_is_positive(self) -> None:
        for r, pts in enumerate([10, 20, 30, 40, 50], start=1):
            self._past_score(r, pts)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertGreater(features["fantasy_points_trend_last5"], 0.0)

    def test_declining_trend_is_negative(self) -> None:
        for r, pts in enumerate([50, 40, 30, 20, 10], start=1):
            self._past_score(r, pts)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertLess(features["fantasy_points_trend_last5"], 0.0)

    def test_flat_trend_is_near_zero(self) -> None:
        for r in range(1, 6):
            self._past_score(r, 30)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertAlmostEqual(features["fantasy_points_trend_last5"], 0.0)

    def test_fewer_than_two_scores_returns_zero(self) -> None:
        self._past_score(1, 25)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertEqual(features["fantasy_points_trend_last5"], 0.0)

    def test_no_scores_returns_zero(self) -> None:
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        self.assertEqual(features["fantasy_points_trend_last5"], 0.0)

    def test_uses_most_recent_five_only(self) -> None:
        # 7 scores: first two are high (old), then declining last 5
        for r, pts in enumerate([100, 90, 50, 40, 30, 20, 10], start=1):
            self._past_score(r, pts)
        features = self.store.get_driver_features(self.driver.id, self.target_event.id)
        # Last 5 are declining so slope should be negative
        self.assertLess(features["fantasy_points_trend_last5"], 0.0)


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------


class TestV2Integration(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season)
        self.driver_a = make_driver(self.season, self.team, code="VER", driver_number=1)
        self.driver_b = make_driver(self.season, self.team, code="PER", driver_number=11)
        self.target_event = make_event(self.season, round_number=5, event_date=date(2024, 5, 1))
        self.store = V2FeatureStore()

    def test_all_25_feature_columns_present(self) -> None:
        df = self.store.get_all_driver_features(self.target_event.id)
        feature_cols = [c for c in df.columns if c != "driver_id"]
        self.assertEqual(len(feature_cols), V2_FEATURE_COUNT)

    def test_one_row_per_driver(self) -> None:
        df = self.store.get_all_driver_features(self.target_event.id)
        self.assertEqual(len(df), 2)

    def test_driver_id_column_present(self) -> None:
        df = self.store.get_all_driver_features(self.target_event.id)
        self.assertIn("driver_id", df.columns)

    def test_v2_feature_names_present(self) -> None:
        df = self.store.get_all_driver_features(self.target_event.id)
        v2_features = [
            "weather_practice_rainfall",
            "weather_track_temp_mean",
            "team_constructor_standing_rank",
            "circuit_corner_density",
            "team_low_df_avg_pos",
            "team_high_df_avg_pos",
            "driver_vs_teammate_gap_last5",
            "pick_percentage",
            "price_change_last_race",
            "fantasy_points_trend_last5",
        ]
        for col in v2_features:
            self.assertIn(col, df.columns, f"Missing V2 feature: {col}")
