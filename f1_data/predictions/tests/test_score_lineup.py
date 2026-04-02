from __future__ import annotations

from datetime import date
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from predictions.models import LineupRecommendation, MyLineup
from predictions.scoring import compute_oracle, load_actual_driver_pts, score_roster
from predictions.tests.factories import (
    make_constructor_price,
    make_constructor_score,
    make_driver,
    make_driver_price,
    make_event,
    make_fantasy_score,
    make_lineup_recommendation,
    make_my_lineup,
    make_season,
    make_team,
)


# ---------------------------------------------------------------------------
# score_roster — pure function, no DB
# ---------------------------------------------------------------------------


class TestScoreRoster(SimpleTestCase):
    def test_sums_driver_and_constructor_points(self) -> None:
        result = score_roster(
            driver_ids=[1, 2, 3, 4, 5],
            constructor_ids=[10, 11],
            drs_driver_id=1,
            actual_driver_pts={1: 30.0, 2: 25.0, 3: 20.0, 4: 15.0, 5: 10.0},
            actual_constructor_pts={10: 40.0, 11: 35.0},
        )
        # 30+25+20+15+10 = 100 drivers, 40+35 = 75 constructors, +30 DRS = 205
        self.assertAlmostEqual(result, 205.0)

    def test_drs_driver_points_added_twice(self) -> None:
        result = score_roster(
            driver_ids=[1],
            constructor_ids=[],
            drs_driver_id=1,
            actual_driver_pts={1: 30.0},
            actual_constructor_pts={},
        )
        # 30 base + 30 DRS = 60
        self.assertAlmostEqual(result, 60.0)

    def test_missing_driver_defaults_to_zero(self) -> None:
        result = score_roster(
            driver_ids=[1, 2],
            constructor_ids=[],
            drs_driver_id=1,
            actual_driver_pts={1: 20.0},  # driver 2 absent
            actual_constructor_pts={},
        )
        # 20 + 0 (missing) + 20 DRS = 40
        self.assertAlmostEqual(result, 40.0)

    def test_missing_constructor_defaults_to_zero(self) -> None:
        result = score_roster(
            driver_ids=[1],
            constructor_ids=[10, 11],
            drs_driver_id=1,
            actual_driver_pts={1: 10.0},
            actual_constructor_pts={10: 20.0},  # 11 absent
        )
        # 10 driver + 20 + 0 + 10 DRS = 40
        self.assertAlmostEqual(result, 40.0)

    def test_negative_points_handled_correctly(self) -> None:
        result = score_roster(
            driver_ids=[1],
            constructor_ids=[10],
            drs_driver_id=1,
            actual_driver_pts={1: -5.0},
            actual_constructor_pts={10: 10.0},
        )
        # -5 driver + 10 constructor + -5 DRS = 0
        self.assertAlmostEqual(result, 0.0)


# ---------------------------------------------------------------------------
# compute_oracle — DB-dependent
# ---------------------------------------------------------------------------


class TestComputeOracle(TestCase):
    def setUp(self) -> None:
        self.season = make_season(year=2024)
        self.mclaren = make_team(self.season, name="McLaren")
        self.ferrari = make_team(self.season, name="Ferrari")
        self.drivers = [
            make_driver(self.season, self.mclaren, code=c, driver_number=i)
            for i, c in enumerate(["NOR", "PIA", "LEC", "HAM", "RUS"], start=1)
        ]
        self.event = make_event(self.season, round_number=1)
        self.actual_driver_pts = {d.id: float(10 * (i + 1)) for i, d in enumerate(self.drivers)}
        self.actual_constructor_pts = {self.mclaren.id: 50.0, self.ferrari.id: 40.0}

    def test_returns_none_when_driver_prices_missing(self) -> None:
        # No prices seeded
        result = compute_oracle(self.event, self.actual_driver_pts, self.actual_constructor_pts, budget=100.0)
        self.assertIsNone(result)

    def test_returns_none_when_constructor_prices_missing(self) -> None:
        for d in self.drivers:
            make_driver_price(d, self.event, price=10.0)
        # No constructor prices
        result = compute_oracle(self.event, self.actual_driver_pts, self.actual_constructor_pts, budget=100.0)
        self.assertIsNone(result)

    def test_returns_float_when_prices_available(self) -> None:
        for d in self.drivers:
            make_driver_price(d, self.event, price=10.0)
        make_constructor_price(self.mclaren, self.event, price=15.0)
        make_constructor_price(self.ferrari, self.event, price=15.0)
        result = compute_oracle(self.event, self.actual_driver_pts, self.actual_constructor_pts, budget=100.0)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, float)
        self.assertGreater(result, 0.0)


