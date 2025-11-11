"""
Unit tests for FastF1 session loaders.

Tests session loading with Prefect caching and rate limit integration.

IMPORTANT: All FastF1 API calls are mocked to prevent:
- Real network requests
- Rate limit consumption
- Slow test execution
- External dependencies

Every test that calls load_fastf1_session MUST mock fastf1.get_session.
"""

from unittest import mock
from datetime import datetime
from django.test import TestCase
from django.utils import timezone
import fastf1
from analytics.models import Season, Race, Session, SessionLoadStatus
from analytics.processing.loaders import (
    load_fastf1_session,
    check_session_loadable,
    get_session_info,
    session_cache_key_fn,
)


class MockFastF1Session:
    """Mock FastF1 Session object"""
    def __init__(self, name='Test Session', date=None):
        self.name = name
        self.date = date or datetime(2025, 3, 16, 5, 0, 0)
        self._loaded = False
    
    def load(self):
        """Mock load method"""
        self._loaded = True


class SessionCacheKeyTests(TestCase):
    """Tests for session cache key generation"""
    
    def test_cache_key_generation(self):
        """Should generate cache key from parameters"""
        context = mock.Mock()
        parameters = {
            'year': 2025,
            'round_num': 1,
            'session_type': 'Practice 1'
        }
        
        key = session_cache_key_fn(context, parameters)
        
        self.assertEqual(key, 'fastf1_session_2025_1_Practice 1')
    
    def test_cache_key_consistency(self):
        """Should generate same key for same parameters"""
        context = mock.Mock()
        params1 = {'year': 2025, 'round_num': 1, 'session_type': 'Race'}
        params2 = {'year': 2025, 'round_num': 1, 'session_type': 'Race'}
        
        key1 = session_cache_key_fn(context, params1)
        key2 = session_cache_key_fn(context, params2)
        
        self.assertEqual(key1, key2)
    
    def test_cache_key_uniqueness(self):
        """Should generate different keys for different parameters"""
        context = mock.Mock()
        params1 = {'year': 2025, 'round_num': 1, 'session_type': 'Race'}
        params2 = {'year': 2025, 'round_num': 2, 'session_type': 'Race'}
        
        key1 = session_cache_key_fn(context, params1)
        key2 = session_cache_key_fn(context, params2)
        
        self.assertNotEqual(key1, key2)


class LoadFastF1SessionTests(TestCase):
    """Tests for load_fastf1_session task"""
    
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
    
    @mock.patch('analytics.processing.loaders.get_run_logger')
    @mock.patch('analytics.processing.loaders.fastf1.get_session')
    def test_loads_session_successfully(self, mock_get_session, mock_logger):
        """Should load FastF1 session and record API call"""
        mock_f1_session = MockFastF1Session()
        mock_get_session.return_value = mock_f1_session
        
        result = load_fastf1_session.fn(
            year=2025,
            round_num=1,
            session_type='Practice 1',
            session_id=self.session.id
        )
        
        self.assertIsNotNone(result)
        self.assertTrue(result._loaded)
        mock_get_session.assert_called_once_with(2025, 1, 'FP1')
        # Verify session was loaded
        mock_logger.return_value.info.assert_called()
    
    @mock.patch('analytics.processing.loaders.get_run_logger')
    @mock.patch('analytics.processing.loaders.fastf1.get_session')
    def test_converts_session_type_correctly(self, mock_get_session, mock_logger):
        """Should convert Django session type to FastF1 identifier"""
        mock_f1_session = MockFastF1Session()
        mock_get_session.return_value = mock_f1_session
        
        # Test different session types
        test_cases = [
            ('Practice 1', 'FP1'),
            ('Practice 2', 'FP2'),
            ('Practice 3', 'FP3'),
            ('Qualifying', 'Q'),
            ('Sprint Qualifying', 'SQ'),
            ('Sprint', 'S'),
            ('Race', 'R'),
        ]
        
        for django_type, fastf1_id in test_cases:
            self.session.session_type = django_type
            self.session.save()
            
            # Call underlying function directly
            load_fastf1_session.fn(
                year=2025,
                round_num=1,
                session_type=django_type,
                session_id=self.session.id
            )
            
            # Check that FastF1 was called with correct identifier
            call_args = mock_get_session.call_args_list[-1]
            self.assertEqual(call_args[0][2], fastf1_id)
    
    @mock.patch('analytics.processing.loaders.get_run_logger')
    @mock.patch('analytics.processing.loaders.fastf1.get_session')
    def test_handles_rate_limit_error(self, mock_get_session, mock_logger):
        """Should raise RateLimitExceededError"""
        mock_get_session.side_effect = fastf1.req.RateLimitExceededError('rate limit')
        
        with self.assertRaises(fastf1.req.RateLimitExceededError):
            load_fastf1_session.fn(
                year=2025,
                round_num=1,
                session_type='Practice 1',
                session_id=self.session.id
            )
        
        mock_logger.return_value.error.assert_called()
    
    @mock.patch('analytics.processing.loaders.get_run_logger')
    @mock.patch('analytics.processing.loaders.fastf1.get_session')
    def test_handles_generic_error(self, mock_get_session, mock_logger):
        """Should raise and log generic errors"""
        mock_get_session.side_effect = Exception('Connection error')
        
        with self.assertRaises(Exception):
            load_fastf1_session.fn(
                year=2025,
                round_num=1,
                session_type='Practice 1',
                session_id=self.session.id
            )
        
        mock_logger.return_value.error.assert_called()


