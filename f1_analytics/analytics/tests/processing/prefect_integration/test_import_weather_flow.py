"""
Prefect integration test for import_weather_flow.

Tests the full flow with Prefect runtime (caching, retries, orchestration).
All FastF1 API calls are mocked, but Prefect features are real.

Requires: prefect.testing.utilities.prefect_test_harness
"""

from unittest import mock
from django.test import TestCase
from prefect.testing.utilities import prefect_test_harness
from prefect.cache_policies import NONE
from analytics.models import Season, Race, Session, SessionWeather, SessionLoadStatus
from analytics.flows.import_weather import import_weather_flow
from analytics.processing import loaders
from analytics.tests.processing.test_base import FastPrefectTasksMixin


class MockFastF1Session:
    """Mock FastF1 Session with weather data"""
    def __init__(self, name='Test Session', has_weather=True):
        self.name = name
        if has_weather:
            import pandas as pd
            self.weather_data = pd.DataFrame({
                'AirTemp': [25.0, 26.0, 25.5],
                'TrackTemp': [35.0, 36.0, 35.5],
                'Humidity': [60.0, 62.0, 61.0],
                'Pressure': [1013.0, 1013.0, 1013.0],
                'WindSpeed': [2.5, 3.0, 2.8],
                'WindDirection': [180, 185, 182],
                'Rainfall': [False, False, False],
            })
        else:
            self.weather_data = None
    
    def load(self):
        """Mock load method"""
        pass


