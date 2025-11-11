"""
Unit tests for Prefect flow tasks.

Tests the weather import flow tasks without running full flows.
All external calls (FastF1, database) are mocked.
"""

from unittest import mock
from datetime import datetime
from django.test import TestCase
from django.utils import timezone
from analytics.models import Season, Race, Session, SessionWeather, SessionLoadStatus
from analytics.flows.import_weather import (
    extract_weather_data,
    save_weather_to_db,
    process_session_weather,
)


class MockFastF1Session:
    """Mock FastF1 Session with weather data"""
    def __init__(self, has_weather=True):
        if has_weather:
            import pandas as pd
            self.weather_data = pd.DataFrame({
                'AirTemp': [25.0, 26.0, 25.5],
                'TrackTemp': [35.0, 36.0, 35.5],
                'Humidity': [60.0, 62.0, 61.0],
                'Pressure': [1013.0, 1013.0, 1013.0],
                'WindSpeed': [2.5, 3.0, 2.8],
                'WindDirection': [180, 185, 182],
                'Rainfall': [False, False, False],
            })
        else:
            self.weather_data = None


class ExtractWeatherDataTests(TestCase):
    """Tests for extract_weather_data task"""
    
    @mock.patch('analytics.flows.import_weather.get_run_logger')
    def test_extracts_all_weather_fields(self, mock_logger):
        """Should extract all available weather fields"""
        mock_session = MockFastF1Session(has_weather=True)
        
        result = extract_weather_data.fn(mock_session)
        
        self.assertIsNotNone(result)
        self.assertIn('air_temperature', result)
        self.assertIn('track_temperature', result)
        self.assertIn('humidity', result)
        self.assertIn('pressure', result)
        self.assertIn('wind_speed', result)
        self.assertIn('wind_direction', result)
        self.assertIn('rainfall', result)
    
    @mock.patch('analytics.flows.import_weather.get_run_logger')
    def test_uses_median_for_temperature(self, mock_logger):
        """Should use median values for robust averaging"""
        mock_session = MockFastF1Session(has_weather=True)
        
        result = extract_weather_data.fn(mock_session)
        
        # Median of [25.0, 26.0, 25.5] = 25.5
        self.assertEqual(result['air_temperature'], 25.5)
        # Median of [35.0, 36.0, 35.5] = 35.5
        self.assertEqual(result['track_temperature'], 35.5)
    
    @mock.patch('analytics.flows.import_weather.get_run_logger')
    def test_handles_no_weather_data(self, mock_logger):
        """Should return None when no weather data available"""
        mock_session = MockFastF1Session(has_weather=False)
        
        result = extract_weather_data.fn(mock_session)
        
        self.assertIsNone(result)
        mock_logger.return_value.warning.assert_called()
    
    @mock.patch('analytics.flows.import_weather.get_run_logger')
    def test_handles_empty_weather_dataframe(self, mock_logger):
        """Should return None for empty DataFrame"""
        import pandas as pd
        mock_session = mock.Mock()
        mock_session.weather_data = pd.DataFrame()
        
        result = extract_weather_data.fn(mock_session)
        
        self.assertIsNone(result)
    
    @mock.patch('analytics.flows.import_weather.get_run_logger')
    def test_handles_missing_columns(self, mock_logger):
        """Should handle DataFrames with missing columns"""
        import pandas as pd
        mock_session = mock.Mock()
        mock_session.weather_data = pd.DataFrame({
            'AirTemp': [25.0, 26.0],
            # Missing other columns
        })
        
        result = extract_weather_data.fn(mock_session)
        
        self.assertIsNotNone(result)
        self.assertIn('air_temperature', result)
        self.assertNotIn('track_temperature', result)


class SaveWeatherToDbTests(TestCase):
    """Tests for save_weather_to_db task"""
    
    def setUp(self):
        """Create test data"""
        self.season = Season.objects.create(year=2025, name='2025 Season')
        self.race = Race.objects.create(
            season=self.season,
            name='Test Grand Prix',
            round_number=1
        )
        self.session = Session.objects.create(
            race=self.race,
            session_number=1,
            session_type='Practice 1'
        )
        self.weather_data = {
            'air_temperature': 25.5,
            'track_temperature': 35.5,
            'humidity': 61.0,
            'pressure': 1013.0,
            'wind_speed': 2.8,
            'wind_direction': 182,
            'rainfall': False,
        }
    
    @mock.patch('analytics.flows.import_weather.get_run_logger')
    @mock.patch('analytics.flows.import_weather.mark_data_loaded')
    def test_creates_new_weather(self, mock_mark, mock_logger):
        """Should create new SessionWeather record"""
        self.assertFalse(SessionWeather.objects.filter(session=self.session).exists())
        
        result = save_weather_to_db.fn(self.session.id, self.weather_data)
        
        self.assertEqual(result['status'], 'success')
        self.assertTrue(result['created'])
        
        weather = SessionWeather.objects.get(session=self.session)
        self.assertEqual(weather.air_temperature, 25.5)
        self.assertEqual(weather.track_temperature, 35.5)
        self.assertEqual(weather.data_source, 'fastf1')
    
    @mock.patch('analytics.flows.import_weather.get_run_logger')
    @mock.patch('analytics.flows.import_weather.mark_data_loaded')
    def test_updates_existing_weather(self, mock_mark, mock_logger):
        """Should update existing SessionWeather record"""
        # Create existing weather
        old_weather = SessionWeather.objects.create(
            session=self.session,
            air_temperature=20.0,
            data_source='manual'
        )
        
        result = save_weather_to_db.fn(self.session.id, self.weather_data)
        
        self.assertEqual(result['status'], 'success')
        self.assertFalse(result['created'])
        
        weather = SessionWeather.objects.get(session=self.session)
        self.assertEqual(weather.id, old_weather.id)  # Same record
        self.assertEqual(weather.air_temperature, 25.5)  # Updated
        self.assertEqual(weather.data_source, 'fastf1')  # Updated
    
    @mock.patch('analytics.flows.import_weather.get_run_logger')
    @mock.patch('analytics.flows.import_weather.mark_data_loaded')
    def test_marks_data_loaded(self, mock_mark, mock_logger):
        """Should call mark_data_loaded with flow_run_id"""
        flow_run_id = 'test-flow-123'
        
        save_weather_to_db.fn(self.session.id, self.weather_data, flow_run_id)
        
        mock_mark.assert_called_once()
        call_args = mock_mark.call_args
        self.assertEqual(call_args[0][0], self.session.id)
        self.assertEqual(call_args[0][1], 'weather')
    
    @mock.patch('analytics.flows.import_weather.get_run_logger')
    def test_handles_invalid_session(self, mock_logger):
        """Should handle invalid session ID gracefully"""
        result = save_weather_to_db.fn(99999, self.weather_data)
        
        self.assertEqual(result['status'], 'failed')
        self.assertIn('error', result)


