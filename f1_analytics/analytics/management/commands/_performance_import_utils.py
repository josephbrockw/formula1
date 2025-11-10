"""
Shared utilities for performance import commands.
Contains common logic used by both driver and constructor imports.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from django.conf import settings
from django.core.management.base import CommandError
from analytics.models import Season, Team, Race


def find_most_recent_file(data_dir, pattern):
    """
    Find the most recent file matching a pattern in a directory.
    
    Args:
        data_dir: Path to directory to search
        pattern: Glob pattern (e.g., '*-all-drivers-performance.csv')
    
    Returns:
        Path object or None if no files found
    """
    if not data_dir.exists():
        return None
    
    matching_files = list(data_dir.glob(pattern))
    
    if not matching_files:
        return None
    
    # Sort by filename (starts with date in YYYY-MM-DD format)
    matching_files.sort(reverse=True)
    return matching_files[0]


def get_season(year):
    """
    Retrieve season for given year.
    
    Args:
        year: Season year (int)
    
    Returns:
        Season object
    
    Raises:
        CommandError if season not found
    """
    try:
        return Season.objects.get(year=year)
    except Season.DoesNotExist:
        raise CommandError(
            f'Season {year} not found. Please create it first or run import_fantasy_prices.'
        )


def resolve_csv_file(options, year, file_pattern, data_subdir='outcomes'):
    """
    Resolve CSV file path from options or find most recent.
    
    Args:
        options: Command options dict
        year: Season year
        file_pattern: Glob pattern for file search
        data_subdir: Subdirectory under data/{year}/ (default: 'outcomes')
    
    Returns:
        Path to CSV file
    
    Raises:
        CommandError if file not found
    """
    if options.get('file'):
        csv_file = Path(options['file'])
        if not csv_file.exists():
            raise CommandError(f'File not found: {csv_file}')
        return csv_file
    
    # Find most recent file
    base_dir = Path(settings.BASE_DIR)
    data_dir = base_dir / 'data' / str(year) / data_subdir
    csv_file = find_most_recent_file(data_dir, file_pattern)
    
    if not csv_file:
        raise CommandError(
            f'No performance files found in {data_dir}. '
            'Please export data first using the Chrome extension.'
        )
    
    return csv_file


def get_or_create_race(season, race_name, race_order):
    """
    Get or create a race, tracking round numbers.
    
    Args:
        season: Season object
        race_name: Name of the race
        race_order: Dict tracking race names to round numbers
    
    Returns:
        Tuple of (race, created, current_round)
        - race: Race object
        - created: Boolean indicating if race was created
        - current_round: Updated round counter
    """
    if race_name not in race_order:
        current_round = len(race_order) + 1
        race_order[race_name] = current_round
    
    race, created = Race.objects.get_or_create(
        season=season,
        name=race_name,
        defaults={'round_number': race_order[race_name]}
    )
    
    return race, created


def parse_fantasy_price(price_string):
    """
    Parse fantasy price string to Decimal.
    
    Args:
        price_string: String like '$30.4M' or '30.4M'
    
    Returns:
        Decimal value
    """
    price_str = price_string.replace('$', '').replace('M', '')
    return Decimal(price_str)


def parse_event_score_fields(row):
    """
    Parse common event score fields from CSV row.
    
    Args:
        row: CSV row dict
    
    Returns:
        Dict with parsed fields: {
            'points': int,
            'position': int or None,
            'frequency': int or None
        }
    """
    # Handle unicode minus sign (−) that might appear in CSV exports
    points_str = row['Points'].replace('−', '-') if row['Points'] else ''
    points = int(points_str) if points_str else 0
    
    position = int(row['Position']) if row['Position'] else None
    frequency = int(row['Frequency']) if row['Frequency'] else None
    
    return {
        'points': points,
        'position': position,
        'frequency': frequency
    }


def extract_event_types(rows):
    """
    Extract set of event types from rows.
    
    Args:
        rows: List of CSV row dicts with 'Event Type' field
    
    Returns:
        Set of event type strings
    """
    return set(row['Event Type'] for row in rows)


def get_or_create_team(team_name):
    """
    Get or create a team.
    
    Args:
        team_name: Team name string
    
    Returns:
        Tuple of (team, created)
    """
    return Team.objects.get_or_create(
        name=team_name,
        defaults={'short_name': team_name[:3].upper()}
    )


def parse_totals(race_total_str, season_total_str):
    """
    Parse race and season total strings to integers.
    
    Args:
        race_total_str: Race total points string
        season_total_str: Season total points string
    
    Returns:
        Tuple of (race_total, season_total) as integers
    """
    race_total = int(race_total_str) if race_total_str else 0
    season_total = int(season_total_str) if season_total_str else 0
    return race_total, season_total