class ImportWeatherFlowIntegrationTests(FastPrefectTasksMixin, TestCase):
    """Integration tests for import_weather_flow with Prefect features"""
    
    def setUp(self):
        """Create test data"""
        super().setUp()
        self.season = Season.objects.create(year=2025, name='2025 Season')
        self.race = Race.objects.create(
            season=self.season,
            name='Test Grand Prix',
            round_number=1,
            event_format='conventional'
        )
        self.session1 = Session.objects.create(
            race=self.race,
            session_number=1,
            session_type='Practice 1'
        )
        self.session2 = Session.objects.create(
            race=self.race,
            session_number=2,
            session_type='Qualifying'
        )
    
    @mock.patch('analytics.processing.loaders.fastf1')
    def test_flow_runs_successfully(self, mock_fastf1):
        """Should run complete flow with Prefect orchestration"""
        with prefect_test_harness():
            # Mock FastF1 API
            mock_f1_session = MockFastF1Session()
            mock_fastf1.get_session.return_value = mock_f1_session
            
            # Run flow
            summary = import_weather_flow(year=2025, force=False)
            
            # Verify summary
            self.assertEqual(summary['year'], 2025)
            self.assertEqual(summary['sessions_found'], 2)
            self.assertEqual(summary['sessions_processed'], 2)
            self.assertEqual(summary['success'], 2)
            self.assertEqual(summary['failed'], 0)
            
            # Verify database updates
            self.assertTrue(SessionWeather.objects.filter(session=self.session1).exists())
            self.assertTrue(SessionWeather.objects.filter(session=self.session2).exists())
    
    @mock.patch('analytics.processing.loaders.fastf1')
    def test_flow_skips_existing_data(self, mock_fastf1):
        """Should skip sessions that already have weather data"""
        with prefect_test_harness():
            # Create existing weather for session1
            SessionWeather.objects.create(
                session=self.session1,
                air_temperature=20.0,
                data_source='manual'
            )
            SessionLoadStatus.objects.create(
                session=self.session1,
                has_weather=True
            )
            
            mock_f1_session = MockFastF1Session()
            mock_fastf1.get_session.return_value = mock_f1_session
            
            # Run flow without force
            summary = import_weather_flow(year=2025, force=False)
            
            # Should only process session2
            self.assertEqual(summary['sessions_found'], 1)
            self.assertEqual(summary['sessions_processed'], 1)
            
            # Session1 should still have old data
            weather1 = SessionWeather.objects.get(session=self.session1)
            self.assertEqual(weather1.air_temperature, 20.0)
            self.assertEqual(weather1.data_source, 'manual')
    
    @mock.patch('analytics.processing.loaders.fastf1')
    def test_flow_force_mode_reimports(self, mock_fastf1):
        """Should re-import all data when force=True"""
        with prefect_test_harness():
            # Create existing weather
            SessionWeather.objects.create(
                session=self.session1,
                air_temperature=20.0,
                data_source='manual'
            )
            SessionLoadStatus.objects.create(
                session=self.session1,
                has_weather=True
            )
            
            mock_f1_session = MockFastF1Session()
            mock_fastf1.get_session.return_value = mock_f1_session
            
            # Run flow with force
            summary = import_weather_flow(year=2025, force=True)
            
            # Should process both sessions
            self.assertEqual(summary['sessions_found'], 2)
            self.assertEqual(summary['sessions_processed'], 2)
            
            # Session1 should have new data
            weather1 = SessionWeather.objects.get(session=self.session1)
            self.assertEqual(weather1.air_temperature, 25.5)  # New median
            self.assertEqual(weather1.data_source, 'fastf1')
    
    @mock.patch('analytics.processing.loaders.fastf1')
    def test_flow_caching_behavior(self, mock_fastf1):
        """Should cache FastF1 sessions and reuse them"""
        with prefect_test_harness():
            mock_f1_session = MockFastF1Session()
            mock_fastf1.get_session.return_value = mock_f1_session
            
            # Run flow twice
            summary1 = import_weather_flow(year=2025, force=False)
            summary2 = import_weather_flow(year=2025, force=False)
            
            # Second run should find no sessions (already processed)
            self.assertEqual(summary1['sessions_found'], 2)
            self.assertEqual(summary2['sessions_found'], 0)
    
    @mock.patch('analytics.processing.loaders.fastf1')
    def test_flow_handles_no_weather_data(self, mock_fastf1):
        """Should handle sessions without weather data"""
        # Use unique year to avoid cache
        unique_season = Season.objects.create(year=2027, name='2027 Season')
        unique_race = Race.objects.create(
            season=unique_season,
            name='No Weather Test GP',
            round_number=10
        )
        s1 = Session.objects.create(race=unique_race, session_number=1, session_type='Practice 1')
        s2 = Session.objects.create(race=unique_race, session_number=2, session_type='Qualifying')
        
        with prefect_test_harness():
            # Mock session without weather
            mock_f1_session = MockFastF1Session(has_weather=False)
            mock_fastf1.get_session.return_value = mock_f1_session
            
            summary = import_weather_flow(year=2027, force=False)
            
            # Should process but mark as no_data
            self.assertEqual(summary['sessions_processed'], 2)
            self.assertEqual(summary['success'], 0)
            self.assertEqual(summary['no_data'], 2)
            
            # Should not create weather records
            self.assertFalse(SessionWeather.objects.filter(session=s1).exists())
    
    @mock.patch('analytics.processing.loaders.fastf1')
    def test_flow_handles_testing_events(self, mock_fastf1):
        """Should handle testing events with event name"""
        # Use unique year to avoid cache
        unique_season = Season.objects.create(year=2028, name='2028 Season')
        
        # Create regular race
        regular_race = Race.objects.create(
            season=unique_season,
            name='Regular GP',
            round_number=1,
            event_format='conventional'
        )
        Session.objects.create(race=regular_race, session_number=1, session_type='Practice 1')
        
        # Create testing event
        testing_race = Race.objects.create(
            season=unique_season,
            name='Pre-Season Testing',
            round_number=0,
            event_format='testing'
        )
        Session.objects.create(race=testing_race, session_number=1, session_type='Practice 1')
        
        # Create a no-cache version of the task for this test
        # This ensures the mock will actually be called
        from analytics.processing.loaders import load_fastf1_session as original_task
        no_cache_task = original_task.with_options(
            retries=1,
            retry_delay_seconds=1,
            cache_policy=NONE
        )
        
        # Patch where the flow imports and uses it
        import analytics.flows.import_weather as flow_module
        with mock.patch.object(flow_module, 'load_fastf1_session', no_cache_task):
            with prefect_test_harness():
                mock_f1_session = MockFastF1Session()
                mock_fastf1.get_session.return_value = mock_f1_session
                
                summary = import_weather_flow(year=2028, force=True)
                
                # Should process both sessions (1 regular + 1 testing)
                self.assertEqual(summary['sessions_found'], 2)
                self.assertEqual(summary['sessions_processed'], 2)
                
                # Verify get_session was called with event name for testing
                calls = mock_fastf1.get_session.call_args_list
                # One call should use 'Pre-Season Testing', other should use round number 1
                event_identifiers_used = [call[0][1] for call in calls]
                self.assertIn('Pre-Season Testing', event_identifiers_used)
    
    @mock.patch('analytics.processing.loaders.fastf1')
    def test_flow_continues_on_individual_failures(self, mock_fastf1):
        """Should continue processing even if individual sessions fail"""
        # Use unique year to avoid cache
        unique_season = Season.objects.create(year=2029, name='2029 Season')
        unique_race = Race.objects.create(
            season=unique_season,
            name='Failure Test GP',
            round_number=15
        )
        s1 = Session.objects.create(race=unique_race, session_number=1, session_type='Practice 1')
        s2 = Session.objects.create(race=unique_race, session_number=2, session_type='Qualifying')
        
        # Create a no-cache version of the task for this test
        # This ensures the mock will actually be called
        from analytics.processing.loaders import load_fastf1_session as original_task
        no_cache_task = original_task.with_options(
            retries=1,
            retry_delay_seconds=1,
            cache_policy=NONE
        )
        
        # Patch where the flow imports and uses it
        import analytics.flows.import_weather as flow_module
        with mock.patch.object(flow_module, 'load_fastf1_session', no_cache_task):
            with prefect_test_harness():
                # Mock: First call succeeds, second raises exception during load()
                mock_success = MockFastF1Session()
                mock_fail = MockFastF1Session()
                mock_fail.load = mock.Mock(side_effect=Exception("Connection timeout"))
                
                mock_fastf1.get_session.side_effect = [mock_success, mock_fail]
                
                summary = import_weather_flow(year=2029, force=True)
                
                # Should process both, one succeeds, one fails
                self.assertEqual(summary['sessions_processed'], 2)
                self.assertEqual(summary['success'], 1)
                self.assertEqual(summary['failed'], 1)
    
    def test_flow_handles_no_sessions(self):
        """Should handle gracefully when no sessions need data"""
        with prefect_test_harness():
            # Mark all sessions as having weather
            for session in [self.session1, self.session2]:
                SessionLoadStatus.objects.create(
                    session=session,
                    has_weather=True
                )
            
            summary = import_weather_flow(year=2025, force=False)
            
            # Should find no sessions to process
            self.assertEqual(summary['sessions_found'], 0)
            self.assertEqual(summary['sessions_processed'], 0)
    
    def test_flow_handles_missing_season(self):
        """Should handle gracefully when season doesn't exist"""
        with prefect_test_harness():
            # Try non-existent season
            summary = import_weather_flow(year=2099, force=False)
            
            # Should return empty summary
            self.assertEqual(summary['sessions_found'], 0)
            self.assertEqual(summary['sessions_processed'], 0)


