from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase

from core.flows.collect_season import collect_all, collect_single_session
from core.models import (
    Circuit,
    CollectionRun,
    Driver,
    Event,
    Lap,
    Season,
    Session,
    SessionCollectionStatus,
    SessionResult,
    Team,
    WeatherSample,
)
from core.tests.factories import make_schedule_dataframe, make_session_mock

FLOW = "core.flows.collect_season"


def _stdout() -> MagicMock:
    return MagicMock()


def _patch_all(mock_schedule, mock_load, mock_notify=None):
    """Shared decorator helper — not used directly, see individual tests."""


class TestSyncSchedule(TestCase):
    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_creates_season(self, mock_schedule, mock_load, mock_notify) -> None:
        mock_schedule.return_value = make_schedule_dataframe(sessions=["Race"])
        mock_load.return_value = make_session_mock()
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        self.assertTrue(Season.objects.filter(year=2024).exists())

    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_creates_circuit_from_location(self, mock_schedule, mock_load, mock_notify) -> None:
        mock_schedule.return_value = make_schedule_dataframe(sessions=["Race"])
        mock_load.return_value = make_session_mock()
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        self.assertTrue(Circuit.objects.filter(circuit_key="City1").exists())

    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_creates_event(self, mock_schedule, mock_load, mock_notify) -> None:
        mock_schedule.return_value = make_schedule_dataframe(sessions=["Race"])
        mock_load.return_value = make_session_mock()
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        self.assertTrue(Event.objects.filter(round_number=1).exists())

    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_creates_session_for_each_session_type(
        self, mock_schedule, mock_load, mock_notify
    ) -> None:
        mock_schedule.return_value = make_schedule_dataframe(
            sessions=["Practice 1", "Qualifying", "Race"]
        )
        mock_load.return_value = make_session_mock()
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        self.assertEqual(Session.objects.count(), 3)
        self.assertTrue(Session.objects.filter(session_type="FP1").exists())
        self.assertTrue(Session.objects.filter(session_type="Q").exists())
        self.assertTrue(Session.objects.filter(session_type="R").exists())

    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_filters_out_testing_events(self, mock_schedule, mock_load, mock_notify) -> None:
        mock_schedule.return_value = make_schedule_dataframe(
            sessions=["Race"], event_format="testing"
        )
        mock_load.return_value = make_session_mock()
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        self.assertEqual(Event.objects.count(), 0)

    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_unknown_session_name_not_created(self, mock_schedule, mock_load, mock_notify) -> None:
        mock_schedule.return_value = make_schedule_dataframe(sessions=["Mystery Session", "Race"])
        mock_load.return_value = make_session_mock()
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        self.assertEqual(Session.objects.count(), 1)
        self.assertTrue(Session.objects.filter(session_type="R").exists())


