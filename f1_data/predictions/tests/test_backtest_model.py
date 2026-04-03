"""
Tests for the backtest_model management command.

Structure:
  TestActualsForSession     — pure DB queries in _actuals_for_session
  TestCommandValidation     — argument validation (family, events count, etc.)
  TestBacktestModelCommand  — integration: full run against minimal DB data
"""
from __future__ import annotations

from datetime import date
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from predictions.management.commands.backtest_model import _actuals_for_session
from predictions.tests.factories import (
    make_driver,
    make_event,
    make_fantasy_score,
    make_result,
    make_season,
    make_session,
    make_team,
)


# ---------------------------------------------------------------------------
# _actuals_for_session — DB queries
# ---------------------------------------------------------------------------


class TestActualsForSession(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season, name="Red Bull")
        self.driver = make_driver(self.season, self.team, code="VER", driver_number=1)
        self.event = make_event(self.season, round_number=1)

    def test_race_session_returns_position_and_fantasy_pts(self) -> None:
        session = make_session(self.event, session_type="R")
        make_result(session, self.driver, self.team, position=3)
        make_fantasy_score(self.driver, self.event, race_total=50, event_type="race", points=25)

        actuals = _actuals_for_session(self.event, "R")

        self.assertIn(self.driver.id, actuals)
        position, pts = actuals[self.driver.id]
        self.assertEqual(position, 3.0)
        self.assertEqual(pts, 25.0)

    def test_qualifying_session_filters_by_event_type(self) -> None:
        # Create both R and Q sessions — only Q should be returned for session_type="Q"
        race_session = make_session(self.event, session_type="R")
        q_session = make_session(self.event, session_type="Q")
        make_result(race_session, self.driver, self.team, position=5)
        make_result(q_session, self.driver, self.team, position=2)
        # Fantasy score for qualifying only
        make_fantasy_score(self.driver, self.event, race_total=10, event_type="qualifying", points=9)

        actuals = _actuals_for_session(self.event, "Q")

        self.assertIn(self.driver.id, actuals)
        position, pts = actuals[self.driver.id]
        self.assertEqual(position, 2.0)
        self.assertEqual(pts, 9.0)

    def test_fantasy_pts_sums_multiple_line_items(self) -> None:
        """
        FantasyDriverScore stores one row per scoring action (e.g. "Race Position"
        and "Race Overtake Bonus" are separate rows). _actuals_for_session must sum
        them, not take only the first row.
        """
        session = make_session(self.event, session_type="R")
        make_result(session, self.driver, self.team, position=1)
        # Two separate scoring actions for the same race event_type
        make_fantasy_score(
            self.driver, self.event, race_total=50, event_type="race",
            scoring_item="Race Position", points=25,
        )
        make_fantasy_score(
            self.driver, self.event, race_total=50, event_type="race",
            scoring_item="Race Overtake Bonus", points=6,
        )

        actuals = _actuals_for_session(self.event, "R")

        _, pts = actuals[self.driver.id]
        # 25 + 6 = 31, not just 25
        self.assertAlmostEqual(pts, 31.0)

    def test_race_total_not_used_qualifying_pts_stay_separate(self) -> None:
        """
        race_total is the full-weekend aggregate (race + qualifying + sprint).
        We must NOT use race_total when evaluating a qualifying session — only
        the qualifying line items should contribute.
        """
        session = make_session(self.event, session_type="Q")
        make_result(session, self.driver, self.team, position=1)
        # Qualifying score: 9 pts, but race_total would be 50 (full weekend)
        make_fantasy_score(
            self.driver, self.event, race_total=50, event_type="qualifying",
            scoring_item="Qualifying Position", points=9,
        )

        actuals = _actuals_for_session(self.event, "Q")

        _, pts = actuals[self.driver.id]
        self.assertAlmostEqual(pts, 9.0)    # not 50

    def test_driver_with_null_position_excluded(self) -> None:
        """Drivers with position=None (DNF/DSQ/DNS) must be excluded — they can't
        be used as a target for position prediction."""
        session = make_session(self.event, session_type="R")
        make_result(session, self.driver, self.team, position=None)
        make_fantasy_score(self.driver, self.event, race_total=0, event_type="race", points=0)

        actuals = _actuals_for_session(self.event, "R")

        self.assertNotIn(self.driver.id, actuals)

    def test_no_session_data_returns_empty(self) -> None:
        # No session created at all
        actuals = _actuals_for_session(self.event, "Q")
        self.assertEqual(actuals, {})

    def test_driver_without_fantasy_score_defaults_to_zero(self) -> None:
        """
        When we have a finishing position but no FantasyDriverScore rows for that
        session type (common for qualifying/sprint when CSVs haven't been imported),
        the driver should still appear in actuals with pts=0.0. This lets us
        evaluate position ranking even without fantasy point data.
        """
        session = make_session(self.event, session_type="Q")
        make_result(session, self.driver, self.team, position=1)
        # No FantasyDriverScore created

        actuals = _actuals_for_session(self.event, "Q")

        self.assertIn(self.driver.id, actuals)
        _, pts = actuals[self.driver.id]
        self.assertEqual(pts, 0.0)


