"""
Rate limit management for FastF1 API.

FastF1 has a limit of 500 requests per hour to the Ergast API.
This module provides Prefect tasks to track and manage rate limits.
"""

import time
from datetime import datetime, timedelta
from typing import Optional
from prefect import task, get_run_logger
from django.utils import timezone
from django.db.models import Count, Q


# FastF1 API limits
MAX_REQUESTS_PER_HOUR = 500
RATE_LIMIT_WINDOW = timedelta(hours=1)


@task(name="Check Rate Limit")
def check_rate_limit() -> bool:
    """
    Check if we can make API calls without exceeding rate limit.
    
    Returns:
        bool: True if we can make requests, False if limit reached
    """
    logger = get_run_logger()
    
    from analytics.models import SessionLoadStatus
    
    # Count API calls in the last hour
    one_hour_ago = timezone.now() - RATE_LIMIT_WINDOW
    
    recent_calls = SessionLoadStatus.objects.filter(
        last_api_call__gte=one_hour_ago
    ).aggregate(
        total_calls=Count('id')
    )['total_calls'] or 0
    
    remaining = MAX_REQUESTS_PER_HOUR - recent_calls
    
    if remaining <= 0:
        logger.warning(f"Rate limit reached: {recent_calls}/{MAX_REQUESTS_PER_HOUR} calls in last hour")
        return False
    
    if remaining < 50:
        logger.warning(f"Approaching rate limit: {remaining} requests remaining")
    else:
        logger.info(f"Rate limit OK: {remaining}/{MAX_REQUESTS_PER_HOUR} requests remaining")
    
    return True


@task(name="Calculate Next Available Time")
def calculate_next_available_time() -> Optional[datetime]:
    """
    Calculate when we can make API calls again.
    
    Returns:
        datetime: When rate limit window resets (None if safe to proceed)
    """
    from analytics.models import SessionLoadStatus
    
    one_hour_ago = timezone.now() - RATE_LIMIT_WINDOW
    
    # Find oldest call in current window
    oldest_call = SessionLoadStatus.objects.filter(
        last_api_call__gte=one_hour_ago
    ).order_by('last_api_call').first()
    
    if not oldest_call:
        return None
    
    # Rate limit window resets 1 hour after oldest call
    next_available = oldest_call.last_api_call + RATE_LIMIT_WINDOW
    
    return next_available


@task(name="Wait for Rate Limit", 
      task_run_name="Wait for rate limit reset")
def wait_for_rate_limit(next_available: Optional[datetime] = None):
    """
    Pause execution until rate limit window resets.
    
    This is a Prefect task that logs clear status messages and waits
    until the rate limit window resets.
    
    Args:
        next_available: When to resume (calculated if not provided)
    """
    logger = get_run_logger()
    
    if next_available is None:
        next_available = calculate_next_available_time()
    
    if next_available is None:
        logger.info("No rate limit restriction, proceeding")
        return
    
    now = timezone.now()
    
    if next_available <= now:
        logger.info("Rate limit window has passed, proceeding")
        return
    
    wait_seconds = (next_available - now).total_seconds()
    wait_minutes = int(wait_seconds / 60)
    
    logger.warning(
        f"⏸️  RATE LIMIT REACHED - Pausing for {wait_minutes} minutes\n"
        f"   Current time: {now.strftime('%H:%M:%S')}\n"
        f"   Resume time:  {next_available.strftime('%H:%M:%S')}\n"
        f"   This is normal behavior to respect API limits."
    )
    
    # Sleep in chunks to allow for monitoring
    chunk_size = 60  # 1 minute chunks
    remaining = wait_seconds
    
    while remaining > 0:
        sleep_time = min(chunk_size, remaining)
        time.sleep(sleep_time)
        remaining -= sleep_time
        
        if remaining > 0:
            mins_left = int(remaining / 60)
            logger.info(f"⏳ Still waiting... {mins_left} minutes until resume")
    
    logger.info(f"✅ Rate limit window reset! Resuming at {timezone.now().strftime('%H:%M:%S')}")


@task(name="Record API Call")
def record_api_call(session_id: int):
    """
    Record that an API call was made for a session.
    
    Updates the SessionLoadStatus to track when the call was made
    for rate limit management.
    
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


@task(name="Get Rate Limit Stats")
def get_rate_limit_stats() -> dict:
    """
    Get current rate limit statistics.
    
    Returns:
        dict: Stats including calls made, remaining, and next reset time
    """
    from analytics.models import SessionLoadStatus
    
    one_hour_ago = timezone.now() - RATE_LIMIT_WINDOW
    
    recent_calls = SessionLoadStatus.objects.filter(
        last_api_call__gte=one_hour_ago
    ).count()
    
    remaining = MAX_REQUESTS_PER_HOUR - recent_calls
    next_reset = calculate_next_available_time()
    
    return {
        'calls_made': recent_calls,
        'max_calls': MAX_REQUESTS_PER_HOUR,
        'remaining': remaining,
        'next_reset': next_reset.isoformat() if next_reset else None,
        'status': 'OK' if remaining > 50 else 'WARNING' if remaining > 0 else 'EXCEEDED'
    }
