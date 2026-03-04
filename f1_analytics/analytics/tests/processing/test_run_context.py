"""
Unit tests for rate_limiter run context functions.

Covers:
- update_run_context / clear_run_context state management
- _send_rate_limit_pause_notification multi-season branch (context populated)
- _send_rate_limit_pause_notification single-year fallback (context empty)
- Resilience when Slack send fails

No FastF1 or network requests are made.
send_slack_notification is patched at its source (config.notifications) because
it is imported inside the function body, not at module level.
"""

from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone


def _block_text(blocks):
    """Concatenate all text strings out of a Slack blocks list."""
    parts = []
    for block in blocks:
        if isinstance(block.get('text'), dict):
            parts.append(block['text'].get('text', ''))
        for field in block.get('fields', []):
            if isinstance(field, dict):
                parts.append(field.get('text', ''))
        for element in block.get('elements', []):
            if isinstance(element, dict):
                parts.append(element.get('text', ''))
    return ' '.join(parts)


class RunContextStateTests(TestCase):
    """update_run_context and clear_run_context behaviour."""

    def setUp(self):
        import analytics.processing.rate_limiter as rl
        self.rl = rl
        rl.clear_run_context()

    def tearDown(self):
        self.rl.clear_run_context()

    def test_context_is_empty_initially(self):
        self.assertEqual(self.rl._run_context, {})

    def test_update_adds_keys(self):
        self.rl.update_run_context(sessions_done=5, sessions_succeeded=4)
        self.assertEqual(self.rl._run_context['sessions_done'], 5)
        self.assertEqual(self.rl._run_context['sessions_succeeded'], 4)

    def test_update_overwrites_existing_key(self):
        self.rl.update_run_context(sessions_done=3)
        self.rl.update_run_context(sessions_done=7)
        self.assertEqual(self.rl._run_context['sessions_done'], 7)

    def test_update_accumulates_across_calls(self):
        self.rl.update_run_context(a=1)
        self.rl.update_run_context(b=2)
        self.assertEqual(self.rl._run_context, {'a': 1, 'b': 2})

    def test_clear_empties_dict(self):
        self.rl.update_run_context(sessions_done=5, sessions_failed=1)
        self.rl.clear_run_context()
        self.assertEqual(self.rl._run_context, {})

    def test_clear_is_idempotent_on_empty(self):
        self.rl.clear_run_context()
        self.rl.clear_run_context()
        self.assertEqual(self.rl._run_context, {})

    def test_update_stores_nested_dict_by_reference(self):
        data = {'weather': 5, 'circuit': 3}
        self.rl.update_run_context(data_extracted=data)
        self.assertIs(self.rl._run_context['data_extracted'], data)


