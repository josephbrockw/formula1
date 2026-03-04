"""
Rate limit management for FastF1 API.

REACTIVE APPROACH:
- We don't proactively check rate limits
- FastF1 tells us when we hit the limit via RateLimitExceededError
- We catch it, pause for 1 hour, and retry
- Simple, reliable, and lets FastF1 manage its own limits

Functions:
- wait_for_rate_limit(): Pause for 1 hour when limit hit
- record_api_call(): Track session loads for metrics/monitoring
"""

import time
import asyncio
from datetime import timedelta
from prefect import task, get_run_logger
from django.utils import timezone
from django.conf import settings


# ---------------------------------------------------------------------------
# Run context — updated by import_all_seasons_flow before each session so
# the rate-limit pause notification can show multi-season progress.
# Empty when running single-year imports (falls back to existing behavior).
# ---------------------------------------------------------------------------
_run_context: dict = {}


def update_run_context(**kwargs) -> None:
    """Merge kwargs into the module-level run context dict."""
    _run_context.update(kwargs)


def clear_run_context() -> None:
    """Reset the run context (call at start and end of a multi-season run)."""
    _run_context.clear()


@task(name="Wait for Rate Limit")
def wait_for_rate_limit():
    """
    Pause for 1 hour when rate limit is hit.
    
    This is called by loaders when FastF1 raises RateLimitExceededError.
    Centralizes the pause behavior so all loaders behave consistently.
    
    Uses tqdm progress bar for visual countdown.
    Sends Slack notifications at start and end of pause.
    """
    logger = get_run_logger()
    
    wait_seconds = settings.FASTF1_RATE_LIMIT_WAIT
    
    resume_time = timezone.now() + timedelta(seconds=wait_seconds)
    
    logger.warning(
        f"⏸️  RATE LIMIT HIT - Pausing for 1 hour\n"
        f"   This is normal behavior to respect API limits.\n"
        f"   Will resume automatically at {resume_time.strftime('%H:%M:%S')}"
    )
    
    # Send Slack notification with gap report summary
    _send_rate_limit_pause_notification(resume_time)
    
    # Use tqdm for visual progress bar during wait
    try:
        from tqdm import tqdm
        
        # Sleep in 1-second increments with progress bar
        for _ in tqdm(
            range(wait_seconds),
            desc="⏳ Rate limit pause",
            unit="sec",
            bar_format="{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
            position=2,  # Below session and lap progress bars
            leave=False
        ):
            time.sleep(1)
    
    except ImportError:
        # Fallback to chunked logging if tqdm not available
        chunk_size = 300  # 5 minutes
        remaining = wait_seconds
        
        while remaining > 0:
            sleep_time = min(chunk_size, remaining)
            time.sleep(sleep_time)
            remaining -= sleep_time
            
            if remaining > 0:
                mins_left = int(remaining / 60)
                logger.info(f"⏳ Still paused... {mins_left} minutes remaining")
    
    logger.info(f"✅ Rate limit pause complete! Resuming at {timezone.now().strftime('%H:%M:%S')}")
    
    # Send resume notification
    _send_rate_limit_resume_notification()


@task(name="Record API Call")
def record_api_call(session_id: int):
    """
    Record that a session was loaded.
    
    Tracks session loads for metrics and monitoring.
    Not used for rate limiting - we let FastF1 handle that.
    
    Args:
        session_id: ID of the session that was loaded
    """
    from analytics.models import SessionLoadStatus, Session
    
    logger = get_run_logger()
    
    try:
        session = Session.objects.get(id=session_id)
        status, created = SessionLoadStatus.objects.get_or_create(session=session)
        
        status.last_api_call = timezone.now()
        status.api_calls_count += 1
        status.save()
        
        logger.debug(f"Recorded API call for {session} (total: {status.api_calls_count})")
        
    except Session.DoesNotExist:
        logger.error(f"Session {session_id} not found")


