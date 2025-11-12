"""
Prefect flow for importing weather data from FastF1.

This flow orchestrates the weather import process:
1. Discover which sessions need weather data
2. Check rate limits
3. Load FastF1 sessions (with automatic caching)
4. Extract weather data
5. Save to database
6. Update load status

Features:
- Automatic session caching (avoids duplicate API calls)
- Rate limit management (auto-pause when limit reached)
- Error handling (continues on individual session failures)
- Progress tracking
"""

from typing import List, Dict, Optional
from prefect import flow, task, get_run_logger
from prefect.cache_policies import INPUTS
from datetime import timedelta

# Import processing tasks
from analytics.processing.loaders import load_fastf1_session
from analytics.processing.utils import (
    get_sessions_without_data,
    mark_data_loaded,
)


@task(name="Extract Weather from Session")
def extract_weather_data(f1_session) -> Optional[Dict]:
    """
    Extract weather data from a loaded FastF1 session.
    
    Args:
        f1_session: Loaded FastF1 Session object
    
    Returns:
        dict: Weather data with keys: air_temperature, track_temperature,
              humidity, pressure, wind_speed, wind_direction, rainfall
    """
    logger = get_run_logger()
    
    try:
        # Get weather data DataFrame from FastF1
        weather_df = f1_session.weather_data
        
        if weather_df is None or weather_df.empty:
            logger.warning("No weather data available for session")
            return None
        
        # Calculate median values (robust against outliers)
        weather_data = {}
        
        if 'AirTemp' in weather_df.columns:
            weather_data['air_temperature'] = float(weather_df['AirTemp'].median())
        
        if 'TrackTemp' in weather_df.columns:
            weather_data['track_temperature'] = float(weather_df['TrackTemp'].median())
        
        if 'Humidity' in weather_df.columns:
            weather_data['humidity'] = float(weather_df['Humidity'].median())
        
        if 'Pressure' in weather_df.columns:
            weather_data['pressure'] = float(weather_df['Pressure'].median())
        
        if 'WindSpeed' in weather_df.columns:
            weather_data['wind_speed'] = float(weather_df['WindSpeed'].median())
        
        if 'WindDirection' in weather_df.columns:
            weather_data['wind_direction'] = int(weather_df['WindDirection'].median())
        
        if 'Rainfall' in weather_df.columns:
            weather_data['rainfall'] = bool(weather_df['Rainfall'].any())
        
        logger.info(f"Extracted weather: {weather_data.get('air_temperature', 'N/A')}°C")
        
        return weather_data if weather_data else None
        
    except Exception as e:
        logger.error(f"Failed to extract weather data: {e}")
        return None


@task(name="Save Weather to Database")
def save_weather_to_db(session_id: int, weather_data: Dict, flow_run_id: Optional[str] = None):
    """
    Save weather data to database and update load status.
    
    Args:
        session_id: Django Session ID
        weather_data: Weather data dict
        flow_run_id: Prefect flow run ID for tracking
    """
    from analytics.models import Session, SessionWeather
    from django.utils import timezone
    
    logger = get_run_logger()
    
    try:
        session = Session.objects.get(id=session_id)
        
        # Create or update SessionWeather
        weather, created = SessionWeather.objects.update_or_create(
            session=session,
            defaults={
                'air_temperature': weather_data.get('air_temperature'),
                'track_temperature': weather_data.get('track_temperature'),
                'humidity': weather_data.get('humidity'),
                'pressure': weather_data.get('pressure'),
                'wind_speed': weather_data.get('wind_speed'),
                'wind_direction': weather_data.get('wind_direction'),
                'rainfall': weather_data.get('rainfall', False),
                'data_source': 'fastf1',
            }
        )
        
        action = "Created" if created else "Updated"
        logger.info(f"{action} weather for {session}: {weather.weather_summary}")
        
        # Update load status
        mark_data_loaded(session_id, 'weather', flow_run_id)
        
        return {'session_id': session_id, 'status': 'success', 'created': created}
        
    except Exception as e:
        logger.error(f"Failed to save weather for session {session_id}: {e}")
        return {'session_id': session_id, 'status': 'failed', 'error': str(e)}


