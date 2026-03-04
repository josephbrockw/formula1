from __future__ import annotations

import requests
from django.conf import settings

_EMOJI = {"info": "ℹ️", "warning": "⚠️", "error": "🚨"}


def send_slack_notification(message: str, level: str = "info") -> bool:
    if not settings.SLACK_WEBHOOK_URL:
        return False

    payload = {
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{_EMOJI.get(level, 'ℹ️')} {message}",
                },
            }
        ]
    }

    try:
        response = requests.post(settings.SLACK_WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception:
        return False