class TestCollectSingleSession(TestCase):
    def _setup_session(self) -> Session:
        season = Season.objects.create(year=2024)
        circuit = Circuit.objects.create(
            circuit_key="city1", name="Test Circuit", country="AU", city="City"
        )
        event = Event.objects.create(
            season=season,
            round_number=1,
            event_name="Test GP",
            country="AU",
            circuit=circuit,
            event_date="2024-03-24",
            event_format="conventional",
        )
        return Session.objects.create(event=event, session_type="R")

    @patch(f"{FLOW}.load_session")
    def test_creates_laps(self, mock_load) -> None:
        session = self._setup_session()
        mock_load.return_value = make_session_mock(num_drivers=1, num_laps=5)
        collect_single_session(session)
        self.assertEqual(Lap.objects.filter(session=session).count(), 5)

    @patch(f"{FLOW}.load_session")
    def test_creates_results(self, mock_load) -> None:
        session = self._setup_session()
        mock_load.return_value = make_session_mock(num_drivers=1)
        collect_single_session(session)
        self.assertEqual(SessionResult.objects.filter(session=session).count(), 1)

    @patch(f"{FLOW}.load_session")
    def test_creates_weather_samples(self, mock_load) -> None:
        session = self._setup_session()
        mock_load.return_value = make_session_mock()
        collect_single_session(session)
        self.assertEqual(WeatherSample.objects.filter(session=session).count(), 5)

    @patch(f"{FLOW}.load_session")
    def test_creates_driver_and_team(self, mock_load) -> None:
        session = self._setup_session()
        mock_load.return_value = make_session_mock(num_drivers=1)
        collect_single_session(session)
        self.assertTrue(Driver.objects.filter(code="VER").exists())
        self.assertTrue(Team.objects.filter(name="Red Bull Racing").exists())

    @patch(f"{FLOW}.load_session")
    def test_marks_status_completed(self, mock_load) -> None:
        session = self._setup_session()
        mock_load.return_value = make_session_mock()
        collect_single_session(session)
        scs = SessionCollectionStatus.objects.get(session=session)
        self.assertEqual(scs.status, "completed")

    @patch(f"{FLOW}.load_session")
    def test_stores_lap_count_on_status(self, mock_load) -> None:
        session = self._setup_session()
        mock_load.return_value = make_session_mock(num_drivers=1, num_laps=7)
        collect_single_session(session)
        scs = SessionCollectionStatus.objects.get(session=session)
        self.assertEqual(scs.lap_count, 7)

    @patch(f"{FLOW}.load_session")
    def test_is_idempotent(self, mock_load) -> None:
        session = self._setup_session()
        mock_load.return_value = make_session_mock(num_drivers=1, num_laps=5)
        collect_single_session(session)
        mock_load.return_value = make_session_mock(num_drivers=1, num_laps=5)
        collect_single_session(session)
        self.assertEqual(Lap.objects.filter(session=session).count(), 5)
        self.assertEqual(SessionResult.objects.filter(session=session).count(), 1)

    @patch(f"{FLOW}.load_session")
    def test_calls_load_session_with_correct_args(self, mock_load) -> None:
        session = self._setup_session()
        mock_load.return_value = make_session_mock()
        collect_single_session(session)
        mock_load.assert_called_once_with(2024, 1, "R")