@task(name="Process Session Weather")
def process_session_weather(session_info: Dict, force: bool = False) -> Dict:
    """
    Process weather data for a single session.
    
    This task:
    1. Loads FastF1 session (cached automatically by Prefect)
    2. Extracts weather data
    3. Saves to database
    
    Args:
        session_info: Session metadata dict
        force: Force re-import even if data exists
    
    Returns:
        dict: Processing result with status
    """
    from analytics.processing.loaders import NonRetryableError
    
    logger = get_run_logger()
    
    session_id = session_info['session_id']
    year = session_info['year']
    round_number = session_info['round_number']
    session_type = session_info['session_type']
    event_format = session_info.get('event_format', 'conventional')
    event_name = session_info.get('event_name')
    
    # For testing events, use event name instead of round number
    is_testing = event_format == 'testing'
    identifier = event_name if is_testing else f"R{round_number}"
    
    logger.info(f"Processing {year} {identifier} {session_type}")
    
    try:
        # Load FastF1 session (Prefect caches this automatically)
        # For testing events, pass event_name; for regular events, pass None (uses round_num)
        f1_session = load_fastf1_session(
            year=year,
            round_num=round_number,
            session_type=session_type,
            session_id=session_id,
            event_name=event_name if is_testing else None
        )
        
        # Extract weather data
        weather_data = extract_weather_data(f1_session)
        
        if not weather_data:
            logger.warning(f"No weather data available for {session_type}")
            return {
                'session_id': session_id,
                'status': 'no_data',
                'message': 'No weather data available'
            }
        
        # Save to database
        result = save_weather_to_db(session_id, weather_data)
        
        return result
    
    except NonRetryableError as e:
        # Data fundamentally not available (testing event, not found, etc.)
        logger.warning(f"⚠️ Session data not available: {e}")
        return {
            'session_id': session_id,
            'status': 'no_data',
            'message': str(e)
        }
        
    except Exception as e:
        # Other errors (transient failures after retries exhausted)
        logger.error(f"❌ Failed to process session {session_id}: {e}")
        return {
            'session_id': session_id,
            'status': 'error',
            'error': str(e)
        }


@flow(name="Import Weather Data", log_prints=True)
def import_weather_flow(year: int, force: bool = False) -> Dict:
    """
    Import weather data for all sessions in a season.
    
    This flow:
    1. Discovers sessions without weather data
    2. Checks rate limits before starting
    3. Processes each session (with automatic caching)
    4. Handles errors gracefully
    5. Returns summary statistics
    
    Args:
        year: Season year
        force: Force re-import even if data exists
    
    Returns:
        dict: Summary with success/failure counts
    """
    logger = get_run_logger()
    
    logger.info(f"{'='*60}")
    logger.info(f"Starting weather import for {year} season")
    logger.info(f"Force mode: {force}")
    logger.info(f"{'='*60}")
    
    # 1. Find sessions needing weather data
    logger.info("Discovering sessions without weather data...")
    sessions_to_process = get_sessions_without_data(year, 'weather', force)
    
    if not sessions_to_process:
        logger.info("✅ All sessions already have weather data!")
        return {
            'year': year,
            'sessions_found': 0,
            'sessions_processed': 0,
            'success': 0,
            'failed': 0,
            'no_data': 0,
        }
    
    logger.info(f"Found {len(sessions_to_process)} sessions needing weather data")
    
    # NOTE: Rate limiting is now handled reactively in loaders.py
    # FastF1 will raise RateLimitExceededError when limit is hit,
    # and loaders will automatically pause for 1 hour and retry
    
    # 2. Process each session
    results = []
    for i, session_info in enumerate(sessions_to_process, 1):
        logger.info(f"\n[{i}/{len(sessions_to_process)}] Processing session...")
        
        # Process session (errors are caught and logged)
        result = process_session_weather(session_info, force)
        results.append(result)
    
    # 4. Generate summary
    summary = {
        'year': year,
        'sessions_found': len(sessions_to_process),
        'sessions_processed': len(results),
        'success': sum(1 for r in results if r['status'] == 'success'),
        'failed': sum(1 for r in results if r['status'] == 'error'),
        'no_data': sum(1 for r in results if r['status'] == 'no_data'),
    }
    
    logger.info(f"\n{'='*60}")
    logger.info("Weather Import Complete!")
    logger.info(f"{'='*60}")
    logger.info(f"Sessions processed: {summary['sessions_processed']}")
    logger.info(f"  ✅ Success:       {summary['success']}")
    logger.info(f"  ❌ Failed:        {summary['failed']}")
    logger.info(f"  ⚠️  No data:       {summary['no_data']}")
    logger.info(f"{'='*60}")
    
    return summary
