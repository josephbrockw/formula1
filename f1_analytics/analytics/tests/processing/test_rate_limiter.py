"""
Unit tests for rate limiter functions.

Tests rate limit checking, tracking, and pause functionality.

IMPORTANT: No external API calls are made:
- All Prefect logging is mocked (get_run_logger)
- Database queries use Django TestCase (isolated transactions)
- No FastF1 or network requests
"""

from unittest import mock
from datetime import datetime, timedelta
from django.test import TestCase
from django.utils import timezone
from analytics.models import Season, Race, Session, SessionLoadStatus
from analytics.processing.rate_limiter import (
    check_rate_limit,
    calculate_next_available_time,
    wait_for_rate_limit,
    record_api_call,
    get_rate_limit_stats,
    MAX_REQUESTS_PER_HOUR,
    RATE_LIMIT_WINDOW,
)


class CheckRateLimitTests(TestCase):
    """Tests for check_rate_limit task"""
    
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
    
    @mock.patch('analytics.processing.rate_limiter.get_run_logger')
    def test_check_rate_limit_no_calls(self, mock_logger):
        """Should return True when no API calls have been made"""
        result = check_rate_limit.fn()
        
        self.assertTrue(result)
        mock_logger.return_value.info.assert_called()
    
    @mock.patch('analytics.processing.rate_limiter.get_run_logger')
    def test_check_rate_limit_under_limit(self, mock_logger):
        """Should return True when under rate limit"""
        # Create 100 recent API calls (need separate sessions due to OneToOne relationship)
        one_hour_ago = timezone.now() - timedelta(minutes=30)
        for i in range(100):
            # Create a new session for each status (OneToOne relationship)
            session = Session.objects.create(
                race=self.race,
                session_number=i + 2,  # Start at 2 since self.session is 1
                session_type='Practice 1'
            )
            status = SessionLoadStatus.objects.create(session=session)
            status.last_api_call = one_hour_ago + timedelta(minutes=i * 0.3)
            status.save()
        
        result = check_rate_limit.fn()
        
        self.assertTrue(result)
        # Should have 400 remaining
        mock_logger.return_value.info.assert_called()
    
    @mock.patch('analytics.processing.rate_limiter.get_run_logger')
    def test_check_rate_limit_approaching_limit(self, mock_logger):
        """Should warn when approaching rate limit"""
        # Create 460 recent API calls (40 remaining)
        one_hour_ago = timezone.now() - timedelta(minutes=30)
        
        for i in range(460):
            session = Session.objects.create(
                race=self.race,
                session_number=i + 2,
                session_type='Practice 1'
            )
            status = SessionLoadStatus.objects.create(session=session)
            status.last_api_call = one_hour_ago
            status.save()
        
        result = check_rate_limit.fn()
        
        self.assertTrue(result)
        mock_logger.return_value.warning.assert_called()
        # Should mention "Approaching rate limit"
    
    @mock.patch('analytics.processing.rate_limiter.get_run_logger')
    def test_check_rate_limit_exceeded(self, mock_logger):
        """Should return False when rate limit exceeded"""
        # Create 500+ recent API calls
        one_hour_ago = timezone.now() - timedelta(minutes=30)
        
        for i in range(510):
            session = Session.objects.create(
                race=self.race,
                session_number=i + 2,
                session_type='Practice 1'
            )
            status = SessionLoadStatus.objects.create(session=session)
            status.last_api_call = one_hour_ago
            status.save()
        
        result = check_rate_limit.fn()
        
        self.assertFalse(result)
        mock_logger.return_value.warning.assert_called()


class CalculateNextAvailableTimeTests(TestCase):
    """Tests for calculate_next_available_time task"""
    
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
    
    def test_no_recent_calls(self):
        """Should return None when no recent calls"""
        result = calculate_next_available_time()
        self.assertIsNone(result)
    
    def test_with_recent_calls(self):
        """Should return time when oldest call + 1 hour"""
        oldest_call_time = timezone.now() - timedelta(minutes=45)
        
        status = SessionLoadStatus.objects.create(session=self.session)
        status.last_api_call = oldest_call_time
        status.save()
        
        result = calculate_next_available_time()
        
        self.assertIsNotNone(result)
        expected = oldest_call_time + RATE_LIMIT_WINDOW
        # Allow 1 second difference for test execution time
        self.assertAlmostEqual(
            result.timestamp(),
            expected.timestamp(),
            delta=1
        )
    
    def test_with_multiple_calls(self):
        """Should return time based on oldest call"""
        oldest = timezone.now() - timedelta(minutes=55)
        newer = timezone.now() - timedelta(minutes=30)
        
        # Create older call
        session1 = Session.objects.create(
            race=self.race,
            session_number=2,
            session_type='Practice 2'
        )
        status1 = SessionLoadStatus.objects.create(session=session1)
        status1.last_api_call = oldest
        status1.save()
        
        # Create newer call
        session2 = Session.objects.create(
            race=self.race,
            session_number=3,
            session_type='Practice 3'
        )
        status2 = SessionLoadStatus.objects.create(session=session2)
        status2.last_api_call = newer
        status2.save()
        
        result = calculate_next_available_time()
        
        # Should be based on oldest call
        expected = oldest + RATE_LIMIT_WINDOW
        self.assertAlmostEqual(
            result.timestamp(),
            expected.timestamp(),
            delta=1
        )


