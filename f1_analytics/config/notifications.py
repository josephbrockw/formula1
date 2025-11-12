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
