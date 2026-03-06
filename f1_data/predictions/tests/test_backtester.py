from __future__ import annotations

from datetime import date

import pandas as pd
from django.test import SimpleTestCase, TestCase

from predictions.evaluation.backtester import (
    BacktestResult,
    Backtester,
    RaceBacktestResult,
    _compute_mae,
    _score_lineup,
)
from predictions.optimizers.base import Lineup
from predictions.optimizers.greedy_v2 import GreedyOptimizerV2
from predictions.tests.factories import (
    make_constructor_price,
    make_constructor_score,
    make_driver,
    make_driver_price,
    make_event,
    make_fantasy_score,
    make_result,
    make_season,
    make_session,
    make_team,
)


# ---------------------------------------------------------------------------
# BacktestResult aggregation — no DB needed
# ---------------------------------------------------------------------------


class TestBacktestResultAggregation(SimpleTestCase):
    def _make_result(self, mae_pos: float, mae_pts: float, lineup_actual: float | None, optimal: float | None) -> RaceBacktestResult:
        return RaceBacktestResult(
            event_id=1,
            event_name="GP",
            n_train=5,
            mae_position=mae_pos,
            mae_fantasy_points=mae_pts,
            lineup_predicted_points=None,
            lineup_actual_points=lineup_actual,
            optimal_actual_points=optimal,
        )

    def test_mean_mae_position(self) -> None:
        result = BacktestResult(race_results=[
            self._make_result(2.0, 5.0, None, None),
            self._make_result(4.0, 9.0, None, None),
        ])
        self.assertAlmostEqual(result.mean_mae_position, 3.0)

    def test_mean_mae_fantasy_points(self) -> None:
        result = BacktestResult(race_results=[
            self._make_result(2.0, 5.0, None, None),
            self._make_result(4.0, 9.0, None, None),
        ])
        self.assertAlmostEqual(result.mean_mae_fantasy_points, 7.0)

    def test_total_lineup_points_sums_non_none(self) -> None:
        result = BacktestResult(race_results=[
            self._make_result(1.0, 1.0, 50.0, 60.0),
            self._make_result(1.0, 1.0, None, None),
            self._make_result(1.0, 1.0, 30.0, 40.0),
        ])
        self.assertAlmostEqual(result.total_lineup_points, 80.0)

    def test_total_lineup_points_none_when_all_missing(self) -> None:
        result = BacktestResult(race_results=[
            self._make_result(1.0, 1.0, None, None),
        ])
        self.assertIsNone(result.total_lineup_points)

    def test_empty_race_results_gives_zero_mae(self) -> None:
        result = BacktestResult(race_results=[])
        self.assertEqual(result.mean_mae_position, 0.0)
        self.assertEqual(result.mean_mae_fantasy_points, 0.0)


# ---------------------------------------------------------------------------
# _compute_mae — pure function, no DB
# ---------------------------------------------------------------------------