class PauseNotificationMultiSeasonTests(TestCase):
    """_send_rate_limit_pause_notification with run context populated."""

    def setUp(self):
        import analytics.processing.rate_limiter as rl
        self.rl = rl
        rl.clear_run_context()
        self.resume_time = timezone.now() + timedelta(hours=1)

    def tearDown(self):
        self.rl.clear_run_context()

    def _populate_context(self, **overrides):
        defaults = dict(
            sessions_done=42,
            sessions_succeeded=38,
            sessions_failed=4,
            data_extracted={'weather': 40, 'circuit': 38, 'telemetry': 35},
            sessions_remaining_by_year={2025: 5, 2024: 18, 2023: 22},
        )
        defaults.update(overrides)
        self.rl.update_run_context(**defaults)

    @mock.patch('config.notifications.send_slack_notification')
    def test_uses_multi_season_branch_when_context_set(self, mock_slack):
        self._populate_context()
        self.rl._send_rate_limit_pause_notification(self.resume_time)
        mock_slack.assert_called_once()
        text = _block_text(mock_slack.call_args[1]['blocks'])
        self.assertIn('This Run So Far', text)
        self.assertIn('Still Outstanding', text)

    @mock.patch('config.notifications.send_slack_notification')
    def test_sessions_done_and_breakdown_in_notification(self, mock_slack):
        self._populate_context()
        self.rl._send_rate_limit_pause_notification(self.resume_time)
        text = _block_text(mock_slack.call_args[1]['blocks'])
        self.assertIn('42 sessions processed', text)
        self.assertIn('38 succeeded', text)
        self.assertIn('4 failed', text)

    @mock.patch('config.notifications.send_slack_notification')
    def test_data_collected_breakdown_in_notification(self, mock_slack):
        self._populate_context()
        self.rl._send_rate_limit_pause_notification(self.resume_time)
        text = _block_text(mock_slack.call_args[1]['blocks'])
        self.assertIn('weather ×40', text)
        self.assertIn('circuit ×38', text)
        self.assertIn('telemetry ×35', text)

    @mock.patch('config.notifications.send_slack_notification')
    def test_remaining_per_year_in_notification(self, mock_slack):
        self._populate_context()
        self.rl._send_rate_limit_pause_notification(self.resume_time)
        text = _block_text(mock_slack.call_args[1]['blocks'])
        self.assertIn('2025', text)
        self.assertIn('2024', text)
        self.assertIn('2023', text)

    @mock.patch('config.notifications.send_slack_notification')
    def test_zero_count_years_excluded_from_outstanding(self, mock_slack):
        self.rl.update_run_context(
            sessions_done=5,
            sessions_succeeded=5,
            sessions_failed=0,
            data_extracted={},
            sessions_remaining_by_year={2025: 0, 2024: 10},
        )
        self.rl._send_rate_limit_pause_notification(self.resume_time)
        text = _block_text(mock_slack.call_args[1]['blocks'])
        # 2024 has remaining sessions, must appear
        self.assertIn('2024', text)

    @mock.patch('config.notifications.send_slack_notification')
    def test_collect_all_data_resume_hint_in_notification(self, mock_slack):
        self._populate_context()
        self.rl._send_rate_limit_pause_notification(self.resume_time)
        text = _block_text(mock_slack.call_args[1]['blocks'])
        self.assertIn('collect_all_data', text)

    @mock.patch('config.notifications.send_slack_notification')
    def test_no_exception_when_slack_fails_with_context(self, mock_slack):
        self._populate_context()
        mock_slack.side_effect = Exception('Slack down')
        # Must not raise
        self.rl._send_rate_limit_pause_notification(self.resume_time)


class PauseNotificationSingleYearFallbackTests(TestCase):
    """_send_rate_limit_pause_notification falls back when context is empty."""

    def setUp(self):
        import analytics.processing.rate_limiter as rl
        self.rl = rl
        rl.clear_run_context()
        self.resume_time = timezone.now() + timedelta(hours=1)

    def tearDown(self):
        self.rl.clear_run_context()

    @mock.patch('analytics.processing.session_processor.get_sessions_to_process')
    @mock.patch('config.notifications.send_slack_notification')
    def test_single_year_fallback_used_when_context_empty(self, mock_slack, mock_sessions):
        mock_sessions.return_value = [mock.Mock()] * 10
        self.rl._send_rate_limit_pause_notification(self.resume_time)
        mock_slack.assert_called_once()
        text = _block_text(mock_slack.call_args[1]['blocks'])
        self.assertIn('Work Remaining', text)

    @mock.patch('analytics.processing.session_processor.get_sessions_to_process')
    @mock.patch('config.notifications.send_slack_notification')
    def test_single_year_shows_session_count(self, mock_slack, mock_sessions):
        mock_sessions.return_value = [mock.Mock()] * 7
        self.rl._send_rate_limit_pause_notification(self.resume_time)
        text = _block_text(mock_slack.call_args[1]['blocks'])
        self.assertIn('7', text)

    @mock.patch('analytics.processing.session_processor.get_sessions_to_process')
    @mock.patch('config.notifications.send_slack_notification')
    def test_multi_season_blocks_absent_in_single_year_fallback(self, mock_slack, mock_sessions):
        mock_sessions.return_value = []
        self.rl._send_rate_limit_pause_notification(self.resume_time)
        text = _block_text(mock_slack.call_args[1]['blocks'])
        self.assertNotIn('This Run So Far', text)
        self.assertNotIn('Still Outstanding', text)
        self.assertNotIn('collect_all_data', text)

    @mock.patch('analytics.processing.session_processor.get_sessions_to_process')
    @mock.patch('config.notifications.send_slack_notification')
    def test_no_exception_when_slack_fails_in_fallback(self, mock_slack, mock_sessions):
        mock_sessions.return_value = []
        mock_slack.side_effect = Exception('Slack down')
        self.rl._send_rate_limit_pause_notification(self.resume_time)
