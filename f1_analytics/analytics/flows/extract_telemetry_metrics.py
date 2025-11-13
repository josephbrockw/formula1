"""
Telemetry metrics extraction utilities.

Extracts and aggregates detailed telemetry data (speed, throttle, brake, gear, RPM, DRS)
from FastF1 sessions for individual laps.

This module provides reusable functions for extracting telemetry metrics that can be
used by import flows or standalone analysis.
"""

from typing import Dict, Optional, List
import pandas as pd


def extract_lap_telemetry(f1_session, driver_number: str, lap_number: int) -> Optional[Dict]:
    """
    Extract aggregated telemetry metrics for a specific lap.
    
    Instead of storing every telemetry sample (100s per lap), we aggregate
    key metrics to optimize database storage and query performance.
    
    Args:
        f1_session: Loaded FastF1 Session object
        driver_number: Driver's racing number as string
        lap_number: Lap number to extract telemetry for
    
    Returns:
        dict: Aggregated telemetry metrics or None if unavailable
        
    Metrics returned:
        - max_speed, min_speed, avg_speed: Speed metrics in km/h
        - throttle_pct_full: Percentage of lap at 100% throttle
        - throttle_pct_avg: Average throttle percentage
        - brake_pct: Percentage of lap spent braking
        - max_gear: Highest gear used
        - max_rpm, avg_rpm: RPM metrics
        - drs_activations: Number of times DRS was activated
        - drs_distance: Distance covered with DRS open (meters)
    """
    try:
        # Get the specific lap (using new API - pick_drivers/pick_laps are plural)
        lap = f1_session.laps.pick_drivers(str(driver_number)).pick_laps(lap_number)
        
        if lap is None or lap.empty:
            return None
        
        # Get telemetry data for this lap
        telemetry = lap.get_telemetry()
        
        if telemetry is None or telemetry.empty:
            return None
        
        # Aggregate metrics
        metrics = {}
        
        # Speed metrics (km/h)
        if 'Speed' in telemetry.columns:
            metrics['max_speed'] = float(telemetry['Speed'].max())
            metrics['min_speed'] = float(telemetry['Speed'].min())
            metrics['avg_speed'] = float(telemetry['Speed'].mean())
        
        # Throttle metrics (0-100%)
        if 'Throttle' in telemetry.columns:
            metrics['throttle_pct_full'] = float(
                (telemetry['Throttle'] == 100).sum() / len(telemetry) * 100
            )
            metrics['throttle_pct_avg'] = float(telemetry['Throttle'].mean())
        
        # Brake metrics
        if 'Brake' in telemetry.columns:
            metrics['brake_pct'] = float(
                (telemetry['Brake'] > 0).sum() / len(telemetry) * 100
            )
        
        # Gear usage
        if 'nGear' in telemetry.columns:
            metrics['max_gear'] = int(telemetry['nGear'].max())
        
        # RPM
        if 'RPM' in telemetry.columns:
            metrics['max_rpm'] = int(telemetry['RPM'].max())
            metrics['avg_rpm'] = int(telemetry['RPM'].mean())
        
        # DRS
        if 'DRS' in telemetry.columns:
            # DRS values: 0=off, 8=available, 10=open, 12=open+available, 14=open+error
            drs_active = telemetry['DRS'] >= 10
            metrics['drs_activations'] = int((drs_active.diff() > 0).sum())
            
            if 'Distance' in telemetry.columns and drs_active.any():
                # Calculate distance with DRS active
                drs_distance = telemetry[drs_active]['Distance'].diff().sum()
                metrics['drs_distance'] = float(drs_distance) if pd.notna(drs_distance) else None
        
        return metrics if metrics else None
        
    except Exception as e:
        # Telemetry might not be available for all laps (e.g., in/out laps, incomplete data)
        return None


