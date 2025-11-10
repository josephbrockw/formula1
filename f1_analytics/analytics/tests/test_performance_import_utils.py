"""
Unit tests for performance import utilities.

Tests cover all helper functions in _performance_import_utils.py
with various edge cases and error conditions.
"""

import tempfile
from decimal import Decimal
from pathlib import Path
from django.test import TestCase
from django.core.management.base import CommandError
from django.conf import settings
from analytics.models import Season, Team, Race
from analytics.management.commands._performance_import_utils import (
    find_most_recent_file,
    get_season,
    resolve_csv_file,
    get_or_create_race,
    parse_fantasy_price,
    parse_event_score_fields,
    extract_event_types,
    get_or_create_team,
    parse_totals,
)


class FindMostRecentFileTests(TestCase):
    """Tests for find_most_recent_file() function"""
    
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_path = Path(self.test_dir)
    
    def tearDown(self):
        # Clean up test files
        import shutil
        shutil.rmtree(self.test_dir)
    
    def test_finds_most_recent_file(self):
        """Should return most recent file when multiple exist"""
        # Create test files with different dates
        (self.test_path / '2025-01-15-drivers.csv').touch()
        (self.test_path / '2025-01-20-drivers.csv').touch()
        (self.test_path / '2025-01-10-drivers.csv').touch()
        
        result = find_most_recent_file(self.test_path, '*-drivers.csv')
        
        self.assertEqual(result.name, '2025-01-20-drivers.csv')
    
    def test_returns_none_when_no_files_match(self):
        """Should return None when no files match pattern"""
        (self.test_path / 'other-file.csv').touch()
        
        result = find_most_recent_file(self.test_path, '*-drivers.csv')
        
        self.assertIsNone(result)
    
    def test_returns_none_when_directory_does_not_exist(self):
        """Should return None when directory doesn't exist"""
        non_existent = Path('/nonexistent/directory')
        
        result = find_most_recent_file(non_existent, '*.csv')
        
        self.assertIsNone(result)
    
    def test_finds_single_file(self):
        """Should return single file when only one matches"""
        (self.test_path / '2025-01-15-drivers.csv').touch()
        
        result = find_most_recent_file(self.test_path, '*-drivers.csv')
        
        self.assertEqual(result.name, '2025-01-15-drivers.csv')


class GetSeasonTests(TestCase):
    """Tests for get_season() function"""
    
    def test_retrieves_existing_season(self):
        """Should return season when it exists"""
        season = Season.objects.create(year=2025, name='2025 Season')
        
        result = get_season(2025)
        
        self.assertEqual(result, season)
        self.assertEqual(result.year, 2025)
    
    def test_raises_error_when_season_not_found(self):
        """Should raise CommandError when season doesn't exist"""
        with self.assertRaises(CommandError) as context:
            get_season(2025)
        
        self.assertIn('Season 2025 not found', str(context.exception))


class ResolveCSVFileTests(TestCase):
    """Tests for resolve_csv_file() function"""
    
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_path = Path(self.test_dir)
    
    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir)
    
    def test_returns_explicit_file_when_provided(self):
        """Should return explicit file path when --file option used"""
        test_file = self.test_path / 'test.csv'
        test_file.touch()
        options = {'file': str(test_file)}
        
        result = resolve_csv_file(options, 2025, '*.csv')
        
        self.assertEqual(result, test_file)
    
    def test_raises_error_when_explicit_file_not_found(self):
        """Should raise CommandError when explicit file doesn't exist"""
        options = {'file': '/nonexistent/file.csv'}
        
        with self.assertRaises(CommandError) as context:
            resolve_csv_file(options, 2025, '*.csv')
        
        self.assertIn('File not found', str(context.exception))
    
    def test_finds_most_recent_file_when_no_explicit_file(self):
        """Should auto-discover most recent file when --file not provided"""
        # Create mock directory structure
        data_dir = self.test_path / 'data' / '2025' / 'outcomes'
        data_dir.mkdir(parents=True)
        (data_dir / '2025-01-15-drivers.csv').touch()
        (data_dir / '2025-01-20-drivers.csv').touch()
        
        options = {}
        
        with self.settings(BASE_DIR=self.test_path):
            result = resolve_csv_file(options, 2025, '*-drivers.csv')
        
        self.assertEqual(result.name, '2025-01-20-drivers.csv')
    
    def test_raises_error_when_no_files_found(self):
        """Should raise CommandError when no matching files found"""
        data_dir = self.test_path / 'data' / '2025' / 'outcomes'
        data_dir.mkdir(parents=True)
        
        options = {}
        
        with self.settings(BASE_DIR=self.test_path):
            with self.assertRaises(CommandError) as context:
                resolve_csv_file(options, 2025, '*-drivers.csv')
        
        self.assertIn('No performance files found', str(context.exception))


