from __future__ import annotations

from unittest.mock import MagicMock, call, patch

from django.test import SimpleTestCase

from core.tasks.fastf1_loader import get_event_schedule, load_session


class TestGetEventSchedule(SimpleTestCase):
    @patch("core.tasks.fastf1_loader.fastf1.get_event_schedule")
    def test_get_event_schedule_calls_fastf1_with_year(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock()
        get_event_schedule(2024)
        mock_get.assert_called_once_with(2024)

    @patch("core.tasks.fastf1_loader.fastf1.get_event_schedule")
    def test_get_event_schedule_returns_fastf1_result(self, mock_get: MagicMock) -> None:
        mock_schedule = MagicMock()
        mock_get.return_value = mock_schedule
        result = get_event_schedule(2024)
        self.assertIs(result, mock_schedule)

    @patch("core.tasks.fastf1_loader.fastf1.get_event_schedule")
    def test_get_event_schedule_passes_year_through(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock()
        get_event_schedule(2018)
        mock_get.assert_called_once_with(2018)


class TestLoadSession(SimpleTestCase):
    @patch("core.tasks.fastf1_loader.fastf1.get_session")
    def test_load_session_calls_get_session_with_correct_args(
        self, mock_get_session: MagicMock
    ) -> None:
        mock_get_session.return_value = MagicMock()
        load_session(2024, 1, "R")
        mock_get_session.assert_called_once_with(2024, 1, "R")

    @patch("core.tasks.fastf1_loader.fastf1.get_session")
    def test_load_session_loads_with_telemetry_disabled(
        self, mock_get_session: MagicMock
    ) -> None:
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        load_session(2024, 1, "R")
        mock_session.load.assert_called_once_with(
            laps=True, telemetry=False, weather=True, messages=False
        )

    @patch("core.tasks.fastf1_loader.fastf1.get_session")
    def test_load_session_returns_loaded_session(self, mock_get_session: MagicMock) -> None:
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        result = load_session(2024, 1, "R")
        self.assertIs(result, mock_session)

    @patch("core.tasks.fastf1_loader.fastf1.get_session")
    def test_load_session_works_for_all_session_types(
        self, mock_get_session: MagicMock
    ) -> None:
        mock_get_session.return_value = MagicMock()
        for session_type in ("FP1", "FP2", "FP3", "Q", "SQ", "S", "R"):
            load_session(2024, 1, session_type)
        self.assertEqual(mock_get_session.call_count, 7)

    @patch("core.tasks.fastf1_loader.fastf1.get_session")
    def test_load_session_does_not_suppress_exceptions(
        self, mock_get_session: MagicMock
    ) -> None:
        mock_get_session.side_effect = ConnectionError("rate limited")
        with self.assertRaises(ConnectionError):
            load_session(2024, 1, "R")
