"""
Session processing coordination.

Handles determining which sessions need processing based on gap detection
and user preferences (force mode, round filtering, etc.)
"""

from typing import List, Optional, Dict
from prefect import task, get_run_logger

from analytics.processing.gap_detection import (
    generate_gap_report,
    SessionGap
)


@task(name="Get Sessions to Process")
def get_sessions_to_process(
    year: int,
    round_number: Optional[int] = None,
    force: bool = False
) -> List[SessionGap]:
    """
    Determine which sessions need processing.
    
    This is the single entry point for session gap logic.
    Handles gap detection, round filtering, and force mode.
    
    Args:
        year: Season year
        round_number: Specific round to process (None = all rounds)
        force: If True, re-process sessions even if they have data
        
    Returns:
        List of SessionGap objects ready to process
    """
    logger = get_run_logger()
    
    # STEP 1: Run gap detection
    logger.info("Running gap detection...")
    gap_report = generate_gap_report(year)
    
    session_gaps = gap_report.session_gaps
    logger.info(f"Gap detection found {len(session_gaps)} sessions with missing data")
    
    # STEP 2: Filter by round if specified
    if round_number:
        logger.info(f"Filtering to round {round_number} only")
        session_gaps = [
            gap for gap in session_gaps
            if gap.round_number == round_number
        ]
        logger.info(f"After filtering: {len(session_gaps)} sessions")
    
    # STEP 3: Handle force mode
    # If force=True, ensure ALL matching sessions are included (not just those with gaps)
    if force:
        logger.info("Force mode: Ensuring all matching sessions are included for re-import")
        from analytics.models import Session as DjangoSession
        
        # Get all sessions for the year/round
        sessions_query = DjangoSession.objects.filter(race__season__year=year)
        if round_number:
            sessions_query = sessions_query.filter(race__round_number=round_number)
        
        sessions = sessions_query.select_related('race').order_by('race__round_number', 'session_number')
        
        # Build set of session IDs already in gaps
        existing_session_ids = {gap.session_id for gap in session_gaps}
        
        # Add any sessions not already in gaps
        added = 0
        for session in sessions:
            if session.id not in existing_session_ids:
                gap = SessionGap(
                    session_id=session.id,
                    year=year,
                    round_number=session.race.round_number,
                    session_type=session.session_type,
                    session_number=session.session_number,
                    event_name=session.race.name if session.race.event_format == 'testing' else None,
                    missing_weather=True  # Force re-import
                )
                session_gaps.append(gap)
                added += 1
        
        if added > 0:
            logger.info(f"Force mode added {added} additional sessions for re-import")
        else:
            logger.info(f"Force mode: All {len(session_gaps)} sessions already in gaps")
    
    logger.info(f"✅ Returning {len(session_gaps)} sessions to process")
    
    return session_gaps


@task(name="Build Processing Plan")
def build_processing_plan(sessions: List[SessionGap]) -> Dict:
    """
    Build processing plan by grouping sessions.
    
    No rate limit checking - we let FastF1 tell us when we hit the limit,
    then handle it gracefully with automatic pause/retry.
    
    Args:
        sessions: List of SessionGap objects to process
        
    Returns:
        Dict with plan details (sessions to process, races affected, etc.)
    """
    logger = get_run_logger()
    
    # Group by race for reporting
    by_race = {}
    for gap in sessions:
        race_key = (gap.year, gap.round_number)
        if race_key not in by_race:
            by_race[race_key] = []
        by_race[race_key].append(gap)
    
    plan = {
        'sessions': sessions,
        'total_sessions': len(sessions),
        'total_races': len(by_race),
        'by_race': by_race,
    }
    
    logger.info(f"📋 Plan: {plan['total_sessions']} sessions across {plan['total_races']} races")
    
    return plan
