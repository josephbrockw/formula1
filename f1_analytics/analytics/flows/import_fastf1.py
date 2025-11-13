"""
Master FastF1 Import Pipeline.

This is the main orchestration flow for importing all F1 data from FastF1 API.
It implements a smart, efficient approach that:

1. **Gap Detection**: Scans the database to find missing data
2. **Optimization**: Groups imports to minimize API calls (session-once-extract-many)
3. **Rate Limiting**: Respects FastF1 API limits and pauses when needed
4. **Resumability**: Can be stopped and resumed without losing progress
5. **Notifications**: Reports progress and completion status

Architecture:
    Master Flow (import_fastf1_flow)
      ├─> Gap Detection (generate_gap_report)
      ├─> Check Rate Limit
      ├─> Process Each Session Gap
      │     ├─> Load FastF1 Session (once)
      │     ├─> Extract Driver Info (FIRST - populates identifiers)
      │     ├─> Extract Weather (if missing)
      │     ├─> Extract Circuit Data (if missing)
      │     └─> Extract Telemetry Data (laps, pit stops)
      └─> Send Summary Notification

Usage:
    # Import full season
    import_fastf1_flow(year=2024)
    
    # Import specific race
    import_fastf1_flow(year=2024, round_number=5)
    
    # Force re-import all data
    import_fastf1_flow(year=2024, force=True)
"""

from typing import Optional, Dict, List
from prefect import flow, task, get_run_logger
from prefect.cache_policies import NONE
from datetime import datetime

from analytics.processing.gap_detection import SessionGap
from analytics.processing.session_processor import get_sessions_to_process
# Rate limiting now handled reactively in loaders.py
# When FastF1 hits limit, it raises RateLimitExceededError
# loaders.py catches it, calls wait_for_rate_limit(), and retries
from analytics.flows.import_weather import (
    extract_weather_data,
    save_weather_to_db
)
from analytics.flows.import_circuit import (
    extract_circuit_data,
    save_circuit_to_db
)
from analytics.flows.import_telemetry import (
    extract_lap_data,
    save_telemetry_to_db
)
from analytics.flows.import_drivers import (
    extract_driver_info,
    save_driver_info_to_db
)
from analytics.processing.loaders import load_fastf1_session
from config.notifications import send_slack_notification_async


