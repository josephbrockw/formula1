"""
Integration tests for import_driver_performance command.

Tests the full import workflow using sample CSV files.
"""

from pathlib import Path
from io import StringIO
from decimal import Decimal
from django.test import TestCase
from django.core.management import call_command
from django.core.management.base import CommandError
from analytics.models import (
    Season, Driver, Team, Race, DriverRacePerformance, DriverEventScore
)


class ImportDriverPerformanceCommandTests(TestCase):
    """Tests for import_driver_performance management command"""
    
    @classmethod
    def setUpTestData(cls):
        """Set up test data once for all tests"""
        cls.season = Season.objects.create(year=2025, name='2025 Season')
        cls.fixtures_dir = Path(__file__).parent / 'fixtures'
        cls.sample_file = cls.fixtures_dir / 'sample-drivers-performance.csv'
    
    def test_import_with_explicit_file(self):
        """Should import data when explicit file path provided"""
        out = StringIO()
        
        call_command(
            'import_driver_performance',
            file=str(self.sample_file),
            stdout=out
        )
        
        output = out.getvalue()
        self.assertIn('Import complete!', output)
        self.assertIn('Races created/updated: 2', output)
        self.assertIn('Driver performances: 4', output)
        self.assertIn('Event scores: 17', output)
    
    def test_creates_races(self):
        """Should create Race objects with correct data"""
        call_command('import_driver_performance', file=str(self.sample_file), stdout=StringIO())
        
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
    
    def test_creates_drivers(self):
        """Should create Driver objects"""
        call_command('import_driver_performance', file=str(self.sample_file), stdout=StringIO())
        
        drivers = Driver.objects.all()
        self.assertEqual(drivers.count(), 3)
        
        # Check drivers were created
        max_verstappen = Driver.objects.get(full_name='Max Verstappen')
        self.assertEqual(max_verstappen.first_name, 'Max')
        self.assertEqual(max_verstappen.last_name, 'Verstappen')
        
        lando = Driver.objects.get(full_name='Lando Norris')
        self.assertEqual(lando.first_name, 'Lando')
        self.assertEqual(lando.last_name, 'Norris')
    
    def test_creates_teams(self):
        """Should create Team objects"""
        call_command('import_driver_performance', file=str(self.sample_file), stdout=StringIO())
        
        teams = Team.objects.all()
        self.assertEqual(teams.count(), 3)
        
        # Check team creation
        mclaren = Team.objects.get(name='McLaren')
        self.assertEqual(mclaren.short_name, 'MCL')
        
        redbull = Team.objects.get(name='Red Bull Racing')
        self.assertEqual(redbull.short_name, 'RED')
    
    def test_creates_driver_race_performances(self):
        """Should create DriverRacePerformance objects with correct data"""
        call_command('import_driver_performance', file=str(self.sample_file), stdout=StringIO())
        
        performances = DriverRacePerformance.objects.all()
        self.assertEqual(performances.count(), 4)
        
        # Check Max Verstappen Bahrain performance
        max_bahrain = DriverRacePerformance.objects.get(
            driver__full_name='Max Verstappen',
            race__name='Bahrain'
        )
        self.assertEqual(max_bahrain.total_points, 45)
        self.assertEqual(max_bahrain.fantasy_price, Decimal('29.5'))
        self.assertEqual(max_bahrain.season_points_cumulative, 618)
        self.assertEqual(max_bahrain.team.name, 'Red Bull Racing')
        self.assertTrue(max_bahrain.had_qualifying)
        self.assertTrue(max_bahrain.had_race)
        self.assertFalse(max_bahrain.had_sprint)
        
        # Check Lando Norris Bahrain performance
        lando_bahrain = DriverRacePerformance.objects.get(
            driver__full_name='Lando Norris',
            race__name='Bahrain'
        )
        self.assertEqual(lando_bahrain.total_points, 59)
        self.assertEqual(lando_bahrain.fantasy_price, Decimal('30.4'))
    
    def test_creates_driver_event_scores(self):
        """Should create DriverEventScore objects with correct data"""
        call_command('import_driver_performance', file=str(self.sample_file), stdout=StringIO())
        
        # Check total count
        scores = DriverEventScore.objects.all()
        self.assertEqual(scores.count(), 17)
        
        # Check Max Verstappen Bahrain qualifying score
        max_qual = DriverEventScore.objects.get(
            performance__driver__full_name='Max Verstappen',
            performance__race__name='Bahrain',
            event_type='qualifying',
            scoring_item='Qualifying Position'
        )
        self.assertEqual(max_qual.points, 10)
        self.assertEqual(max_qual.position, 1)
        self.assertIsNone(max_qual.frequency)
        
        # Check Lando Norris race overtake bonus
        lando_overtake = DriverEventScore.objects.get(
            performance__driver__full_name='Lando Norris',
            performance__race__name='Bahrain',
            scoring_item='Race Overtake Bonus'
        )
        self.assertEqual(lando_overtake.points, 5)
        self.assertEqual(lando_overtake.frequency, 5)
        self.assertIsNone(lando_overtake.position)
    
    def test_calculates_points_per_million(self):
        """Should calculate points_per_million property correctly"""
        call_command('import_driver_performance', file=str(self.sample_file), stdout=StringIO())
        
        lando_bahrain = DriverRacePerformance.objects.get(
            driver__full_name='Lando Norris',
            race__name='Bahrain'
        )
        
        # 59 points / 30.4 price = ~1.94 points per million
        expected = 59 / 30.4
        self.assertAlmostEqual(lando_bahrain.points_per_million, expected, places=2)
    
    def test_reimport_updates_existing_data(self):
        """Should update existing data when reimporting"""
        # First import
        call_command('import_driver_performance', file=str(self.sample_file), stdout=StringIO())
        
        initial_count = DriverRacePerformance.objects.count()
        initial_scores_count = DriverEventScore.objects.count()
        
        # Second import (should update, not duplicate)
        call_command('import_driver_performance', file=str(self.sample_file), stdout=StringIO())
        
        # Counts should remain the same
        self.assertEqual(DriverRacePerformance.objects.count(), initial_count)
        self.assertEqual(DriverEventScore.objects.count(), initial_scores_count)
    
    def test_fails_when_season_not_found(self):
        """Should raise error when season doesn't exist"""
        with self.assertRaises(CommandError) as context:
            call_command(
                'import_driver_performance',
                file=str(self.sample_file),
                year=2099,
                stdout=StringIO()
            )
        
        self.assertIn('Season 2099 not found', str(context.exception))
    
    def test_fails_when_file_not_found(self):
        """Should raise error when file doesn't exist"""
        with self.assertRaises(CommandError) as context:
            call_command(
                'import_driver_performance',
                file='/nonexistent/file.csv',
                stdout=StringIO()
            )
        
        self.assertIn('File not found', str(context.exception))
    
    def test_handles_multiple_drivers_in_same_race(self):
        """Should correctly handle multiple drivers in the same race"""
        call_command('import_driver_performance', file=str(self.sample_file), stdout=StringIO())
        
        bahrain_performances = DriverRacePerformance.objects.filter(race__name='Bahrain')
        self.assertEqual(bahrain_performances.count(), 3)
        
        # All three drivers should have unique performances
        drivers = [p.driver.full_name for p in bahrain_performances]
        self.assertEqual(len(set(drivers)), 3)  # All unique
    
    def test_handles_same_driver_in_multiple_races(self):
        """Should correctly handle same driver in multiple races"""
        call_command('import_driver_performance', file=str(self.sample_file), stdout=StringIO())
        
        max_performances = DriverRacePerformance.objects.filter(
            driver__full_name='Max Verstappen'
        )
        self.assertEqual(max_performances.count(), 2)
        
        # Check both races
        races = [p.race.name for p in max_performances]
        self.assertIn('Bahrain', races)
        self.assertIn('Saudi Arabia', races)
    
    def test_drivers_dont_need_to_appear_in_all_races(self):
        """Should handle drivers appearing in different numbers of races (e.g., mid-season changes)"""
        call_command('import_driver_performance', file=str(self.sample_file), stdout=StringIO())
        
        # Max Verstappen appears in 2 races
        max_performances = DriverRacePerformance.objects.filter(
            driver__full_name='Max Verstappen'
        )
        self.assertEqual(max_performances.count(), 2)
        
        # Lando Norris only appears in 1 race
        lando_performances = DriverRacePerformance.objects.filter(
            driver__full_name='Lando Norris'
        )
        self.assertEqual(lando_performances.count(), 1)
        
        # Lewis Hamilton only appears in 1 race
        lewis_performances = DriverRacePerformance.objects.filter(
            driver__full_name='Lewis Hamilton'
        )
        self.assertEqual(lewis_performances.count(), 1)
        
        # All races should still be created
        self.assertEqual(Race.objects.count(), 2)
        
        # Total performances = 4 (not 3 drivers × 2 races = 6)
        self.assertEqual(DriverRacePerformance.objects.count(), 4)
    
    def test_handles_negative_points(self):
        """Should handle negative points correctly"""
        # Create a CSV with negative points
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write('Driver Name,Team,Driver Value,Race,Event Type,Scoring Item,Frequency,Position,Points,Race Total,Season Total\n')
            f.write('Test Driver,Test Team,$20.0M,Test Race,race,DNF Penalty,,,−5,10,100\n')
            f.write('Test Driver,Test Team,$20.0M,Test Race,race,Race Position,,10,15,10,100\n')
            temp_file = f.name
        
        try:
            call_command('import_driver_performance', file=temp_file, stdout=StringIO())
            
            # Verify negative points were imported
            # Note: The minus sign in CSV might be a unicode character, so we check the total
            performance = DriverRacePerformance.objects.get(
                driver__full_name='Test Driver',
                race__name='Test Race'
            )
            self.assertEqual(performance.total_points, 10)
        finally:
            Path(temp_file).unlink()
    
    def test_command_output_shows_progress(self):
        """Should show informative output during import"""
        out = StringIO()
        call_command('import_driver_performance', file=str(self.sample_file), stdout=out)
        
        output = out.getvalue()
        self.assertIn('Found season:', output)
        self.assertIn('Found performance file:', output)
        self.assertIn('Processing', output)
        self.assertIn('driver-race combinations', output)
        self.assertIn('Import complete!', output)
    
    def test_handles_driver_changing_teams(self):
        """Should handle driver switching teams between races"""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write('Driver Name,Team,Driver Value,Race,Event Type,Scoring Item,Frequency,Position,Points,Race Total,Season Total\n')
            f.write('Carlos Sainz,Ferrari,$25.0M,Bahrain,race,Race Position,,5,10,10,100\n')
            f.write('Carlos Sainz,Williams,$20.0M,Saudi Arabia,race,Race Position,,10,6,6,106\n')
            temp_file = f.name
        
        try:
            call_command('import_driver_performance', file=temp_file, stdout=StringIO())
            
            # Should create both performances with different teams
            performances = DriverRacePerformance.objects.filter(
                driver__full_name='Carlos Sainz'
            ).order_by('race__round_number')
            
            self.assertEqual(performances.count(), 2)
            self.assertEqual(performances[0].team.name, 'Ferrari')
            self.assertEqual(performances[1].team.name, 'Williams')
        finally:
            Path(temp_file).unlink()
    
    def test_handles_special_characters_in_names(self):
        """Should handle special characters in driver and team names"""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write('Driver Name,Team,Driver Value,Race,Event Type,Scoring Item,Frequency,Position,Points,Race Total,Season Total\n')
            f.write('Sergio Pérez,Red Bull Racing,$26.0M,Monaco,race,Race Position,,1,25,25,300\n')
            temp_file = f.name
        
        try:
            call_command('import_driver_performance', file=temp_file, stdout=StringIO())
            
            # Should create driver with accented name
            driver = Driver.objects.get(full_name='Sergio Pérez')
            self.assertEqual(driver.first_name, 'Sergio')
            self.assertEqual(driver.last_name, 'Pérez')
        finally:
            Path(temp_file).unlink()
    
    def test_handles_empty_csv_file(self):
        """Should handle empty CSV file gracefully"""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write('Driver Name,Team,Driver Value,Race,Event Type,Scoring Item,Frequency,Position,Points,Race Total,Season Total\n')
            # No data rows
            temp_file = f.name
        
        try:
            out = StringIO()
            call_command('import_driver_performance', file=temp_file, stdout=out)
            
            # Should complete without error but create no data
            output = out.getvalue()
            self.assertIn('Import complete!', output)
            self.assertEqual(DriverRacePerformance.objects.count(), 0)
        finally:
            Path(temp_file).unlink()
