"""
Prefect tasks for loading FastF1 sessions.

These tasks handle the actual API calls to load session data,
with automatic caching via Prefect to avoid duplicate loads.
"""

from datetime import timedelta
from typing import Any, Optional
import fastf1
from django.conf import settings
from prefect import task, get_run_logger
from prefect.cache_policies import INPUTS

from .utils import get_fastf1_session_identifier, generate_session_cache_key
from .rate_limiter import record_api_call


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


@task(
    name="Load FastF1 Session",
    cache_policy=INPUTS,
    cache_expiration=timedelta(hours=1),
    retries=settings.FASTF1_TASK_RETRIES,
    retry_delay_seconds=settings.FASTF1_TASK_RETRY_DELAY
)
def load_fastf1_session(
    year: int,
    round_num: int,
    session_type: str,
    session_id: int,
    event_name: Optional[str] = None
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
    
    # Use event name for testing events, round number for regular events
    event_identifier = event_name if event_name else round_num
    
    logger.info(
        f"Loading FastF1 session: {year} {event_identifier} {session_type} "
        f"(identifier: {fastf1_identifier})"
    )
    
    try:
        # This is the actual API call that counts against rate limit
        # Use event_name for testing events, round_num for regular races
        f1_session = fastf1.get_session(year, event_identifier, fastf1_identifier)
        
        # Load session data (this downloads telemetry)
        logger.info(f"Calling session.load() - this may take 30-60 seconds...")
        f1_session.load()
        
        # Record API call for rate limit tracking
        record_api_call(session_id)
        
        logger.info(f"✅ Successfully loaded {year} {event_identifier} {session_type}")
        
        return f1_session
        
    except fastf1.req.RateLimitExceededError as e:
        logger.error(f"❌ Rate limit exceeded: {e}")
        raise
    
    except Exception as e:
        error_msg = str(e).lower()
        
        # Check for non-retryable errors (data not available)
        non_retryable_keywords = [
            'not found',
            'does not exist',
            'no data available',
            'invalid round',
            'invalid event',
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
