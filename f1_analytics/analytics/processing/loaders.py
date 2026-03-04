"""
Prefect tasks for loading FastF1 sessions.

These tasks handle the actual API calls to load session data,
with automatic caching via Prefect to avoid duplicate loads.
"""

import contextlib
import threading
from typing import Any, Optional

import fastf1
from fastf1.req import RateLimitExceededError as _FastF1RateLimitError
from django.conf import settings
from prefect import task, get_run_logger
from prefect.cache_policies import NONE

from .utils import get_fastf1_session_identifier, generate_session_cache_key
from .rate_limiter import record_api_call, wait_for_rate_limit


@contextlib.contextmanager
def _detect_rate_limit():
    """
    Detect if RateLimitExceededError was raised inside session.load().

    FastF1 catches RateLimitExceededError internally and logs warnings,
    so the exception never propagates out of session.load(). This context
    manager patches the rate limiter class to set a flag when the limit
    fires, letting us detect it after session.load() returns and re-raise.
    """
    import fastf1.req as _req

    _hit = threading.Event()
    _limiter_cls = _req._CallsPerIntervalLimitRaise
    _original_limit = _limiter_cls.limit

    def _patched_limit(self):
        try:
            return _original_limit(self)
        except _req.RateLimitExceededError:
            _hit.set()
            raise

    _limiter_cls.limit = _patched_limit
    try:
        yield _hit
    finally:
        _limiter_cls.limit = _original_limit


def session_cache_key_fn(context, parameters):
    """
    Generate cache key for FastF1 session loading.
    
    Cache key is based on year, round, and session type.
    Sessions with the same parameters will reuse cached result
    within the cache expiration window.
    """
    year = parameters.get('year')
    round_num = parameters.get('round_num')
    session_type = parameters.get('session_type')
    
    return generate_session_cache_key(year, round_num, session_type)


class NonRetryableError(Exception):
    """Exception for errors that should not be retried (data not available, testing events, etc.)"""
    pass


def _should_retry_load_session(task, task_run, state) -> bool:
    """Don't retry NonRetryableError (permanent failures like missing session types)."""
    exc = state.result(raise_on_failure=False)
    return not isinstance(exc, NonRetryableError)


