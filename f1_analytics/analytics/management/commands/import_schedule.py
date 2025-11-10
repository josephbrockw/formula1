"""
Management command to import F1 schedule data from FastF1.

This command fetches the F1 event schedule and imports:
- Season
- Races (with all FastF1 metadata)
- Sessions (Practice, Qualifying, Race)
- Circuits (with geometry data if --with-circuits flag is set)

Usage:
    python manage.py import_schedule --year 2024
    python manage.py import_schedule --year 2025 --with-circuits
"""

import fastf1
import pandas as pd
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from analytics.models import (
    Season, Race, Session, Circuit, 
    Corner, MarshalLight, MarshalSector
)


class Command(BaseCommand):
    help = 'Import F1 schedule data from FastF1 into the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--year',
            type=int,
            default=timezone.now().year,
            help='Season year (default: current year)'
        )
        parser.add_argument(
            '--with-circuits',
            action='store_true',
            help='Import full circuit geometry data (corners, marshal lights/sectors). Warning: This downloads session data (~50-100MB per race).'
        )
        parser.add_argument(
            '--event',
            type=int,
            help='Import only a specific event/round number (useful for testing)'
        )

    def handle(self, *args, **options):
        year = options['year']
        with_circuits = options.get('with_circuits', False)
        specific_event = options.get('event')
        
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS(f'F1 Schedule Import - {year} Season'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))
        
        try:
            # Fetch the event schedule
            self.stdout.write('Fetching event schedule from FastF1...\n')
            schedule = fastf1.get_event_schedule(year)
            
            self.stdout.write(self.style.SUCCESS(f'✓ Found {len(schedule)} events\n'))
            
            # Filter to specific event if requested
            if specific_event:
                schedule = schedule[schedule['RoundNumber'] == specific_event]
                if len(schedule) == 0:
                    self.stdout.write(self.style.ERROR(f'Event {specific_event} not found'))
                    return
                self.stdout.write(self.style.NOTICE(f'Importing only Round {specific_event}\n'))
            
            # Import season
            season = self.import_season(year)
            
            # Track statistics
            stats = {
                'races_created': 0,
                'races_updated': 0,
                'sessions_created': 0,
                'circuits_created': 0,
                'circuits_updated': 0,
            }
            
            # Import each event
            for idx, event_row in schedule.iterrows():
                self.stdout.write(f'\nProcessing Round {event_row["RoundNumber"]}: {event_row["EventName"]}...')
                
                race_stats = self.import_race(season, event_row, with_circuits)
                
                # Update statistics
                for key, value in race_stats.items():
                    if key in stats:
                        stats[key] += value
            
            # Display summary
            self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
            self.stdout.write(self.style.SUCCESS('Import Complete!'))
            self.stdout.write(self.style.SUCCESS(f'{"="*80}'))
            self.stdout.write(f'\nSummary:')
            self.stdout.write(f'  Races created:    {stats["races_created"]}')
            self.stdout.write(f'  Races updated:    {stats["races_updated"]}')
            self.stdout.write(f'  Sessions created: {stats["sessions_created"]}')
            if with_circuits:
                self.stdout.write(f'  Circuits created: {stats["circuits_created"]}')
                self.stdout.write(f'  Circuits updated: {stats["circuits_updated"]}')
            self.stdout.write('')
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'\nError during import: {e}')
            )
            import traceback
            traceback.print_exc()
            raise

    def import_season(self, year):
        """Get or create Season for the given year."""
        season, created = Season.objects.get_or_create(
            year=year,
            defaults={
                'name': f'{year} Formula 1 Season',
                'is_active': (year == timezone.now().year)
            }
        )
        
        if created:
            self.stdout.write(self.style.SUCCESS(f'✓ Created season: {season}'))
        else:
            self.stdout.write(f'  Using existing season: {season}')
        
        return season
    
    def import_race(self, season, event_row, with_circuits):
        """Import a single race event with all sessions."""
        stats = {
            'races_created': 0,
            'races_updated': 0,
            'sessions_created': 0,
            'circuits_created': 0,
            'circuits_updated': 0,
        }
        
        # Get or create race
        race, created = Race.objects.get_or_create(
            season=season,
            round_number=int(event_row['RoundNumber']),
            defaults={
                'name': event_row['EventName'],
            }
        )
        
        # Update race fields
        race.name = event_row['EventName']
        race.country = event_row.get('Country', '')
        race.location = event_row.get('Location', '')
        race.official_event_name = event_row.get('OfficialEventName', '')
        race.event_format = event_row.get('EventFormat', 'conventional')
        race.f1_api_support = event_row.get('F1ApiSupport', True)
        
        # Handle dates
        if event_row.get('EventDate') and not pd.isna(event_row['EventDate']):
            race.event_date = event_row['EventDate'].date()
            race.race_date = event_row['EventDate'].date()
        
        race.save()
        
        if created:
            stats['races_created'] += 1
            self.stdout.write(f'  ✓ Created race: {race.name}')
        else:
            stats['races_updated'] += 1
            self.stdout.write(f'  ✓ Updated race: {race.name}')
        
        # Import sessions
        session_count = self.import_sessions(race, event_row)
        stats['sessions_created'] += session_count
        self.stdout.write(f'    → Created {session_count} sessions')
        
        # Import circuit data if requested
        if with_circuits:
            circuit_stats = self.import_circuit_for_race(season.year, race, event_row)
            stats['circuits_created'] += circuit_stats.get('circuits_created', 0)
            stats['circuits_updated'] += circuit_stats.get('circuits_updated', 0)
        
        return stats
    
    def import_sessions(self, race, event_row):
        """Import all sessions for a race."""
        sessions_created = 0
        
        # Process Session1 through Session5
        for session_num in range(1, 6):
            session_type_col = f'Session{session_num}'
            session_date_col = f'Session{session_num}DateUtc'
            
            session_type = event_row.get(session_type_col)
            session_date_utc = event_row.get(session_date_col)
            
            # Skip if session doesn't exist
            if not session_type or pd.isna(session_type):
                continue
            
            # Get or create session
            session, created = Session.objects.get_or_create(
                race=race,
                session_number=session_num,
                defaults={
                    'session_type': session_type,
                }
            )
            
            # Update session fields
            session.session_type = session_type
            
            # Handle session date
            if session_date_utc and not pd.isna(session_date_utc):
                session.session_date_utc = session_date_utc
            
            # Store local time as string
            session_date_local_col = f'Session{session_num}Date'
            if session_date_local_col in event_row:
                local_date = event_row[session_date_local_col]
                if local_date and not pd.isna(local_date):
                    session.session_date_local = str(local_date)
            
            session.save()
            
            if created:
                sessions_created += 1
        
        return sessions_created
    
    def import_circuit_for_race(self, year, race, event_row):
        """Import circuit data by loading a FastF1 session."""
        stats = {'circuits_created': 0, 'circuits_updated': 0}
        
        try:
            self.stdout.write(f'    Loading circuit data...')
            
            # Load race session to get circuit info
            f1_session = fastf1.get_session(year, int(race.round_number), 'R')
            f1_session.load()
            
            circuit_info = f1_session.get_circuit_info()
            
            # Create or update circuit
            circuit_name = event_row.get('Location', '') + ' Circuit'
            circuit, created = Circuit.objects.get_or_create(
                name=circuit_name,
                defaults={'rotation': circuit_info.rotation if hasattr(circuit_info, 'rotation') else None}
            )
            
            if not created and hasattr(circuit_info, 'rotation'):
                circuit.rotation = circuit_info.rotation
                circuit.save()
                stats['circuits_updated'] += 1
            else:
                stats['circuits_created'] += 1
            
            # Link circuit to race
            race.circuit = circuit
            race.save()
            
            # Import corners
            if hasattr(circuit_info, 'corners') and circuit_info.corners is not None:
                self.import_corners(circuit, circuit_info.corners)
            
            # Import marshal lights
            if hasattr(circuit_info, 'marshal_lights') and circuit_info.marshal_lights is not None:
                self.import_marshal_lights(circuit, circuit_info.marshal_lights)
            
            # Import marshal sectors
            if hasattr(circuit_info, 'marshal_sectors') and circuit_info.marshal_sectors is not None:
                self.import_marshal_sectors(circuit, circuit_info.marshal_sectors)
            
            self.stdout.write(f'      ✓ Circuit data imported')
            
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'      ⚠ Could not load circuit data: {e}'))
        
        return stats
    
    def import_corners(self, circuit, corners_df):
        """Import corner data from FastF1 DataFrame."""
        # Delete existing corners for this circuit
        Corner.objects.filter(circuit=circuit).delete()
        
        for idx, row in corners_df.iterrows():
            Corner.objects.create(
                circuit=circuit,
                number=int(row['Number']),
                letter=str(row['Letter']) if row['Letter'] else '',
                x=float(row['X']),
                y=float(row['Y']),
                angle=float(row['Angle']),
                distance=float(row['Distance'])
            )
    
    def import_marshal_lights(self, circuit, lights_df):
        """Import marshal light data from FastF1 DataFrame."""
        # Delete existing lights for this circuit
        MarshalLight.objects.filter(circuit=circuit).delete()
        
        for idx, row in lights_df.iterrows():
            MarshalLight.objects.create(
                circuit=circuit,
                number=int(row['Number']),
                letter=str(row['Letter']) if row['Letter'] else '',
                x=float(row['X']),
                y=float(row['Y']),
                angle=float(row['Angle']),
                distance=float(row['Distance'])
            )
    
    def import_marshal_sectors(self, circuit, sectors_df):
        """Import marshal sector data from FastF1 DataFrame."""
        # Delete existing sectors for this circuit
        MarshalSector.objects.filter(circuit=circuit).delete()
        
        for idx, row in sectors_df.iterrows():
            MarshalSector.objects.create(
                circuit=circuit,
                number=int(row['Number']),
                letter=str(row['Letter']) if row['Letter'] else '',
                x=float(row['X']),
                y=float(row['Y']),
                angle=float(row['Angle']),
                distance=float(row['Distance'])
            )
