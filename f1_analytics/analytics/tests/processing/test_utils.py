"""
Unit tests for processing utility functions.

Tests gap detection, session queries, and helper functions.

IMPORTANT: No external API calls are made:
- All Prefect logging is mocked (get_run_logger)
- Database queries use Django TestCase with real models (isolated transactions)
- No FastF1 or network requests
- Helper functions are pure (no side effects)
"""

from unittest import mock
from datetime import datetime
from django.test import TestCase
from django.utils import timezone
from analytics.models import Season, Race, Session, SessionLoadStatus
from analytics.processing.utils import (
    get_sessions_without_data,
    discover_data_gaps,
    get_or_create_load_status,
    mark_data_loaded,
    get_fastf1_session_identifier,
    generate_session_cache_key,
)


class GetSessionsWithoutDataTests(TestCase):
    """Tests for get_sessions_without_data task"""
    
    def setUp(self):
        """Create test data"""
        self.season = Season.objects.create(year=2025, name='2025 Season')
        self.race = Race.objects.create(
            season=self.season,
            name='Test Grand Prix',
            round_number=1
        )
        self.session1 = Session.objects.create(
            race=self.race,
            session_number=1,
            session_type='Practice 1'
        )
        self.session2 = Session.objects.create(
            race=self.race,
            session_number=2,
            session_type='Qualifying'
        )
    
    @mock.patch('analytics.processing.utils.get_run_logger')
    def test_no_season(self, mock_logger):
        """Should return empty list for non-existent season"""
        result = get_sessions_without_data(2099, 'weather', force=False)
        
        self.assertEqual(result, [])
        mock_logger.return_value.error.assert_called()
    
    @mock.patch('analytics.processing.utils.get_run_logger')
    def test_all_sessions_missing_data(self, mock_logger):
        """Should return all sessions when none have data"""
        result = get_sessions_without_data(2025, 'weather', force=False)
        
        self.assertEqual(len(result), 2)
        self.assertIn(self.session1.id, [s['session_id'] for s in result])
        self.assertIn(self.session2.id, [s['session_id'] for s in result])
    
    @mock.patch('analytics.processing.utils.get_run_logger')
    def test_some_sessions_have_data(self, mock_logger):
        """Should return only sessions without data"""
        # Give session1 weather data
        status1 = SessionLoadStatus.objects.create(
            session=self.session1,
            has_weather=True
        )
        
        result = get_sessions_without_data(2025, 'weather', force=False)
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['session_id'], self.session2.id)
        self.assertFalse(result[0]['has_data'])
    
    @mock.patch('analytics.processing.utils.get_run_logger')
    def test_all_sessions_have_data(self, mock_logger):
        """Should return empty list when all sessions have data"""
        # Give both sessions weather data
        SessionLoadStatus.objects.create(
            session=self.session1,
            has_weather=True
        )
        SessionLoadStatus.objects.create(
            session=self.session2,
            has_weather=True
        )
        
        result = get_sessions_without_data(2025, 'weather', force=False)
        
        self.assertEqual(len(result), 0)
    
    @mock.patch('analytics.processing.utils.get_run_logger')
    def test_force_returns_all(self, mock_logger):
        """Should return all sessions when force=True"""
        # Give session1 weather data
        SessionLoadStatus.objects.create(
            session=self.session1,
            has_weather=True
        )
        
        result = get_sessions_without_data(2025, 'weather', force=True)
        
        self.assertEqual(len(result), 2)
    
    @mock.patch('analytics.processing.utils.get_run_logger')
    def test_different_data_types(self, mock_logger):
        """Should correctly check different data types"""
        status = SessionLoadStatus.objects.create(
            session=self.session1,
            has_weather=True,
            has_circuit=False,
            has_lap_times=False
        )
        
        # Check weather - should not include session1
        weather_result = get_sessions_without_data(2025, 'weather', force=False)
        self.assertEqual(len(weather_result), 1)
        self.assertEqual(weather_result[0]['session_id'], self.session2.id)
        
        # Check circuit - should include both
        circuit_result = get_sessions_without_data(2025, 'circuit', force=False)
        self.assertEqual(len(circuit_result), 2)
        
        # Check laps - should include both
        laps_result = get_sessions_without_data(2025, 'laps', force=False)
        self.assertEqual(len(laps_result), 2)


