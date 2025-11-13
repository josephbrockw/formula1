"""
Pit stop extraction from FastF1 sessions.

This module handles extracting pit stop data from FastF1 sessions,
including timing, duration, and lap context.

FastF1 doesn't provide a direct pit stops API, so we extract from lap data
by identifying laps with PitInTime and PitOutTime values.
"""

import pandas as pd
from typing import List, Dict, Optional


def _to_seconds(time_value) -> Optional[float]:
    """
    Convert FastF1 time value (timedelta or similar) to seconds.
    
    Args:
        time_value: Time value from FastF1 (could be timedelta, Timedelta, or numeric)
        
    Returns:
        float: Time in seconds, or None if invalid
    """
    if pd.isna(time_value):
        return None
    
    try:
        # Try to get total_seconds if it's a timedelta-like object
        if hasattr(time_value, 'total_seconds'):
            return float(time_value.total_seconds())
        # If it's already numeric, use it directly
        return float(time_value)
    except (ValueError, TypeError, AttributeError):
        return None


def extract_pit_stops_from_session(f1_session, driver_info_map: Dict = None, logger=None) -> List[Dict]:
    """
    Extract pit stop data from a FastF1 session.
    
    Identifies pit stops by finding laps with both PitInTime and PitOutTime values.
    Calculates pit duration and associates with driver and lap information.
    
    Args:
        f1_session: Loaded FastF1 Session object
        driver_info_map: Dict mapping driver abbreviations to driver info (optional)
        logger: Logger instance for output (optional)
        
    Returns:
        List of pit stop dicts with keys:
            - driver_number: Driver's race number
            - driver_abbr: Driver abbreviation (e.g., 'VER', 'HAM')
            - full_name: Driver's full name
            - lap_number: Lap when pit stop occurred
            - pit_in_time: Time when driver entered pits (seconds from session start)
            - pit_out_time: Time when driver exited pits (seconds from session start)
            - pit_duration: Duration of pit stop (seconds)
    """
    if logger:
        logger.info("Extracting pit stop data from session...")
    
    pit_stops_data = []
    
    try:
        laps_df = f1_session.laps
        
        if laps_df is None or len(laps_df) == 0:
            if logger:
                logger.warning("No laps data available for pit stop extraction")
            return []
        
        # Debug: Check what pit-related columns exist
        if logger:
            pit_columns = [col for col in laps_df.columns if 'pit' in col.lower()]
            logger.info(f"Available pit-related columns: {pit_columns}")
            
            # Count laps with pit data
            pit_in_count = laps_df['PitInTime'].notna().sum() if 'PitInTime' in laps_df.columns else 0
            pit_out_count = laps_df['PitOutTime'].notna().sum() if 'PitOutTime' in laps_df.columns else 0
            logger.info(f"Laps with PitInTime: {pit_in_count}, PitOutTime: {pit_out_count}")
        
        # Group laps by driver to track pit in/out across laps
        # In FastF1, PitOutTime is on the lap AFTER the pit stop
        # PitInTime is on the lap where driver entered pits
        drivers_laps = {}
        for idx, lap_row in laps_df.iterrows():
            driver_abbr = str(lap_row.get('Driver', ''))
            if driver_abbr not in drivers_laps:
                drivers_laps[driver_abbr] = []
            drivers_laps[driver_abbr].append(lap_row)
        
        # Extract pit stops for each driver
        for driver_abbr, driver_laps in drivers_laps.items():
            driver_number = None
            full_name = ''
            
            # Get driver info from first lap
            if len(driver_laps) > 0:
                driver_number = str(driver_laps[0].get('DriverNumber', ''))
                if driver_info_map and driver_abbr in driver_info_map:
                    full_name = driver_info_map[driver_abbr].get('full_name', '')
            
            # Look for pit stops by checking consecutive laps
            for i in range(len(driver_laps)):
                lap_row = driver_laps[i]
                
                # Check if this lap has PitInTime (driver pitted on this lap)
                if pd.notna(lap_row.get('PitInTime')):
                    pit_in = _to_seconds(lap_row.get('PitInTime'))
                    lap_number = int(lap_row.get('LapNumber', 0))
                    
                    # Look for PitOutTime in same lap or next lap
                    pit_out = None
                    if pd.notna(lap_row.get('PitOutTime')):
                        pit_out = _to_seconds(lap_row.get('PitOutTime'))
                    elif i + 1 < len(driver_laps):
                        next_lap = driver_laps[i + 1]
                        if pd.notna(next_lap.get('PitOutTime')):
                            pit_out = _to_seconds(next_lap.get('PitOutTime'))
                    
                    if pit_in and pit_out and pit_out > pit_in:
                        pit_stop = {
                            'driver_number': driver_number,
                            'driver_abbr': driver_abbr,
                            'full_name': full_name,
                            'lap_number': lap_number,
                            'pit_in_time': pit_in,
                            'pit_out_time': pit_out,
                            'pit_duration': pit_out - pit_in,
                        }
                        pit_stops_data.append(pit_stop)
        
        if logger:
            logger.info(f"Extracted {len(pit_stops_data)} pit stops")
        
    except Exception as e:
        if logger:
            logger.error(f"Error extracting pit stops: {e}")
        else:
            print(f"Error extracting pit stops: {e}")
    
    return pit_stops_data


def save_pit_stops_to_db(session, pit_stops_data: List[Dict], driver_map: Dict, logger=None):
    """
    Save pit stops to database.
    
    Groups pit stops by driver and assigns stop numbers (1st stop, 2nd stop, etc.).
    Creates or updates PitStop records in the database.
    
    Args:
        session: Django Session object
        pit_stops_data: List of pit stop dicts from extract_pit_stops_from_session
        driver_map: Dict mapping driver numbers to Driver objects
        logger: Logger instance (optional)
        
    Returns:
        int: Number of pit stops created
    """
    from analytics.models import PitStop, Lap
    
    pit_stops_created = 0
    
    try:
        # Group pit stops by driver
        pit_stops_by_driver = {}
        for pit_stop_dict in pit_stops_data:
            driver_number = pit_stop_dict['driver_number']
            if driver_number not in pit_stops_by_driver:
                pit_stops_by_driver[driver_number] = []
            pit_stops_by_driver[driver_number].append(pit_stop_dict)
        
        # Save pit stops for each driver
        for driver_number, stops in pit_stops_by_driver.items():
            if driver_number not in driver_map:
                if logger:
                    logger.warning(f"Driver {driver_number} not found in driver map, skipping pit stops")
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
        
        if logger:
            logger.info(f"Saved {pit_stops_created} pit stops to database")
    
    except Exception as e:
        if logger:
            logger.error(f"Error saving pit stops: {e}")
        else:
            print(f"Error saving pit stops: {e}")
    
    return pit_stops_created