@task(
    name="Load FastF1 Session",
    cache_policy=NONE,
    retries=settings.FASTF1_TASK_RETRIES,
    retry_delay_seconds=settings.FASTF1_TASK_RETRY_DELAY,
    retry_condition_fn=_should_retry_load_session,
)
def load_fastf1_session(
    year: int,
    round_num: int,
    session_type: str,
    session_id: int,
    event_name: Optional[str] = None,
    force: bool = False  # If True, bypasses Prefect cache
) -> Any:
    """
    Load a FastF1 session with automatic caching and rate limit tracking.
    
    This is the PRIMARY task that makes FastF1 API calls. Prefect automatically:
    - Caches the loaded session for 1 hour
    - Reuses cached session if called again with same parameters
    - Retries on transient failures (network, timeouts) but NOT on data unavailable
    - Logs all attempts and outcomes
    
    Testing events are loaded by event name instead of round number.
    
    Non-retryable errors (data not available):
    - Session not found
    - Invalid identifiers
    
    Args:
        year: Season year
        round_num: Race round number (ignored for testing events)
        session_type: Django session type (e.g., 'Practice 1', 'Race')
        session_id: Django Session ID (for tracking)
        event_name: Event name for testing events (e.g., 'Pre-Season Testing')
    
    Returns:
        Loaded FastF1 Session object
        
    Raises:
        NonRetryableError: For fundamental data availability issues
        RateLimitExceededError: When rate limit is hit
        Exception: For other transient errors (will retry)
    """
    logger = get_run_logger()
    
    # Convert Django session type to FastF1 identifier
    fastf1_identifier = get_fastf1_session_identifier(session_type)
    
    if round_num == 0:
        # Testing events can't be accessed by round number in FastF1
        # (raises "Cannot get testing event by round number!").
        # Look up the canonical EventName from the schedule instead.
        schedule = fastf1.get_event_schedule(year, include_testing=True)
        testing_rows = schedule[schedule['RoundNumber'] == 0]
        if testing_rows.empty:
            raise NonRetryableError(f"No testing event found in {year} FastF1 schedule")
        event_identifier = testing_rows.iloc[0]['EventName']
    else:
        event_identifier = event_name if event_name else round_num
    event_display = f"{round_num} {event_name}" if event_name else f"Round {round_num}"
    
    logger.info(
        f"Loading FastF1 session: {year} {event_display} {session_type} "
        f"(identifier: {fastf1_identifier})"
    )
    
    # Retry loop for rate limit handling (1 hour between attempts)
    max_rate_limit_retries = 8
    
    for attempt in range(max_rate_limit_retries):
        try:
            # This is the actual API call that counts against rate limit
            # Use event_name for testing events, round_num for regular races
            f1_session = fastf1.get_session(year, event_identifier, fastf1_identifier)
            
            # Load session data (this downloads telemetry).
            # FastF1 catches RateLimitExceededError internally and logs warnings
            # instead of propagating — use _detect_rate_limit() to surface it.
            logger.info(f"Calling session.load() - this may take 30-60 seconds...")
            with _detect_rate_limit() as _rate_limit_hit:
                f1_session.load()
            if _rate_limit_hit.is_set():
                raise _FastF1RateLimitError(
                    "any API: rate limit hit inside session.load()"
                )
            
            # Record API call for rate limit tracking
            # This tracks function calls, not HTTP requests
            # (FastF1 makes ~3-5 HTTP requests per session.load())
            record_api_call(session_id)
            
            # Get race name from session event
            race_name = getattr(f1_session.event, 'EventName', None) or event_identifier
            logger.info(f"✅ Successfully loaded {year} {race_name} {session_type}")
            
            return f1_session
            
        except _FastF1RateLimitError as e:
            logger.error(f"❌ Rate limit exceeded by FastF1 (attempt {attempt + 1}/{max_rate_limit_retries})")
            logger.error(f"   Error: {e}")
            
            # If this was our last attempt, re-raise the error
            if attempt == max_rate_limit_retries - 1:
                logger.error(f"❌ Max rate limit retries reached. Giving up.")
                raise
            
            # Use centralized pause function for consistent behavior (1 hour)
            wait_for_rate_limit()
            
            # Loop will retry automatically
            event_display = event_name if event_name else f"Round {event_identifier}"
            logger.info(f"🔄 Retrying: {year} {event_display} {session_type}")
        
        except Exception as e:
            error_msg = str(e).lower()
            
            # Check for non-retryable errors (data not available)
            non_retryable_keywords = [
                'not found',
                'does not exist',
                'no data available',
                'invalid round',
                'invalid event',
                'by round number',       # "Cannot get testing event by round number!"
            ]
            
            if any(keyword in error_msg for keyword in non_retryable_keywords):
                logger.warning(f"⚠️ Data not available (will not retry): {e}")
                # Raise as NonRetryableError to prevent retries
                raise NonRetryableError(f"Data not available for {year} {event_identifier} {session_type}: {e}") from e
            
            # For other errors, log and re-raise (Prefect will retry)
            logger.error(f"❌ Failed to load session (will retry): {e}")
            raise


@task(name="Check Session Loadable")
def check_session_loadable(year: int, round_num: int, session_type: str) -> bool:
    """
    Check if a session can be loaded from FastF1.
    
    Some sessions may not have data available (old seasons, canceled sessions, etc.).
    This task attempts to verify if data exists before attempting full load.
    
    Args:
        year: Season year
        round_num: Race round number
        session_type: Django session type
    
    Returns:
        bool: True if session appears loadable
    """
    logger = get_run_logger()
    
    fastf1_identifier = get_fastf1_session_identifier(session_type)
    
    try:
        # Get session without loading (quick check)
        f1_session = fastf1.get_session(year, round_num, fastf1_identifier)
        
        # Basic validation
        if f1_session is None:
            logger.warning(f"Session {year} R{round_num} {session_type} not found")
            return False
        
        logger.debug(f"Session {year} R{round_num} {session_type} appears loadable")
        return True
        
    except Exception as e:
        logger.warning(f"Session {year} R{round_num} {session_type} not loadable: {e}")
        return False


@task(name="Get Session Info")
def get_session_info(year: int, round_num: int, session_type: str) -> dict:
    """
    Get basic session information without loading full data.
    
    Useful for planning and validation before expensive load operations.
    
    Args:
        year: Season year
        round_num: Race round number
        session_type: Django session type
    
    Returns:
        dict: Session metadata
    """
    logger = get_run_logger()
    
    fastf1_identifier = get_fastf1_session_identifier(session_type)
    
    try:
        f1_session = fastf1.get_session(year, round_num, fastf1_identifier)
        
        return {
            'year': year,
            'round': round_num,
            'session_type': session_type,
            'fastf1_identifier': fastf1_identifier,
            'name': getattr(f1_session, 'name', None),
            'date': getattr(f1_session, 'date', None),
        }
        
    except Exception as e:
        logger.warning(f"Could not get session info: {e}")
        return {
            'year': year,
            'round': round_num,
            'session_type': session_type,
            'error': str(e)
        }