class DiscoverDataGapsTests(TestCase):
    """Tests for discover_data_gaps task"""
    
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
    
    @mock.patch('analytics.processing.utils.get_run_logger')
    @mock.patch('analytics.processing.utils.get_sessions_without_data')
    def test_discover_all_data_types(self, mock_get_sessions, mock_logger):
        """Should check all data types by default"""
        mock_get_sessions.return_value = []
        
        result = discover_data_gaps(2025)
        
        self.assertIn('circuit', result)
        self.assertIn('weather', result)
        self.assertIn('laps', result)
        self.assertIn('telemetry', result)
        
        # Should have called get_sessions_without_data 4 times
        self.assertEqual(mock_get_sessions.call_count, 4)
    
    @mock.patch('analytics.processing.utils.get_run_logger')
    @mock.patch('analytics.processing.utils.get_sessions_without_data')
    def test_discover_specific_data_types(self, mock_get_sessions, mock_logger):
        """Should check only specified data types"""
        mock_get_sessions.return_value = []
        
        result = discover_data_gaps(2025, data_types=['weather', 'laps'])
        
        self.assertIn('weather', result)
        self.assertIn('laps', result)
        self.assertNotIn('circuit', result)
        self.assertNotIn('telemetry', result)
        
        # Should have called get_sessions_without_data 2 times
        self.assertEqual(mock_get_sessions.call_count, 2)


class GetOrCreateLoadStatusTests(TestCase):
    """Tests for get_or_create_load_status task"""
    
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
    
    def test_creates_status_if_not_exists(self):
        """Should create SessionLoadStatus if it doesn't exist"""
        self.assertFalse(SessionLoadStatus.objects.filter(session=self.session).exists())
        
        status = get_or_create_load_status(self.session.id)
        
        self.assertIsNotNone(status)
        self.assertEqual(status.session, self.session)
        self.assertTrue(SessionLoadStatus.objects.filter(session=self.session).exists())
    
    def test_returns_existing_status(self):
        """Should return existing SessionLoadStatus"""
        existing = SessionLoadStatus.objects.create(
            session=self.session,
            has_weather=True
        )
        
        status = get_or_create_load_status(self.session.id)
        
        self.assertEqual(status.id, existing.id)
        self.assertTrue(status.has_weather)
    
    def test_invalid_session_id(self):
        """Should raise error for invalid session ID"""
        with self.assertRaises(ValueError):
            get_or_create_load_status(99999)


