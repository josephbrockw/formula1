"""
Management command to collect F1 data across all seasons.

Runs the multi-season import pipeline (2018–current by default), filling
gaps in weather, circuit, driver, and telemetry data for every session
in the specified year range.

Data is processed in descending year order (newest first), most-recent
event first within each year. Re-running is safe — gap detection skips
sessions already in the database.

Usage:
    # Full backfill (dry run first to see scope)
    python manage.py collect_all_data --dry-run

    # Collect everything from 2018 to current year
    python manage.py collect_all_data --notify

    # Only recent seasons
    python manage.py collect_all_data --start-year 2024

    # Specific range
    python manage.py collect_all_data --start-year 2022 --end-year 2023

    # Force re-import even if data exists
    python manage.py collect_all_data --start-year 2025 --force
"""

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Collect FastF1 data across all seasons (default: 2018–current year)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--start-year',
            type=int,
            default=2018,
            help='First year to backfill (default: 2018)',
        )
        parser.add_argument(
            '--end-year',
            type=int,
            default=None,
            help='Last year to collect (default: current year)',
        )
        parser.add_argument(
            '--notify',
            action='store_true',
            help='Send Slack notifications on completion',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Re-import data that already exists in the database',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show gap summary and exit without importing any data',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show full Prefect task logs (default: progress summary only)',
        )

    def handle(self, *args, **options):
        start_year = options['start_year']
        end_year = options['end_year'] or timezone.now().year
        notify = options['notify']
        force = options['force']
        dry_run = options['dry_run']

        self.stdout.write(self.style.SUCCESS(f'\n{"=" * 80}'))
        self.stdout.write(self.style.SUCCESS('F1 Multi-Season Data Collection'))
        self.stdout.write(self.style.SUCCESS(f'Seasons: {start_year}–{end_year}'))
        if force:
            self.stdout.write(self.style.WARNING('FORCE MODE: Re-importing all data'))
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN: No data will be imported'))
        if notify:
            self.stdout.write(self.style.SUCCESS('Notifications: Enabled'))
        self.stdout.write(self.style.SUCCESS(f'{"=" * 80}\n'))

        if dry_run:
            self._handle_dry_run(start_year, end_year, force)
            return

        try:
            import logging
            if not options.get('verbose'):
                logging.getLogger('prefect').setLevel(logging.WARNING)

            from analytics.flows.import_all_seasons import import_all_seasons_flow

            summary = import_all_seasons_flow(
                start_year=start_year,
                end_year=end_year,
                force=force,
                notify=notify,
            )

            self.stdout.write(self.style.SUCCESS(f'\n{"=" * 80}'))
            self.stdout.write(self.style.SUCCESS('Multi-Season Import Complete!'))
            self.stdout.write(self.style.SUCCESS(f'{"=" * 80}'))
            self.stdout.write('\nSummary:')
            self.stdout.write(f'  Seasons covered:      {start_year}–{end_year}')
            self.stdout.write(f'  Sessions to process:  {summary["gaps_detected"]}')
            self.stdout.write(f'  Sessions processed:   {summary["sessions_processed"]}')
            self.stdout.write(f'  Succeeded:            {summary["sessions_succeeded"]}')
            self.stdout.write(f'  Failed:               {summary["sessions_failed"]}')
            self.stdout.write('')
            self.stdout.write('Data Extracted:')
            extracted = summary.get('data_extracted', {})
            self.stdout.write(f'  Weather:              {extracted.get("weather", 0)}')
            self.stdout.write(f'  Circuit:              {extracted.get("circuit", 0)}')
            self.stdout.write(f'  Telemetry:            {extracted.get("telemetry", 0)}')
            self.stdout.write('')

            by_year = summary.get('by_year', {})
            if by_year:
                self.stdout.write('Per-Year Results:')
                for year in sorted(by_year.keys(), reverse=True):
                    info = by_year[year]
                    self.stdout.write(
                        f'  {year}:  '
                        f'{info["detected"]:>3} detected  '
                        f'{info["succeeded"]:>3} succeeded  '
                        f'{info["failed"]:>3} failed'
                    )
                self.stdout.write('')

            self.stdout.write(f'  Duration:             {summary.get("duration_seconds", 0):.1f}s')
            self.stdout.write('')

            if summary['status'] == 'complete':
                self.stdout.write(self.style.SUCCESS('✅ Status: COMPLETE'))
            elif summary['status'] == 'failed':
                self.stdout.write(self.style.ERROR('❌ Status: FAILED'))
            elif summary['status'] == 'rate_limited':
                self.stdout.write(self.style.WARNING(
                    '⚠️  Status: RATE LIMITED — API quota exhausted. '
                    'Re-run collect_all_data to resume from where it left off.'
                ))
            else:
                self.stdout.write(
                    self.style.WARNING(f'⚠️  Status: {summary["status"].upper()}')
                )

        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('\n\n⚠️  Import interrupted by user'))
            self.stdout.write(
                'Re-run collect_all_data to resume — '
                'gap detection will skip already-imported sessions.'
            )
            raise

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ Error during import: {e}'))
            import traceback
            traceback.print_exc()
            raise

    def _handle_dry_run(self, start_year, end_year, force):
        """Show per-year gap counts and exit without importing."""
        from analytics.processing.session_processor import get_sessions_to_process

        self.stdout.write(f'Scanning for gaps across {start_year}–{end_year}...\n')

        year_counts = {}
        total = 0

        for year in range(end_year, start_year - 1, -1):
            gaps = get_sessions_to_process(year=year, force=force)
            year_counts[year] = len(gaps)
            total += len(gaps)

        self.stdout.write(f'{"Year":<8} {"Sessions"}')
        self.stdout.write('─' * 20)
        for year in sorted(year_counts.keys(), reverse=True):
            self.stdout.write(f'{year:<8} {year_counts[year]}')
        self.stdout.write('─' * 20)
        self.stdout.write(f'{"Total":<8} {total}  (~{total} API calls)')
        self.stdout.write('')
        self.stdout.write(
            f'Processing order: {end_year} → {start_year}, '
            f'most-recent event first within each year.'
        )
        self.stdout.write(self.style.WARNING('\nDRY RUN — no data imported.'))
