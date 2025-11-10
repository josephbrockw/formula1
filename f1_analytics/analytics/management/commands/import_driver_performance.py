"""
Management command to import driver performance data from CSV files

Usage:
    python manage.py import_driver_performance
    
The command looks for the most recent *-all-drivers-performance.csv file 
in the data/{year}/outcomes directory.
"""

import csv
from datetime import date
from django.core.management.base import BaseCommand
from django.db import transaction
from analytics.models import Driver, DriverRacePerformance, DriverEventScore
from ._performance_import_utils import (
    get_season, resolve_csv_file, get_or_create_race,
    parse_fantasy_price, parse_event_score_fields,
    extract_event_types, get_or_create_team, parse_totals
)


class Command(BaseCommand):
    help = 'Import F1 Fantasy driver performance data from CSV'

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            help='Specific CSV file to import. If not provided, uses most recent file.'
        )
        parser.add_argument(
            '--year',
            type=int,
            help='Season year. Defaults to current year.'
        )

    def handle(self, *args, **options):
        # Determine year
        year = options.get('year') or date.today().year
        
        # Get season
        season = get_season(year)
        self.stdout.write(f"Found season: {season}")
        
        # Resolve CSV file
        csv_file = resolve_csv_file(
            options, 
            year, 
            '*-all-drivers-performance.csv'
        )
        self.stdout.write(f"Found performance file: {csv_file.name}")
        
        # Import the data
        stats = self.import_performance_data(csv_file, season)
        
        self.stdout.write(self.style.SUCCESS(
            f'\nImport complete!\n'
            f'  Races created/updated: {stats["races"]}\n'
            f'  Driver performances: {stats["performances"]}\n'
            f'  Event scores: {stats["scores"]}'
        ))

    @transaction.atomic
    def import_performance_data(self, csv_file, season):
        """Import performance data from CSV"""
        races_created = 0
        performances_created = 0
        scores_created = 0
        
        # Track races we've seen to assign round numbers
        race_order = {}
        
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            # Group rows by driver and race for efficient processing
            driver_race_data = {}
            
            for row in reader:
                driver_name = row['Driver Name']
                race_name = row['Race']
                
                # Create key for grouping
                key = (driver_name, race_name)
                
                if key not in driver_race_data:
                    driver_race_data[key] = {
                        'rows': [],
                        'team_name': row['Team'],
                        'driver_value': row['Driver Value'],
                        'race_total': row['Race Total'],
                        'season_total': row['Season Total']
                    }
                
                driver_race_data[key]['rows'].append(row)
            
            self.stdout.write(f"Processing {len(driver_race_data)} driver-race combinations...")
            
            # Process each driver-race combination
            for (driver_name, race_name), data in driver_race_data.items():
                # Get or create driver
                driver, _ = Driver.objects.get_or_create(
                    full_name=driver_name,
                    defaults={
                        'first_name': driver_name.split()[0],
                        'last_name': ' '.join(driver_name.split()[1:])
                    }
                )
                
                # Get or create team
                team, _ = get_or_create_team(data['team_name'])
                
                # Get or create race
                race, created = get_or_create_race(season, race_name, race_order)
                if created:
                    races_created += 1
                
                # Parse driver value
                fantasy_price = parse_fantasy_price(data['driver_value'])
                
                # Parse totals
                race_total, season_total = parse_totals(
                    data['race_total'], 
                    data['season_total']
                )
                
                # Determine which events this driver participated in
                event_types = extract_event_types(data['rows'])
                
                # Create or update DriverRacePerformance
                performance, perf_created = DriverRacePerformance.objects.update_or_create(
                    driver=driver,
                    race=race,
                    defaults={
                        'team': team,
                        'total_points': race_total,
                        'fantasy_price': fantasy_price,
                        'season_points_cumulative': season_total,
                        'had_qualifying': 'qualifying' in event_types,
                        'had_sprint': 'sprint' in event_types,
                        'had_race': 'race' in event_types,
                    }
                )
                
                if perf_created:
                    performances_created += 1
                
                # Delete existing event scores for this performance (to handle reimports)
                DriverEventScore.objects.filter(performance=performance).delete()
                
                # Create event scores
                for row in data['rows']:
                    score_fields = parse_event_score_fields(row)
                    
                    DriverEventScore.objects.create(
                        performance=performance,
                        event_type=row['Event Type'],
                        scoring_item=row['Scoring Item'],
                        **score_fields
                    )
                    scores_created += 1
                
                # Progress indicator
                if performances_created % 20 == 0:
                    self.stdout.write(f"  Processed {performances_created} performances...")
        
        return {
            'races': races_created,
            'performances': performances_created,
            'scores': scores_created
        }
