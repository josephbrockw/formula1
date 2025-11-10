"""
Management command to import driver and constructor data from CSV files

Usage:
    python manage.py import_fantasy_prices --date 2025-11-07
    python manage.py import_fantasy_prices  # Uses today's date
    
If a file with the exact date is not found, the command will automatically
fall back to the most recent file in the snapshots directory.
"""

import csv
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from analytics.models import (
    Season, Team, Driver, DriverSnapshot, ConstructorSnapshot
)


class Command(BaseCommand):
    help = 'Import F1 Fantasy data from CSV files'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Snapshot date (YYYY-MM-DD). Defaults to today if not provided'
        )

    def find_most_recent_file(self, data_dir, file_type):
        """Find the most recent file of a given type in the data directory
        
        Args:
            data_dir: Path to the data directory
            file_type: Either 'drivers' or 'constructors'
            
        Returns:
            Tuple of (file_path, date) or (None, None) if no files found
        """
        if not data_dir.exists():
            return None, None
        
        # Find all files matching the pattern
        pattern = f'*-{file_type}.csv'
        matching_files = list(data_dir.glob(pattern))
        
        if not matching_files:
            return None, None
        
        # Sort by filename (which starts with date in YYYY-MM-DD format)
        matching_files.sort(reverse=True)
        most_recent_file = matching_files[0]
        
        # Extract date from filename (format: YYYY-MM-DD-drivers.csv)
        filename = most_recent_file.name
        date_str = filename.split('-' + file_type)[0]
        file_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        return most_recent_file, file_date

    def handle(self, *args, **options):
        # Get snapshot date (default to today)
        date_str = options['date']
        is_historical = bool(date_str)  # True if user provided a date, False if using today
        
        if date_str:
            snapshot_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        else:
            snapshot_date = date.today()
        
        if is_historical:
            self.stdout.write(f"Processing historical data for date: {snapshot_date}")
        else:
            self.stdout.write(f"Processing current data for date: {snapshot_date} (will update driver teams)")
        
        # Derive season from the year in the date
        season_year = snapshot_date.year
        
        if is_historical:
            # For historical data, require season to exist
            try:
                season = Season.objects.get(year=season_year)
                self.stdout.write(f"Found season: {season}")
            except Season.DoesNotExist:
                raise CommandError(
                    f'Season {season_year} not found in database. Please create it first.'
                )
        else:
            # For current data (today), create season if needed and manage active status
            season, created = Season.objects.get_or_create(
                year=season_year,
                defaults={
                    'name': f'{season_year} Formula 1 Season',
                    'is_active': True
                }
            )
            
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created new season: {season}'))
                # Deactivate all other seasons
                deactivated_count = Season.objects.exclude(year=season_year).filter(is_active=True).update(is_active=False)
                if deactivated_count > 0:
                    self.stdout.write(f"Deactivated {deactivated_count} other season(s)")
            else:
                self.stdout.write(f"Found season: {season}")
                # Ensure this season is active and others are not
                if not season.is_active:
                    season.is_active = True
                    season.save()
                    self.stdout.write(f"Activated season: {season}")
                
                deactivated_count = Season.objects.exclude(year=season_year).filter(is_active=True).update(is_active=False)
                if deactivated_count > 0:
                    self.stdout.write(f"Deactivated {deactivated_count} other season(s)")
        
        # Construct file paths relative to project root
        date_formatted = snapshot_date.strftime('%Y-%m-%d')
        # Get the project base directory (f1_analytics)
        base_dir = Path(settings.BASE_DIR)
        data_dir = base_dir / 'data' / str(season.year) / 'snapshots'
        drivers_file = data_dir / f'{date_formatted}-drivers.csv'
        constructors_file = data_dir / f'{date_formatted}-constructors.csv'
        
        # Import drivers
        if os.path.exists(drivers_file):
            driver_count = self.import_drivers(drivers_file, season, snapshot_date, update_current_team=not is_historical)
            self.stdout.write(self.style.SUCCESS(
                f'Successfully imported {driver_count} driver snapshots for {snapshot_date}'
            ))
        else:
            # Try to find most recent file
            self.stdout.write(self.style.WARNING(
                f'Drivers file not found: {drivers_file}'
            ))
            most_recent_file, file_date = self.find_most_recent_file(data_dir, 'drivers')
            if most_recent_file:
                self.stdout.write(f'Found most recent drivers file: {most_recent_file.name} (date: {file_date})')
                driver_count = self.import_drivers(most_recent_file, season, file_date, update_current_team=not is_historical)
                self.stdout.write(self.style.SUCCESS(
                    f'Successfully imported {driver_count} driver snapshots from {file_date}'
                ))
            else:
                self.stdout.write(self.style.ERROR('No drivers files found in directory'))
        
        # Import constructors
        if os.path.exists(constructors_file):
            constructor_count = self.import_constructors(constructors_file, season, snapshot_date)
            self.stdout.write(self.style.SUCCESS(
                f'Successfully imported {constructor_count} constructor snapshots for {snapshot_date}'
            ))
        else:
            # Try to find most recent file
            self.stdout.write(self.style.WARNING(
                f'Constructors file not found: {constructors_file}'
            ))
            most_recent_file, file_date = self.find_most_recent_file(data_dir, 'constructors')
            if most_recent_file:
                self.stdout.write(f'Found most recent constructors file: {most_recent_file.name} (date: {file_date})')
                constructor_count = self.import_constructors(most_recent_file, season, file_date)
                self.stdout.write(self.style.SUCCESS(
                    f'Successfully imported {constructor_count} constructor snapshots from {file_date}'
                ))
            else:
                self.stdout.write(self.style.ERROR('No constructors files found in directory'))

    def import_drivers(self, csv_file, season, snapshot_date, update_current_team=False):
        """Import driver data from CSV
        
        Args:
            csv_file: Path to the CSV file
            season: Season object
            snapshot_date: Date of the snapshot
            update_current_team: If True, updates the driver's current_team field (for current data)
        """
        count = 0
        
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                # Get or create driver
                driver, created = Driver.objects.get_or_create(
                    full_name=row['Driver Name'],
                    defaults={
                        'first_name': row['Driver Name'].split()[0],
                        'last_name': ' '.join(row['Driver Name'].split()[1:])
                    }
                )
                
                # Get or create team
                team, _ = Team.objects.get_or_create(
                    name=row['Team'],
                    defaults={'short_name': row['Team'][:3].upper()}
                )
                
                # Update current team if processing current data
                if update_current_team and driver.current_team != team:
                    driver.current_team = team
                    driver.save()
                    self.stdout.write(f"Updated {driver.full_name} current team to {team.name}")
                
                # Parse price (remove $ and M)
                price_str = row['Current Value'].replace('$', '').replace('M', '')
                fantasy_price = Decimal(price_str)
                
                # Parse price change (remove $ and M, keep negative)
                price_change_str = row['Price Change'].replace('$', '').replace('M', '')
                price_change = Decimal(price_change_str)
                
                # Create or update snapshot
                snapshot, created = DriverSnapshot.objects.update_or_create(
                    driver=driver,
                    snapshot_date=snapshot_date,
                    defaults={
                        'team': team,
                        'season': season,
                        'fantasy_price': fantasy_price,
                        'price_change': price_change,
                        'season_points': int(row['Season Points']),
                        'percent_picked': Decimal(row['% Picked']),
                    }
                )
                
                count += 1
                action = "Created" if created else "Updated"
                self.stdout.write(f"{action}: {driver.full_name} - {snapshot_date}")
        
        return count

    def import_constructors(self, csv_file, season, snapshot_date):
        """Import constructor data from CSV"""
        count = 0
        
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                # Get or create team
                team, _ = Team.objects.get_or_create(
                    name=row['Constructor Name'],
                    defaults={'short_name': row['Constructor Name'][:3].upper()}
                )
                
                # Parse price (remove $ and M)
                price_str = row['Current Value'].replace('$', '').replace('M', '')
                fantasy_price = Decimal(price_str)
                
                # Parse price change (remove $ and M, keep negative)
                price_change_str = row['Price Change'].replace('$', '').replace('M', '')
                price_change = Decimal(price_change_str)
                
                # Create or update snapshot
                snapshot, created = ConstructorSnapshot.objects.update_or_create(
                    team=team,
                    snapshot_date=snapshot_date,
                    defaults={
                        'season': season,
                        'fantasy_price': fantasy_price,
                        'price_change': price_change,
                        'season_points': int(row['Season Points']),
                        'percent_picked': Decimal(row['% Picked']),
                    }
                )
                
                count += 1
                action = "Created" if created else "Updated"
                self.stdout.write(f"{action}: {team.name} - {snapshot_date}")
        
        return count
