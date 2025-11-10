"""
Management command to import constructor performance data from CSV files

Usage:
    python manage.py import_constructor_performance
    
The command looks for the most recent *-all-constructors-performance.csv file 
in the data/{year}/outcomes directory.
"""

import csv
from datetime import date
from django.core.management.base import BaseCommand
from django.db import transaction
from analytics.models import ConstructorRacePerformance, ConstructorEventScore
from ._performance_import_utils import (
    get_season, resolve_csv_file, get_or_create_race,
    parse_fantasy_price, parse_event_score_fields,
    extract_event_types, get_or_create_team, parse_totals
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
            '*-all-constructors-performance.csv'
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
                team, _ = get_or_create_team(constructor_name)
                
                # Get or create race
                race, created = get_or_create_race(season, race_name, race_order)
                if created:
                    races_created += 1
                
                # Parse constructor value
                fantasy_price = parse_fantasy_price(data['constructor_value'])
                
                # Parse totals
                race_total, season_total = parse_totals(
                    data['race_total'],
                    data['season_total']
                )
                
                # Determine which events this constructor participated in
                event_types = extract_event_types(data['rows'])
                
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
                    score_fields = parse_event_score_fields(row)
                    
                    ConstructorEventScore.objects.create(
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
