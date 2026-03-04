"""
Tests for extract_lap_data and save_telemetry_to_db tasks.
"""

import pandas as pd
from unittest import mock
from django.test import TestCase
from analytics.models import Lap
from analytics.flows.import_telemetry import (
    extract_lap_data,
    save_telemetry_to_db,
    _to_seconds,
)
from analytics.tests.factories import make_season, make_race, make_session, make_driver


def _make_lap_dict(driver_number='1', full_name='Max Verstappen', lap_number=1, lap_time=90.0,
                   driver_abbr='VER'):
    """Build a minimal lap data dict matching what save_telemetry_to_db expects."""
    return {
        'driver_number': driver_number,
        'full_name': full_name,
        'driver_abbr': driver_abbr,
        'team_abbr': '',
        'lap_number': lap_number,
        'lap_time': lap_time,
        'sector_1_time': None,
        'sector_2_time': None,
        'sector_3_time': None,
        'compound': 'SOFT',
        'tire_life': 5,
        'fresh_tire': False,
        'track_status': '1',
        'position': 1,
        'pit_out_time': None,
        'pit_in_time': None,
        'speed_i1': None,
        'speed_i2': None,
        'speed_fl': None,
        'speed_st': None,
        'is_personal_best': False,
        'is_accurate': True,
        'lap_start_time': None,
    }


class ToSecondsTests(TestCase):
    def test_converts_timedelta(self):
        from datetime import timedelta
        val = timedelta(seconds=90, milliseconds=500)
        self.assertAlmostEqual(_to_seconds(val), 90.5)

    def test_returns_none_for_nat(self):
        result = _to_seconds(pd.NaT)
        self.assertIsNone(result)

    def test_returns_none_for_none(self):
        result = _to_seconds(None)
        self.assertIsNone(result)


class ExtractLapDataTests(TestCase):
    @mock.patch('analytics.flows.import_telemetry.get_run_logger')
    def test_extracts_laps_list(self, mock_logger):
        """Should return a dict with a 'laps' list when data is present."""
        from datetime import timedelta

        mock_session = mock.Mock()
        mock_session.laps = pd.DataFrame([{
            'DriverNumber': '1',
            'Driver': 'VER',
            'LapNumber': 1,
            'LapTime': timedelta(seconds=90),
            'Sector1Time': None,
            'Sector2Time': None,
            'Sector3Time': None,
            'Compound': 'SOFT',
            'TyreLife': 5,
            'FreshTyre': False,
            'TrackStatus': '1',
            'Position': 1,
            'PitOutTime': None,
            'PitInTime': None,
            'SpeedI1': None,
            'SpeedI2': None,
            'SpeedFL': None,
            'SpeedST': None,
            'IsPersonalBest': False,
            'IsAccurate': True,
            'LapStartTime': None,
            'Team': '',
        }])
        mock_session.results = pd.DataFrame([{
            'Abbreviation': 'VER',
            'FullName': 'Max Verstappen',
            'DriverNumber': '1',
        }])

        with mock.patch('analytics.flows.extract_pit_stops.extract_pit_stops_from_session', return_value=[]):
            result = extract_lap_data.fn(mock_session)

        self.assertIsNotNone(result)
        self.assertIn('laps', result)
        self.assertEqual(len(result['laps']), 1)

    @mock.patch('analytics.flows.import_telemetry.get_run_logger')
    def test_empty_laps_returns_none(self, mock_logger):
        """Should return None when session has no lap data."""
        mock_session = mock.Mock()
        mock_session.laps = pd.DataFrame()

        result = extract_lap_data.fn(mock_session)

        self.assertIsNone(result)

    @mock.patch('analytics.flows.import_telemetry.get_run_logger')
    def test_converts_timedelta_to_seconds(self, mock_logger):
        """Timedelta lap times should be stored as float seconds."""
        from datetime import timedelta

        mock_session = mock.Mock()
        mock_session.laps = pd.DataFrame([{
            'DriverNumber': '1',
            'Driver': 'VER',
            'LapNumber': 1,
            'LapTime': timedelta(seconds=91, milliseconds=234),
            'Sector1Time': None,
            'Sector2Time': None,
            'Sector3Time': None,
            'Compound': 'SOFT',
            'TyreLife': 5,
            'FreshTyre': False,
            'TrackStatus': '1',
            'Position': 1,
            'PitOutTime': None,
            'PitInTime': None,
            'SpeedI1': None,
            'SpeedI2': None,
            'SpeedFL': None,
            'SpeedST': None,
            'IsPersonalBest': False,
            'IsAccurate': True,
            'LapStartTime': None,
            'Team': '',
        }])
        mock_session.results = pd.DataFrame(columns=[
            'Abbreviation', 'FullName', 'DriverNumber',
        ])

        with mock.patch('analytics.flows.extract_pit_stops.extract_pit_stops_from_session', return_value=[]):
            result = extract_lap_data.fn(mock_session)

        self.assertAlmostEqual(result['laps'][0]['lap_time'], 91.234, places=2)


