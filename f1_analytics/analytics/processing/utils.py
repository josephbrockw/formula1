"""
Utility functions for FastF1 data processing.

Helper functions for querying session load status, discovering data gaps,
and managing the import pipeline.
"""

from typing import List, Dict, Optional
from django.db.models import Q, Prefetch
from prefect import task, get_run_logger


@task(name="Get Sessions Without Data")
def get_sessions_without_data(
    year: int,
    data_type: str,
    force: bool = False
) -> List[Dict]:
    """
    Find sessions that are missing a specific data type.
    
    Args:
        year: Season year
        data_type: Type of data to check ('circuit', 'weather', 'laps', 'telemetry')
        force: If True, return all sessions regardless of status
    
    Returns:
        List of dicts with session info and missing data flags
    """
    from analytics.models import Session, SessionLoadStatus, Race, Season
    
    logger = get_run_logger()
    
    try:
        season = Season.objects.get(year=year)
    except Season.DoesNotExist:
        logger.error(f"Season {year} not found")
        return []
    
    # Get all sessions for the year
    sessions = Session.objects.filter(
        race__season=season
    ).select_related(
        'race', 'race__circuit', 'race__season'
    ).prefetch_related(
        'load_status'
    ).order_by('race__round_number', 'session_number')
    
    result = []
    
    for session in sessions:
        # Check if data is already loaded
        has_data = False
        
        if not force and hasattr(session, 'load_status'):
            status = session.load_status
            if data_type == 'circuit':
                has_data = status.has_circuit
            elif data_type == 'weather':
                has_data = status.has_weather
            elif data_type == 'laps':
                has_data = status.has_lap_times
            elif data_type == 'telemetry':
                has_data = status.has_telemetry
        
        # Add to result if data is missing or force=True
        if not has_data or force:
            result.append({
                'session_id': session.id,
                'session': session,
                'session_type': session.session_type,
                'race': session.race,
                'round_number': session.race.round_number,
                'year': year,
                'has_data': has_data,
            })
    
    logger.info(f"Found {len(result)} sessions needing {data_type} data")
    
    return result


@task(name="Discover Data Gaps")
def discover_data_gaps(
    year: int,
    data_types: Optional[List[str]] = None
) -> Dict[str, List[Dict]]:
    """
    Discover what data is missing across all data types.
    
    Args:
        year: Season year
        data_types: List of data types to check (defaults to all)
    
    Returns:
        Dict mapping data_type to list of sessions needing that data
    """
    logger = get_run_logger()
    
    if data_types is None:
        data_types = ['circuit', 'weather', 'laps', 'telemetry']
    
    gaps = {}
    
    for data_type in data_types:
        sessions = get_sessions_without_data(year, data_type, force=False)
        gaps[data_type] = sessions
        logger.info(f"{data_type}: {len(sessions)} sessions missing")
    
    return gaps


@task(name="Get or Create Load Status")
def get_or_create_load_status(session_id: int):
    """
    Get or create SessionLoadStatus for a session.
    
    Args:
        session_id: ID of the session
    
    Returns:
        SessionLoadStatus instance
    """
    from analytics.models import Session, SessionLoadStatus
    
    try:
        session = Session.objects.get(id=session_id)
        status, created = SessionLoadStatus.objects.get_or_create(session=session)
        
        if created:
            logger = get_run_logger()
            logger.debug(f"Created load status for {session}")
        
        return status
        
    except Session.DoesNotExist:
        raise ValueError(f"Session {session_id} not found")


@task(name="Mark Data Loaded")
def mark_data_loaded(
    session_id: int,
    data_type: str,
    flow_run_id: Optional[str] = None
):
    """
    Mark a data type as loaded for a session.
    
    Args:
        session_id: ID of the session
        data_type: Type of data that was loaded
        flow_run_id: Prefect flow run ID (optional)
    """
    from analytics.models import SessionLoadStatus, Session
    from django.utils import timezone
    
    logger = get_run_logger()
    
    try:
        session = Session.objects.get(id=session_id)
        status, _ = SessionLoadStatus.objects.get_or_create(session=session)
        
        # Use the model's method to mark loaded
        status.mark_loaded(data_type, timezone.now(), flow_run_id)
        
        logger.info(f"Marked {data_type} as loaded for {session}")
        
    except Session.DoesNotExist:
        logger.error(f"Session {session_id} not found")


def get_fastf1_session_identifier(session_type: str) -> str:
    """
    Convert Django session type to FastF1 session identifier.
    
    Args:
        session_type: Django Session.session_type value
    
    Returns:
        FastF1 session identifier (FP1, FP2, FP3, Q, SQ, S, R)
    """
    mapping = {
        'Practice 1': 'FP1',
        'Practice 2': 'FP2',
        'Practice 3': 'FP3',
        'Qualifying': 'Q',
        'Sprint Qualifying': 'SQ',
        'Sprint': 'S',
        'Race': 'R',
    }
    return mapping.get(session_type, 'R')


def generate_session_cache_key(year: int, round_num: int, session_type: str) -> str:
    """
    Generate cache key for FastF1 session.
    
    Args:
        year: Season year
        round_num: Race round number
        session_type: FastF1 session identifier
    
    Returns:
        Cache key string
    """
    return f"fastf1_session_{year}_{round_num}_{session_type}"
