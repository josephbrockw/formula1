from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from core.tasks.notifier import send_slack_notification

WEBHOOK_URL = "https://hooks.slack.com/services/test/webhook"


class TestSendSlackNotification(SimpleTestCase):
    @override_settings(SLACK_WEBHOOK_URL=WEBHOOK_URL)
    @patch("core.tasks.notifier.requests.post")
    def test_sends_post_to_webhook_url(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        send_slack_notification("hello")
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args[0][0], WEBHOOK_URL)

    @override_settings(SLACK_WEBHOOK_URL=WEBHOOK_URL)
    @patch("core.tasks.notifier.requests.post")
    def test_payload_contains_message_text(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        send_slack_notification("collection complete")
        payload = mock_post.call_args[1]["json"]
        text = payload["blocks"][0]["text"]["text"]
        self.assertIn("collection complete", text)

    @override_settings(SLACK_WEBHOOK_URL=WEBHOOK_URL)
    @patch("core.tasks.notifier.requests.post")
    def test_info_level_uses_info_emoji(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        send_slack_notification("msg", level="info")
        text = mock_post.call_args[1]["json"]["blocks"][0]["text"]["text"]
        self.assertIn("ℹ️", text)

    @override_settings(SLACK_WEBHOOK_URL=WEBHOOK_URL)
    @patch("core.tasks.notifier.requests.post")
    def test_warning_level_uses_warning_emoji(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        send_slack_notification("msg", level="warning")
        text = mock_post.call_args[1]["json"]["blocks"][0]["text"]["text"]
        self.assertIn("⚠️", text)

    @override_settings(SLACK_WEBHOOK_URL=WEBHOOK_URL)
    @patch("core.tasks.notifier.requests.post")
    def test_error_level_uses_error_emoji(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        send_slack_notification("msg", level="error")
        text = mock_post.call_args[1]["json"]["blocks"][0]["text"]["text"]
        self.assertIn("🚨", text)

    @override_settings(SLACK_WEBHOOK_URL=WEBHOOK_URL)
    @patch("core.tasks.notifier.requests.post")
    def test_payload_uses_mrkdwn_type(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        send_slack_notification("msg")
        text_block = mock_post.call_args[1]["json"]["blocks"][0]["text"]
        self.assertEqual(text_block["type"], "mrkdwn")

    @override_settings(SLACK_WEBHOOK_URL=WEBHOOK_URL)
    @patch("core.tasks.notifier.requests.post")
    def test_returns_true_on_success(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        result = send_slack_notification("msg")
        self.assertTrue(result)

    @override_settings(SLACK_WEBHOOK_URL=WEBHOOK_URL)
    @patch("core.tasks.notifier.requests.post")
    def test_returns_false_on_http_error(self, mock_post: MagicMock) -> None:
        mock_post.side_effect = Exception("connection error")
        result = send_slack_notification("msg")
        self.assertFalse(result)

    @override_settings(SLACK_WEBHOOK_URL=WEBHOOK_URL)
    @patch("core.tasks.notifier.requests.post")
    def test_does_not_raise_on_http_error(self, mock_post: MagicMock) -> None:
        mock_post.side_effect = Exception("connection error")
        try:
            send_slack_notification("msg")
        except Exception:
            self.fail("send_slack_notification raised an exception")

    @override_settings(SLACK_WEBHOOK_URL="")
    @patch("core.tasks.notifier.requests.post")
    def test_no_request_when_webhook_url_not_configured(self, mock_post: MagicMock) -> None:
        send_slack_notification("msg")
        mock_post.assert_not_called()

    @override_settings(SLACK_WEBHOOK_URL="")
    @patch("core.tasks.notifier.requests.post")
    def test_returns_false_when_webhook_url_not_configured(self, mock_post: MagicMock) -> None:
        result = send_slack_notification("msg")
        self.assertFalse(result)

    @override_settings(SLACK_WEBHOOK_URL=WEBHOOK_URL)
    @patch("core.tasks.notifier.requests.post")
    def test_request_uses_ten_second_timeout(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        send_slack_notification("msg")
        self.assertEqual(mock_post.call_args[1]["timeout"], 10)
