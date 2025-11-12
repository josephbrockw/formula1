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
from datetime import timedelta
from prefect import task, get_run_logger
from django.utils import timezone


@task(name="Wait for Rate Limit")
def wait_for_rate_limit():
    """
    Pause for 1 hour when rate limit is hit.
    
    This is called by loaders when FastF1 raises RateLimitExceededError.
    Centralizes the pause behavior so all loaders behave consistently.
    
    Sleeps in 5-minute chunks with progress logging.
    """
    logger = get_run_logger()
    
    wait_seconds = 3600  # 1 hour
    chunk_size = 300  # 5 minutes
    remaining = wait_seconds
    
    logger.warning(
        f"⏸️  RATE LIMIT HIT - Pausing for 1 hour\n"
        f"   This is normal behavior to respect API limits.\n"
        f"   Will resume automatically at {(timezone.now() + timedelta(seconds=wait_seconds)).strftime('%H:%M:%S')}"
    )
    
    while remaining > 0:
        sleep_time = min(chunk_size, remaining)
        time.sleep(sleep_time)
        remaining -= sleep_time
        
        if remaining > 0:
            mins_left = int(remaining / 60)
            logger.info(f"⏳ Still paused... {mins_left} minutes remaining")
    
    logger.info(f"✅ Rate limit pause complete! Resuming at {timezone.now().strftime('%H:%M:%S')}")


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