# ---------------------------------------------------------------------------
# score_lineup command
# ---------------------------------------------------------------------------


class TestScoreLineupCommand(TestCase):
    def setUp(self) -> None:
        self.season = make_season(year=2024)
        self.mclaren = make_team(self.season, name="McLaren")
        self.ferrari = make_team(self.season, name="Ferrari")
        self.drivers = [
            make_driver(self.season, self.mclaren, code=c, driver_number=i)
            for i, c in enumerate(["NOR", "PIA", "LEC", "HAM", "RUS"], start=1)
        ]
        self.event = make_event(self.season, round_number=1, event_date=date(2024, 3, 1))

    def _seed_fantasy_scores(self, pts_per_driver: int = 30) -> None:
        for driver in self.drivers:
            make_fantasy_score(driver, self.event, race_total=pts_per_driver)
        make_constructor_score(self.mclaren, self.event, race_total=50)
        make_constructor_score(self.ferrari, self.event, race_total=40)

    def _call(self, **kwargs) -> StringIO:
        out = StringIO()
        defaults = dict(year=2024, round=1)
        defaults.update(kwargs)
        call_command("score_lineup", stdout=out, **defaults)
        return out

    def test_raises_if_event_not_found(self) -> None:
        with self.assertRaises(CommandError):
            self._call(round=99)

    def test_raises_if_no_fantasy_score_data(self) -> None:
        with self.assertRaises(CommandError):
            self._call()

    def test_updates_my_lineup_actual_points(self) -> None:
        self._seed_fantasy_scores(pts_per_driver=30)
        make_my_lineup(self.event, self.drivers, [self.mclaren, self.ferrari], drs_driver=self.drivers[0])

        self._call()

        my_lineup = MyLineup.objects.get(event=self.event)
        # 30 * 5 drivers + 50 + 40 constructors + 30 DRS bonus = 270
        self.assertAlmostEqual(my_lineup.actual_points, 270.0)

    def test_skips_my_lineup_when_not_recorded(self) -> None:
        self._seed_fantasy_scores()
        out = self._call()
        self.assertIn("skipping", out.getvalue())

    def test_updates_recommendation_actual_points(self) -> None:
        self._seed_fantasy_scores(pts_per_driver=30)
        make_lineup_recommendation(
            self.event, self.drivers, [self.mclaren, self.ferrari], drs_driver=self.drivers[0]
        )

        self._call()

        rec = LineupRecommendation.objects.get(event=self.event)
        self.assertIsNotNone(rec.actual_points)
        self.assertAlmostEqual(rec.actual_points, 270.0)

    def test_skips_recommendation_when_none_recorded(self) -> None:
        self._seed_fantasy_scores()
        out = self._call()
        self.assertIn("none found", out.getvalue())

    def test_sets_oracle_actual_points_when_prices_available(self) -> None:
        self._seed_fantasy_scores(pts_per_driver=30)
        for d in self.drivers:
            make_driver_price(d, self.event, price=10.0)
        make_constructor_price(self.mclaren, self.event, price=15.0)
        make_constructor_price(self.ferrari, self.event, price=15.0)
        make_lineup_recommendation(
            self.event, self.drivers, [self.mclaren, self.ferrari], drs_driver=self.drivers[0]
        )

        self._call()

        rec = LineupRecommendation.objects.get(event=self.event)
        self.assertIsNotNone(rec.oracle_actual_points)
        self.assertGreaterEqual(rec.oracle_actual_points, rec.actual_points)

    def test_oracle_is_null_when_prices_missing(self) -> None:
        self._seed_fantasy_scores()
        make_lineup_recommendation(
            self.event, self.drivers, [self.mclaren, self.ferrari], drs_driver=self.drivers[0]
        )

        self._call()

        rec = LineupRecommendation.objects.get(event=self.event)
        self.assertIsNone(rec.oracle_actual_points)


# ---------------------------------------------------------------------------
# next_race auto-scoring integration
# ---------------------------------------------------------------------------