class ProcessSessionWeatherTests(TestCase):
    """Tests for process_session_weather task"""
    
    def setUp(self):
        """Create test data"""
        self.season = Season.objects.create(year=2025, name='2025 Season')
        self.race = Race.objects.create(
            season=self.season,
            name='Test Grand Prix',
            round_number=1
        )
        self.session = Session.objects.create(
            race=self.race,
            session_number=1,
            session_type='Practice 1'
        )
        self.session_info = {
            'session_id': self.session.id,
            'year': 2025,
            'round_number': 1,
            'session_type': 'Practice 1',
            'event_format': 'conventional',
            'event_name': 'Test Grand Prix',
        }
    
    @mock.patch('analytics.flows.import_weather.get_run_logger')
    @mock.patch('analytics.flows.import_weather.save_weather_to_db')
    @mock.patch('analytics.flows.import_weather.extract_weather_data')
    @mock.patch('analytics.flows.import_weather.load_fastf1_session')
    def test_processes_regular_race_session(self, mock_load, mock_extract, mock_save, mock_logger):
        """Should process regular race session successfully"""
        mock_session = MockFastF1Session(has_weather=True)
        mock_load.return_value = mock_session
        mock_extract.return_value = {'air_temperature': 25.0}
        mock_save.return_value = {'status': 'success', 'created': True}
        
        result = process_session_weather.fn(self.session_info)
        
        self.assertEqual(result['status'], 'success')
        mock_load.assert_called_once()
        # Should pass None for event_name (regular race)
        self.assertIsNone(mock_load.call_args[1]['event_name'])
    
    @mock.patch('analytics.flows.import_weather.get_run_logger')
    @mock.patch('analytics.flows.import_weather.save_weather_to_db')
    @mock.patch('analytics.flows.import_weather.extract_weather_data')
    @mock.patch('analytics.flows.import_weather.load_fastf1_session')
    def test_processes_testing_event(self, mock_load, mock_extract, mock_save, mock_logger):
        """Should process testing event with event name"""
        # Update session info for testing event
        testing_info = self.session_info.copy()
        testing_info['event_format'] = 'testing'
        testing_info['event_name'] = 'Pre-Season Testing'
        testing_info['round_number'] = 0
        
        mock_session = MockFastF1Session(has_weather=True)
        mock_load.return_value = mock_session
        mock_extract.return_value = {'air_temperature': 25.0}
        mock_save.return_value = {'status': 'success', 'created': True}
        
        result = process_session_weather.fn(testing_info)
        
        self.assertEqual(result['status'], 'success')
        # Should pass event name for testing event
        self.assertEqual(mock_load.call_args[1]['event_name'], 'Pre-Season Testing')
    
    @mock.patch('analytics.flows.import_weather.get_run_logger')
    @mock.patch('analytics.flows.import_weather.extract_weather_data')
    @mock.patch('analytics.flows.import_weather.load_fastf1_session')
    def test_handles_no_weather_data(self, mock_load, mock_extract, mock_logger):
        """Should handle sessions with no weather data"""
        mock_session = MockFastF1Session(has_weather=False)
        mock_load.return_value = mock_session
        mock_extract.return_value = None
        
        result = process_session_weather.fn(self.session_info)
        
        self.assertEqual(result['status'], 'no_data')
        self.assertIn('message', result)
    
    @mock.patch('analytics.flows.import_weather.get_run_logger')
    @mock.patch('analytics.flows.import_weather.load_fastf1_session')
    def test_handles_non_retryable_error(self, mock_load, mock_logger):
        """Should handle NonRetryableError as no_data"""
        from analytics.processing.loaders import NonRetryableError
        
        mock_load.side_effect = NonRetryableError("Testing event not found")
        
        result = process_session_weather.fn(self.session_info)
        
        self.assertEqual(result['status'], 'no_data')
        self.assertIn('message', result)
        mock_logger.return_value.warning.assert_called()
    
    @mock.patch('analytics.flows.import_weather.get_run_logger')
    @mock.patch('analytics.flows.import_weather.load_fastf1_session')
    def test_handles_generic_error(self, mock_load, mock_logger):
        """Should handle generic errors as error status"""
        mock_load.side_effect = Exception("Connection timeout")
        
        result = process_session_weather.fn(self.session_info)
        
        self.assertEqual(result['status'], 'error')
        self.assertIn('error', result)
        mock_logger.return_value.error.assert_called()