@task(name="Process Session Gap")
def process_session_gap(gap: SessionGap, force: bool = False) -> Dict:
    """
    Process a single session gap using session-once-extract-many pattern.
    
    This is the core efficiency optimization: we load the FastF1 session
    once and extract ALL needed data types from that single load.
    
    Args:
        gap: SessionGap describing what's missing
        force: If True, re-extract even if data exists
        
    Returns:
        Dict with status and what was extracted
    """
    logger = get_run_logger()
    logger.info(f"Processing gap: {gap}")
    
    result = {
        'session_id': gap.session_id,
        'year': gap.year,
        'round': gap.round_number,
        'session_type': gap.session_type,
        'extracted': [],
        'failed': [],
        'status': 'success'
    }
    
    if not gap.session_id:
        logger.warning(f"Cannot process gap - session doesn't exist in database yet")
        result['status'] = 'skipped'
        result['reason'] = 'session_not_created'
        return result
    
    try:
        # STEP 1: Load FastF1 session ONCE
        # This is the expensive API call - we only do it once per session
        logger.info(f"Loading FastF1 session for {gap.year} Round {gap.round_number} {gap.session_type}")
        
        fastf1_session = load_fastf1_session(
            year=gap.year,
            round_num=gap.round_number,
            session_type=gap.session_type,
            session_id=gap.session_id,
            event_name=gap.event_name,
            force=force  # Pass through to bypass Prefect cache
        )
        
        # STEP 2: Extract and save driver information FIRST
        # This ensures driver_number and abbreviation fields are populated
        # before telemetry import attempts to match drivers
        try:
            logger.info("Extracting driver information from session")
            
            # Extract driver info from the already-loaded session
            driver_data = extract_driver_info.fn(fastf1_session)
            
            if driver_data:
                # Save to database
                save_result = save_driver_info_to_db.fn(gap.session_id, driver_data)
                
                if save_result['status'] == 'success':
                    result['extracted'].append('drivers')
                    logger.info(
                        f"Driver info: {save_result.get('drivers_created', 0)} created, "
                        f"{save_result.get('drivers_updated', 0)} updated, "
                        f"{save_result.get('results_created', 0)} session results"
                    )
                else:
                    result['failed'].append('drivers')
                    logger.warning(f"Driver save failed: {save_result.get('error')}")
            else:
                logger.info("No driver data available for this session")
                
        except Exception as e:
            logger.error(f"Error extracting drivers: {e}")
            result['failed'].append('drivers')
        
        # STEP 3: Extract weather data
        # Since gap detection only flags sessions with missing weather,
        # we know this session needs weather extraction
        try:
            logger.info("Extracting weather data from session")
            
            # Import here to avoid circular imports
            from analytics.flows.import_weather import extract_weather_data
            
            # Extract weather from the already-loaded session
            weather_data = extract_weather_data.fn(fastf1_session)
            
            if weather_data:
                # Save to database
                save_result = save_weather_to_db.fn(gap.session_id, weather_data)
                
                if save_result['status'] == 'success':
                    result['extracted'].append('weather')
                    logger.info("Weather data extracted and saved successfully")
                else:
                    result['failed'].append('weather')
                    logger.warning(f"Weather save failed: {save_result.get('error')}")
            else:
                logger.warning("No weather data available for this session")
                result['failed'].append('weather')
                
        except Exception as e:
            logger.error(f"Error extracting weather: {e}")
            result['failed'].append('weather')
        
        # STEP 4: Extract circuit data
        # Extract circuit information (corners, marshal lights, sectors)
        try:
            logger.info("Extracting circuit data from session")
            
            # Extract circuit from the already-loaded session
            circuit_data = extract_circuit_data.fn(fastf1_session)
            
            if circuit_data:
                # Save to database
                save_result = save_circuit_to_db.fn(gap.session_id, circuit_data)
                
                if save_result['status'] == 'success':
                    result['extracted'].append('circuit')
                    counts = save_result.get('counts', {})
                    logger.info(
                        f"Circuit data extracted: {counts.get('corners', 0)} corners, "
                        f"{counts.get('marshal_lights', 0)} lights, {counts.get('marshal_sectors', 0)} sectors"
                    )
                else:
                    result['failed'].append('circuit')
                    logger.warning(f"Circuit save failed: {save_result.get('error')}")
            else:
                logger.info("No circuit data available for this session")
                # Not a failure - some sessions just don't have detailed circuit data
                
        except Exception as e:
            logger.error(f"Error extracting circuit: {e}")
            result['failed'].append('circuit')
        
        # STEP 5: Extract telemetry data (laps, pit stops)
        # Extract lap times, sector times, tire data, and pit stops
        # Driver info is already populated from STEP 2, so matching should be fast and accurate
        try:
            logger.info("Extracting telemetry data from session")
            
            # Extract telemetry from the already-loaded session
            telemetry_data = extract_lap_data.fn(fastf1_session)
            
            if telemetry_data:
                # Save to database (pass f1_session for telemetry metrics extraction)
                save_result = save_telemetry_to_db.fn(gap.session_id, telemetry_data, fastf1_session)
                
                if save_result['status'] == 'success':
                    result['extracted'].append('telemetry')
                    logger.info(
                        f"Telemetry data extracted: {save_result.get('laps_created', 0)} laps, "
                        f"{save_result.get('telemetry_created', 0)} telemetry metrics, "
                        f"{save_result.get('pit_stops_created', 0)} pit stops"
                    )
                else:
                    result['failed'].append('telemetry')
                    logger.warning(f"Telemetry save failed: {save_result.get('error')}")
            else:
                logger.warning("No telemetry data available for this session")
                result['failed'].append('telemetry')
                
        except Exception as e:
            logger.error(f"Error extracting telemetry: {e}")
            result['failed'].append('telemetry')
        
        # FUTURE: Add more extractors as needed
        # When adding a new extractor:
        # 1. Add missing_xxx field to SessionGap if needed
        # 2. Update detect_session_data_gaps() to check for missing xxx if needed
        # 3. Add extraction logic here: extract_xxx(fastf1_session)
        
        # Mark as failed if any extractions failed
        if result['failed']:
            result['status'] = 'partial'
        
    except Exception as e:
        logger.error(f"Failed to process session gap: {e}")
        result['status'] = 'failed'
        result['error'] = str(e)
    
    return result