def _send_rate_limit_pause_notification(resume_time):
    """
    Send Slack notification when rate limit pause begins.

    If a multi-season run context is set (via update_run_context), includes
    a rich breakdown of progress so far and what's still outstanding.
    Falls back to the original single-year "sessions remaining" summary when
    no context is set.
    """
    from config.notifications import send_slack_notification
    import pytz

    try:
        cst = pytz.timezone('America/Chicago')
        current_time_cst = timezone.now().astimezone(cst)
        resume_time_cst = resume_time.astimezone(cst)

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "FastF1 Rate Limit - Pausing Import",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": "*Pause Duration:*\n1 hour"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Resume Time:*\n{resume_time_cst.strftime('%I:%M:%S %p CST')}",
                    },
                ],
            },
            {"type": "divider"},
        ]

        if _run_context:
            # Multi-season run — show rich progress summary
            sessions_done = _run_context.get('sessions_done', 0)
            sessions_succeeded = _run_context.get('sessions_succeeded', 0)
            sessions_failed = _run_context.get('sessions_failed', 0)
            data_extracted = _run_context.get('data_extracted', {})
            sessions_remaining_by_year = _run_context.get('sessions_remaining_by_year', {})
            total_remaining = sum(sessions_remaining_by_year.values())

            data_parts = [
                f"{dtype} ×{count}"
                for dtype, count in data_extracted.items()
                if count > 0
            ]
            data_str = ' · '.join(data_parts) if data_parts else 'none'

            year_lines = '\n'.join(
                f"• {year}: {cnt:>3} sessions"
                for year, cnt in sorted(sessions_remaining_by_year.items(), reverse=True)
                if cnt > 0
            )

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*This Run So Far*\n"
                        f"• {sessions_done} sessions processed  "
                        f"({sessions_succeeded} succeeded · {sessions_failed} failed)\n"
                        f"• Collected: {data_str}"
                    ),
                },
            })
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Still Outstanding*\n"
                        f"{year_lines}\n"
                        f"• (total: {total_remaining} sessions remaining)"
                    ),
                },
            })
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"Will resume automatically at {resume_time_cst.strftime('%I:%M %p CST')}.\n"
                        f"Re-run `collect_all_data` if you stopped the script."
                    ),
                },
            })
        else:
            # Single-year run — original summary
            from analytics.processing.session_processor import get_sessions_to_process

            current_year = current_time_cst.year
            sessions_remaining = get_sessions_to_process(year=current_year, force=False)

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Work Remaining:*\n"
                        f"• {len(sessions_remaining)} sessions left to process\n"
                        f"• Import will resume automatically"
                    ),
                },
            })

        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"This is normal behavior to respect API limits | "
                        f"{current_time_cst.strftime('%Y-%m-%d %I:%M:%S %p CST')}"
                    ),
                }
            ],
        })

        send_slack_notification(
            message=(
                f"⏸️ FastF1 Rate Limit Hit - Pausing for 1 hour. "
                f"Will resume at {resume_time_cst.strftime('%I:%M %p CST')}"
            ),
            blocks=blocks,
        )
    except Exception as e:
        print(f"Failed to send pause notification: {e}")


def _send_rate_limit_resume_notification():
    """
    Send Slack notification when rate limit pause ends.
    """
    from config.notifications import send_slack_notification
    import pytz
    
    try:
        # Convert to CST for display
        cst = pytz.timezone('America/Chicago')
        current_time_cst = timezone.now().astimezone(cst)
        
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "✅ *Rate Limit Pause Complete*\nResuming FastF1 data import..."
                }
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Resumed at {current_time_cst.strftime('%Y-%m-%d %I:%M:%S %p CST')}"
                    }
                ]
            }
        ]
        
        send_slack_notification(
            message=f"✅ Rate Limit Pause Complete - Resuming import at {current_time_cst.strftime('%I:%M %p CST')}",
            blocks=blocks
        )
    except Exception as e:
        print(f"Failed to send resume notification: {e}")
