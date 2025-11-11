"""
Management command to import session weather data from FastF1.

This command fetches weather data for sessions and imports:
- Air temperature
- Track temperature
- Humidity
- Wind speed/direction
- Rainfall
- Atmospheric pressure

Usage:
    python manage.py import_weather --year 2024
    python manage.py import_weather --year 2025 --force
    python manage.py import_weather --year 2025 --event 1
"""

import fastf1
from django.core.management.base import BaseCommand
from django.utils import timezone
from analytics.models import Season, Race, Session, SessionWeather


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
            '--event',
            type=int,
            help='Import only a specific event/round number (useful for testing)'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force re-import of weather data, even if it already exists'
        )

    def handle(self, *args, **options):
        year = options['year']
        specific_event = options.get('event')
        force = options.get('force', False)
        
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS(f'Weather Data Import - {year} Season'))
        if force:
            self.stdout.write(self.style.WARNING('FORCE MODE: Re-importing all weather data'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
        
        try:
            # Get season
            try:
                season = Season.objects.get(year=year)
            except Season.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'Season {year} not found. Run import_schedule first.'))
                return
            
            # Get races
            races = Race.objects.filter(season=season)
            if specific_event:
                races = races.filter(round_number=specific_event)
                if not races.exists():
                    self.stdout.write(self.style.ERROR(f'Event {specific_event} not found'))
                    return
                self.stdout.write(self.style.NOTICE(f'Importing weather for Round {specific_event} only\n'))
            
            # Track statistics
            stats = {
                'weather_created': 0,
                'weather_updated': 0,
                'weather_skipped': 0,
                'sessions_without_data': 0,
            }
            
            # Process each race
            for race in races:
                self.stdout.write(f'\nProcessing Round {race.round_number}: {race.name}...')
                
                # Get sessions for this race
                sessions = race.sessions.all()
                
                for session in sessions:
                    session_stats = self.import_weather_for_session(
                        year, race.round_number, session, force
                    )
                    
                    # Update statistics
                    for key, value in session_stats.items():
                        if key in stats:
                            stats[key] += value
            
            # Display summary
            self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
            self.stdout.write(self.style.SUCCESS('Weather Import Complete!'))
            self.stdout.write(self.style.SUCCESS(f'{"="*80}'))
            self.stdout.write(f'\nSummary:')
            self.stdout.write(f'  Weather records created:        {stats["weather_created"]}')
            self.stdout.write(f'  Weather records updated:        {stats["weather_updated"]}')
            self.stdout.write(f'  Weather records skipped:        {stats["weather_skipped"]}')
            self.stdout.write(f'  Sessions without weather data:  {stats["sessions_without_data"]}')
            self.stdout.write('')
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'\nError during import: {e}')
            )
            import traceback
            traceback.print_exc()
            raise
    
    def import_weather_for_session(self, year, round_number, session, force=False):
        """Import weather data for a single session."""
        stats = {
            'weather_created': 0,
            'weather_updated': 0,
            'weather_skipped': 0,
            'sessions_without_data': 0,
        }
        
        # Check if weather already exists
        if not force and hasattr(session, 'weather'):
            stats['weather_skipped'] += 1
            self.stdout.write(f'  ⊙ Skipped {session.session_type} (already has weather)')
            return stats
        
        try:
            self.stdout.write(f'  → Importing weather for {session.session_type}...')
            
            # Get FastF1 session identifier
            # FastF1 uses: 'FP1', 'FP2', 'FP3', 'Q', 'S', 'SQ', 'R'
            fastf1_identifier = self._get_fastf1_session_identifier(session.session_type)
            
            # Load FastF1 session
            f1_session = fastf1.get_session(year, round_number, fastf1_identifier)
            f1_session.load()
            
            # Get weather data from FastF1
            weather_data = self._extract_weather_data(f1_session)
            
            if not weather_data:
                stats['sessions_without_data'] += 1
                self.stdout.write(f'    ⚠ No weather data available for {session.session_type}')
                return stats
            
            # Create or update SessionWeather
            weather, created = SessionWeather.objects.update_or_create(
                session=session,
                defaults={
                    'air_temperature': weather_data.get('air_temperature'),
                    'track_temperature': weather_data.get('track_temperature'),
                    'humidity': weather_data.get('humidity'),
                    'pressure': weather_data.get('pressure'),
                    'wind_speed': weather_data.get('wind_speed'),
                    'wind_direction': weather_data.get('wind_direction'),
                    'rainfall': weather_data.get('rainfall', False),
                    'data_source': 'fastf1',
                }
            )
            
            if created:
                stats['weather_created'] += 1
                self.stdout.write(f'    ✓ Created weather: {weather.weather_summary}')
            else:
                stats['weather_updated'] += 1
                self.stdout.write(f'    ✓ Updated weather: {weather.weather_summary}')
            
        except Exception as e:
            stats['sessions_without_data'] += 1
            self.stdout.write(self.style.WARNING(
                f'    ⚠ Could not load weather for {session.session_type}: {e}'
            ))
        
        return stats
    
    def _get_fastf1_session_identifier(self, session_type):
        """Convert our session type to FastF1 session identifier."""
        mapping = {
            'Practice 1': 'FP1',
            'Practice 2': 'FP2',
            'Practice 3': 'FP3',
            'Qualifying': 'Q',
            'Sprint Qualifying': 'SQ',
            'Sprint': 'S',
            'Race': 'R',
        }
        return mapping.get(session_type, 'R')
    
    def _extract_weather_data(self, f1_session):
        """
        Extract weather data from FastF1 session.
        
        FastF1 provides weather data through:
        - session.weather_data (DataFrame with weather over time)
        
        We extract average/representative values for the session.
        """
        try:
            # Get weather data DataFrame
            weather_df = f1_session.weather_data
            
            if weather_df is None or weather_df.empty:
                return None
            
            # Calculate average/representative values
            # Use median for more robust averages (less affected by outliers)
            weather_data = {}
            
            # Air temperature (°C)
            if 'AirTemp' in weather_df.columns:
                weather_data['air_temperature'] = float(weather_df['AirTemp'].median())
            
            # Track temperature (°C)
            if 'TrackTemp' in weather_df.columns:
                weather_data['track_temperature'] = float(weather_df['TrackTemp'].median())
            
            # Humidity (%)
            if 'Humidity' in weather_df.columns:
                weather_data['humidity'] = float(weather_df['Humidity'].median())
            
            # Pressure (mbar)
            if 'Pressure' in weather_df.columns:
                weather_data['pressure'] = float(weather_df['Pressure'].median())
            
            # Wind speed (m/s)
            if 'WindSpeed' in weather_df.columns:
                weather_data['wind_speed'] = float(weather_df['WindSpeed'].median())
            
            # Wind direction (degrees)
            if 'WindDirection' in weather_df.columns:
                # For wind direction, use circular mean
                weather_data['wind_direction'] = int(weather_df['WindDirection'].median())
            
            # Rainfall (boolean - true if rain occurred at any point)
            if 'Rainfall' in weather_df.columns:
                weather_data['rainfall'] = bool(weather_df['Rainfall'].any())
            
            return weather_data if weather_data else None
            
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'Error extracting weather: {e}'))
            return None