class TestNextRaceAutoScoring(TestCase):
    """
    Tests that next_race auto-scores the previous round when post-race data exists.
    The ML pipeline is mocked so we only test the auto-scoring side-effect.
    """

    def setUp(self) -> None:
        import pandas as pd
        from predictions.optimizers.base import Lineup

        self.season = make_season(year=2024)
        self.mclaren = make_team(self.season, name="McLaren")
        self.ferrari = make_team(self.season, name="Ferrari")
        self.drivers = [
            make_driver(self.season, self.mclaren, code=c, driver_number=i)
            for i, c in enumerate(["NOR", "PIA", "LEC", "HAM", "RUS"], start=1)
        ]
        self.past_event = make_event(self.season, round_number=1, event_date=date(2024, 3, 1))
        self.target_event = make_event(self.season, round_number=2, event_date=date(2024, 3, 15))

        driver_ids = [d.id for d in self.drivers]
        self._mock_lineup = Lineup(
            driver_ids=driver_ids,
            constructor_ids=[self.mclaren.id, self.ferrari.id],
            drs_boost_driver_id=self.drivers[0].id,
            total_cost=90.0,
            predicted_points=200.0,
        )
        self._mock_predictions = pd.DataFrame({
            "driver_id": driver_ids,
            "predicted_position": [float(i + 1) for i in range(5)],
            "predicted_fantasy_points": [30.0 - i * 2 for i in range(5)],
            "confidence_lower": [20.0] * 5,
            "confidence_upper": [40.0] * 5,
        })
        self._mock_features = pd.DataFrame({
            "driver_id": driver_ids,
            "event_id": [self.target_event.id] * 5,
        })
        self._mock_X = pd.DataFrame({"driver_id": driver_ids, "event_id": [1] * 5})
        self._mock_y = pd.DataFrame({"finishing_position": [1.0, 2.0, 3.0, 4.0, 5.0], "fantasy_points": [30.0] * 5})

        # Seed target event prices so next_race doesn't raise
        for d in self.drivers:
            make_driver_price(d, self.target_event, price=10.0)
        make_constructor_price(self.mclaren, self.target_event, price=15.0)
        make_constructor_price(self.ferrari, self.target_event, price=15.0)

    def _call_next_race(self) -> StringIO:
        from unittest.mock import patch
        out = StringIO()
        with patch("predictions.management.commands.next_race.build_training_dataset",
                   return_value=(self._mock_X, self._mock_y)), \
             patch("predictions.management.commands.next_race.V2FeatureStore") as MockStore, \
             patch("predictions.management.commands.next_race.XGBoostPredictorV4") as MockPred, \
             patch("predictions.management.commands.next_race.ILPOptimizer") as MockOpt:
            MockStore.return_value.get_all_driver_features.return_value = self._mock_features
            MockPred.return_value.predict.return_value = self._mock_predictions
            MockOpt.return_value.optimize_single_race.return_value = self._mock_lineup
            call_command("next_race", year=2024, round=2, budget=150.0, stdout=out)
        return out

    def test_auto_scores_my_lineup_when_fantasy_data_available(self) -> None:
        make_my_lineup(self.past_event, self.drivers, [self.mclaren, self.ferrari])
        for driver in self.drivers:
            make_fantasy_score(driver, self.past_event, race_total=30)

        self._call_next_race()

        my_lineup = MyLineup.objects.get(event=self.past_event)
        self.assertIsNotNone(my_lineup.actual_points)

    def test_auto_scores_recommendation_when_fantasy_data_available(self) -> None:
        make_lineup_recommendation(self.past_event, self.drivers, [self.mclaren, self.ferrari])
        for driver in self.drivers:
            make_fantasy_score(driver, self.past_event, race_total=30)
        make_constructor_score(self.mclaren, self.past_event, race_total=50)
        make_constructor_score(self.ferrari, self.past_event, race_total=40)

        self._call_next_race()

        rec = LineupRecommendation.objects.get(event=self.past_event)
        self.assertIsNotNone(rec.actual_points)

    def test_skips_auto_scoring_when_no_fantasy_data(self) -> None:
        make_my_lineup(self.past_event, self.drivers, [self.mclaren, self.ferrari])
        # No FantasyDriverScore records

        self._call_next_race()

        my_lineup = MyLineup.objects.get(event=self.past_event)
        self.assertIsNone(my_lineup.actual_points)

    def test_skips_auto_scoring_when_already_scored(self) -> None:
        make_my_lineup(self.past_event, self.drivers, [self.mclaren, self.ferrari], actual_points=200.0)
        for driver in self.drivers:
            make_fantasy_score(driver, self.past_event, race_total=30)

        out = self._call_next_race()

        # Should not print auto-scoring message since already scored
        self.assertNotIn("Auto-scored", out.getvalue())

    def test_auto_score_message_printed_when_data_available(self) -> None:
        make_my_lineup(self.past_event, self.drivers, [self.mclaren, self.ferrari])
        for driver in self.drivers:
            make_fantasy_score(driver, self.past_event, race_total=30)

        out = self._call_next_race()

        self.assertIn("Auto-scored", out.getvalue())
        self.assertIn("Grand Prix 1", out.getvalue())
