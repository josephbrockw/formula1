"""
Gap detection for F1 data.

Identifies missing data across the database to optimize FastF1 API calls.
Implements the "session-once-extract-many" pattern by detecting all gaps
before making any API calls.

Architecture:
1. Scan database for missing data (seasons, races, sessions, related data)
2. Group gaps by what can be extracted from a single FastF1 session load
3. Return optimized collection plan

This minimizes API calls by:
- Loading each FastF1 session only once
- Extracting all possible data types from that single load
- Respecting rate limits throughout the process
"""

from typing import Dict, List, Set, Optional
from dataclasses import dataclass, field
from django.db.models import Q, Exists, OuterRef
from prefect import task, get_run_logger

from analytics.models import Season, Race, Session, SessionWeather, Circuit
from analytics.models import Corner, MarshalLight, MarshalSector


@dataclass
class SessionGap:
    """
    Represents missing data for a specific session.
    
    Currently only tracks weather data since that's all we extract.
    In the future, add fields for laps, telemetry, circuit, etc.
    
    Attributes:
        session_id: Database ID of the session (None if session doesn't exist)
        year: Season year
        round_number: Race round number
        session_type: Type of session (e.g., 'Practice 1', 'Race')
        session_number: Session number (1-5)
        event_name: Event name for testing events (optional)
        missing_weather: Whether weather data is missing
    """
    session_id: Optional[int]
    year: int
    round_number: int
    session_type: str
    session_number: int
    event_name: Optional[str] = None
    missing_weather: bool = False
    
    def __repr__(self):
        parts = [f"{self.year} Round {self.round_number} {self.session_type}"]
        if self.event_name:
            parts.append(f"({self.event_name})")
        if self.missing_weather:
            parts.append("[missing: weather]")
        return " ".join(parts)


@dataclass
class GapReport:
    """
    Complete report of missing data in the database.
    
    Attributes:
        season_year: Year being analyzed
        missing_races: List of race round numbers with no Race record
        missing_sessions: List of (round, session_number) tuples with no Session record
        session_gaps: List of SessionGap objects detailing what's missing per session
        total_api_calls_needed: Estimated number of FastF1 API calls needed
    """
    season_year: int
    missing_races: List[int] = field(default_factory=list)
    missing_sessions: List[tuple] = field(default_factory=list)
    session_gaps: List[SessionGap] = field(default_factory=list)
    total_api_calls_needed: int = 0
    
    def __repr__(self):
        lines = [
            f"Gap Report for {self.season_year} Season",
            f"  Missing Races: {len(self.missing_races)}",
            f"  Missing Sessions: {len(self.missing_sessions)}",
            f"  Sessions with gaps: {len(self.session_gaps)}",
            f"  Estimated API calls: {self.total_api_calls_needed}"
        ]
        return "\n".join(lines)
    
    @property
    def has_gaps(self):
        """Check if there are any gaps to fill"""
        return (
            len(self.missing_races) > 0 or
            len(self.missing_sessions) > 0 or
            len(self.session_gaps) > 0
        )


@task(name="Detect Missing Races")
def detect_missing_races(season_year: int, expected_rounds: Optional[int] = None) -> List[int]:
    """
    Detect race rounds that don't exist in the database.
    
    Args:
        season_year: Year to check
        expected_rounds: Number of rounds expected (if known). If None, checks against FastF1.
        
    Returns:
        List of round numbers that are missing
    """
    logger = get_run_logger()
    
    try:
        season = Season.objects.get(year=season_year)
    except Season.DoesNotExist:
        logger.warning(f"Season {season_year} does not exist in database")
        return []
    
    # Get existing races
    existing_rounds = set(
        Race.objects.filter(season=season)
        .values_list('round_number', flat=True)
    )
    
    if expected_rounds:
        # Check against expected count
        all_rounds = set(range(1, expected_rounds + 1))
        missing = sorted(all_rounds - existing_rounds)
    else:
        # TODO: Query FastF1 to get actual schedule
        # For now, just return empty if we don't know what to expect
        logger.info(f"Found {len(existing_rounds)} races for {season_year}")
        missing = []
    
    if missing:
        logger.info(f"Missing {len(missing)} races: {missing}")
    
    return missing