class MarkDataLoadedTests(TestCase):
    """Tests for mark_data_loaded task"""
    
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
    
    @mock.patch('analytics.processing.utils.get_run_logger')
    def test_marks_weather_loaded(self, mock_logger):
        """Should mark weather as loaded"""
        mark_data_loaded(self.session.id, 'weather')
        
        status = SessionLoadStatus.objects.get(session=self.session)
        self.assertTrue(status.has_weather)
        self.assertIsNotNone(status.weather_loaded_at)
        self.assertFalse(status.has_circuit)
        self.assertFalse(status.has_lap_times)
    
    @mock.patch('analytics.processing.utils.get_run_logger')
    def test_marks_circuit_loaded(self, mock_logger):
        """Should mark circuit as loaded"""
        mark_data_loaded(self.session.id, 'circuit')
        
        status = SessionLoadStatus.objects.get(session=self.session)
        self.assertTrue(status.has_circuit)
        self.assertIsNotNone(status.circuit_loaded_at)
    
    @mock.patch('analytics.processing.utils.get_run_logger')
    def test_marks_laps_loaded(self, mock_logger):
        """Should mark laps as loaded"""
        mark_data_loaded(self.session.id, 'laps')
        
        status = SessionLoadStatus.objects.get(session=self.session)
        self.assertTrue(status.has_lap_times)
        self.assertIsNotNone(status.laps_loaded_at)
    
    @mock.patch('analytics.processing.utils.get_run_logger')
    def test_marks_telemetry_loaded(self, mock_logger):
        """Should mark telemetry as loaded"""
        mark_data_loaded(self.session.id, 'telemetry')
        
        status = SessionLoadStatus.objects.get(session=self.session)
        self.assertTrue(status.has_telemetry)
        self.assertIsNotNone(status.telemetry_loaded_at)
    
    @mock.patch('analytics.processing.utils.get_run_logger')
    def test_stores_flow_run_id(self, mock_logger):
        """Should store Prefect flow run ID in metadata"""
        mark_data_loaded(self.session.id, 'weather', flow_run_id='abc-123')
        
        status = SessionLoadStatus.objects.get(session=self.session)
        self.assertTrue(status.has_weather)
        self.assertIn('load_history', status.prefect_metadata)
        self.assertEqual(status.prefect_metadata['last_flow_run_id'], 'abc-123')
    
    @mock.patch('analytics.processing.utils.get_run_logger')
    def test_invalid_session_id(self, mock_logger):
        """Should log error for invalid session ID"""
        mark_data_loaded(99999, 'weather')
        
        mock_logger.return_value.error.assert_called()


class HelperFunctionTests(TestCase):
    """Tests for helper functions"""
    
    def test_get_fastf1_session_identifier(self):
        """Should convert Django session types to FastF1 identifiers"""
        self.assertEqual(get_fastf1_session_identifier('Practice 1'), 'FP1')
        self.assertEqual(get_fastf1_session_identifier('Practice 2'), 'FP2')
        self.assertEqual(get_fastf1_session_identifier('Practice 3'), 'FP3')
        self.assertEqual(get_fastf1_session_identifier('Qualifying'), 'Q')
        self.assertEqual(get_fastf1_session_identifier('Sprint Qualifying'), 'SQ')
        self.assertEqual(get_fastf1_session_identifier('Sprint'), 'S')
        self.assertEqual(get_fastf1_session_identifier('Race'), 'R')
    
    def test_get_fastf1_session_identifier_default(self):
        """Should return 'R' for unknown session types"""
        self.assertEqual(get_fastf1_session_identifier('Unknown'), 'R')
        self.assertEqual(get_fastf1_session_identifier(''), 'R')
    
    def test_generate_session_cache_key(self):
        """Should generate consistent cache keys"""
        key1 = generate_session_cache_key(2025, 1, 'FP1')
        self.assertEqual(key1, 'fastf1_session_2025_1_FP1')
        
        key2 = generate_session_cache_key(2024, 10, 'R')
        self.assertEqual(key2, 'fastf1_session_2024_10_R')
    
    def test_generate_session_cache_key_consistency(self):
        """Should generate same key for same inputs"""
        key1 = generate_session_cache_key(2025, 5, 'Q')
        key2 = generate_session_cache_key(2025, 5, 'Q')
        self.assertEqual(key1, key2)
    
    def test_generate_session_cache_key_uniqueness(self):
        """Should generate different keys for different inputs"""
        key1 = generate_session_cache_key(2025, 1, 'FP1')
        key2 = generate_session_cache_key(2025, 1, 'FP2')
        key3 = generate_session_cache_key(2025, 2, 'FP1')
        key4 = generate_session_cache_key(2024, 1, 'FP1')
        
        self.assertNotEqual(key1, key2)
        self.assertNotEqual(key1, key3)
        self.assertNotEqual(key1, key4)
