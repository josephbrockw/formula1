"""
Import driver information from FastF1 sessions.

This flow extracts and updates driver data from FastF1 session results,
including:
- Full names
- Driver numbers
- Abbreviations
- Team information

This should run BEFORE telemetry import to ensure driver identifiers
are populated for accurate matching.

Architecture:
    1. Extract driver info from session.results
    2. Match or create Driver records
    3. Update identifiers (driver_number, abbreviation)
    4. Update team assignments
"""

from typing import Dict, List, Optional, Set
from prefect import task, get_run_logger
import pandas as pd

from analytics.processing.driver_matching import find_driver_by_fastf1_data


def _count_safety_car_laps(f1_session, logger) -> Optional[int]:
    """
    Count unique lap numbers where a safety car or VSC was deployed.

    Uses the TrackStatus column from session laps:
      '4' = Safety Car deployed
      '6' = Virtual Safety Car (VSC)
      '7' = Safety Car ending / Slow zone
    """
    try:
        laps_df = f1_session.laps
        if laps_df is None or laps_df.empty:
            return None
        if 'TrackStatus' not in laps_df.columns or 'LapNumber' not in laps_df.columns:
            return None

        sc_mask = laps_df['TrackStatus'].astype(str).str.contains(r'[467]', na=False)
        sc_lap_numbers: Set = set(laps_df.loc[sc_mask, 'LapNumber'].dropna().astype(int).tolist())
        count = len(sc_lap_numbers)
        logger.info(f"Safety car laps detected: {count}")
        return count
    except Exception as exc:
        logger.warning(f"Could not count safety car laps: {exc}")
        return None


@task(name="Extract Driver Info from Session")
def extract_driver_info(f1_session) -> Optional[Dict]:
    """
    Extract driver information from FastF1 session results.
    
    FastF1 provides driver data in session.results with fields:
    - FullName: Complete driver name
    - DriverNumber: Racing number (e.g., "1", "44", "55")
    - Abbreviation: Three-letter code (e.g., "VER", "HAM", "SAI")
    - TeamName: Full team name
    - TeamColor: Team color hex code
    
    Args:
        f1_session: Loaded FastF1 Session object
    
    Returns:
        dict: {
            'drivers': List of driver dicts with extracted info,
            'session_id': Session ID for reference
        }
    """
    logger = get_run_logger()
    
    try:
        # Check if session has results
        if not hasattr(f1_session, 'results') or f1_session.results is None or f1_session.results.empty:
            logger.warning("No session results available - cannot extract driver info")
            return None
        
        results_df = f1_session.results
        logger.info(f"Processing driver info from session with {len(results_df)} drivers")
        
        drivers_data = []

        for _, driver_result in results_df.iterrows():
            # Convert FastF1 Time (timedelta gap to leader) to total seconds
            time_val = driver_result.get('Time')
            if time_val is not None and hasattr(time_val, 'total_seconds') and pd.notna(time_val):
                time_str = str(round(time_val.total_seconds(), 3))
            else:
                time_str = ''

            # Extract championship points (float, 0 if missing)
            points_val = driver_result.get('Points')
            if points_val is not None and pd.notna(points_val):
                points = float(points_val)
            else:
                points = None

            # Extract all available driver fields
            driver_dict = {
                'full_name': str(driver_result.get('FullName', '')),
                'driver_number': str(driver_result.get('DriverNumber', '')),
                'abbreviation': str(driver_result.get('Abbreviation', '')),
                'team_name': str(driver_result.get('TeamName', '')),
                'team_color': str(driver_result.get('TeamColor', '')),
                'position': int(driver_result.get('Position', 0)) if pd.notna(driver_result.get('Position')) else None,
                'grid_position': int(driver_result.get('GridPosition', 0)) if pd.notna(driver_result.get('GridPosition')) else None,
                'status': str(driver_result.get('Status', '')),
                'points': points,
                'time': time_str,
            }

            # Skip if missing critical fields
            if not driver_dict['full_name'] or not driver_dict['driver_number']:
                logger.warning(f"Skipping driver with missing critical data: {driver_dict}")
                continue

            drivers_data.append(driver_dict)

        logger.info(f"Extracted info for {len(drivers_data)} drivers")

        # Count safety car / VSC laps from lap track status
        safety_car_laps = _count_safety_car_laps(f1_session, logger)

        return {
            'drivers': drivers_data,
            'safety_car_laps': safety_car_laps,
        }
        
    except Exception as e:
        logger.error(f"Failed to extract driver info: {e}")
        return None


