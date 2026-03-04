"""
data_status management command.

Read-only command showing F1 data coverage across years without triggering imports.

Usage:
    python manage.py data_status              # Multi-year summary table
    python manage.py data_status --year 2025  # Detailed per-session breakdown
"""

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from analytics.models import Season, Session, SessionWeather, SessionResult, Lap, PitStop, Corner


class Command(BaseCommand):
    help = 'Show data coverage status without triggering any imports.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--year',
            type=int,
            default=None,
            help='Show detailed per-session breakdown for a specific year.',
        )

    def handle(self, *args, **options):
        year = options['year']
        now = timezone.now()

        if year is not None:
            self._show_year_detail(year, now)
        else:
            self._show_summary(now)

    def _past_sessions_qs(self, season, now):
        """Return queryset of sessions that have already occurred."""
        return Session.objects.filter(
            race__season=season,
        ).filter(
            Q(session_date_utc__lte=now) |
            Q(session_date_utc__isnull=True, race__race_date__lt=now.date())
        ).select_related('race', 'race__circuit')

    def _show_summary(self, now):
        seasons = Season.objects.order_by('-year')
        if not seasons.exists():
            self.stdout.write('No seasons found in database.')
            return

        today = now.date().isoformat()
        self.stdout.write(f'Data Collection Status (as of {today})')
        self.stdout.write('=' * 70)
        self.stdout.write(
            f'{"Year":<6}  {"Sessions":<9}  {"Past":<6}  {"Complete":<14}'
            f'  {"Weather":<8}  {"Drivers":<8}  {"Telemetry":<10}  {"Pit Stops"}'
        )
        self.stdout.write('-' * 70)

        for season in seasons:
            all_sessions = Session.objects.filter(race__season=season)
            total = all_sessions.count()

            past_qs = self._past_sessions_qs(season, now)
            past = past_qs.count()

            if past == 0:
                self.stdout.write(
                    f'{season.year:<6}  {total:<9}  {past:<6}  {"—":<14}'
                    f'  {"—":<8}  {"—":<8}  {"—":<10}  {"—"}'
                )
                continue

            past_ids = list(past_qs.values_list('id', flat=True))

            weather = SessionWeather.objects.filter(session_id__in=past_ids).values('session_id').distinct().count()
            drivers = SessionResult.objects.filter(session_id__in=past_ids).values('session_id').distinct().count()
            telemetry = Lap.objects.filter(session_id__in=past_ids).values('session_id').distinct().count()
            pit_stops = PitStop.objects.filter(session_id__in=past_ids).values('session_id').distinct().count()

            # A session is "complete" if it has all four data types
            complete = 0
            for sid in past_ids:
                has_w = SessionWeather.objects.filter(session_id=sid).exists()
                has_d = SessionResult.objects.filter(session_id=sid).exists()
                has_t = Lap.objects.filter(session_id=sid).exists()
                has_p = PitStop.objects.filter(session_id=sid).exists()
                if has_w and has_d and has_t and has_p:
                    complete += 1

            pct = int(complete / past * 100) if past else 0
            complete_str = f'{complete} ({pct}%)'

            self.stdout.write(
                f'{season.year:<6}  {total:<9}  {past:<6}  {complete_str:<14}'
                f'  {weather:<8}  {drivers:<8}  {telemetry:<10}  {pit_stops}'
            )

        self.stdout.write('')
        self.stdout.write('Use --year YYYY for a detailed breakdown by event.')

    def _show_year_detail(self, year, now):
        try:
            season = Season.objects.get(year=year)
        except Season.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Season {year} not found in database.'))
            return

        past_qs = self._past_sessions_qs(season, now).order_by('race__round_number', 'session_number')

        self.stdout.write(f'{year} Season — Data Status')
        self.stdout.write('=' * 80)
        self.stdout.write(
            f'{"Rnd":<4}  {"Event":<18}  {"Session":<16}  {"Date (UTC)":<18}'
            f'  {"Weather":<8}  {"Drivers":<8}  {"Telem":<6}  {"Pits"}'
        )
        self.stdout.write('-' * 80)

        missing_count = 0
        missing_by_type = {'weather': 0, 'drivers': 0, 'telemetry': 0, 'pit_stops': 0}

        for session in past_qs:
            has_w = SessionWeather.objects.filter(session=session).exists()
            has_d = SessionResult.objects.filter(session=session).exists()
            has_t = Lap.objects.filter(session=session).exists()
            has_p = PitStop.objects.filter(session=session).exists()

            any_missing = not (has_w and has_d and has_t and has_p)
            if any_missing:
                missing_count += 1
                if not has_w:
                    missing_by_type['weather'] += 1
                if not has_d:
                    missing_by_type['drivers'] += 1
                if not has_t:
                    missing_by_type['telemetry'] += 1
                if not has_p:
                    missing_by_type['pit_stops'] += 1

            date_str = session.session_date_utc.strftime('%Y-%m-%d %H:%M') if session.session_date_utc else '—'
            event_name = (session.race.circuit.name if session.race.circuit else session.race.name)[:17]
            session_name = session.session_type[:15]
            rnd = session.race.round_number

            w = '✓' if has_w else '✗'
            d = '✓' if has_d else '✗'
            t = '✓' if has_t else '✗'
            p = '✓' if has_p else '✗'

            line = (
                f'{rnd:<4}  {event_name:<18}  {session_name:<16}  {date_str:<18}'
                f'  {w:<8}  {d:<8}  {t:<6}  {p}'
            )
            if any_missing:
                self.stdout.write(self.style.WARNING(line))
            else:
                self.stdout.write(line)

        self.stdout.write('')
        if missing_count == 0:
            self.stdout.write(self.style.SUCCESS('All past sessions have complete data.'))
        else:
            gap_types = ', '.join(
                f'{k}: {v}' for k, v in missing_by_type.items() if v > 0
            )
            self.stdout.write(
                self.style.WARNING(f'Gaps: {missing_count} sessions missing data  ({gap_types})')
            )
            self.stdout.write(
                f'Run: python manage.py collect_all_data --start-year {year} --end-year {year}'
            )
