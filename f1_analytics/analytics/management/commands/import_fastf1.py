"""
Management command to import FastF1 data (weather, circuit, etc.).

This is the master import command that orchestrates all FastF1 data imports
using the session-once-extract-many pattern for efficiency.

Features:
- Gap detection: Only imports missing data
- Rate limiting: Automatic pause/retry when API limit hit
- Force mode: Re-import data even if it exists
- Round filtering: Import specific rounds or full season
- Slack notifications: Optional completion notifications

Usage:
    # Import full season (only missing data)
    python manage.py import_fastf1 --year 2025
    
    # Import specific round
    python manage.py import_fastf1 --year 2025 --round 1
    
    # Force re-import all data
    python manage.py import_fastf1 --year 2025 --force
    
    # With Slack notifications
    python manage.py import_fastf1 --year 2025 --notify
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from analytics.flows.import_fastf1 import import_fastf1_flow


class Command(BaseCommand):
    help = 'Import FastF1 data (weather, circuit) for sessions'

    def add_arguments(self, parser):
        parser.add_argument(
            '--year',
            type=int,
            default=timezone.now().year,
            help='Season year (default: current year)'
        )
        parser.add_argument(
            '--round',
            type=int,
            help='Specific round number to import (default: all rounds)'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force re-import of data, even if it already exists'
        )
        parser.add_argument(
            '--notify',
            action='store_true',
            help='Send Slack notification on completion'
        )

    def handle(self, *args, **options):
        year = options['year']
        round_number = options.get('round')
        force = options.get('force', False)
        notify = options.get('notify', False)
        
        # Header
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS(f'FastF1 Data Import - {year} Season'))
        if round_number:
            self.stdout.write(self.style.SUCCESS(f'Target: Round {round_number}'))
        else:
            self.stdout.write(self.style.SUCCESS('Target: Full Season'))
        if force:
            self.stdout.write(self.style.WARNING('FORCE MODE: Re-importing all data'))
        if notify:
            self.stdout.write(self.style.SUCCESS('Notifications: Enabled'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
        
        try:
            # Call Prefect flow
            # The flow handles:
            # - Gap detection (what's missing)
            # - Rate limiting (automatic pause/retry)
            # - Session loading (cached, efficient)
            # - Data extraction (weather, circuit, future: laps, telemetry)
            summary = import_fastf1_flow(
                year=year,
                round_number=round_number,
                force=force,
                notify=notify
            )
            
            # Display summary
            self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
            self.stdout.write(self.style.SUCCESS('FastF1 Import Complete!'))
            self.stdout.write(self.style.SUCCESS(f'{"="*80}'))
            self.stdout.write(f'\nSummary:')
            self.stdout.write(f'  Sessions to process:  {summary["gaps_detected"]}')
            self.stdout.write(f'  Sessions processed:   {summary["sessions_processed"]}')
            self.stdout.write(f'  Succeeded:            {summary["sessions_succeeded"]}')
            self.stdout.write(f'  Failed:               {summary["sessions_failed"]}')
            self.stdout.write('')
            self.stdout.write(f'Data Extracted:')
            self.stdout.write(f'  Weather:              {summary["data_extracted"]["weather"]}')
            self.stdout.write(f'  Circuit:              {summary["data_extracted"]["circuit"]}')
            self.stdout.write('')
            self.stdout.write(f'  Duration:             {summary.get("duration_seconds", 0):.1f}s')
            self.stdout.write('')
            
            # Status indicator
            if summary['status'] == 'complete':
                self.stdout.write(self.style.SUCCESS('✅ Status: COMPLETE'))
            elif summary['status'] == 'failed':
                self.stdout.write(self.style.ERROR('❌ Status: FAILED'))
            else:
                self.stdout.write(self.style.WARNING(f'⚠️  Status: {summary["status"].upper()}'))
            
        except KeyboardInterrupt:
            self.stdout.write(
                self.style.WARNING('\n\n⚠️  Import interrupted by user')
            )
            self.stdout.write('Note: Prefect may have cached partial progress')
            raise
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'\n❌ Error during import: {e}')
            )
            import traceback
            traceback.print_exc()
            raise