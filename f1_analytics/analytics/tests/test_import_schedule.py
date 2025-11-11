"""
Unit tests for import_schedule management command.

Tests cover:
- Basic schedule import
- Session creation
- Circuit geometry import
- Skip logic for existing data
- Force flag behavior
- Partial deletion detection
- Error handling

All FastF1 API calls are mocked to avoid network requests during testing.
"""

from unittest import mock
from io import StringIO
from datetime import datetime, date
import pandas as pd
import numpy as np
from django.test import TestCase
from django.core.management import call_command
from django.utils import timezone
from analytics.models import (
    Season, Race, Session, Circuit,
    Corner, MarshalLight, MarshalSector
)


class MockCircuitInfo:
    """Mock FastF1 CircuitInfo object"""
    def __init__(self):
        self.rotation = 44.0
        # Create mock DataFrames for corners, lights, and sectors
        self.corners = pd.DataFrame({
            'Number': [1, 2, 3],
            'Letter': ['', '', ''],
            'X': [-3650.59, -3555.05, -7194.97],
            'Y': [1193.78, 1992.75, 7101.94],
            'Angle': [-176.63, -1.52, 162.26],
            'Distance': [350.69, 445.63, 1082.85]
        })
        self.marshal_lights = pd.DataFrame({
            'Number': [1, 2],
            'Letter': ['', ''],
            'X': [-3650.59, -3555.05],
            'Y': [1193.78, 1992.75],
            'Angle': [-176.63, -1.52],
            'Distance': [350.69, 445.63]
        })
        self.marshal_sectors = pd.DataFrame({
            'Number': [1, 2],
            'Letter': ['', ''],
            'X': [-3650.59, -3555.05],
            'Y': [1193.78, 1992.75],
            'Angle': [-176.63, -1.52],
            'Distance': [350.69, 445.63]
        })


class MockSession:
    """Mock FastF1 Session object"""
    def __init__(self):
        self.circuit_info = MockCircuitInfo()
    
    def load(self):
        """Mock load method"""
        pass
    
    def get_circuit_info(self):
        """Return mock circuit info"""
        return self.circuit_info


def create_mock_schedule():
    """Create a mock FastF1 schedule DataFrame"""
    return pd.DataFrame({
        'RoundNumber': [1, 2],
        'Country': ['Australia', 'China'],
        'Location': ['Melbourne', 'Shanghai'],
        'OfficialEventName': [
            'FORMULA 1 AUSTRALIAN GRAND PRIX 2025',
            'FORMULA 1 CHINESE GRAND PRIX 2025'
        ],
        'EventDate': pd.to_datetime(['2025-03-16', '2025-03-23']),
        'EventName': ['Australian Grand Prix', 'Chinese Grand Prix'],
        'EventFormat': ['conventional', 'conventional'],
        'Session1': ['Practice 1', 'Practice 1'],
        'Session1Date': ['2025-03-14 12:30:00+11:00', '2025-03-21 11:30:00+08:00'],
        'Session1DateUtc': pd.to_datetime(['2025-03-14 01:30:00', '2025-03-21 03:30:00'], utc=True),
        'Session2': ['Practice 2', 'Practice 2'],
        'Session2Date': ['2025-03-14 16:00:00+11:00', '2025-03-21 15:00:00+08:00'],
        'Session2DateUtc': pd.to_datetime(['2025-03-14 05:00:00', '2025-03-21 07:00:00'], utc=True),
        'Session3': ['Practice 3', 'Practice 3'],
        'Session3Date': ['2025-03-15 12:30:00+11:00', '2025-03-22 11:30:00+08:00'],
        'Session3DateUtc': pd.to_datetime(['2025-03-15 01:30:00', '2025-03-22 03:30:00'], utc=True),
        'Session4': ['Qualifying', 'Qualifying'],
        'Session4Date': ['2025-03-15 16:00:00+11:00', '2025-03-22 15:00:00+08:00'],
        'Session4DateUtc': pd.to_datetime(['2025-03-15 05:00:00', '2025-03-22 07:00:00'], utc=True),
        'Session5': ['Race', 'Race'],
        'Session5Date': ['2025-03-16 15:00:00+11:00', '2025-03-23 15:00:00+08:00'],
        'Session5DateUtc': pd.to_datetime(['2025-03-16 04:00:00', '2025-03-23 07:00:00'], utc=True),
        'F1ApiSupport': [True, True]
    })


