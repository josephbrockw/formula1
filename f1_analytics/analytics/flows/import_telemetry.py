"""
Prefect flow for importing telemetry data from FastF1.

This flow orchestrates the telemetry import process:
1. Discover which sessions need telemetry data
2. Load FastF1 sessions (with automatic caching)
3. Extract lap data, telemetry metrics, and pit stops

Features:
- Automatic session caching (avoids duplicate API calls)
- Rate limit management (auto-pause when limit reached)
- Error handling (continues on individual session failures)
- Progress tracking
- Optimized for performance (aggregated telemetry metrics)

Data extracted:
- Lap: Individual lap times, sector times, tire data, positions
- Telemetry: Aggregated speed, throttle, brake, gear, RPM, DRS metrics
- PitStop: Pit stop timing and duration
"""

from typing import List, Dict, Optional
from prefect import task, get_run_logger
import pandas as pd

from analytics.processing.utils import mark_data_loaded
from analytics.processing.driver_matching import (
    find_driver_by_fastf1_data,
    update_driver_identifiers
)


@task(name="Extract Lap Data from Session")
def extract_lap_data(f1_session) -> Optional[Dict]:
    """
    Extract lap data from a loaded FastF1 session.
    
    Extracts lap times, sector times, tire compounds, and pit stop data
    for all drivers in the session.
    
    Args:
        f1_session: Loaded FastF1 Session object
    
    Returns:
        dict: Lap data with keys: laps (list of lap dicts), pit_stops (list of pit stop dicts)
    """
    logger = get_run_logger()
    
    try:
        # Get laps DataFrame from FastF1
        laps_df = f1_session.laps
        
        if laps_df is None or laps_df.empty:
            logger.warning("No lap data available for session")
            return None
        
        logger.info(f"Processing {len(laps_df)} laps from session")
        
        # Build driver info map from session results
        # FastF1 laps don't have FullName, need to get from session.results
        driver_info_map = {}
        try:
            if hasattr(f1_session, 'results') and f1_session.results is not None:
                for _, driver_result in f1_session.results.iterrows():
                    abbr = str(driver_result.get('Abbreviation', ''))
                    full_name = str(driver_result.get('FullName', ''))
                    driver_number = str(driver_result.get('DriverNumber', ''))
                    driver_info_map[abbr] = {
                        'full_name': full_name,
                        'driver_number': driver_number
                    }
                logger.info(f"Extracted info for {len(driver_info_map)} drivers from session.results")
                if not driver_info_map:
                    logger.warning("session.results is available but contains no driver data")
        except Exception as e:
            logger.warning(f"Could not extract driver info from session.results: {e}")
        
        # Extract lap data
        laps_data = []
        for idx, lap_row in laps_df.iterrows():
            # Get driver info from map
            driver_abbr = str(lap_row.get('Driver', ''))
            driver_info = driver_info_map.get(driver_abbr, {})
            
            # Convert lap data to dict
            lap_dict = {
                'driver_number': str(lap_row.get('DriverNumber', '')),
                'full_name': driver_info.get('full_name', ''),  # From session.results
                'lap_number': int(lap_row.get('LapNumber', 0)),
                'lap_time': _to_seconds(lap_row.get('LapTime')),
                'sector_1_time': _to_seconds(lap_row.get('Sector1Time')),
                'sector_2_time': _to_seconds(lap_row.get('Sector2Time')),
                'sector_3_time': _to_seconds(lap_row.get('Sector3Time')),
                'compound': str(lap_row.get('Compound', '')),
                'tire_life': int(lap_row.get('TyreLife', 0)) if pd.notna(lap_row.get('TyreLife')) else None,
                'fresh_tire': bool(lap_row.get('FreshTyre', False)),
                'track_status': str(lap_row.get('TrackStatus', '')),
                'position': int(lap_row.get('Position', 0)) if pd.notna(lap_row.get('Position')) else None,
                'pit_out_time': _to_seconds(lap_row.get('PitOutTime')),
                'pit_in_time': _to_seconds(lap_row.get('PitInTime')),
                'speed_i1': float(lap_row.get('SpeedI1', 0)) if pd.notna(lap_row.get('SpeedI1')) else None,
                'speed_i2': float(lap_row.get('SpeedI2', 0)) if pd.notna(lap_row.get('SpeedI2')) else None,
                'speed_fl': float(lap_row.get('SpeedFL', 0)) if pd.notna(lap_row.get('SpeedFL')) else None,
                'speed_st': float(lap_row.get('SpeedST', 0)) if pd.notna(lap_row.get('SpeedST')) else None,
                'is_personal_best': bool(lap_row.get('IsPersonalBest', False)),
                'is_accurate': bool(lap_row.get('IsAccurate', True)),
                'lap_start_time': _to_seconds(lap_row.get('LapStartTime')),
                # Store team abbreviation and driver abbreviation for later lookup
                'team_abbr': str(lap_row.get('Team', '')),
                'driver_abbr': str(lap_row.get('Driver', '')),  # 3-letter abbreviation
            }
            
            laps_data.append(lap_dict)
        
        logger.info(f"Extracted {len(laps_data)} laps")
        
        # Extract pit stop data
        pit_stops_data = []
        try:
            pit_stops_df = f1_session.laps.get_pos_changes()
            # Note: FastF1 doesn't have a direct pit_stops attribute,
            # we need to infer from lap data or use race results
            # For now, we'll extract from lap data where pit_in_time is not null
            
            for idx, lap_row in laps_df.iterrows():
                if pd.notna(lap_row.get('PitInTime')) and pd.notna(lap_row.get('PitOutTime')):
                    pit_in = _to_seconds(lap_row.get('PitInTime'))
                    pit_out = _to_seconds(lap_row.get('PitOutTime'))
                    
                    if pit_in and pit_out:
                        # Get driver info from map
                        driver_abbr = str(lap_row.get('Driver', ''))
                        driver_info = driver_info_map.get(driver_abbr, {})
                        
                        pit_stop = {
                            'driver_number': str(lap_row.get('DriverNumber', '')),
                            'full_name': driver_info.get('full_name', ''),
                            'lap_number': int(lap_row.get('LapNumber', 0)),
                            'pit_in_time': pit_in,
                            'pit_out_time': pit_out,
                            'pit_duration': pit_out - pit_in,
                        }
                        pit_stops_data.append(pit_stop)
            
            logger.info(f"Extracted {len(pit_stops_data)} pit stops")
        except Exception as e:
            logger.warning(f"Could not extract pit stops: {e}")
        
        return {
            'laps': laps_data,
            'pit_stops': pit_stops_data
        }
        
    except Exception as e:
        logger.error(f"Failed to extract lap data: {e}")
        return None