class GetOrCreateRaceTests(TestCase):
    """Tests for get_or_create_race() function"""
    
    def setUp(self):
        self.season = Season.objects.create(year=2025, name='2025 Season')
    
    def test_creates_new_race_with_round_number(self):
        """Should create new race with correct round number"""
        race_order = {}
        
        race, created = get_or_create_race(self.season, 'Bahrain', race_order)
        
        self.assertTrue(created)
        self.assertEqual(race.name, 'Bahrain')
        self.assertEqual(race.round_number, 1)
        self.assertEqual(race_order['Bahrain'], 1)
    
    def test_retrieves_existing_race(self):
        """Should retrieve existing race without creating duplicate"""
        existing_race = Race.objects.create(
            season=self.season,
            name='Bahrain',
            round_number=1
        )
        race_order = {'Bahrain': 1}
        
        race, created = get_or_create_race(self.season, 'Bahrain', race_order)
        
        self.assertFalse(created)
        self.assertEqual(race, existing_race)
    
    def test_assigns_sequential_round_numbers(self):
        """Should assign sequential round numbers to multiple races"""
        race_order = {}
        
        race1, _ = get_or_create_race(self.season, 'Bahrain', race_order)
        race2, _ = get_or_create_race(self.season, 'Saudi Arabia', race_order)
        race3, _ = get_or_create_race(self.season, 'Australia', race_order)
        
        self.assertEqual(race1.round_number, 1)
        self.assertEqual(race2.round_number, 2)
        self.assertEqual(race3.round_number, 3)
        self.assertEqual(len(race_order), 3)
    
    def test_uses_existing_round_number_from_order(self):
        """Should use existing round number when race already in order dict"""
        race_order = {'Bahrain': 5}
        
        race, created = get_or_create_race(self.season, 'Bahrain', race_order)
        
        self.assertTrue(created)
        self.assertEqual(race.round_number, 5)


class ParseFantasyPriceTests(TestCase):
    """Tests for parse_fantasy_price() function"""
    
    def test_parses_standard_price_format(self):
        """Should parse standard price format ($30.4M)"""
        result = parse_fantasy_price('$30.4M')
        
        self.assertEqual(result, Decimal('30.4'))
        self.assertIsInstance(result, Decimal)
    
    def test_parses_price_without_dollar_sign(self):
        """Should parse price without dollar sign (30.4M)"""
        result = parse_fantasy_price('30.4M')
        
        self.assertEqual(result, Decimal('30.4'))
    
    def test_parses_price_without_m_suffix(self):
        """Should parse price without M suffix ($30.4)"""
        result = parse_fantasy_price('$30.4')
        
        self.assertEqual(result, Decimal('30.4'))
    
    def test_parses_integer_price(self):
        """Should parse integer price ($30M)"""
        result = parse_fantasy_price('$30M')
        
        self.assertEqual(result, Decimal('30'))
    
    def test_parses_minimal_format(self):
        """Should parse minimal format (30.4)"""
        result = parse_fantasy_price('30.4')
        
        self.assertEqual(result, Decimal('30.4'))


class ParseEventScoreFieldsTests(TestCase):
    """Tests for parse_event_score_fields() function"""
    
    def test_parses_all_fields_present(self):
        """Should parse all fields when present"""
        row = {
            'Points': '10',
            'Position': '1',
            'Frequency': '5'
        }
        
        result = parse_event_score_fields(row)
        
        self.assertEqual(result['points'], 10)
        self.assertEqual(result['position'], 1)
        self.assertEqual(result['frequency'], 5)
    
    def test_parses_with_empty_position(self):
        """Should set position to None when empty"""
        row = {
            'Points': '10',
            'Position': '',
            'Frequency': '5'
        }
        
        result = parse_event_score_fields(row)
        
        self.assertEqual(result['points'], 10)
        self.assertIsNone(result['position'])
        self.assertEqual(result['frequency'], 5)
    
    def test_parses_with_empty_frequency(self):
        """Should set frequency to None when empty"""
        row = {
            'Points': '10',
            'Position': '1',
            'Frequency': ''
        }
        
        result = parse_event_score_fields(row)
        
        self.assertEqual(result['points'], 10)
        self.assertEqual(result['position'], 1)
        self.assertIsNone(result['frequency'])
    
    def test_parses_with_all_empty_except_points(self):
        """Should handle case where only points is present"""
        row = {
            'Points': '10',
            'Position': '',
            'Frequency': ''
        }
        
        result = parse_event_score_fields(row)
        
        self.assertEqual(result['points'], 10)
        self.assertIsNone(result['position'])
        self.assertIsNone(result['frequency'])
    
    def test_parses_negative_points(self):
        """Should parse negative points correctly"""
        row = {
            'Points': '-5',
            'Position': '',
            'Frequency': ''
        }
        
        result = parse_event_score_fields(row)
        
        self.assertEqual(result['points'], -5)
    
    def test_parses_zero_points(self):
        """Should handle zero points"""
        row = {
            'Points': '0',
            'Position': '',
            'Frequency': ''
        }
        
        result = parse_event_score_fields(row)
        
        self.assertEqual(result['points'], 0)
    
    def test_defaults_to_zero_when_points_empty(self):
        """Should default to 0 when points is empty"""
        row = {
            'Points': '',
            'Position': '',
            'Frequency': ''
        }
        
        result = parse_event_score_fields(row)
        
        self.assertEqual(result['points'], 0)
    
    def test_handles_unicode_minus_sign(self):
        """Should handle unicode minus sign (−) in points"""
        row = {
            'Points': '−5',  # Unicode minus sign
            'Position': '',
            'Frequency': ''
        }
        
        result = parse_event_score_fields(row)
        
        self.assertEqual(result['points'], -5)