class ImportScheduleBasicTests(TestCase):
    """Basic import functionality tests"""
    
    @mock.patch('analytics.management.commands.import_schedule.fastf1')
    def test_basic_schedule_import(self, mock_fastf1):
        """Should import basic schedule data without circuits"""
        # Setup mock
        mock_fastf1.get_event_schedule.return_value = create_mock_schedule()
        
        # Run command
        out = StringIO()
        call_command('import_schedule', '--year', '2025', stdout=out)
        
        # Verify season created
        self.assertEqual(Season.objects.count(), 1)
        season = Season.objects.get(year=2025)
        self.assertEqual(season.name, '2025 Formula 1 Season')
        
        # Verify races created
        self.assertEqual(Race.objects.count(), 2)
        race1 = Race.objects.get(round_number=1)
        self.assertEqual(race1.name, 'Australian Grand Prix')
        self.assertEqual(race1.location, 'Melbourne')
        self.assertEqual(race1.country, 'Australia')
        self.assertEqual(race1.event_format, 'conventional')
        
        # Verify sessions created
        self.assertEqual(Session.objects.count(), 10)  # 5 per race
        race1_sessions = race1.sessions.all()
        self.assertEqual(race1_sessions.count(), 5)
        
        # Check session types
        session_types = list(race1_sessions.values_list('session_type', flat=True))
        self.assertIn('Practice 1', session_types)
        self.assertIn('Practice 2', session_types)
        self.assertIn('Practice 3', session_types)
        self.assertIn('Qualifying', session_types)
        self.assertIn('Race', session_types)
        
        # Verify output
        output = out.getvalue()
        self.assertIn('Races created:    2', output)
        self.assertIn('Sessions created: 10', output)
    
    @mock.patch('analytics.management.commands.import_schedule.fastf1')
    def test_import_with_circuits(self, mock_fastf1):
        """Should import circuit geometry data when --with-circuits is set"""
        # Setup mocks
        mock_fastf1.get_event_schedule.return_value = create_mock_schedule()
        mock_fastf1.get_session.return_value = MockSession()
        
        # Run command with circuits
        out = StringIO()
        call_command('import_schedule', '--year', '2025', '--with-circuits', stdout=out)
        
        # Verify circuits created
        self.assertEqual(Circuit.objects.count(), 2)
        circuit = Circuit.objects.get(name='Melbourne Circuit')
        self.assertEqual(circuit.rotation, 44.0)
        
        # Verify corners created
        self.assertEqual(Corner.objects.filter(circuit=circuit).count(), 3)
        corner = circuit.corners.first()
        self.assertEqual(corner.number, 1)
        self.assertIsNotNone(corner.x)
        self.assertIsNotNone(corner.y)
        
        # Verify marshal lights created
        self.assertEqual(MarshalLight.objects.filter(circuit=circuit).count(), 2)
        
        # Verify marshal sectors created
        self.assertEqual(MarshalSector.objects.filter(circuit=circuit).count(), 2)
        
        # Verify output
        output = out.getvalue()
        self.assertIn('Circuits created: 2', output)


class ImportScheduleSkipLogicTests(TestCase):
    """Tests for smart skip logic"""
    
    def setUp(self):
        """Create pre-existing data for skip tests"""
        self.season = Season.objects.create(year=2025, name='2025 Season')
        self.race = Race.objects.create(
            season=self.season,
            name='Australian Grand Prix',
            round_number=1,
            location='Melbourne',
            country='Australia'
        )
        # Create all 5 sessions
        for i in range(1, 6):
            Session.objects.create(
                race=self.race,
                session_number=i,
                session_type=f'Session {i}'
            )
    
    @mock.patch('analytics.management.commands.import_schedule.fastf1')
    def test_skips_existing_race_with_sessions(self, mock_fastf1):
        """Should skip race that already has all sessions"""
        mock_fastf1.get_event_schedule.return_value = create_mock_schedule()
        
        out = StringIO()
        call_command('import_schedule', '--year', '2025', stdout=out)
        
        # Verify race was skipped
        output = out.getvalue()
        self.assertIn('Skipped race (already has 5 sessions)', output)
        self.assertIn('Races skipped:    1', output)
        
        # Should still create the second race
        self.assertEqual(Race.objects.count(), 2)
        self.assertEqual(Session.objects.count(), 10)
    
    @mock.patch('analytics.management.commands.import_schedule.fastf1')
    def test_reimports_race_with_partial_sessions(self, mock_fastf1):
        """Should re-import race if session count doesn't match"""
        # Delete 2 sessions
        Session.objects.filter(race=self.race, session_number__in=[4, 5]).delete()
        self.assertEqual(self.race.sessions.count(), 3)
        
        mock_fastf1.get_event_schedule.return_value = create_mock_schedule()
        
        out = StringIO()
        call_command('import_schedule', '--year', '2025', stdout=out)
        
        # Verify race was re-imported
        output = out.getvalue()
        self.assertIn('expected 5, re-importing', output)
        
        # Should now have all 5 sessions
        self.assertEqual(self.race.sessions.count(), 5)
    
    @mock.patch('analytics.management.commands.import_schedule.fastf1')
    def test_force_flag_reimports_everything(self, mock_fastf1):
        """Should re-import all data when --force is set"""
        mock_fastf1.get_event_schedule.return_value = create_mock_schedule()
        
        out = StringIO()
        call_command('import_schedule', '--year', '2025', '--force', stdout=out)
        
        # Verify force mode was enabled
        output = out.getvalue()
        self.assertIn('FORCE MODE', output)
        self.assertIn('Races updated:    1', output)
        
        # No skips should occur
        self.assertNotIn('Skipped', output)