class TestComputeMAE(SimpleTestCase):
    def _make_predictions(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_perfect_predictions_give_zero_mae(self) -> None:
        preds = self._make_predictions([
            {"driver_id": 1, "predicted_position": 3.0, "predicted_fantasy_points": 20.0},
        ])
        actuals = {1: (3.0, 20.0)}
        mae_pos, mae_pts = _compute_mae(preds, actuals)
        self.assertAlmostEqual(mae_pos, 0.0)
        self.assertAlmostEqual(mae_pts, 0.0)

    def test_mae_computed_correctly(self) -> None:
        preds = self._make_predictions([
            {"driver_id": 1, "predicted_position": 1.0, "predicted_fantasy_points": 25.0},
            {"driver_id": 2, "predicted_position": 5.0, "predicted_fantasy_points": 10.0},
        ])
        # actual: driver 1 finished 3rd (error=2), driver 2 finished 4th (error=1)
        # mae_pos = (2+1)/2 = 1.5
        actuals = {1: (3.0, 20.0), 2: (4.0, 8.0)}
        mae_pos, mae_pts = _compute_mae(preds, actuals)
        self.assertAlmostEqual(mae_pos, 1.5)
        self.assertAlmostEqual(mae_pts, (5.0 + 2.0) / 2)

    def test_drivers_missing_from_actuals_are_skipped(self) -> None:
        preds = self._make_predictions([
            {"driver_id": 1, "predicted_position": 1.0, "predicted_fantasy_points": 25.0},
            {"driver_id": 99, "predicted_position": 5.0, "predicted_fantasy_points": 10.0},  # no actual
        ])
        actuals = {1: (1.0, 25.0)}
        mae_pos, mae_pts = _compute_mae(preds, actuals)
        self.assertAlmostEqual(mae_pos, 0.0)

    def test_no_matching_drivers_returns_zero(self) -> None:
        preds = self._make_predictions([
            {"driver_id": 99, "predicted_position": 1.0, "predicted_fantasy_points": 25.0},
        ])
        actuals = {1: (1.0, 25.0)}
        mae_pos, mae_pts = _compute_mae(preds, actuals)
        self.assertEqual(mae_pos, 0.0)
        self.assertEqual(mae_pts, 0.0)


# ---------------------------------------------------------------------------
# _score_lineup — pure function, no DB
# ---------------------------------------------------------------------------


class TestScoreLineup(SimpleTestCase):
    def _make_lineup(self, driver_ids: list[int], constructor_ids: list[int], drs_id: int) -> Lineup:
        return Lineup(
            driver_ids=driver_ids,
            constructor_ids=constructor_ids,
            drs_boost_driver_id=drs_id,
            total_cost=80.0,
            predicted_points=0.0,
        )

    def test_score_sums_drivers_plus_constructors_plus_drs(self) -> None:
        lineup = self._make_lineup([1, 2, 3, 4, 5], [101, 102], drs_id=1)
        # Driver 1 (DRS): 30pts counted twice = 30 + 30 bonus
        driver_pts = {1: 30.0, 2: 20.0, 3: 15.0, 4: 10.0, 5: 8.0}
        constructor_pts = {101: 40.0, 102: 35.0}
        score = _score_lineup(lineup, driver_pts, constructor_pts)
        self.assertAlmostEqual(score, 30 + 20 + 15 + 10 + 8 + 40 + 35 + 30)

    def test_missing_driver_scores_zero(self) -> None:
        lineup = self._make_lineup([1, 2, 3, 4, 5], [101, 102], drs_id=1)
        driver_pts = {1: 25.0}  # only driver 1 has actual data
        constructor_pts = {}
        score = _score_lineup(lineup, driver_pts, constructor_pts)
        # driver 1 = 25, drs bonus = 25; rest = 0
        self.assertAlmostEqual(score, 50.0)


# ---------------------------------------------------------------------------
# Backtester.run — DB integration
# ---------------------------------------------------------------------------


class TestBacktesterRun(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season, name="Red Bull")
        self.driver = make_driver(self.season, self.team, code="VER", driver_number=1)

    def _make_event_with_data(self, round_number: int, position: int, fantasy_pts: int) -> object:
        event = make_event(self.season, round_number=round_number, event_date=date(2024, round_number, 1))
        session = make_session(event, session_type="R")
        make_result(session, self.driver, self.team, position=position)
        make_fantasy_score(self.driver, event, race_total=fantasy_pts)
        return event

    def test_run_returns_backtest_result(self) -> None:
        from predictions.evaluation.backtester import Backtester
        from predictions.features.v1_pandas import V1FeatureStore
        from predictions.predictors.xgboost_v1 import XGBoostPredictor

        events = [self._make_event_with_data(i, position=i, fantasy_pts=50 - i * 3) for i in range(1, 5)]
        backtester = Backtester()
        result = backtester.run(
            events=events,
            feature_store=V1FeatureStore(),
            predictor=XGBoostPredictor(),
            optimizer=GreedyOptimizerV2(),
            min_train=1,
        )
        self.assertIsInstance(result, BacktestResult)

    def test_run_produces_race_results_for_each_testable_event(self) -> None:
        from predictions.evaluation.backtester import Backtester
        from predictions.features.v1_pandas import V1FeatureStore
        from predictions.predictors.xgboost_v1 import XGBoostPredictor

        events = [self._make_event_with_data(i, position=i, fantasy_pts=50 - i * 3) for i in range(1, 5)]
        result = Backtester().run(
            events=events,
            feature_store=V1FeatureStore(),
            predictor=XGBoostPredictor(),
            optimizer=GreedyOptimizerV2(),
            min_train=1,
        )
        # 4 events with min_train=1 → 3 test events
        self.assertEqual(len(result.race_results), 3)

    def test_mae_fields_are_floats(self) -> None:
        from predictions.evaluation.backtester import Backtester
        from predictions.features.v1_pandas import V1FeatureStore
        from predictions.predictors.xgboost_v1 import XGBoostPredictor

        events = [self._make_event_with_data(i, position=i, fantasy_pts=50 - i * 3) for i in range(1, 4)]
        result = Backtester().run(
            events=events,
            feature_store=V1FeatureStore(),
            predictor=XGBoostPredictor(),
            optimizer=GreedyOptimizerV2(),
            min_train=1,
        )
        for race in result.race_results:
            self.assertIsInstance(race.mae_position, float)
            self.assertIsInstance(race.mae_fantasy_points, float)

    def test_lineup_metrics_none_without_price_data(self) -> None:
        from predictions.evaluation.backtester import Backtester
        from predictions.features.v1_pandas import V1FeatureStore
        from predictions.predictors.xgboost_v1 import XGBoostPredictor

        events = [self._make_event_with_data(i, position=i, fantasy_pts=50 - i * 3) for i in range(1, 4)]
        result = Backtester().run(
            events=events,
            feature_store=V1FeatureStore(),
            predictor=XGBoostPredictor(),
            optimizer=GreedyOptimizerV2(),
            min_train=1,
        )
        for race in result.race_results:
            self.assertIsNone(race.lineup_actual_points)
            self.assertIsNone(race.optimal_actual_points)

    # ---------------------------------------------------------------------------
    # Helpers for price-data tests
    # ---------------------------------------------------------------------------

    def _make_world(self) -> tuple[list, list]:
        """Create 3 teams × 2 drivers = 6 drivers. Returns (drivers, teams)."""
        season = make_season(2025)
        team_a = make_team(season, name="Red Bull")
        team_b = make_team(season, name="Ferrari")
        team_c = make_team(season, name="Mercedes")
        teams = [team_a, team_b, team_c]
        drivers = [
            make_driver(season, team_a, code="VER", driver_number=33),
            make_driver(season, team_a, code="PER", driver_number=11),
            make_driver(season, team_b, code="LEC", driver_number=16),
            make_driver(season, team_b, code="SAI", driver_number=55),
            make_driver(season, team_c, code="HAM", driver_number=44),
            make_driver(season, team_c, code="RUS", driver_number=63),
        ]
        return drivers, teams

    def _make_full_event(self, season, round_number: int, drivers: list, teams: list, with_prices: bool = False):
        event = make_event(season, round_number=round_number, event_date=date(2024, round_number, 1))
        session = make_session(event, session_type="R")
        driver_teams = [t for t in teams for _ in range(2)]
        for i, (driver, team) in enumerate(zip(drivers, driver_teams)):
            make_result(session, driver, team, position=i + 1)
            make_fantasy_score(driver, event, race_total=50 - i * 5)
        if with_prices:
            prices = [15.0, 12.0, 14.0, 11.0, 13.0, 10.0]
            for driver, price in zip(drivers, prices):
                make_driver_price(driver, event, price=price)
            for team in teams:
                make_constructor_price(team, event, price=12.0)
                make_constructor_score(team, event, race_total=60)
        return event

    def test_lineup_metrics_populated_with_price_data(self) -> None:
        from predictions.evaluation.backtester import Backtester
        from predictions.features.v1_pandas import V1FeatureStore
        from predictions.predictors.xgboost_v1 import XGBoostPredictor

        drivers, teams = self._make_world()
        season = drivers[0].season
        event1 = self._make_full_event(season, round_number=1, drivers=drivers, teams=teams, with_prices=False)
        event2 = self._make_full_event(season, round_number=2, drivers=drivers, teams=teams, with_prices=True)

        result = Backtester().run(
            events=[event1, event2],
            feature_store=V1FeatureStore(),
            predictor=XGBoostPredictor(),
            optimizer=GreedyOptimizerV2(),
            min_train=1,
        )
        race = result.race_results[0]
        self.assertIsNotNone(race.lineup_actual_points)
        self.assertIsNotNone(race.optimal_actual_points)
        self.assertIsInstance(race.lineup_actual_points, float)
        self.assertIsInstance(race.optimal_actual_points, float)

    def test_optimal_score_at_least_as_good_as_lineup(self) -> None:
        from predictions.evaluation.backtester import Backtester
        from predictions.features.v1_pandas import V1FeatureStore
        from predictions.predictors.xgboost_v1 import XGBoostPredictor

        drivers, teams = self._make_world()
        season = drivers[0].season
        event1 = self._make_full_event(season, round_number=1, drivers=drivers, teams=teams, with_prices=False)
        event2 = self._make_full_event(season, round_number=2, drivers=drivers, teams=teams, with_prices=True)

        result = Backtester().run(
            events=[event1, event2],
            feature_store=V1FeatureStore(),
            predictor=XGBoostPredictor(),
            optimizer=GreedyOptimizerV2(),
            min_train=1,
        )
        race = result.race_results[0]
        self.assertGreaterEqual(race.optimal_actual_points, race.lineup_actual_points)