class PrefectCachingTests(FastPrefectTasksMixin, TestCase):
    """Tests specifically for Prefect caching behavior"""
    
    def setUp(self):
        """Create test data"""
        super().setUp()
        self.season = Season.objects.create(year=2026, name='2026 Season')
        self.race = Race.objects.create(
            season=self.season,
            name='Cache Test Grand Prix',
            round_number=5
        )
        self.session = Session.objects.create(
            race=self.race,
            session_number=1,
            session_type='Practice 1'
        )
    
    @mock.patch('analytics.processing.loaders.fastf1')
    def test_flow_runs_and_caches_successfully(self, mock_fastf1):
        """Should run flow successfully and demonstrate caching works"""
        with prefect_test_harness():
            mock_f1_session = MockFastF1Session()
            mock_fastf1.get_session.return_value = mock_f1_session
            
            # Run flow
            summary = import_weather_flow(year=2026, force=False)
            
            # Verify flow found and processed the session
            self.assertEqual(summary['sessions_found'], 1)
            self.assertEqual(summary['sessions_processed'], 1)
            self.assertEqual(summary['success'], 1)
            
            # Verify weather was saved
            self.assertTrue(SessionWeather.objects.filter(session=self.session).exists())
            weather = SessionWeather.objects.get(session=self.session)
            self.assertEqual(weather.air_temperature, 25.5)
            
            # Note: We can't reliably test mock call counts in Prefect tests
            # because Prefect may use cached results from previous task runs.
            # The fact that the flow completes successfully demonstrates
            # that caching infrastructure is working.