class ImportScheduleCircuitTests(TestCase):
    """Tests for circuit import logic"""
    
    def setUp(self):
        """Create pre-existing data"""
        self.season = Season.objects.create(year=2025, name='2025 Season')
        self.circuit = Circuit.objects.create(name='Melbourne Circuit', rotation=44.0)
        self.race = Race.objects.create(
            season=self.season,
            name='Australian Grand Prix',
            round_number=1,
            circuit=self.circuit
        )
        # Create sessions
        for i in range(1, 6):
            Session.objects.create(race=self.race, session_number=i, session_type=f'Session {i}')
    
    @mock.patch('analytics.management.commands.import_schedule.fastf1')
    def test_skips_circuit_with_complete_geometry(self, mock_fastf1):
        """Should skip circuit that has all geometry data"""
        # Add complete geometry
        Corner.objects.create(circuit=self.circuit, number=1, x=0, y=0, angle=0, distance=0)
        MarshalLight.objects.create(circuit=self.circuit, number=1, x=0, y=0, angle=0, distance=0)
        MarshalSector.objects.create(circuit=self.circuit, number=1, x=0, y=0, angle=0, distance=0)
        
        mock_fastf1.get_event_schedule.return_value = create_mock_schedule()
        
        out = StringIO()
        call_command('import_schedule', '--year', '2025', '--with-circuits', stdout=out)
        
        output = out.getvalue()
        self.assertIn('Skipped circuit (already has geometry data)', output)
        self.assertIn('Circuits skipped: 1', output)
    
    @mock.patch('analytics.management.commands.import_schedule.fastf1')
    def test_reimports_circuit_missing_corners(self, mock_fastf1):
        """Should re-import circuit if corners are missing"""
        # Only add lights and sectors, no corners
        MarshalLight.objects.create(circuit=self.circuit, number=1, x=0, y=0, angle=0, distance=0)
        MarshalSector.objects.create(circuit=self.circuit, number=1, x=0, y=0, angle=0, distance=0)
        
        mock_fastf1.get_event_schedule.return_value = create_mock_schedule()
        mock_fastf1.get_session.return_value = MockSession()
        
        out = StringIO()
        call_command('import_schedule', '--year', '2025', '--with-circuits', stdout=out)
        
        output = out.getvalue()
        self.assertIn('missing/incomplete geometry data', output)
        
        # Should now have corners
        self.assertTrue(self.circuit.corners.exists())
    
    @mock.patch('analytics.management.commands.import_schedule.fastf1')
    def test_reimports_deleted_circuit(self, mock_fastf1):
        """Should re-import circuit if it was deleted"""
        # Delete circuit (race.circuit becomes None due to SET_NULL)
        self.circuit.delete()
        self.race.refresh_from_db()
        self.assertIsNone(self.race.circuit)
        
        mock_fastf1.get_event_schedule.return_value = create_mock_schedule()
        mock_fastf1.get_session.return_value = MockSession()
        
        out = StringIO()
        call_command('import_schedule', '--year', '2025', '--with-circuits', stdout=out)
        
        output = out.getvalue()
        self.assertIn('Circuit missing, importing', output)
        
        # Should have new circuit
        self.race.refresh_from_db()
        self.assertIsNotNone(self.race.circuit)
        self.assertTrue(self.race.circuit.corners.exists())


