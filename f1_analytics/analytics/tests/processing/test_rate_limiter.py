"""
Unit tests for rate limiter functions.

Tests the reactive rate-limit pause and API call recording.

IMPORTANT: No external API calls are made:
- All Prefect logging is mocked (get_run_logger)
- Slack notifications are mocked
- Database queries use Django TestCase (isolated transactions)
"""

from unittest import mock
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from analytics.models import Season, Race, Session, SessionLoadStatus
from analytics.processing.rate_limiter import (
    wait_for_rate_limit,
    record_api_call,
    update_run_context,
    clear_run_context,
)


class WaitForRateLimitTests(TestCase):
    """Tests for wait_for_rate_limit task."""

    @mock.patch('analytics.processing.rate_limiter._send_rate_limit_resume_notification')
    @mock.patch('analytics.processing.rate_limiter._send_rate_limit_pause_notification')
    @mock.patch('analytics.processing.rate_limiter.get_run_logger')
    def test_completes_immediately_in_tests(self, mock_logger, mock_pause, mock_resume):
        """wait_for_rate_limit should complete without sleeping in test mode (FASTF1_RATE_LIMIT_WAIT=0)."""
        wait_for_rate_limit.fn()
        # No exception means it completed cleanly; sleep(1) is never called because range(0) = no iterations

    @mock.patch('analytics.processing.rate_limiter._send_rate_limit_resume_notification')
    @mock.patch('analytics.processing.rate_limiter._send_rate_limit_pause_notification')
    @mock.patch('analytics.processing.rate_limiter.get_run_logger')
    def test_sends_notifications(self, mock_logger, mock_pause, mock_resume):
        """Should send pause and resume Slack notifications."""
        wait_for_rate_limit.fn()
        mock_pause.assert_called_once()
        mock_resume.assert_called_once()


class RecordApiCallTests(TestCase):
    """Tests for record_api_call task."""

    def setUp(self):
        self.season = Season.objects.create(year=2025, name='2025 Season')
        self.race = Race.objects.create(season=self.season, name='Test Grand Prix', round_number=1)
        self.session = Session.objects.create(race=self.race, session_number=1, session_type='Practice 1')

    @mock.patch('analytics.processing.rate_limiter.get_run_logger')
    def test_creates_load_status(self, mock_logger):
        """Should create SessionLoadStatus if it doesn't exist."""
        record_api_call.fn(self.session.id)

        self.session.refresh_from_db()
        self.assertIsNotNone(self.session.load_status.last_api_call)
        self.assertEqual(self.session.load_status.api_calls_count, 1)

    @mock.patch('analytics.processing.rate_limiter.get_run_logger')
    def test_increments_existing_count(self, mock_logger):
        """Should increment api_calls_count on an existing status."""
        status = SessionLoadStatus.objects.create(session=self.session, api_calls_count=5)
        old_time = timezone.now() - timedelta(minutes=30)
        status.last_api_call = old_time
        status.save()

        record_api_call.fn(self.session.id)

        status.refresh_from_db()
        self.assertEqual(status.api_calls_count, 6)
        self.assertGreater(status.last_api_call, old_time)

    @mock.patch('analytics.processing.rate_limiter.get_run_logger')
    def test_invalid_session_id_logs_error(self, mock_logger):
        """Should log error for unknown session ID."""
        record_api_call.fn(99999)
        mock_logger.return_value.error.assert_called()


class RunContextTests(TestCase):
    """Tests for update_run_context / clear_run_context helpers."""

    def tearDown(self):
        clear_run_context()

    def test_update_merges_keys(self):
        """update_run_context should merge kwargs into the module-level dict."""
        from analytics.processing.rate_limiter import _run_context
        update_run_context(sessions_done=3, sessions_succeeded=2)
        self.assertEqual(_run_context['sessions_done'], 3)
        self.assertEqual(_run_context['sessions_succeeded'], 2)

    def test_clear_empties_context(self):
        """clear_run_context should reset the dict to empty."""
        from analytics.processing.rate_limiter import _run_context
        update_run_context(sessions_done=5)
        clear_run_context()
        self.assertEqual(_run_context, {})