class CheckSessionLoadableTests(TestCase):
    """Tests for check_session_loadable task"""
    
    @mock.patch('analytics.processing.loaders.get_run_logger')
    @mock.patch('analytics.processing.loaders.fastf1.get_session')
    def test_session_loadable(self, mock_get_session, mock_logger):
        """Should return True for loadable session"""
        mock_f1_session = MockFastF1Session()
        mock_get_session.return_value = mock_f1_session
        
        result = check_session_loadable.fn(2025, 1, 'Practice 1')
        
        self.assertTrue(result)
        mock_get_session.assert_called_once_with(2025, 1, 'FP1')
    
    @mock.patch('analytics.processing.loaders.get_run_logger')
    @mock.patch('analytics.processing.loaders.fastf1.get_session')
    def test_session_not_found(self, mock_get_session, mock_logger):
        """Should return False when session is None"""
        mock_get_session.return_value = None
        
        result = check_session_loadable.fn(2025, 99, 'Practice 1')
        
        self.assertFalse(result)
        mock_logger.return_value.warning.assert_called()
    
    @mock.patch('analytics.processing.loaders.get_run_logger')
    @mock.patch('analytics.processing.loaders.fastf1.get_session')
    def test_session_raises_error(self, mock_get_session, mock_logger):
        """Should return False when session raises error"""
        mock_get_session.side_effect = Exception('Session not available')
        
        result = check_session_loadable.fn(2025, 1, 'Practice 1')
        
        self.assertFalse(result)
        mock_logger.return_value.warning.assert_called()


class GetSessionInfoTests(TestCase):
    """Tests for get_session_info task"""
    
    @mock.patch('analytics.processing.loaders.get_run_logger')
    @mock.patch('analytics.processing.loaders.fastf1.get_session')
    def test_gets_session_info(self, mock_get_session, mock_logger):
        """Should return session metadata"""
        mock_f1_session = MockFastF1Session(
            name='Australian Grand Prix - Practice 1',
            date=datetime(2025, 3, 14, 1, 30, 0)
        )
        mock_get_session.return_value = mock_f1_session
        
        result = get_session_info.fn(2025, 1, 'Practice 1')
        
        self.assertEqual(result['year'], 2025)
        self.assertEqual(result['round'], 1)
        self.assertEqual(result['session_type'], 'Practice 1')
        self.assertEqual(result['fastf1_identifier'], 'FP1')
        self.assertEqual(result['name'], 'Australian Grand Prix - Practice 1')
        self.assertIsNotNone(result['date'])
    
    @mock.patch('analytics.processing.loaders.get_run_logger')
    @mock.patch('analytics.processing.loaders.fastf1.get_session')
    def test_handles_missing_attributes(self, mock_get_session, mock_logger):
        """Should handle sessions missing name/date attributes"""
        mock_f1_session = mock.Mock()
        # Remove name and date attributes
        del mock_f1_session.name
        del mock_f1_session.date
        mock_get_session.return_value = mock_f1_session
        
        result = get_session_info.fn(2025, 1, 'Practice 1')
        
        self.assertEqual(result['year'], 2025)
        self.assertEqual(result['round'], 1)
        self.assertIsNone(result['name'])
        self.assertIsNone(result['date'])
    
    @mock.patch('analytics.processing.loaders.get_run_logger')
    @mock.patch('analytics.processing.loaders.fastf1.get_session')
    def test_handles_error(self, mock_get_session, mock_logger):
        """Should return error info when session not available"""
        mock_get_session.side_effect = Exception('API Error')
        
        result = get_session_info.fn(2025, 1, 'Practice 1')
        
        self.assertEqual(result['year'], 2025)
        self.assertEqual(result['round'], 1)
        self.assertEqual(result['session_type'], 'Practice 1')
        self.assertIn('error', result)
        self.assertEqual(result['error'], 'API Error')
        mock_logger.return_value.warning.assert_called()


class LoaderIntegrationTests(TestCase):
    """Integration tests for loader tasks"""
    
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
            session_type='Race'
        )
    
    @mock.patch('analytics.processing.loaders.get_run_logger')
    @mock.patch('analytics.processing.loaders.fastf1.get_session')
    def test_load_session_with_fastf1_mocked(self, mock_get_session, mock_logger):
        """Should load FastF1 session without making real API calls"""
        mock_f1_session = MockFastF1Session()
        mock_get_session.return_value = mock_f1_session
        
        # Call the underlying function directly (bypasses Prefect decorator in tests)
        result = load_fastf1_session.fn(
            year=2025,
            round_num=1,
            session_type='Race',
            session_id=self.session.id
        )
        
        # Verify session was loaded successfully
        self.assertIsNotNone(result)
        self.assertTrue(result._loaded)
        
        # Verify FastF1 API was called with correct parameters
        mock_get_session.assert_called_once_with(2025, 1, 'R')
        
        # Verify no actual network calls were made
        self.assertEqual(mock_get_session.call_count, 1)
    
    def test_record_api_call_integration(self):
        """Test that record_api_call creates SessionLoadStatus"""
        from analytics.processing.rate_limiter import record_api_call
        
        # Verify no status exists yet
        self.assertFalse(SessionLoadStatus.objects.filter(session=self.session).exists())
        
        # Call record_api_call directly (not as Prefect task)
        with mock.patch('analytics.processing.rate_limiter.get_run_logger'):
            record_api_call.fn(self.session.id)  # Call the underlying function
        
        # Verify SessionLoadStatus was created and updated
        self.assertTrue(SessionLoadStatus.objects.filter(session=self.session).exists())
        status = SessionLoadStatus.objects.get(session=self.session)
        self.assertIsNotNone(status.last_api_call)
        self.assertEqual(status.api_calls_count, 1)
