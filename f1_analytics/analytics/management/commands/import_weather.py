"""
Management command to import session weather data from FastF1.

This command is a thin CLI wrapper around the Prefect import_weather_flow.
The flow handles all orchestration, caching, rate limiting, and error handling.

Usage:
    python manage.py import_weather --year 2025
    python manage.py import_weather --year 2025 --force
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from analytics.flows.import_weather import import_weather_flow


class Command(BaseCommand):
    help = 'Import weather data for sessions from FastF1'

    def add_arguments(self, parser):
        parser.add_argument(
            '--year',
            type=int,
            default=timezone.now().year,
            help='Season year (default: current year)'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force re-import of weather data, even if it already exists'
        )

    def handle(self, *args, **options):
        year = options['year']
        force = options.get('force', False)
        
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS(f'Weather Data Import - {year} Season'))
        if force:
            self.stdout.write(self.style.WARNING('FORCE MODE: Re-importing all weather data'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
        
        try:
            # Call Prefect flow
            # Prefect handles all orchestration, caching, rate limiting, and error handling
            summary = import_weather_flow(year=year, force=force)
            
            # Display summary
            self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
            self.stdout.write(self.style.SUCCESS('Weather Import Complete!'))
            self.stdout.write(self.style.SUCCESS(f'{"="*80}'))
            self.stdout.write(f'\nSummary:')
            self.stdout.write(f'  Sessions found:      {summary["sessions_found"]}')
            self.stdout.write(f'  Sessions processed:  {summary["sessions_processed"]}')
            self.stdout.write(f'  Success:             {summary["success"]}')
            self.stdout.write(f'  Failed:              {summary["failed"]}')
            self.stdout.write(f'  No data:             {summary["no_data"]}')
            self.stdout.write('')
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'\nError during import: {e}')
            )
            import traceback
            traceback.print_exc()
            raise