class SaveTelemetryToDbTests(TestCase):
    def setUp(self):
        season = make_season()
        race = make_race(season)
        self.session = make_session(race)
        self.driver = make_driver()

    @mock.patch('analytics.flows.import_telemetry.get_run_logger')
    @mock.patch('analytics.flows.import_telemetry.mark_data_loaded')
    def test_creates_lap_records_for_known_driver(self, mock_mark, mock_logger):
        """Should create Lap records for a driver that exists in the DB."""
        telemetry_data = {'laps': [_make_lap_dict()], 'pit_stops': []}

        result = save_telemetry_to_db.fn(self.session.id, telemetry_data)

        self.assertEqual(result['status'], 'success')
        self.assertEqual(Lap.objects.filter(session=self.session).count(), 1)

    @mock.patch('analytics.flows.import_telemetry.get_run_logger')
    @mock.patch('analytics.flows.import_telemetry.mark_data_loaded')
    def test_skips_laps_for_unknown_driver(self, mock_mark, mock_logger):
        """Should skip laps when the driver cannot be found in the DB."""
        telemetry_data = {
            'laps': [_make_lap_dict(driver_number='99', full_name='Nobody Known', driver_abbr='UNK')],
            'pit_stops': [],
        }

        result = save_telemetry_to_db.fn(self.session.id, telemetry_data)

        self.assertEqual(result['status'], 'success')
        self.assertEqual(Lap.objects.filter(session=self.session).count(), 0)

    @mock.patch('analytics.flows.import_telemetry.get_run_logger')
    @mock.patch('analytics.flows.import_telemetry.mark_data_loaded')
    def test_marks_fastest_lap(self, mock_mark, mock_logger):
        """Lap with the lowest lap_time should have is_fastest_lap=True."""
        telemetry_data = {
            'laps': [
                _make_lap_dict(lap_number=1, lap_time=92.5),
                _make_lap_dict(lap_number=2, lap_time=91.0),
            ],
            'pit_stops': [],
        }

        save_telemetry_to_db.fn(self.session.id, telemetry_data)

        fastest = Lap.objects.get(session=self.session, lap_number=2)
        slow = Lap.objects.get(session=self.session, lap_number=1)
        self.assertTrue(fastest.is_fastest_lap)
        self.assertFalse(slow.is_fastest_lap)

    @mock.patch('analytics.flows.import_telemetry.get_run_logger')
    @mock.patch('analytics.flows.import_telemetry.mark_data_loaded')
    def test_update_existing_lap_does_not_duplicate(self, mock_mark, mock_logger):
        """Calling save twice with the same lap should update, not create a duplicate."""
        telemetry_data = {'laps': [_make_lap_dict(lap_time=90.0)], 'pit_stops': []}

        save_telemetry_to_db.fn(self.session.id, telemetry_data)
        save_telemetry_to_db.fn(self.session.id, telemetry_data)

        self.assertEqual(Lap.objects.filter(session=self.session).count(), 1)

    @mock.patch('analytics.flows.import_telemetry.get_run_logger')
    def test_invalid_session_id_returns_failed(self, mock_logger):
        """Should return failed status for an unknown session ID."""
        telemetry_data = {'laps': [], 'pit_stops': []}

        result = save_telemetry_to_db.fn(99999, telemetry_data)

        self.assertEqual(result['status'], 'failed')
        self.assertIn('error', result)

    @mock.patch('analytics.flows.import_telemetry.get_run_logger')
    @mock.patch('analytics.flows.import_telemetry.mark_data_loaded')
    def test_returns_counts_dict(self, mock_mark, mock_logger):
        """Returned dict should include laps_created, telemetry_created, pit_stops_created."""
        telemetry_data = {'laps': [], 'pit_stops': []}

        result = save_telemetry_to_db.fn(self.session.id, telemetry_data)

        self.assertEqual(result['status'], 'success')
        self.assertIn('laps_created', result)
        self.assertIn('telemetry_created', result)
        self.assertIn('pit_stops_created', result)