# ---------------------------------------------------------------------------
# Command argument validation
# ---------------------------------------------------------------------------


class TestCommandValidation(TestCase):
    def setUp(self) -> None:
        self.season = make_season(2024)
        self.team = make_team(self.season, name="Alpha")
        self.driver = make_driver(self.season, self.team, code="AAA", driver_number=1)

    def _make_events(self, n: int) -> list:
        events = []
        for i in range(1, n + 1):
            event = make_event(self.season, round_number=i, event_date=date(2024, i, 1))
            session = make_session(event, session_type="R")
            make_result(session, self.driver, self.team, position=i)
            make_fantasy_score(self.driver, event, race_total=50 - i, event_type="race", points=50 - i)
            events.append(event)
        return events

    def test_empty_family_raises_command_error(self) -> None:
        """race_ranker has an empty predictor registry — must raise CommandError."""
        self._make_events(6)
        with self.assertRaises(CommandError) as ctx:
            call_command("backtest_model", "race_ranker", seasons=[2024])
        self.assertIn("race_ranker", str(ctx.exception))

    def test_price_heuristic_raises_command_error(self) -> None:
        """price_heuristic is not a ranking predictor — must raise CommandError."""
        self._make_events(6)
        with self.assertRaises(CommandError) as ctx:
            call_command("backtest_model", "price_heuristic", seasons=[2024])
        self.assertIn("price_heuristic", str(ctx.exception))

    def test_too_few_events_raises_command_error(self) -> None:
        """Fewer events than min_train + 1 must raise CommandError."""
        self._make_events(5)   # min_train=5 default → need ≥6
        with self.assertRaises(CommandError) as ctx:
            call_command("backtest_model", "xgboost", seasons=[2024], min_train=5)
        self.assertIn("need at least", str(ctx.exception))

    def test_unknown_predictor_version_raises_command_error(self) -> None:
        """Requesting a predictor version not in the family registry must raise CommandError."""
        self._make_events(6)
        with self.assertRaises(CommandError) as ctx:
            call_command("backtest_model", "xgboost", seasons=[2024], predictor=["v99"])
        self.assertIn("v99", str(ctx.exception))


# ---------------------------------------------------------------------------
# Integration — full command run
# ---------------------------------------------------------------------------