@task(name="Save Telemetry to Database")
def save_telemetry_to_db(session_id: int, telemetry_data: Dict, f1_session=None, flow_run_id: Optional[str] = None):
    """
    Save telemetry data to database and update load status.
    
    Saves lap data, aggregated telemetry metrics, and pit stops to database.
    
    Args:
        session_id: Django Session ID
        telemetry_data: Telemetry data dict with laps and pit_stops
        f1_session: FastF1 session object (optional, for telemetry metrics extraction)
        flow_run_id: Prefect flow run ID for tracking
    """
    from analytics.models import Session, Driver, Team, Lap, Telemetry, PitStop
    
    logger = get_run_logger()
    
    try:
        session = Session.objects.get(id=session_id)
        
        laps_created = 0
        telemetry_created = 0
        pit_stops_created = 0
        
        # Create a mapping of driver numbers to Driver objects
        driver_map = {}
        team_map = {}
        
        # STEP 1: Save laps
        for lap_dict in telemetry_data.get('laps', []):
            driver_number = lap_dict['driver_number']
            full_name = lap_dict.get('full_name', '')
            abbreviation = lap_dict.get('driver_abbr', '')
            
            # Get or cache driver lookup
            if driver_number not in driver_map:
                try:
                    # Use robust matching utility to find driver
                    driver, match_method = find_driver_by_fastf1_data(
                        full_name=full_name,
                        driver_number=driver_number,
                        abbreviation=abbreviation,
                        create_if_missing=False  # Don't auto-create; log for manual review
                    )
                    
                    if not driver:
                        logger.warning(
                            f"Driver not found: {full_name} (#{driver_number}, {abbreviation}). "
                            f"Skipping lap. May need manual driver creation."
                        )
                        continue
                    
                    # Update driver identifiers if needed
                    update_driver_identifiers(
                        driver=driver,
                        driver_number=driver_number,
                        abbreviation=abbreviation,
                        fastf1_name=full_name
                    )
                    
                    driver_map[driver_number] = driver
                    logger.debug(f"Matched driver via {match_method}: {full_name} -> {driver.full_name}")
                    
                except Exception as e:
                    logger.error(f"Error finding driver {full_name}: {e}")
                    continue
            
            driver = driver_map[driver_number]
            
            # Get team if available
            team = None
            team_abbr = lap_dict.get('team_abbr', '')
            if team_abbr and team_abbr not in team_map:
                try:
                    team = Team.objects.filter(
                        short_name__iexact=team_abbr
                    ).first() or Team.objects.filter(
                        name__icontains=team_abbr
                    ).first()
                    team_map[team_abbr] = team
                except Exception as e:
                    logger.debug(f"Could not find team {team_abbr}: {e}")
            
            if team_abbr in team_map:
                team = team_map[team_abbr]
            
            # Create or update lap
            lap, created = Lap.objects.update_or_create(
                session=session,
                driver=driver,
                lap_number=lap_dict['lap_number'],
                defaults={
                    'team': team,
                    'driver_number': lap_dict['driver_number'],
                    'lap_time': lap_dict['lap_time'],
                    'sector_1_time': lap_dict['sector_1_time'],
                    'sector_2_time': lap_dict['sector_2_time'],
                    'sector_3_time': lap_dict['sector_3_time'],
                    'compound': lap_dict['compound'],
                    'tire_life': lap_dict['tire_life'],
                    'fresh_tire': lap_dict['fresh_tire'],
                    'track_status': lap_dict['track_status'],
                    'position': lap_dict['position'],
                    'pit_out_time': lap_dict['pit_out_time'],
                    'pit_in_time': lap_dict['pit_in_time'],
                    'speed_i1': lap_dict['speed_i1'],
                    'speed_i2': lap_dict['speed_i2'],
                    'speed_fl': lap_dict['speed_fl'],
                    'speed_st': lap_dict['speed_st'],
                    'is_personal_best': lap_dict['is_personal_best'],
                    'is_accurate': lap_dict['is_accurate'],
                    'lap_start_time': lap_dict['lap_start_time'],
                }
            )
            
            if created:
                laps_created += 1
        
        # STEP 2: Save pit stops
        # Group pit stops by driver and assign stop numbers
        pit_stops_by_driver = {}
        for pit_stop_dict in telemetry_data.get('pit_stops', []):
            driver_number = pit_stop_dict['driver_number']
            if driver_number not in pit_stops_by_driver:
                pit_stops_by_driver[driver_number] = []
            pit_stops_by_driver[driver_number].append(pit_stop_dict)
        
        for driver_number, stops in pit_stops_by_driver.items():
            if driver_number not in driver_map:
                continue
            
            driver = driver_map[driver_number]
            
            # Sort by lap number to assign stop numbers
            stops.sort(key=lambda x: x['lap_number'])
            
            for stop_num, pit_stop_dict in enumerate(stops, 1):
                # Find corresponding lap
                lap = Lap.objects.filter(
                    session=session,
                    driver=driver,
                    lap_number=pit_stop_dict['lap_number']
                ).first()
                
                pit_stop, created = PitStop.objects.update_or_create(
                    session=session,
                    driver=driver,
                    stop_number=stop_num,
                    defaults={
                        'lap': lap,
                        'lap_number': pit_stop_dict['lap_number'],
                        'pit_in_time': pit_stop_dict['pit_in_time'],
                        'pit_out_time': pit_stop_dict['pit_out_time'],
                        'pit_duration': pit_stop_dict['pit_duration'],
                    }
                )
                
                if created:
                    pit_stops_created += 1
        
        # STEP 3: Extract and save telemetry metrics (aggregated per lap)
        # Use dedicated telemetry metrics extraction module
        if f1_session is not None:
            from analytics.flows.extract_telemetry_metrics import (
                extract_all_telemetry_for_session,
                save_telemetry_metrics
            )
            
            try:
                # Extract telemetry metrics for all laps
                telemetry_map = extract_all_telemetry_for_session(
                    f1_session=f1_session,
                    laps_data=telemetry_data.get('laps', []),
                    session_type=session.session_type,
                    logger=logger
                )
                
                if telemetry_map:
                    # Build a map of lap objects for efficient lookup
                    lap_objects_map = {}
                    for driver_number, driver in driver_map.items():
                        laps = Lap.objects.filter(session=session, driver=driver).only('id', 'lap_number')
                        for lap_obj in laps:
                            lap_objects_map[(driver_number, lap_obj.lap_number)] = lap_obj
                    
                    # Save telemetry metrics to database
                    telemetry_created = save_telemetry_metrics(
                        telemetry_map=telemetry_map,
                        session=session,
                        driver_map=driver_map,
                        lap_objects_map=lap_objects_map,
                        logger=logger
                    )
            except Exception as e:
                logger.warning(f"Could not extract detailed telemetry metrics: {e}")
        else:
            logger.info("Telemetry metrics extraction skipped - f1_session not provided")
        
        logger.info(
            f"Saved telemetry for {session}: "
            f"{laps_created} laps, {telemetry_created} telemetry, {pit_stops_created} pit stops"
        )
        
        # Update load status
        mark_data_loaded(session_id, 'telemetry', flow_run_id)
        
        return {
            'session_id': session_id,
            'status': 'success',
            'laps_created': laps_created,
            'telemetry_created': telemetry_created,
            'pit_stops_created': pit_stops_created
        }
        
    except Exception as e:
        logger.error(f"Failed to save telemetry for session {session_id}: {e}")
        return {'session_id': session_id, 'status': 'failed', 'error': str(e)}


def _to_seconds(timedelta_val) -> Optional[float]:
    """
    Convert pandas Timedelta to seconds.
    
    Args:
        timedelta_val: pandas Timedelta or None
    
    Returns:
        float: seconds, or None if input is None/NaT
    """
    if pd.isna(timedelta_val):
        return None
    
    try:
        return float(timedelta_val.total_seconds())
    except (AttributeError, ValueError):
        return None
