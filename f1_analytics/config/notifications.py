import httpx
import asyncio
from django.conf import settings
from datetime import datetime

async def send_slack_notification_async(message: str, blocks: list = None):
    """
    Send a message to Slack via webhook.
    
    Args:
        message: Plain text message to send
        blocks: Optional list of Slack Block Kit blocks for rich formatting
        
    Returns:
        True if message sent successfully, False otherwise
    """
    if not settings.SLACK_WEBHOOK_URL:
        print(f"No Slack webhook configured. Message: {message}")
        return False
    
    payload = {"text": message}
    
    if blocks:
        payload["blocks"] = blocks
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                settings.SLACK_WEBHOOK_URL,
                headers={"Content-type": "application/json"},
                json=payload,
                timeout=10.0
            )
            response.raise_for_status()
            return True
    except httpx.HTTPError as e:
        print(f"Failed to send Slack notification: {e}")
        return False


def send_slack_notification(message: str, blocks: list = None):
    """
    Synchronous wrapper for send_slack_notification_async.
    
    Use this in Django shell, management commands, or any synchronous context.
    For async contexts (like Prefect flows), use send_slack_notification_async directly.
    
    Args:
        message: Plain text message to send
        blocks: Optional list of Slack Block Kit blocks for rich formatting
        
    Returns:
        True if message sent successfully, False otherwise
    
    Example:
        >>> from config.notifications import send_slack_notification
        >>> send_slack_notification("Hello from Django!")
    """
    return asyncio.run(send_slack_notification_async(message, blocks))


def send_import_completion_notification(summary: dict, year: int, round_number: int = None):
    """
    Send Slack notification with FastF1 import completion summary.
    
    Args:
        summary: Import summary dict with status, counts, duration, etc.
        year: Season year
        round_number: Specific round number (None for full season)
        
    Returns:
        True if notification sent successfully, False otherwise
    """
    try:
        # Determine status emoji and text
        if summary['status'] == 'complete':
            status_text = 'Complete'
        elif summary['status'] == 'failed':
            status_text = 'Failed'
        else:
            status_text = summary['status'].title()
        
        # Build scope description
        if round_number:
            scope = f"{year} Season - Round {round_number}"
        else:
            scope = f"{year} Season - Full Import"
        
        # Format duration
        duration = summary.get('duration_seconds', 0)
        if duration >= 3600:
            duration_str = f"{duration/3600:.1f} hours"
        elif duration >= 60:
            duration_str = f"{duration/60:.1f} minutes"
        else:
            duration_str = f"{duration:.1f} seconds"
        
        # Build blocks
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"FastF1 Import {status_text}",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Scope:*\n{scope}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Duration:*\n{duration_str}"
                    }
                ]
            },
            {
                "type": "divider"
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Sessions Processed:*\n{summary['sessions_processed']}/{summary['gaps_detected']}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Success Rate:*\n{summary['sessions_succeeded']}/{summary['sessions_processed']}"
                    }
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Data Extracted:*\n• Weather: {summary['data_extracted']['weather']} sessions\n• Circuit: {summary['data_extracted']['circuit']} tracks\n• Telemetry: {summary['data_extracted'].get('telemetry', 0)} sessions"
                }
            }
        ]
        
        # Add failure info if there were failures
        if summary.get('sessions_failed', 0) > 0:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Failed Sessions:* {summary['sessions_failed']}"
                }
            })
        
        # Send notification
        return send_slack_notification(
            message=f"FastF1 Import {status_text}: {scope}",
            blocks=blocks
        )
        
    except Exception as e:
        print(f"Failed to send import completion notification: {e}")
        return False
