"""
Management command to import constructor performance data from CSV files

Usage:
    python manage.py import_constructor_performance
    
The command looks for the most recent *-all-constructors-performance.csv file 
in the data/{year}/outcomes directory.
"""

import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import transaction
from analytics.models import (
    Season, Team, Race, ConstructorRacePerformance, ConstructorEventScore
)


class Command(BaseCommand):
    help = 'Import F1 Fantasy constructor performance data from CSV'

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

    def find_most_recent_performance_file(self, data_dir):
        """Find the most recent constructor performance CSV file"""
        if not data_dir.exists():
            return None
        
        pattern = '*-all-constructors-performance.csv'
        matching_files = list(data_dir.glob(pattern))
        
        if not matching_files:
            return None
        
        # Sort by filename (starts with date)
        matching_files.sort(reverse=True)
        return matching_files[0]

    def handle(self, *args, **options):
        # Determine year
        year = options.get('year') or date.today().year
        
        # Get or validate season
        try:
            season = Season.objects.get(year=year)
            self.stdout.write(f"Found season: {season}")
        except Season.DoesNotExist:
            raise CommandError(
                f'Season {year} not found. Please create it first or run import_fantasy_prices.'
            )
        
        # Determine file to import
        if options.get('file'):
            csv_file = Path(options['file'])
            if not csv_file.exists():
                raise CommandError(f'File not found: {csv_file}')
        else:
            # Find most recent file
            base_dir = Path(settings.BASE_DIR)
            data_dir = base_dir / 'data' / str(year) / 'outcomes'
            csv_file = self.find_most_recent_performance_file(data_dir)
            
            if not csv_file:
                raise CommandError(
                    f'No constructor performance files found in {data_dir}. '
                    'Please export data first using the Chrome extension.'
                )
            
            self.stdout.write(f"Found performance file: {csv_file.name}")
        
        # Import the data
        stats = self.import_performance_data(csv_file, season)
        
        self.stdout.write(self.style.SUCCESS(
            f'\nImport complete!\n'
            f'  Races created/updated: {stats["races"]}\n'
            f'  Constructor performances: {stats["performances"]}\n'
            f'  Event scores: {stats["scores"]}'
        ))

    @transaction.atomic
    def import_performance_data(self, csv_file, season):
        """Import constructor performance data from CSV"""
        races_created = 0
        performances_created = 0
        scores_created = 0
        
        # Track races we've seen to assign round numbers
        race_order = {}
        current_round = 1
        
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            # Group rows by constructor and race for efficient processing
            constructor_race_data = {}
            
            for row in reader:
                constructor_name = row['Constructor Name']
                race_name = row['Race']
                
                # Create key for grouping
                key = (constructor_name, race_name)
                
                if key not in constructor_race_data:
                    constructor_race_data[key] = {
                        'rows': [],
                        'constructor_value': row['Constructor Value'],
                        'race_total': row['Race Total'],
                        'season_total': row['Season Total']
                    }
                
                constructor_race_data[key]['rows'].append(row)
            
            self.stdout.write(f"Processing {len(constructor_race_data)} constructor-race combinations...")
            
            # Process each constructor-race combination
            for (constructor_name, race_name), data in constructor_race_data.items():
                # Get or create team
                team, _ = Team.objects.get_or_create(
                    name=constructor_name,
                    defaults={'short_name': constructor_name[:3].upper()}
                )
                
                # Get or create race
                if race_name not in race_order:
                    race_order[race_name] = current_round
                    current_round += 1
                
                race, created = Race.objects.get_or_create(
                    season=season,
                    name=race_name,
                    defaults={'round_number': race_order[race_name]}
                )
                
                if created:
                    races_created += 1
                
                # Parse constructor value
                price_str = data['constructor_value'].replace('$', '').replace('M', '')
                fantasy_price = Decimal(price_str)
                
                # Parse totals
                race_total = int(data['race_total']) if data['race_total'] else 0
                season_total = int(data['season_total']) if data['season_total'] else 0
                
                # Determine which events this constructor participated in
                event_types = set(row['Event Type'] for row in data['rows'])
                
                # Create or update ConstructorRacePerformance
                performance, perf_created = ConstructorRacePerformance.objects.update_or_create(
                    team=team,
                    race=race,
                    defaults={
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
                ConstructorEventScore.objects.filter(performance=performance).delete()
                
                # Create event scores
                for row in data['rows']:
                    points = int(row['Points']) if row['Points'] else 0
                    position = int(row['Position']) if row['Position'] else None
                    frequency = int(row['Frequency']) if row['Frequency'] else None
                    
                    ConstructorEventScore.objects.create(
                        performance=performance,
                        event_type=row['Event Type'],
                        scoring_item=row['Scoring Item'],
                        points=points,
                        position=position,
                        frequency=frequency
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