class TestCollectAll(TestCase):
    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_creates_collection_run(self, mock_schedule, mock_load, mock_notify) -> None:
        mock_schedule.return_value = make_schedule_dataframe(sessions=["Race"])
        mock_load.return_value = make_session_mock()
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        self.assertEqual(CollectionRun.objects.count(), 1)

    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_marks_run_completed(self, mock_schedule, mock_load, mock_notify) -> None:
        mock_schedule.return_value = make_schedule_dataframe(sessions=["Race"])
        mock_load.return_value = make_session_mock()
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        run = CollectionRun.objects.get()
        self.assertEqual(run.status, "completed")
        self.assertIsNotNone(run.finished_at)

    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_increments_sessions_processed(self, mock_schedule, mock_load, mock_notify) -> None:
        mock_schedule.return_value = make_schedule_dataframe(sessions=["Race"])
        mock_load.return_value = make_session_mock()
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        run = CollectionRun.objects.get()
        self.assertEqual(run.sessions_processed, 1)

    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_skips_already_completed_sessions(
        self, mock_schedule, mock_load, mock_notify
    ) -> None:
        mock_schedule.return_value = make_schedule_dataframe(sessions=["Race"])
        mock_load.return_value = make_session_mock()
        # First run
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        first_call_count = mock_load.call_count
        # Second run — session already completed
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        self.assertEqual(mock_load.call_count, first_call_count)  # no new loads

    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_force_recollect_reruns_completed_sessions(
        self, mock_schedule, mock_load, mock_notify
    ) -> None:
        mock_schedule.return_value = make_schedule_dataframe(sessions=["Race"])
        mock_load.return_value = make_session_mock()
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        first_call_count = mock_load.call_count
        collect_all(years=[2024], force_recollect=True, stdout=_stdout())
        self.assertGreater(mock_load.call_count, first_call_count)

    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_sends_completion_notification(self, mock_schedule, mock_load, mock_notify) -> None:
        mock_schedule.return_value = make_schedule_dataframe(sessions=["Race"])
        mock_load.return_value = make_session_mock()
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        mock_notify.assert_called()
        last_call_args = mock_notify.call_args
        self.assertIn("complete", last_call_args[0][0].lower())

    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_session_error_marks_failed_and_continues(
        self, mock_schedule, mock_load, mock_notify
    ) -> None:
        mock_schedule.return_value = make_schedule_dataframe(sessions=["Qualifying", "Race"])
        mock_load.side_effect = [Exception("boom"), make_session_mock()]
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        failed = SessionCollectionStatus.objects.filter(status="failed").count()
        completed = SessionCollectionStatus.objects.filter(status="completed").count()
        self.assertEqual(failed, 1)
        self.assertEqual(completed, 1)

    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_session_error_increments_sessions_skipped(
        self, mock_schedule, mock_load, mock_notify
    ) -> None:
        mock_schedule.return_value = make_schedule_dataframe(sessions=["Race"])
        mock_load.side_effect = Exception("boom")
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        run = CollectionRun.objects.get()
        self.assertEqual(run.sessions_skipped, 1)

    @patch(f"{FLOW}.time.sleep")
    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_rate_limit_sleeps_61_minutes(
        self, mock_schedule, mock_load, mock_notify, mock_sleep
    ) -> None:
        from requests.exceptions import HTTPError

        mock_schedule.return_value = make_schedule_dataframe(sessions=["Race"])
        error = HTTPError()
        error.response = MagicMock(status_code=429)
        mock_load.side_effect = [error, make_session_mock()]
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        mock_sleep.assert_called_once_with(61 * 60)

    @patch(f"{FLOW}.time.sleep")
    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_rate_limit_retries_same_session(
        self, mock_schedule, mock_load, mock_notify, mock_sleep
    ) -> None:
        from requests.exceptions import HTTPError

        mock_schedule.return_value = make_schedule_dataframe(sessions=["Race"])
        error = HTTPError()
        error.response = MagicMock(status_code=429)
        mock_load.side_effect = [error, make_session_mock()]
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        self.assertEqual(mock_load.call_count, 2)
        scs = SessionCollectionStatus.objects.get(session__session_type="R")
        self.assertEqual(scs.status, "completed")

    @patch(f"{FLOW}.time.sleep")
    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_rate_limit_sends_warning_notification(
        self, mock_schedule, mock_load, mock_notify, mock_sleep
    ) -> None:
        from requests.exceptions import HTTPError

        mock_schedule.return_value = make_schedule_dataframe(sessions=["Race"])
        error = HTTPError()
        error.response = MagicMock(status_code=429)
        mock_load.side_effect = [error, make_session_mock()]
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        warning_calls = [c for c in mock_notify.call_args_list if c[1].get("level") == "warning"]
        self.assertEqual(len(warning_calls), 1)

    @patch(f"{FLOW}.time.sleep")
    @patch(f"{FLOW}.send_slack_notification")
    @patch(f"{FLOW}.load_session")
    @patch(f"{FLOW}.get_event_schedule")
    def test_rate_limit_notification_includes_progress(
        self, mock_schedule, mock_load, mock_notify, mock_sleep
    ) -> None:
        from requests.exceptions import HTTPError

        mock_schedule.return_value = make_schedule_dataframe(sessions=["Race"])
        error = HTTPError()
        error.response = MagicMock(status_code=429)
        mock_load.side_effect = [error, make_session_mock()]
        collect_all(years=[2024], force_recollect=False, stdout=_stdout())
        warning_calls = [c for c in mock_notify.call_args_list if c[1].get("level") == "warning"]
        msg = warning_calls[0][0][0]
        self.assertIn("remaining", msg)
        self.assertIn("processed", msg)
        self.assertIn("Season status", msg)
        self.assertIn("2024", msg)
