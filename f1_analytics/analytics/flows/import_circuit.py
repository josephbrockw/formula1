"""
Circuit data import flow.

Extracts circuit information (corners, marshal lights, sectors) from FastF1 sessions
and saves to database.

Functions:
- extract_circuit_data: Extract circuit data from FastF1 session
- save_circuit_to_db: Save extracted circuit data to database
"""

from typing import Dict, Optional
from prefect import task, get_run_logger

from analytics.processing.utils import mark_data_loaded


@task(name="Extract Circuit Data from Session")
def extract_circuit_data(f1_session) -> Optional[Dict]:
    """
    Extract circuit data from a loaded FastF1 session.
    
    Extracts corners, marshal lights, and marshal sectors for circuit visualization
    and track analysis.
    
    Args:
        f1_session: Loaded FastF1 Session object
    
    Returns:
        dict: Circuit data with keys: circuit_info, corners, marshal_lights, marshal_sectors
              or None if no circuit data available
    """
    logger = get_run_logger()
    
    try:
        # Get circuit info from FastF1
        circuit_info = f1_session.get_circuit_info()
        
        if circuit_info is None:
            logger.warning("No circuit info available for session")
            return None
        
        circuit_data = {
            'circuit_key': circuit_info.circuit_key if hasattr(circuit_info, 'circuit_key') else None,
            'corners': [],
            'marshal_lights': [],
            'marshal_sectors': []
        }
        
        # Extract corners
        if hasattr(circuit_info, 'corners') and circuit_info.corners is not None:
            corners_df = circuit_info.corners
            for _, corner in corners_df.iterrows():
                circuit_data['corners'].append({
                    'number': int(corner.get('Number', 0)),
                    'letter': str(corner.get('Letter', '')) if corner.get('Letter') else '',
                    'x': float(corner.get('X', 0)),
                    'y': float(corner.get('Y', 0)),
                    'angle': float(corner.get('Angle', 0)),
                    'distance': float(corner.get('Distance', 0))
                })
            logger.info(f"Extracted {len(circuit_data['corners'])} corners")
        
        # Extract marshal lights
        if hasattr(circuit_info, 'marshal_lights') and circuit_info.marshal_lights is not None:
            lights_df = circuit_info.marshal_lights
            for _, light in lights_df.iterrows():
                circuit_data['marshal_lights'].append({
                    'number': int(light.get('Number', 0)),
                    'letter': str(light.get('Letter', '')) if light.get('Letter') else '',
                    'x': float(light.get('X', 0)),
                    'y': float(light.get('Y', 0)),
                    'angle': float(light.get('Angle', 0)),
                    'distance': float(light.get('Distance', 0))
                })
            logger.info(f"Extracted {len(circuit_data['marshal_lights'])} marshal lights")
        
        # Extract marshal sectors
        if hasattr(circuit_info, 'marshal_sectors') and circuit_info.marshal_sectors is not None:
            sectors_df = circuit_info.marshal_sectors
            for _, sector in sectors_df.iterrows():
                circuit_data['marshal_sectors'].append({
                    'number': int(sector.get('Number', 0)),
                    'letter': str(sector.get('Letter', '')) if sector.get('Letter') else '',
                    'x': float(sector.get('X', 0)),
                    'y': float(sector.get('Y', 0)),
                    'angle': float(sector.get('Angle', 0)),
                    'distance': float(sector.get('Distance', 0))
                })
            logger.info(f"Extracted {len(circuit_data['marshal_sectors'])} marshal sectors")
        
        # Only return if we have some data
        if circuit_data['corners'] or circuit_data['marshal_lights'] or circuit_data['marshal_sectors']:
            return circuit_data
        else:
            logger.warning("No circuit details available (corners, lights, sectors)")
            return None
        
    except Exception as e:
        logger.error(f"Failed to extract circuit data: {e}")
        return None


@task(name="Save Circuit Data to Database")
def save_circuit_to_db(session_id: int, circuit_data: Dict, flow_run_id: Optional[str] = None):
    """
    Save circuit data to database and update load status.
    
    Creates/updates Circuit, Corner, MarshalLight, and MarshalSector records.
    
    Args:
        session_id: Django Session ID
        circuit_data: Circuit data dict with corners, lights, sectors
        flow_run_id: Prefect flow run ID for tracking
    """
    from analytics.models import Session, Circuit, Corner, MarshalLight, MarshalSector
    
    logger = get_run_logger()
    
    try:
        session = Session.objects.get(id=session_id)
        circuit = session.race.circuit
        
        if not circuit:
            logger.error(f"No circuit associated with session {session_id}")
            return {'session_id': session_id, 'status': 'failed', 'error': 'No circuit'}
        
        counts = {
            'corners': 0,
            'marshal_lights': 0,
            'marshal_sectors': 0
        }
        
        # Save corners
        for corner_data in circuit_data.get('corners', []):
            Corner.objects.update_or_create(
                circuit=circuit,
                number=corner_data['number'],
                letter=corner_data['letter'],
                defaults={
                    'x': corner_data['x'],
                    'y': corner_data['y'],
                    'angle': corner_data['angle'],
                    'distance': corner_data['distance']
                }
            )
            counts['corners'] += 1
        
        # Save marshal lights
        for light_data in circuit_data.get('marshal_lights', []):
            MarshalLight.objects.update_or_create(
                circuit=circuit,
                number=light_data['number'],
                letter=light_data['letter'],
                defaults={
                    'x': light_data['x'],
                    'y': light_data['y'],
                    'angle': light_data['angle'],
                    'distance': light_data['distance']
                }
            )
            counts['marshal_lights'] += 1
        
        # Save marshal sectors
        for sector_data in circuit_data.get('marshal_sectors', []):
            MarshalSector.objects.update_or_create(
                circuit=circuit,
                number=sector_data['number'],
                letter=sector_data['letter'],
                defaults={
                    'x': sector_data['x'],
                    'y': sector_data['y'],
                    'angle': sector_data['angle'],
                    'distance': sector_data['distance']
                }
            )
            counts['marshal_sectors'] += 1
        
        logger.info(
            f"Saved circuit data for {circuit.name}: "
            f"{counts['corners']} corners, {counts['marshal_lights']} lights, "
            f"{counts['marshal_sectors']} sectors"
        )
        
        # Update load status
        mark_data_loaded(session_id, 'circuit', flow_run_id)
        
        return {'session_id': session_id, 'status': 'success', 'counts': counts}
        
    except Exception as e:
        logger.error(f"Failed to save circuit data for session {session_id}: {e}")
        return {'session_id': session_id, 'status': 'failed', 'error': str(e)}