def extract_all_telemetry_for_session(
    f1_session,
    laps_data: List[Dict],
    session_type: str,
    logger=None
) -> Dict[tuple, Dict]:
    """
    Extract telemetry metrics for all laps in a session.
    
    This is optimized to process many laps efficiently and only extract
    telemetry for session types where it's valuable (Race, Qualifying, Sprint).
    
    Args:
        f1_session: Loaded FastF1 Session object
        laps_data: List of lap dicts with 'driver_number' and 'lap_number'
        session_type: Session type (e.g., 'Race', 'Qualifying', 'Practice 1')
        logger: Optional logger for progress tracking
    
    Returns:
        dict: Mapping of (driver_number, lap_number) -> telemetry metrics
        
    Example:
        {
            ('44', 1): {'max_speed': 320.5, 'avg_speed': 215.3, ...},
            ('44', 2): {'max_speed': 318.2, 'avg_speed': 218.1, ...},
            ...
        }
    """
    # Only extract telemetry for important sessions
    if session_type not in ['Race', 'Qualifying', 'Sprint']:
        if logger:
            logger.info(f"Skipping telemetry metrics extraction for {session_type} session")
        return {}
    
    if logger:
        logger.info(f"Extracting telemetry metrics for {len(laps_data)} laps...")
    
    telemetry_map = {}
    extracted_count = 0
    skipped_count = 0
    
    # Add progress bar for terminal output
    try:
        from tqdm import tqdm
        import sys
        
        laps_iterator = tqdm(
            laps_data, 
            desc="  └─ Extracting telemetry",  # Indent to show nesting
            unit="lap",
            position=None,  # Auto-position
            leave=False,  # Clear after completion
            file=sys.stderr,  # Use stderr to avoid mixing with logs
            disable=logger is None,  # Disable if no logger (silent mode)
            mininterval=0.5  # Update every 0.5s to reduce flicker
        )
    except ImportError:
        # tqdm not available, use regular iterator
        laps_iterator = laps_data
    
    for lap_dict in laps_iterator:
        driver_number = str(lap_dict.get('driver_number', ''))
        lap_number = lap_dict.get('lap_number', 0)
        
        if not driver_number or not lap_number:
            skipped_count += 1
            continue
        
        try:
            metrics = extract_lap_telemetry(f1_session, driver_number, lap_number)
            
            if metrics:
                telemetry_map[(driver_number, lap_number)] = metrics
                extracted_count += 1
            else:
                skipped_count += 1
                
        except Exception as e:
            if logger:
                logger.debug(
                    f"Could not extract telemetry for driver {driver_number}, "
                    f"lap {lap_number}: {e}"
                )
            skipped_count += 1
            continue
    
    if logger:
        logger.info(
            f"Telemetry extraction complete: {extracted_count} laps processed, "
            f"{skipped_count} skipped"
        )
    
    return telemetry_map


def save_telemetry_metrics(
    telemetry_map: Dict[tuple, Dict],
    session,
    driver_map: Dict[str, 'Driver'],
    lap_objects_map: Dict[tuple, 'Lap'],
    logger=None
) -> int:
    """
    Save extracted telemetry metrics to the database.
    
    Args:
        telemetry_map: Mapping of (driver_number, lap_number) -> metrics
        session: Django Session object
        driver_map: Mapping of driver_number -> Driver object
        lap_objects_map: Mapping of (driver_number, lap_number) -> Lap object
        logger: Optional logger
    
    Returns:
        int: Number of Telemetry records created
    """
    from analytics.models import Telemetry
    
    telemetry_created = 0
    
    for (driver_number, lap_number), metrics in telemetry_map.items():
        # Get the Lap object
        lap_obj = lap_objects_map.get((driver_number, lap_number))
        
        if not lap_obj:
            if logger:
                logger.debug(
                    f"Lap not found for telemetry: driver {driver_number}, "
                    f"lap {lap_number}"
                )
            continue
        
        try:
            # Save Telemetry model
            telemetry_obj, created = Telemetry.objects.update_or_create(
                lap=lap_obj,
                defaults=metrics
            )
            
            if created:
                telemetry_created += 1
                
        except Exception as e:
            if logger:
                logger.error(
                    f"Error saving telemetry for lap {lap_number}, "
                    f"driver {driver_number}: {e}"
                )
            continue
    
    if logger:
        logger.info(f"Saved {telemetry_created} telemetry records to database")
    
    return telemetry_created
