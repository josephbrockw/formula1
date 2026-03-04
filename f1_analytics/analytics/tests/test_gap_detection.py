"""
Tests for detect_session_data_gaps task.

Verifies gap detection accuracy against real SQLite DB state.
All scenarios use factories for setup.
"""

from unittest import mock
from django.test import TestCase
from analytics.models import SessionWeather, SessionResult, Lap, PitStop, Corner
from analytics.processing.gap_detection import detect_session_data_gaps
from analytics.tests.factories import (
    make_season, make_circuit, make_race, make_session, make_driver,
)


class DetectSessionDataGapsTests(TestCase):
    def setUp(self):
        self.season = make_season(year=2025)
        self.circuit = make_circuit()
        self.race = make_race(self.season, circuit=self.circuit)

    @mock.patch('analytics.processing.gap_detection.get_run_logger')
    def test_empty_session_has_all_missing_flags(self, mock_logger):
        """Session with no weather/drivers/laps/pits/circuit returns gap with all flags True."""
        make_session(self.race, past=True)

        gaps = detect_session_data_gaps.fn(2025)

        self.assertEqual(len(gaps), 1)
        gap = gaps[0]
        self.assertTrue(gap.missing_weather)
        self.assertTrue(gap.missing_drivers)
        self.assertTrue(gap.missing_telemetry)
        self.assertTrue(gap.missing_pit_stops)
        self.assertTrue(gap.missing_circuit)

    @mock.patch('analytics.processing.gap_detection.get_run_logger')
    def test_complete_session_not_returned(self, mock_logger):
        """Session with all 4 data types and circuit corners is not in results."""
        import pandas as pd
        session = make_session(self.race, past=True)
        driver = make_driver()

        SessionWeather.objects.create(session=session, data_source='fastf1')
        SessionResult.objects.create(session=session, driver=driver, driver_number='1')
        Lap.objects.create(
            session=session, driver=driver, lap_number=1, driver_number='1',
        )
        PitStop.objects.create(
            session=session, driver=driver, stop_number=1, lap_number=1,
        )
        Corner.objects.create(
            circuit=self.circuit, number=1, letter='', x=0.0, y=0.0,
            angle=0.0, distance=0.0,
        )

        gaps = detect_session_data_gaps.fn(2025)

        self.assertEqual(len(gaps), 0)

    @mock.patch('analytics.processing.gap_detection.get_run_logger')
    def test_partial_session_has_correct_flags(self, mock_logger):
        """Session with only weather has missing_drivers/telemetry/pit_stops True, missing_weather False."""
        session = make_session(self.race, past=True)
        SessionWeather.objects.create(session=session, data_source='fastf1')

        gaps = detect_session_data_gaps.fn(2025)

        self.assertEqual(len(gaps), 1)
        gap = gaps[0]
        self.assertFalse(gap.missing_weather)
        self.assertTrue(gap.missing_drivers)
        self.assertTrue(gap.missing_telemetry)
        self.assertTrue(gap.missing_pit_stops)

    @mock.patch('analytics.processing.gap_detection.get_run_logger')
    def test_future_session_excluded(self, mock_logger):
        """Session with session_date_utc in the future is not in results."""
        make_session(self.race, past=False)

        gaps = detect_session_data_gaps.fn(2025)

        self.assertEqual(len(gaps), 0)

    @mock.patch('analytics.processing.gap_detection.get_run_logger')
    def test_null_session_type_excluded(self, mock_logger):
        """Session with session_type='' (empty string) is not in results."""
        from django.utils import timezone
        from analytics.models import Session

        Session.objects.create(
            race=self.race,
            session_number=5,
            session_type='',
            session_date_utc=timezone.now(),
        )

        gaps = detect_session_data_gaps.fn(2025)

        self.assertEqual(len(gaps), 0)

    @mock.patch('analytics.processing.gap_detection.get_run_logger')
    def test_gap_has_correct_session_id(self, mock_logger):
        """gap.session_id matches the DB session id."""
        session = make_session(self.race, past=True)

        gaps = detect_session_data_gaps.fn(2025)

        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].session_id, session.id)

    @mock.patch('analytics.processing.gap_detection.get_run_logger')
    def test_empty_season_returns_empty_list(self, mock_logger):
        """Season with no sessions returns empty list."""
        gaps = detect_session_data_gaps.fn(2025)

        self.assertEqual(gaps, [])