class TestBacktestModelCommand(TestCase):
    """
    Full-pipeline integration test. Creates minimal DB data (one driver, one team,
    several events with race results and fantasy scores), then runs backtest_model
    and inspects stdout.

    We don't assert on specific numeric values — XGBoost results depend on random
    seeds — but we verify that the output table structure is present and complete.
    """

    def _make_world(self):
        """3 teams × 2 drivers = 6 drivers in 2024 season."""
        season = make_season(2024)
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
        return season, drivers, teams

    def _make_events(self, season, drivers, teams, n: int, session_type: str = "R") -> list:
        """Create n race events each with results and fantasy scores for all drivers."""
        events = []
        for i in range(1, n + 1):
            event = make_event(season, round_number=i, event_date=date(2024, i, 1))
            session = make_session(event, session_type=session_type)
            for j, driver in enumerate(drivers):
                make_result(session, driver, teams[j // 2], position=j + 1)
                make_fantasy_score(
                    driver, event, race_total=50 - j * 3,
                    event_type="race" if session_type == "R" else session_type,
                    points=50 - j * 3,
                )
            events.append(event)
        return events

    def test_command_produces_per_race_table(self) -> None:
        season, drivers, teams = self._make_world()
        self._make_events(season, drivers, teams, n=7)
        out = StringIO()
        call_command(
            "backtest_model", "xgboost",
            seasons=[2024],
            feature_store=["v1"],
            predictor=["v1"],
            min_train=5,
            stdout=out,
        )
        output = out.getvalue()
        # Should have printed at least one race row and the summary header
        self.assertIn("MAE Pos", output)
        self.assertIn("Races evaluated:", output)
        self.assertIn("Spearman", output)

    def test_command_shows_no_optimizer_fields(self) -> None:
        """The output must NOT contain lineup/oracle/transfer columns."""
        season, drivers, teams = self._make_world()
        self._make_events(season, drivers, teams, n=7)
        out = StringIO()
        call_command(
            "backtest_model", "xgboost",
            seasons=[2024],
            feature_store=["v1"],
            predictor=["v1"],
            min_train=5,
            stdout=out,
        )
        output = out.getvalue()
        self.assertNotIn("Lineup", output)
        self.assertNotIn("Optimal", output)
        self.assertNotIn("Trades", output)
        self.assertNotIn("oracle", output.lower())

    def test_multi_combo_produces_comparison_table(self) -> None:
        """When multiple predictor versions are swept, a Comparison table must appear."""
        season, drivers, teams = self._make_world()
        self._make_events(season, drivers, teams, n=8)
        out = StringIO()
        call_command(
            "backtest_model", "xgboost",
            seasons=[2024],
            feature_store=["v1"],
            predictor=["v1", "v2"],
            min_train=5,
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn("Comparison", output)
        # Both predictor versions should appear in the comparison block
        self.assertIn("v1", output)
        self.assertIn("v2", output)

    def test_qualifying_session_evaluated_when_data_exists(self) -> None:
        """
        _actuals_for_session("Q") is used when family='xgboost' is not applicable
        for qualifying, but if we were to have a qualifying_ranker, it would use Q.
        This test validates that _actuals_for_session correctly fetches Q data.
        We test this via _actuals_for_session directly since qualifying_ranker
        now has v1 registered but the full command test is in test_qualifying_ranker_v1.
        """
        season, drivers, teams = self._make_world()
        event = make_event(season, round_number=1)
        q_session = make_session(event, session_type="Q")
        for j, driver in enumerate(drivers):
            make_result(q_session, driver, teams[j // 2], position=j + 1)
            make_fantasy_score(
                driver, event, race_total=20, event_type="qualifying",
                scoring_item="Qualifying Position", points=9 - j,
            )

        actuals = _actuals_for_session(event, "Q")

        self.assertEqual(len(actuals), len(drivers))
        for driver in drivers:
            self.assertIn(driver.id, actuals)

    def test_no_results_for_session_type_skips_gracefully(self) -> None:
        """
        If a season has events but no sprint (S) session results,
        the command should produce 0 races evaluated with no crash.
        """
        season, drivers, teams = self._make_world()
        # Create only race (R) sessions — no sprint sessions
        self._make_events(season, drivers, teams, n=7, session_type="R")

        # Temporarily monkeypatch the session_type mapping so xgboost uses "S"
        # (we can't run sprint_ranker since it has no predictors, so we test
        # _actuals_for_session with "S" directly instead)
        event = make_event(season, round_number=8, event_date=date(2024, 8, 1))
        # No S session created — actuals should be empty
        actuals = _actuals_for_session(event, "S")
        self.assertEqual(actuals, {})