@flow(name="Import FastF1 Master Pipeline", log_prints=True)
def import_fastf1_flow(
    year: int,
    round_number: Optional[int] = None,
    force: bool = False,
    notify: bool = False
) -> Dict:
    """
    Master flow for importing F1 data from FastF1 API.
    
    This is the main entry point for all FastF1 data imports. It:
    1. Detects what data is missing
    2. Checks rate limits
    3. Optimally extracts data (session-once-extract-many)
    4. Respects API limits and pauses when needed
    5. Reports progress and sends notifications
    
    Args:
        year: Season year to import
        round_number: Specific race round (optional, if None imports whole season)
        force: If True, re-import data even if it exists
        notify: If True, send Slack notifications
        
    Returns:
        Summary dict with import statistics
    """
    logger = get_run_logger()
    start_time = datetime.now()
    
    logger.info(f"=" * 80)
    logger.info(f"FastF1 Master Import Pipeline - Season {year}")
    if round_number:
        logger.info(f"Target: Round {round_number} only")
    else:
        logger.info(f"Target: Full season")
    logger.info(f"Force mode: {force}")
    logger.info(f"=" * 80)
    
    summary = {
        'year': year,
        'round_number': round_number,
        'force': force,
        'gaps_detected': 0,
        'sessions_processed': 0,
        'sessions_succeeded': 0,
        'sessions_failed': 0,
        'data_extracted': {
            'weather': 0,
            'circuit': 0,
            'telemetry': 0,
        },
        'status': 'running',
        'start_time': start_time.isoformat(),
    }
    
    try:
        # PHASE 1: DETERMINE SESSIONS TO PROCESS
        logger.info("\n" + "=" * 80)
        logger.info("PHASE 1: Determine Sessions to Process")
        logger.info("=" * 80)
        
        sessions_to_process = get_sessions_to_process(
            year=year,
            round_number=round_number,
            force=force
        )
        
        summary['gaps_detected'] = len(sessions_to_process)
        logger.info(f"📋 Will process {len(sessions_to_process)} sessions")
        
        # PHASE 2: PROCESS SESSIONS
        logger.info("\n" + "=" * 80)
        logger.info("PHASE 2: Process Sessions")
        logger.info("=" * 80)
        
        for i, gap in enumerate(sessions_to_process, 1):
            logger.info(f"\nProcessing session {i}/{len(sessions_to_process)}: {gap}")
            
            # Process the session
            # If FastF1 hits rate limit, loaders.py will auto-pause for 1 hour and retry
            result = process_session_gap(gap, force)
            summary['sessions_processed'] += 1
            
            if result['status'] == 'success':
                summary['sessions_succeeded'] += 1
                logger.info(f"✅ Success - extracted: {', '.join(result['extracted'])}")
                
                # Count extractions
                for data_type in result['extracted']:
                    if data_type in summary['data_extracted']:
                        summary['data_extracted'][data_type] += 1
                        
            elif result['status'] == 'partial':
                summary['sessions_succeeded'] += 1
                logger.warning(f"⚠️  Partial - extracted: {', '.join(result['extracted'])}, failed: {', '.join(result['failed'])}")
            else:
                summary['sessions_failed'] += 1
                logger.error(f"❌ Failed - {result.get('error', 'Unknown error')}")
        
        # PHASE 5: COMPLETION
        logger.info("\n" + "=" * 80)
        logger.info("PHASE 5: Pipeline Complete")
        logger.info("=" * 80)
        
        summary['status'] = 'complete'
        end_time = datetime.now()
        summary['end_time'] = end_time.isoformat()
        summary['duration_seconds'] = (end_time - start_time).total_seconds()
        
        logger.info(f"\nFinal Summary:")
        logger.info(f"  • Sessions processed: {summary['sessions_processed']}")
        logger.info(f"  • Succeeded: {summary['sessions_succeeded']}")
        logger.info(f"  • Failed: {summary['sessions_failed']}")
        logger.info(f"  • Weather extracted: {summary['data_extracted']['weather']}")
        logger.info(f"  • Circuit extracted: {summary['data_extracted']['circuit']}")
        logger.info(f"  • Telemetry extracted: {summary['data_extracted']['telemetry']}")
        logger.info(f"  • Duration: {summary['duration_seconds']:.1f}s")
        
        # Send notification if requested
        if notify:
            send_completion_notification(summary)
        
    except Exception as e:
        logger.error(f"❌ Pipeline failed with error: {e}")
        summary['status'] = 'failed'
        summary['error'] = str(e)
        
        if notify:
            send_failure_notification(summary, str(e))
    
    return summary


@task(name="Send Completion Notification")
async def send_completion_notification(summary: Dict):
    """Send Slack notification on successful completion"""
    logger = get_run_logger()
    
    message = f"✅ FastF1 Import Complete - Season {summary['year']}"
    
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*FastF1 Import Pipeline Complete*\n*Season:* {summary['year']}"
            }
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Sessions Processed:*\n{summary['sessions_processed']}"},
                {"type": "mrkdwn", "text": f"*Succeeded:*\n{summary['sessions_succeeded']}"},
                {"type": "mrkdwn", "text": f"*Failed:*\n{summary['sessions_failed']}"},
                {"type": "mrkdwn", "text": f"*Weather:*\n{summary['data_extracted']['weather']}"},
                {"type": "mrkdwn", "text": f"*Circuit:*\n{summary['data_extracted']['circuit']}"},
                {"type": "mrkdwn", "text": f"*Telemetry:*\n{summary['data_extracted']['telemetry']}"},
                {"type": "mrkdwn", "text": f"*Duration:*\n{summary.get('duration_seconds', 0):.1f}s"}
            ]
        }
    ]
    
    try:
        await send_slack_notification_async(message, blocks)
        logger.info("Slack notification sent successfully")
    except Exception as e:
        logger.warning(f"Failed to send Slack notification: {e}")


@task(name="Send Failure Notification")
async def send_failure_notification(summary: Dict, error: str):
    """Send Slack notification on pipeline failure"""
    logger = get_run_logger()
    
    message = f"❌ FastF1 Import Failed - Season {summary['year']}"
    
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*FastF1 Import Pipeline Failed*\n*Season:* {summary['year']}\n*Error:* {error}"
            }
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Sessions Processed:*\n{summary['sessions_processed']}"},
                {"type": "mrkdwn", "text": f"*Succeeded:*\n{summary['sessions_succeeded']}"},
                {"type": "mrkdwn", "text": f"*Failed:*\n{summary['sessions_failed']}"}
            ]
        }
    ]
    
    try:
        await send_slack_notification_async(message, blocks)
        logger.info("Failure notification sent successfully")
    except Exception as e:
        logger.warning(f"Failed to send Slack notification: {e}")
