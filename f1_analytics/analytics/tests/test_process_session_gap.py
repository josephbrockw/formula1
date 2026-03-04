"""
Tests for process_session_gap task (orchestration layer).

Mocks all extract/save subtasks and load_fastf1_session.
Uses real DB session via factories.
"""

from unittest import mock
from django.test import TestCase
from analytics.flows.import_fastf1 import process_session_gap
from analytics.processing.gap_detection import SessionGap
from analytics.tests.factories import make_season, make_race, make_session


def _make_gap(session_id, **kwargs):
    """Build a minimal SessionGap."""
    defaults = dict(
        session_id=session_id,
        year=2025,
        round_number=1,
        session_type='Race',
        session_number=5,
        missing_weather=True,
        missing_drivers=True,
        missing_telemetry=True,
        missing_pit_stops=True,
        missing_circuit=True,
    )
    defaults.update(kwargs)
    return SessionGap(**defaults)


def _success(data_type=None):
    """Build a successful save-result dict."""
    base = {'status': 'success', 'counts': {}}
    if data_type == 'drivers':
        return {**base, 'drivers_created': 0, 'drivers_updated': 0,
                'drivers_skipped': 0, 'results_created': 1}
    if data_type == 'telemetry':
        return {**base, 'laps_created': 1, 'telemetry_created': 0, 'pit_stops_created': 0}
    return base


