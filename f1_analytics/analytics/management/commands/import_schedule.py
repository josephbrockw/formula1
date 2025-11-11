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
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force re-import of all data, even if it already exists in the database'
        )

    def handle(self, *args, **options):
        year = options['year']
        with_circuits = options.get('with_circuits', False)
        specific_event = options.get('event')
        force = options.get('force', False)
        
        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS(f'F1 Schedule Import - {year} Season'))
        if force:
            self.stdout.write(self.style.WARNING('FORCE MODE: Re-importing all data'))
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
                'races_skipped': 0,
                'sessions_created': 0,
                'sessions_skipped': 0,
                'circuits_created': 0,
                'circuits_updated': 0,
                'circuits_skipped': 0,
            }
            
            # Import each event
            for idx, event_row in schedule.iterrows():
                self.stdout.write(f'\nProcessing Round {event_row["RoundNumber"]}: {event_row["EventName"]}...')
                
                race_stats = self.import_race(season, event_row, with_circuits, force)
                
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
            self.stdout.write(f'  Races skipped:    {stats["races_skipped"]}')
            self.stdout.write(f'  Sessions created: {stats["sessions_created"]}')
            self.stdout.write(f'  Sessions skipped: {stats["sessions_skipped"]}')
            if with_circuits:
                self.stdout.write(f'  Circuits created: {stats["circuits_created"]}')
                self.stdout.write(f'  Circuits updated: {stats["circuits_updated"]}')
                self.stdout.write(f'  Circuits skipped: {stats["circuits_skipped"]}')
            self.stdout.write('')
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'\nError during import: {e}')
            )
            import traceback
            traceback.print_exc()
            raise

    def _circuit_has_complete_geometry(self, circuit):
        """
        Check if circuit has complete geometry data (corners, lights, and sectors).
        Returns True only if ALL three types exist.
        """
        has_corners = circuit.corners.exists()
        has_lights = circuit.marshal_lights.exists()
        has_sectors = circuit.marshal_sectors.exists()
        
        return has_corners and has_lights and has_sectors
    
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
    
    def import_race(self, season, event_row, with_circuits, force=False):
        """Import a single race event with all sessions."""
        stats = {
            'races_created': 0,
            'races_updated': 0,
            'races_skipped': 0,
            'sessions_created': 0,
            'sessions_skipped': 0,
            'circuits_created': 0,
            'circuits_updated': 0,
            'circuits_skipped': 0,
        }
        
        # Get or create race
        race, created = Race.objects.get_or_create(
            season=season,
            round_number=int(event_row['RoundNumber']),
            defaults={
                'name': event_row['EventName'],
            }
        )
        
        # Check if we should skip this race
        if not force and not created:
            # Count expected sessions from the schedule
            expected_sessions = sum([
                1 for i in range(1, 6) 
                if event_row.get(f'Session{i}') and not pd.isna(event_row.get(f'Session{i}'))
            ])
            
            # Check if sessions already exist AND we have the expected count
            existing_sessions = race.sessions.count()
            if existing_sessions > 0 and existing_sessions == expected_sessions:
                stats['races_skipped'] += 1
                stats['sessions_skipped'] += existing_sessions
                self.stdout.write(f'  ⊙ Skipped race (already has {existing_sessions} sessions): {race.name}')
                
                # Check circuit data if requested
                if with_circuits:
                    # Check if circuit exists and has ALL geometry data
                    if race.circuit and self._circuit_has_complete_geometry(race.circuit):
                        stats['circuits_skipped'] += 1
                        self.stdout.write(f'    ⊙ Skipped circuit (already has geometry data)')
                    else:
                        # Import circuit data if missing or circuit was deleted
                        if not race.circuit:
                            self.stdout.write(f'    ⚠ Circuit missing, importing...')
                        else:
                            self.stdout.write(f'    ⚠ Circuit exists but missing/incomplete geometry data, importing...')
                        circuit_stats = self.import_circuit_for_race(season.year, race, event_row, force)
                        for key, value in circuit_stats.items():
                            stats[key] += value
                
                return stats
            elif existing_sessions > 0 and existing_sessions != expected_sessions:
                # Partial session data - need to re-import
                self.stdout.write(f'  ⚠ Race has {existing_sessions} sessions but expected {expected_sessions}, re-importing...')
        
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
        session_count = self.import_sessions(race, event_row, force)
        stats['sessions_created'] += session_count
        self.stdout.write(f'    → Created {session_count} sessions')
        
        # Import circuit data if requested
        if with_circuits:
            circuit_stats = self.import_circuit_for_race(season.year, race, event_row, force)
            for key, value in circuit_stats.items():
                stats[key] += value
        
        return stats
    
    def import_sessions(self, race, event_row, force=False):
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
    
    def import_circuit_for_race(self, year, race, event_row, force=False):
        """Import circuit data by loading a FastF1 session."""
        stats = {'circuits_created': 0, 'circuits_updated': 0, 'circuits_skipped': 0}
        
        try:
            # Get or create circuit first (without loading session data)
            circuit_name = event_row.get('Location', '') + ' Circuit'
            circuit, created = Circuit.objects.get_or_create(name=circuit_name)
            
            # Link circuit to race if not already linked
            if not race.circuit:
                race.circuit = circuit
                race.save()
            
            # Check if circuit already has complete geometry data
            if not force and self._circuit_has_complete_geometry(circuit):
                stats['circuits_skipped'] += 1
                corner_count = circuit.corners.count()
                self.stdout.write(f'    ⊙ Skipped circuit (already has complete geometry: {corner_count} corners, etc.)')
                return stats
            
            # Only load FastF1 session data if we need it
            self.stdout.write(f'    Loading circuit data from FastF1...')
            
            # Load race session to get circuit info (THIS IS THE EXPENSIVE PART)
            f1_session = fastf1.get_session(year, int(race.round_number), 'R')
            f1_session.load()
            
            circuit_info = f1_session.get_circuit_info()
            
            # Update circuit with rotation data
            if hasattr(circuit_info, 'rotation'):
                circuit.rotation = circuit_info.rotation
                circuit.save()
            
            if created:
                stats['circuits_created'] += 1
            else:
                stats['circuits_updated'] += 1
            
            # Import corners
            if hasattr(circuit_info, 'corners') and circuit_info.corners is not None:
                self.import_corners(circuit, circuit_info.corners)
                self.stdout.write(f'      ✓ Imported {circuit_info.corners.shape[0]} corners')
            
            # Import marshal lights
            if hasattr(circuit_info, 'marshal_lights') and circuit_info.marshal_lights is not None:
                self.import_marshal_lights(circuit, circuit_info.marshal_lights)
                self.stdout.write(f'      ✓ Imported {circuit_info.marshal_lights.shape[0]} marshal lights')
            
            # Import marshal sectors
            if hasattr(circuit_info, 'marshal_sectors') and circuit_info.marshal_sectors is not None:
                self.import_marshal_sectors(circuit, circuit_info.marshal_sectors)
                self.stdout.write(f'      ✓ Imported {circuit_info.marshal_sectors.shape[0]} marshal sectors')
            
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