@task(name="Detect Missing Sessions")
def detect_missing_sessions(season_year: int) -> List[tuple]:
    """
    Detect sessions that should exist but don't.
    
    For each Race in the season, checks if expected sessions exist.
    Conventional weekends: 5 sessions (FP1, FP2, FP3, Qualifying, Race)
    Sprint weekends: 5 sessions (FP1, Sprint Qualifying, Sprint, Qualifying, Race)
    
    Args:
        season_year: Year to check
        
    Returns:
        List of (round_number, session_number) tuples for missing sessions
    """
    logger = get_run_logger()
    
    try:
        season = Season.objects.get(year=season_year)
    except Season.DoesNotExist:
        logger.warning(f"Season {season_year} does not exist")
        return []
    
    missing = []
    
    races = Race.objects.filter(season=season).order_by('round_number')
    for race in races:
        # Get existing session numbers for this race
        existing_sessions = set(
            Session.objects.filter(race=race)
            .values_list('session_number', flat=True)
        )
        
        # Expected sessions: 1-5 for most weekends
        # TODO: Adjust based on event_format (sprint vs conventional)
        expected_sessions = {1, 2, 3, 4, 5}
        
        missing_for_race = expected_sessions - existing_sessions
        if missing_for_race:
            for session_num in sorted(missing_for_race):
                missing.append((race.round_number, session_num))
                logger.debug(f"Missing session: Round {race.round_number}, Session {session_num}")
    
    if missing:
        logger.info(f"Found {len(missing)} missing sessions")
    
    return missing


@task(name="Detect Session Data Gaps")
def detect_session_data_gaps(season_year: int) -> List[SessionGap]:
    """
    Detect missing weather data for existing sessions.
    
    Currently only checks for missing weather data since that's all we extract.
    Future: Add checks for laps, telemetry, circuit data when we implement those extractors.
    
    Args:
        season_year: Year to check
        
    Returns:
        List of SessionGap objects for sessions missing weather data
    """
    logger = get_run_logger()
    
    try:
        season = Season.objects.get(year=season_year)
    except Season.DoesNotExist:
        logger.warning(f"Season {season_year} does not exist")
        return []
    
    gaps = []
    
    # Get all sessions for the season
    sessions = Session.objects.filter(
        race__season=season
    ).select_related('race').order_by(
        'race__round_number', 'session_number'
    )
    
    for session in sessions:
        # Check for missing weather
        has_weather = SessionWeather.objects.filter(session=session).exists()
        
        if not has_weather:
            gap = SessionGap(
                session_id=session.id,
                year=season_year,
                round_number=session.race.round_number,
                session_type=session.session_type,
                session_number=session.session_number,
                event_name=session.race.name if session.race.event_format == 'testing' else None,
                missing_weather=True
            )
            gaps.append(gap)
            logger.debug(f"Gap detected: {gap}")
    
    if gaps:
        logger.info(f"Found {len(gaps)} sessions missing weather data")
    
    return gaps


@task(name="Generate Gap Report")
def generate_gap_report(season_year: int, expected_rounds: Optional[int] = None) -> GapReport:
    """
    Generate a complete report of missing data for a season.
    
    This is the main entry point for gap detection. It orchestrates
    all detection tasks and produces a comprehensive report.
    
    Args:
        season_year: Year to analyze
        expected_rounds: Expected number of rounds (optional)
        
    Returns:
        GapReport with all detected gaps
    """
    logger = get_run_logger()
    logger.info(f"Generating gap report for {season_year} season")
    
    # Detect all types of gaps
    missing_races = detect_missing_races(season_year, expected_rounds)
    missing_sessions = detect_missing_sessions(season_year)
    session_gaps = detect_session_data_gaps(season_year)
    
    # Calculate total API calls needed
    # Each session gap = 1 API call (we extract all data from one session load)
    # Missing sessions also need to be created first (separate process)
    total_calls = len(session_gaps)
    
    report = GapReport(
        season_year=season_year,
        missing_races=missing_races,
        missing_sessions=missing_sessions,
        session_gaps=session_gaps,
        total_api_calls_needed=total_calls
    )
    
    logger.info(f"Gap report generated: {report}")
    
    return report