@task(name="Save Driver Info to DB")
def save_driver_info_to_db(session_id: int, driver_data: Dict) -> Dict:
    """
    Save or update driver information and session results in the database.
    
    Creates new drivers if they don't exist, or updates existing drivers
    with FastF1 identifiers and team information. Also creates SessionResult
    records for each driver in this session.
    
    Args:
        session_id: Database ID of the session
        driver_data: Dict with 'drivers' list from extract_driver_info
    
    Returns:
        dict: Summary with counts of created/updated drivers and results
    """
    from analytics.models import Driver, Team, Session, SessionResult
    
    logger = get_run_logger()
    
    drivers_created = 0
    drivers_updated = 0
    drivers_skipped = 0
    results_created = 0
    
    try:
        session = Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        logger.error(f"Session {session_id} not found")
        return {
            'status': 'failed',
            'error': f'Session {session_id} not found',
            'drivers_created': 0,
            'drivers_updated': 0,
            'drivers_skipped': 0,
            'results_created': 0,
        }
    
    try:
        for driver_dict in driver_data.get('drivers', []):
            full_name = driver_dict['full_name']
            driver_number = driver_dict['driver_number']
            abbreviation = driver_dict['abbreviation']
            team_name = driver_dict['team_name']
            
            try:
                # Try to find existing driver using matching utility
                driver, match_method = find_driver_by_fastf1_data(
                    full_name=full_name,
                    driver_number=driver_number,
                    abbreviation=abbreviation,
                    create_if_missing=True  # Auto-create if not found
                )
                
                if not driver:
                    logger.warning(f"Could not create driver: {full_name}")
                    drivers_skipped += 1
                    continue
                
                # Track if this is a new driver
                is_new = match_method == "created_new"
                
                # Update driver fields
                updated = False
                
                # Update driver_number if different
                if driver.driver_number != driver_number:
                    old_val = driver.driver_number or '(empty)'
                    driver.driver_number = driver_number
                    updated = True
                    logger.info(f"Updated {driver.full_name} driver_number: {old_val} → {driver_number}")
                
                # Update abbreviation if different
                if driver.abbreviation != abbreviation:
                    old_val = driver.abbreviation or '(empty)'
                    driver.abbreviation = abbreviation
                    updated = True
                    logger.info(f"Updated {driver.full_name} abbreviation: {old_val} → {abbreviation}")
                
                # Update team if provided
                if team_name:
                    team, _ = Team.objects.get_or_create(
                        name=team_name,
                        defaults={'short_name': team_name[:3].upper()}
                    )
                    if driver.current_team != team:
                        old_team = driver.current_team.name if driver.current_team else '(none)'
                        driver.current_team = team
                        updated = True
                        logger.info(f"Updated {driver.full_name} team: {old_team} → {team_name}")
                
                # Save if updated
                if updated:
                    driver.save()
                    if is_new:
                        drivers_created += 1
                    else:
                        drivers_updated += 1
                elif is_new:
                    drivers_created += 1
                
                # Create or update SessionResult for this driver
                team = driver.current_team if driver.current_team else None
                
                session_result, result_created = SessionResult.objects.update_or_create(
                    session=session,
                    driver=driver,
                    defaults={
                        'team': team,
                        'position': driver_dict.get('position'),
                        'grid_position': driver_dict.get('grid_position'),
                        'status': driver_dict.get('status', ''),
                        'driver_number': driver_number,
                        'abbreviation': abbreviation,
                        'points': driver_dict.get('points'),
                        'time': driver_dict.get('time', ''),
                    }
                )
                
                if result_created:
                    results_created += 1
                
            except Exception as e:
                logger.error(f"Error processing driver {full_name}: {e}")
                drivers_skipped += 1
                continue
        
        # Update session safety_car_laps if provided
        safety_car_laps = driver_data.get('safety_car_laps')
        if safety_car_laps is not None:
            session.safety_car_laps = safety_car_laps
            session.save(update_fields=['safety_car_laps'])
            logger.info(f"Updated session safety_car_laps = {safety_car_laps}")

        logger.info(
            f"Driver info saved: {drivers_created} created, "
            f"{drivers_updated} updated, {drivers_skipped} skipped, "
            f"{results_created} session results created"
        )

        return {
            'status': 'success',
            'drivers_created': drivers_created,
            'drivers_updated': drivers_updated,
            'drivers_skipped': drivers_skipped,
            'results_created': results_created,
        }
        
    except Exception as e:
        logger.error(f"Failed to save driver info: {e}")
        return {
            'status': 'failed',
            'error': str(e),
            'drivers_created': drivers_created,
            'drivers_updated': drivers_updated,
            'drivers_skipped': drivers_skipped,
            'results_created': results_created,
        }
