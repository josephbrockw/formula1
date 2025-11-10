"""
Integration tests for import_constructor_performance command.

Tests the full import workflow using sample CSV files.
"""

from pathlib import Path
from io import StringIO
from decimal import Decimal
from django.test import TestCase
from django.core.management import call_command
from django.core.management.base import CommandError
from analytics.models import (
    Season, Team, Race, ConstructorRacePerformance, ConstructorEventScore
)


class ImportConstructorPerformanceCommandTests(TestCase):
    """Tests for import_constructor_performance management command"""
    
    @classmethod
    def setUpTestData(cls):
        """Set up test data once for all tests"""
        cls.season = Season.objects.create(year=2025, name='2025 Season')
        cls.fixtures_dir = Path(__file__).parent / 'fixtures'
        cls.sample_file = cls.fixtures_dir / 'sample-constructors-performance.csv'
    
    def test_import_with_explicit_file(self):
        """Should import data when explicit file path provided"""
        out = StringIO()
        
        call_command(
            'import_constructor_performance',
            file=str(self.sample_file),
            stdout=out
        )
        
        output = out.getvalue()
        self.assertIn('Import complete!', output)
        self.assertIn('Races created/updated: 2', output)
        self.assertIn('Constructor performances: 4', output)
        self.assertIn('Event scores: 25', output)
    
    def test_creates_races(self):
        """Should create Race objects with correct data"""
        call_command('import_constructor_performance', file=str(self.sample_file), stdout=StringIO())
        
        races = Race.objects.all().order_by('round_number')
        self.assertEqual(races.count(), 2)
        
        # Check first race
        bahrain = races[0]
        self.assertEqual(bahrain.name, 'Bahrain')
        self.assertEqual(bahrain.round_number, 1)
        self.assertEqual(bahrain.season, self.season)
        
        # Check second race
        saudi = races[1]
        self.assertEqual(saudi.name, 'Saudi Arabia')
        self.assertEqual(saudi.round_number, 2)
    
    def test_creates_teams(self):
        """Should create Team objects"""
        call_command('import_constructor_performance', file=str(self.sample_file), stdout=StringIO())
        
        teams = Team.objects.all()
        self.assertEqual(teams.count(), 3)
        
        # Check teams were created
        mclaren = Team.objects.get(name='McLaren')
        self.assertEqual(mclaren.short_name, 'MCL')
        
        redbull = Team.objects.get(name='Red Bull Racing')
        self.assertEqual(redbull.short_name, 'RED')
        
        ferrari = Team.objects.get(name='Ferrari')
        self.assertEqual(ferrari.short_name, 'FER')
    
    def test_creates_constructor_race_performances(self):
        """Should create ConstructorRacePerformance objects with correct data"""
        call_command('import_constructor_performance', file=str(self.sample_file), stdout=StringIO())
        
        performances = ConstructorRacePerformance.objects.all()
        self.assertEqual(performances.count(), 4)
        
        # Check McLaren Bahrain performance
        mclaren_bahrain = ConstructorRacePerformance.objects.get(
            team__name='McLaren',
            race__name='Bahrain'
        )
        self.assertEqual(mclaren_bahrain.total_points, 95)
        self.assertEqual(mclaren_bahrain.fantasy_price, Decimal('32.0'))
        self.assertEqual(mclaren_bahrain.season_points_cumulative, 1199)
        self.assertTrue(mclaren_bahrain.had_qualifying)
        self.assertTrue(mclaren_bahrain.had_race)
        self.assertFalse(mclaren_bahrain.had_sprint)
        
        # Check Red Bull Racing Bahrain performance
        redbull_bahrain = ConstructorRacePerformance.objects.get(
            team__name='Red Bull Racing',
            race__name='Bahrain'
        )
        self.assertEqual(redbull_bahrain.total_points, 82)
        self.assertEqual(redbull_bahrain.fantasy_price, Decimal('30.8'))
    
    def test_creates_constructor_event_scores(self):
        """Should create ConstructorEventScore objects with correct data"""
        call_command('import_constructor_performance', file=str(self.sample_file), stdout=StringIO())
        
        # Check total count
        scores = ConstructorEventScore.objects.all()
        self.assertEqual(scores.count(), 25)
        
        # Check McLaren Bahrain qualifying score
        mclaren_qual = ConstructorEventScore.objects.get(
            performance__team__name='McLaren',
            performance__race__name='Bahrain',
            event_type='qualifying',
            scoring_item='Qualifying Position'
        )
        self.assertEqual(mclaren_qual.points, 10)
        self.assertEqual(mclaren_qual.position, 1)
        self.assertIsNone(mclaren_qual.frequency)
        
        # Check McLaren pitstop bonus
        mclaren_pitstop = ConstructorEventScore.objects.get(
            performance__team__name='McLaren',
            performance__race__name='Bahrain',
            scoring_item='Pitstop Bonus'
        )
        self.assertEqual(mclaren_pitstop.points, 2)
        self.assertEqual(mclaren_pitstop.frequency, 2)
        self.assertIsNone(mclaren_pitstop.position)
        
        # Check Red Bull Racing fastest lap (no frequency or position)
        redbull_fastest = ConstructorEventScore.objects.get(
            performance__team__name='Red Bull Racing',
            performance__race__name='Bahrain',
            scoring_item='Fastest Lap'
        )
        self.assertEqual(redbull_fastest.points, 10)
        self.assertIsNone(redbull_fastest.position)
        self.assertIsNone(redbull_fastest.frequency)
    
    def test_calculates_points_per_million(self):
        """Should calculate points_per_million property correctly"""
        call_command('import_constructor_performance', file=str(self.sample_file), stdout=StringIO())
        
        mclaren_bahrain = ConstructorRacePerformance.objects.get(
            team__name='McLaren',
            race__name='Bahrain'
        )
        
        # 95 points / 32.0 price = ~2.97 points per million
        expected = 95 / 32.0
        self.assertAlmostEqual(mclaren_bahrain.points_per_million, expected, places=2)
    
    def test_reimport_updates_existing_data(self):
        """Should update existing data when reimporting"""
        # First import
        call_command('import_constructor_performance', file=str(self.sample_file), stdout=StringIO())
        
        initial_count = ConstructorRacePerformance.objects.count()
        initial_scores_count = ConstructorEventScore.objects.count()
        
        # Second import (should update, not duplicate)
        call_command('import_constructor_performance', file=str(self.sample_file), stdout=StringIO())
        
        # Counts should remain the same
        self.assertEqual(ConstructorRacePerformance.objects.count(), initial_count)
        self.assertEqual(ConstructorEventScore.objects.count(), initial_scores_count)
    
    def test_fails_when_season_not_found(self):
        """Should raise error when season doesn't exist"""
        with self.assertRaises(CommandError) as context:
            call_command(
                'import_constructor_performance',
                file=str(self.sample_file),
                year=2099,
                stdout=StringIO()
            )
        
        self.assertIn('Season 2099 not found', str(context.exception))
    
    def test_fails_when_file_not_found(self):
        """Should raise error when file doesn't exist"""
        with self.assertRaises(CommandError) as context:
            call_command(
                'import_constructor_performance',
                file='/nonexistent/file.csv',
                stdout=StringIO()
            )
        
        self.assertIn('File not found', str(context.exception))
    
    def test_handles_multiple_constructors_in_same_race(self):
        """Should correctly handle multiple constructors in the same race"""
        call_command('import_constructor_performance', file=str(self.sample_file), stdout=StringIO())
        
        bahrain_performances = ConstructorRacePerformance.objects.filter(race__name='Bahrain')
        self.assertEqual(bahrain_performances.count(), 3)
        
        # All three teams should have unique performances
        teams = [p.team.name for p in bahrain_performances]
        self.assertEqual(len(set(teams)), 3)  # All unique
    
    def test_handles_same_constructor_in_multiple_races(self):
        """Should correctly handle same constructor in multiple races"""
        call_command('import_constructor_performance', file=str(self.sample_file), stdout=StringIO())
        
        mclaren_performances = ConstructorRacePerformance.objects.filter(
            team__name='McLaren'
        )
        self.assertEqual(mclaren_performances.count(), 2)
        
        # Check both races
        races = [p.race.name for p in mclaren_performances]
        self.assertIn('Bahrain', races)
        self.assertIn('Saudi Arabia', races)
    
    def test_constructors_dont_need_to_appear_in_all_races(self):
        """Should handle constructors appearing in different numbers of races"""
        call_command('import_constructor_performance', file=str(self.sample_file), stdout=StringIO())
        
        # McLaren appears in 2 races
        mclaren_performances = ConstructorRacePerformance.objects.filter(
            team__name='McLaren'
        )
        self.assertEqual(mclaren_performances.count(), 2)
        
        # Red Bull Racing only appears in 1 race
        redbull_performances = ConstructorRacePerformance.objects.filter(
            team__name='Red Bull Racing'
        )
        self.assertEqual(redbull_performances.count(), 1)
        
        # Ferrari only appears in 1 race
        ferrari_performances = ConstructorRacePerformance.objects.filter(
            team__name='Ferrari'
        )
        self.assertEqual(ferrari_performances.count(), 1)
        
        # All races should still be created
        self.assertEqual(Race.objects.count(), 2)
        
        # Total performances = 4 (not 3 teams × 2 races = 6)
        self.assertEqual(ConstructorRacePerformance.objects.count(), 4)
    
    def test_shares_races_with_driver_import(self):
        """Should share Race objects when both imports run"""
        # Import constructor data first
        call_command('import_constructor_performance', file=str(self.sample_file), stdout=StringIO())
        initial_race_count = Race.objects.count()
        
        # Import driver data (should reuse same races)
        driver_file = self.fixtures_dir / 'sample-drivers-performance.csv'
        call_command('import_driver_performance', file=str(driver_file), stdout=StringIO())
        
        # Race count should remain the same (races are shared)
        final_race_count = Race.objects.count()
        self.assertEqual(initial_race_count, final_race_count)
        
        # But we should have both types of performances
        self.assertGreater(ConstructorRacePerformance.objects.count(), 0)
        from analytics.models import DriverRacePerformance
        self.assertGreater(DriverRacePerformance.objects.count(), 0)
    
    def test_handles_zero_points(self):
        """Should handle zero points correctly"""
        call_command('import_constructor_performance', file=str(self.sample_file), stdout=StringIO())
        
        # Ferrari had 0 positions gained
        ferrari_positions = ConstructorEventScore.objects.get(
            performance__team__name='Ferrari',
            performance__race__name='Bahrain',
            scoring_item='Race Positions Gained'
        )
        self.assertEqual(ferrari_positions.points, 0)
        self.assertEqual(ferrari_positions.frequency, 0)
    
    def test_command_output_shows_progress(self):
        """Should show informative output during import"""
        out = StringIO()
        call_command('import_constructor_performance', file=str(self.sample_file), stdout=out)
        
        output = out.getvalue()
        self.assertIn('Found season:', output)
        self.assertIn('Found performance file:', output)
        self.assertIn('Processing', output)
        self.assertIn('constructor-race combinations', output)
        self.assertIn('Import complete!', output)
    
    def test_handles_different_event_types(self):
        """Should correctly categorize different event types"""
        call_command('import_constructor_performance', file=str(self.sample_file), stdout=StringIO())
        
        mclaren_bahrain = ConstructorRacePerformance.objects.get(
            team__name='McLaren',
            race__name='Bahrain'
        )
        
        # Check event participation flags
        self.assertTrue(mclaren_bahrain.had_qualifying)
        self.assertTrue(mclaren_bahrain.had_race)
        self.assertFalse(mclaren_bahrain.had_sprint)
        
        # Check event scores by type
        qual_scores = ConstructorEventScore.objects.filter(
            performance=mclaren_bahrain,
            event_type='qualifying'
        )
        self.assertGreater(qual_scores.count(), 0)
        
        race_scores = ConstructorEventScore.objects.filter(
            performance=mclaren_bahrain,
            event_type='race'
        )
        self.assertGreater(race_scores.count(), 0)
    
    def test_handles_special_characters_in_team_names(self):
        """Should handle special characters in team names"""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write('Constructor Name,Constructor Value,Race,Event Type,Scoring Item,Frequency,Position,Points,Race Total,Season Total\n')
            f.write('Alfa Romeo,$18.0M,Monaco,race,Race Position,,8,8,8,150\n')
            temp_file = f.name
        
        try:
            call_command('import_constructor_performance', file=temp_file, stdout=StringIO())
            
            # Should create team with special characters
            team = Team.objects.get(name='Alfa Romeo')
            self.assertEqual(team.name, 'Alfa Romeo')
        finally:
            Path(temp_file).unlink()
    
    def test_handles_empty_csv_file(self):
        """Should handle empty CSV file gracefully"""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write('Constructor Name,Constructor Value,Race,Event Type,Scoring Item,Frequency,Position,Points,Race Total,Season Total\n')
            # No data rows
            temp_file = f.name
        
        try:
            out = StringIO()
            call_command('import_constructor_performance', file=temp_file, stdout=out)
            
            # Should complete without error but create no data
            output = out.getvalue()
            self.assertIn('Import complete!', output)
            self.assertEqual(ConstructorRacePerformance.objects.count(), 0)
        finally:
            Path(temp_file).unlink()