class RecordApiCallTests(TestCase):
    """Tests for record_api_call task"""
    
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
    
    @mock.patch('analytics.processing.rate_limiter.get_run_logger')
    def test_record_api_call_creates_status(self, mock_logger):
        """Should create SessionLoadStatus if it doesn't exist"""
        self.assertFalse(hasattr(self.session, 'load_status'))
        
        record_api_call(self.session.id)
        
        self.session.refresh_from_db()
        self.assertTrue(hasattr(self.session, 'load_status'))
        self.assertIsNotNone(self.session.load_status.last_api_call)
        self.assertEqual(self.session.load_status.api_calls_count, 1)
    
    @mock.patch('analytics.processing.rate_limiter.get_run_logger')
    def test_record_api_call_updates_existing(self, mock_logger):
        """Should update existing SessionLoadStatus"""
        status = SessionLoadStatus.objects.create(
            session=self.session,
            api_calls_count=5
        )
        old_time = timezone.now() - timedelta(minutes=30)
        status.last_api_call = old_time
        status.save()
        
        record_api_call(self.session.id)
        
        status.refresh_from_db()
        self.assertEqual(status.api_calls_count, 6)
        self.assertGreater(status.last_api_call, old_time)
    
    @mock.patch('analytics.processing.rate_limiter.get_run_logger')
    def test_record_api_call_invalid_session(self, mock_logger):
        """Should log error for invalid session ID"""
        record_api_call(99999)
        
        mock_logger.return_value.error.assert_called()


class GetRateLimitStatsTests(TestCase):
    """Tests for get_rate_limit_stats task"""
    
    def setUp(self):
        """Create test data"""
        self.season = Season.objects.create(year=2025, name='2025 Season')
        self.race = Race.objects.create(
            season=self.season,
            name='Test Grand Prix',
            round_number=1
        )
    
    def test_no_recent_calls(self):
        """Should return stats with zero calls"""
        stats = get_rate_limit_stats()
        
        self.assertEqual(stats['calls_made'], 0)
        self.assertEqual(stats['max_calls'], MAX_REQUESTS_PER_HOUR)
        self.assertEqual(stats['remaining'], MAX_REQUESTS_PER_HOUR)
        self.assertIsNone(stats['next_reset'])
        self.assertEqual(stats['status'], 'OK')
    
    def test_with_calls_ok(self):
        """Should return OK status with plenty of calls remaining"""
        recent_time = timezone.now() - timedelta(minutes=30)
        
        for i in range(100):
            session = Session.objects.create(
                race=self.race,
                session_number=i + 1,
                session_type='Practice 1'
            )
            status = SessionLoadStatus.objects.create(session=session)
            status.last_api_call = recent_time
            status.save()
        
        stats = get_rate_limit_stats()
        
        self.assertEqual(stats['calls_made'], 100)
        self.assertEqual(stats['remaining'], 400)
        self.assertEqual(stats['status'], 'OK')
        self.assertIsNotNone(stats['next_reset'])
    
    def test_with_calls_warning(self):
        """Should return WARNING status when approaching limit"""
        recent_time = timezone.now() - timedelta(minutes=30)
        
        for i in range(480):
            session = Session.objects.create(
                race=self.race,
                session_number=i + 1,
                session_type='Practice 1'
            )
            status = SessionLoadStatus.objects.create(session=session)
            status.last_api_call = recent_time
            status.save()
        
        stats = get_rate_limit_stats()
        
        self.assertEqual(stats['calls_made'], 480)
        self.assertEqual(stats['remaining'], 20)
        self.assertEqual(stats['status'], 'WARNING')
    
    def test_with_calls_exceeded(self):
        """Should return EXCEEDED status when over limit"""
        recent_time = timezone.now() - timedelta(minutes=30)
        
        for i in range(510):
            session = Session.objects.create(
                race=self.race,
                session_number=i + 1,
                session_type='Practice 1'
            )
            status = SessionLoadStatus.objects.create(session=session)
            status.last_api_call = recent_time
            status.save()
        
        stats = get_rate_limit_stats()
        
        self.assertEqual(stats['calls_made'], 510)
        self.assertEqual(stats['remaining'], -10)
        self.assertEqual(stats['status'], 'EXCEEDED')


class WaitForRateLimitTests(TestCase):
    """Tests for wait_for_rate_limit task"""
    
    @mock.patch('analytics.processing.rate_limiter.get_run_logger')
    @mock.patch('analytics.processing.rate_limiter.calculate_next_available_time')
    def test_no_wait_needed(self, mock_calc, mock_logger):
        """Should not wait when no rate limit restriction"""
        mock_calc.return_value = None
        
        wait_for_rate_limit()
        
        mock_logger.return_value.info.assert_called()
    
    @mock.patch('analytics.processing.rate_limiter.get_run_logger')
    @mock.patch('analytics.processing.rate_limiter.time')
    def test_wait_already_passed(self, mock_time, mock_logger):
        """Should not wait when rate limit window has passed"""
        past_time = timezone.now() - timedelta(minutes=5)
        
        wait_for_rate_limit(past_time)
        
        mock_logger.return_value.info.assert_called()
        mock_time.sleep.assert_not_called()
    
    @mock.patch('analytics.processing.rate_limiter.get_run_logger')
    @mock.patch('analytics.processing.rate_limiter.time')
    def test_wait_short_duration(self, mock_time, mock_logger):
        """Should wait for short duration"""
        future_time = timezone.now() + timedelta(seconds=30)
        
        wait_for_rate_limit(future_time)
        
        # Should have slept
        mock_time.sleep.assert_called()
        mock_logger.return_value.warning.assert_called()
        mock_logger.return_value.info.assert_called()