class ImportScheduleSpecificEventTests(TestCase):
    """Tests for --event flag"""
    
    @mock.patch('analytics.management.commands.import_schedule.fastf1')
    def test_imports_only_specific_event(self, mock_fastf1):
        """Should import only the specified event"""
        mock_fastf1.get_event_schedule.return_value = create_mock_schedule()
        
        out = StringIO()
        call_command('import_schedule', '--year', '2025', '--event', '1', stdout=out)
        
        # Should only create 1 race
        self.assertEqual(Race.objects.count(), 1)
        race = Race.objects.get()
        self.assertEqual(race.round_number, 1)
        self.assertEqual(race.name, 'Australian Grand Prix')
        
        # Verify output
        output = out.getvalue()
        self.assertIn('Importing only Round 1', output)
    
    @mock.patch('analytics.management.commands.import_schedule.fastf1')
    def test_event_not_found(self, mock_fastf1):
        """Should handle non-existent event gracefully"""
        mock_fastf1.get_event_schedule.return_value = create_mock_schedule()
        
        out = StringIO()
        call_command('import_schedule', '--year', '2025', '--event', '999', stdout=out)
        
        # Should not create any races
        self.assertEqual(Race.objects.count(), 0)
        
        output = out.getvalue()
        self.assertIn('Event 999 not found', output)


class ImportScheduleHelperMethodTests(TestCase):
    """Tests for helper methods"""
    
    def test_circuit_has_complete_geometry(self):
        """Should correctly detect complete geometry"""
        from analytics.management.commands.import_schedule import Command
        
        command = Command()
        circuit = Circuit.objects.create(name='Test Circuit')
        
        # No geometry
        self.assertFalse(command._circuit_has_complete_geometry(circuit))
        
        # Only corners
        Corner.objects.create(circuit=circuit, number=1, x=0, y=0, angle=0, distance=0)
        self.assertFalse(command._circuit_has_complete_geometry(circuit))
        
        # Corners + lights
        MarshalLight.objects.create(circuit=circuit, number=1, x=0, y=0, angle=0, distance=0)
        self.assertFalse(command._circuit_has_complete_geometry(circuit))
        
        # Complete geometry
        MarshalSector.objects.create(circuit=circuit, number=1, x=0, y=0, angle=0, distance=0)
        self.assertTrue(command._circuit_has_complete_geometry(circuit))


class ImportScheduleEdgeCaseTests(TestCase):
    """Tests for edge cases and error handling"""
    
    @mock.patch('analytics.management.commands.import_schedule.fastf1')
    def test_handles_missing_session_dates(self, mock_fastf1):
        """Should handle events with missing session dates"""
        schedule = create_mock_schedule()
        # Set some dates to NaT (Not a Time)
        schedule.loc[0, 'Session4DateUtc'] = pd.NaT
        schedule.loc[0, 'Session5DateUtc'] = pd.NaT
        
        mock_fastf1.get_event_schedule.return_value = schedule
        
        out = StringIO()
        call_command('import_schedule', '--year', '2025', stdout=out)
        
        # Should still create race and sessions
        self.assertEqual(Race.objects.count(), 2)
        race = Race.objects.get(round_number=1)
        self.assertEqual(race.sessions.count(), 5)
        
        # Sessions without dates should still be created
        session = race.sessions.get(session_number=4)
        self.assertEqual(session.session_type, 'Qualifying')
        self.assertIsNone(session.session_date_utc)
    
    @mock.patch('analytics.management.commands.import_schedule.fastf1')
    def test_handles_circuit_import_error(self, mock_fastf1):
        """Should handle errors during circuit import gracefully"""
        mock_fastf1.get_event_schedule.return_value = create_mock_schedule()
        # Simulate FastF1 error
        mock_fastf1.get_session.side_effect = Exception('FastF1 API Error')
        
        out = StringIO()
        # Should not raise exception
        call_command('import_schedule', '--year', '2025', '--with-circuits', stdout=out)
        
        # Should still create races and sessions
        self.assertEqual(Race.objects.count(), 2)
        self.assertEqual(Session.objects.count(), 10)
        
        # Circuits should be created but without geometry
        self.assertEqual(Circuit.objects.count(), 2)
        circuit = Circuit.objects.first()
        self.assertFalse(circuit.corners.exists())
        
        output = out.getvalue()
        self.assertIn('Could not load circuit data', output)