class ExtractEventTypesTests(TestCase):
    """Tests for extract_event_types() function"""
    
    def test_extracts_unique_event_types(self):
        """Should extract unique event types from rows"""
        rows = [
            {'Event Type': 'qualifying'},
            {'Event Type': 'race'},
            {'Event Type': 'qualifying'},
            {'Event Type': 'sprint'},
        ]
        
        result = extract_event_types(rows)
        
        self.assertEqual(result, {'qualifying', 'race', 'sprint'})
    
    def test_handles_single_event_type(self):
        """Should handle single event type"""
        rows = [
            {'Event Type': 'race'},
            {'Event Type': 'race'},
        ]
        
        result = extract_event_types(rows)
        
        self.assertEqual(result, {'race'})
    
    def test_handles_empty_rows(self):
        """Should return empty set for empty rows"""
        rows = []
        
        result = extract_event_types(rows)
        
        self.assertEqual(result, set())


class GetOrCreateTeamTests(TestCase):
    """Tests for get_or_create_team() function"""
    
    def test_creates_new_team(self):
        """Should create new team with short name"""
        team, created = get_or_create_team('McLaren')
        
        self.assertTrue(created)
        self.assertEqual(team.name, 'McLaren')
        self.assertEqual(team.short_name, 'MCL')
    
    def test_retrieves_existing_team(self):
        """Should retrieve existing team without creating duplicate"""
        existing_team = Team.objects.create(name='McLaren', short_name='MCL')
        
        team, created = get_or_create_team('McLaren')
        
        self.assertFalse(created)
        self.assertEqual(team, existing_team)
    
    def test_creates_short_name_from_first_three_chars(self):
        """Should create 3-letter short name from team name"""
        team, _ = get_or_create_team('Ferrari')
        
        self.assertEqual(team.short_name, 'FER')
    
    def test_handles_short_team_names(self):
        """Should handle team names shorter than 3 characters"""
        team, _ = get_or_create_team('AB')
        
        self.assertEqual(team.short_name, 'AB')


class ParseTotalsTests(TestCase):
    """Tests for parse_totals() function"""
    
    def test_parses_both_totals(self):
        """Should parse both race and season totals"""
        race_total, season_total = parse_totals('59', '614')
        
        self.assertEqual(race_total, 59)
        self.assertEqual(season_total, 614)
    
    def test_handles_empty_race_total(self):
        """Should default to 0 when race total is empty"""
        race_total, season_total = parse_totals('', '614')
        
        self.assertEqual(race_total, 0)
        self.assertEqual(season_total, 614)
    
    def test_handles_empty_season_total(self):
        """Should default to 0 when season total is empty"""
        race_total, season_total = parse_totals('59', '')
        
        self.assertEqual(race_total, 59)
        self.assertEqual(season_total, 0)
    
    def test_handles_both_empty(self):
        """Should default both to 0 when both are empty"""
        race_total, season_total = parse_totals('', '')
        
        self.assertEqual(race_total, 0)
        self.assertEqual(season_total, 0)
    
    def test_handles_zero_values(self):
        """Should handle zero values correctly"""
        race_total, season_total = parse_totals('0', '0')
        
        self.assertEqual(race_total, 0)
        self.assertEqual(season_total, 0)
    
    def test_handles_large_numbers(self):
        """Should handle large numbers"""
        race_total, season_total = parse_totals('150', '1500')
        
        self.assertEqual(race_total, 150)
        self.assertEqual(season_total, 1500)
    
    def test_handles_negative_totals(self):
        """Should handle negative totals (edge case)"""
        race_total, season_total = parse_totals('-5', '100')
        
        self.assertEqual(race_total, -5)
        self.assertEqual(season_total, 100)


class AdditionalResolveCSVFileTests(TestCase):
    """Additional edge case tests for resolve_csv_file"""
    
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_path = Path(self.test_dir)
    
    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir)
    
    def test_handles_custom_subdirectory(self):
        """Should work with custom data subdirectory"""
        # Create custom directory structure
        custom_dir = self.test_path / 'data' / '2025' / 'custom'
        custom_dir.mkdir(parents=True)
        test_file = custom_dir / '2025-01-15-test.csv'
        test_file.touch()
        
        options = {}
        
        with self.settings(BASE_DIR=self.test_path):
            result = resolve_csv_file(options, 2025, '*-test.csv', data_subdir='custom')
        
        self.assertEqual(result.name, '2025-01-15-test.csv')