@mock.patch('analytics.flows.import_fastf1.get_run_logger')
@mock.patch('analytics.flows.import_fastf1.save_telemetry_to_db')
@mock.patch('analytics.flows.import_fastf1.extract_lap_data')
@mock.patch('analytics.flows.import_fastf1.save_circuit_to_db')
@mock.patch('analytics.flows.import_fastf1.extract_circuit_data')
@mock.patch('analytics.flows.import_fastf1.save_weather_to_db')
# extract_weather_data is locally re-imported inside process_session_gap, so we
# must mock the source module so the local `from X import y` picks up the mock.
@mock.patch('analytics.flows.import_weather.extract_weather_data')
@mock.patch('analytics.flows.import_fastf1.save_driver_info_to_db')
@mock.patch('analytics.flows.import_fastf1.extract_driver_info')
@mock.patch('analytics.flows.import_fastf1.load_fastf1_session')
class ProcessSessionGapTests(TestCase):
    def setUp(self):
        season = make_season()
        race = make_race(season)
        self.session = make_session(race)

    def _configure_all_success(self, mock_load, mock_extract_drivers, mock_save_drivers,
                                mock_extract_weather, mock_save_weather,
                                mock_extract_circuit, mock_save_circuit,
                                mock_extract_telemetry, mock_save_telemetry):
        mock_load.return_value = mock.Mock()
        mock_extract_drivers.fn.return_value = {'drivers': [], 'safety_car_laps': 0}
        mock_save_drivers.fn.return_value = _success('drivers')
        mock_extract_weather.fn.return_value = {'air_temperature': 25.0}
        mock_save_weather.fn.return_value = _success()
        mock_extract_circuit.fn.return_value = {'corners': [], 'marshal_lights': [], 'marshal_sectors': []}
        mock_save_circuit.fn.return_value = _success()
        mock_extract_telemetry.fn.return_value = {'laps': [], 'pit_stops': []}
        mock_save_telemetry.fn.return_value = _success('telemetry')

    def test_no_session_id_returns_skipped(
        self, mock_load, mock_extract_drivers, mock_save_drivers,
        mock_extract_weather, mock_save_weather,
        mock_extract_circuit, mock_save_circuit,
        mock_extract_telemetry, mock_save_telemetry, mock_logger,
    ):
        """Gap with session_id=None should return status='skipped'."""
        gap = _make_gap(session_id=None)

        result = process_session_gap.fn(gap)

        self.assertEqual(result['status'], 'skipped')
        mock_load.assert_not_called()

    def test_session_load_failure_returns_failed(
        self, mock_load, mock_extract_drivers, mock_save_drivers,
        mock_extract_weather, mock_save_weather,
        mock_extract_circuit, mock_save_circuit,
        mock_extract_telemetry, mock_save_telemetry, mock_logger,
    ):
        """When load_fastf1_session raises, result status should be 'failed'."""
        mock_load.side_effect = Exception('Network error')
        gap = _make_gap(session_id=self.session.id)

        result = process_session_gap.fn(gap)

        self.assertEqual(result['status'], 'failed')
        self.assertIn('error', result)

    def test_all_extractions_succeed_returns_success(
        self, mock_load, mock_extract_drivers, mock_save_drivers,
        mock_extract_weather, mock_save_weather,
        mock_extract_circuit, mock_save_circuit,
        mock_extract_telemetry, mock_save_telemetry, mock_logger,
    ):
        """When all extract/save steps succeed, result status should be 'success'."""
        self._configure_all_success(
            mock_load, mock_extract_drivers, mock_save_drivers,
            mock_extract_weather, mock_save_weather,
            mock_extract_circuit, mock_save_circuit,
            mock_extract_telemetry, mock_save_telemetry,
        )
        gap = _make_gap(session_id=self.session.id)

        result = process_session_gap.fn(gap)

        self.assertEqual(result['status'], 'success')

    def test_weather_save_fails_marks_weather_in_failed(
        self, mock_load, mock_extract_drivers, mock_save_drivers,
        mock_extract_weather, mock_save_weather,
        mock_extract_circuit, mock_save_circuit,
        mock_extract_telemetry, mock_save_telemetry, mock_logger,
    ):
        """When weather save returns failed, 'weather' should be in result['failed']."""
        self._configure_all_success(
            mock_load, mock_extract_drivers, mock_save_drivers,
            mock_extract_weather, mock_save_weather,
            mock_extract_circuit, mock_save_circuit,
            mock_extract_telemetry, mock_save_telemetry,
        )
        mock_save_weather.fn.return_value = {'status': 'failed', 'error': 'DB error'}
        gap = _make_gap(session_id=self.session.id)

        result = process_session_gap.fn(gap)

        self.assertIn('weather', result['failed'])

    def test_weather_extraction_returns_none_not_in_extracted_or_failed(
        self, mock_load, mock_extract_drivers, mock_save_drivers,
        mock_extract_weather, mock_save_weather,
        mock_extract_circuit, mock_save_circuit,
        mock_extract_telemetry, mock_save_telemetry, mock_logger,
    ):
        """When extract_weather_data returns None, weather is NOT in extracted (goes to failed path)."""
        self._configure_all_success(
            mock_load, mock_extract_drivers, mock_save_drivers,
            mock_extract_weather, mock_save_weather,
            mock_extract_circuit, mock_save_circuit,
            mock_extract_telemetry, mock_save_telemetry,
        )
        mock_extract_weather.fn.return_value = None
        gap = _make_gap(session_id=self.session.id)

        result = process_session_gap.fn(gap)

        self.assertNotIn('weather', result['extracted'])

    def test_extracted_list_accurate(
        self, mock_load, mock_extract_drivers, mock_save_drivers,
        mock_extract_weather, mock_save_weather,
        mock_extract_circuit, mock_save_circuit,
        mock_extract_telemetry, mock_save_telemetry, mock_logger,
    ):
        """extracted list should contain all data types that saved successfully."""
        self._configure_all_success(
            mock_load, mock_extract_drivers, mock_save_drivers,
            mock_extract_weather, mock_save_weather,
            mock_extract_circuit, mock_save_circuit,
            mock_extract_telemetry, mock_save_telemetry,
        )
        gap = _make_gap(session_id=self.session.id)

        result = process_session_gap.fn(gap)

        for data_type in ('drivers', 'weather', 'circuit', 'telemetry'):
            self.assertIn(data_type, result['extracted'])

    def test_failed_list_accurate(
        self, mock_load, mock_extract_drivers, mock_save_drivers,
        mock_extract_weather, mock_save_weather,
        mock_extract_circuit, mock_save_circuit,
        mock_extract_telemetry, mock_save_telemetry, mock_logger,
    ):
        """failed list should contain data types where save returned non-success."""
        self._configure_all_success(
            mock_load, mock_extract_drivers, mock_save_drivers,
            mock_extract_weather, mock_save_weather,
            mock_extract_circuit, mock_save_circuit,
            mock_extract_telemetry, mock_save_telemetry,
        )
        mock_save_telemetry.fn.return_value = {'status': 'failed', 'error': 'db error'}
        mock_save_circuit.fn.return_value = {'status': 'failed', 'error': 'no circuit'}
        gap = _make_gap(session_id=self.session.id)

        result = process_session_gap.fn(gap)

        self.assertIn('telemetry', result['failed'])
        self.assertIn('circuit', result['failed'])
        self.assertNotIn('weather', result['failed'])
        self.assertNotIn('drivers', result['failed'])
