"""
Tests for extract_driver_info and save_driver_info_to_db tasks.
"""

import pandas as pd
from unittest import mock
from django.test import TestCase
from analytics.models import Driver, Session, SessionResult
from analytics.flows.import_drivers import extract_driver_info, save_driver_info_to_db
from analytics.tests.factories import make_season, make_race, make_session, make_driver


def _make_results_df(rows):
    """Build a minimal FastF1-style results DataFrame."""
    defaults = {
        'FullName': 'Max Verstappen',
        'DriverNumber': '1',
        'Abbreviation': 'VER',
        'TeamName': 'Red Bull Racing',
        'TeamColor': 'FFFFFF',
        'Position': 1,
        'GridPosition': 1,
        'Status': 'Finished',
        'Points': 25.0,
        'Time': None,
    }
    return pd.DataFrame([{**defaults, **row} for row in rows])


def _make_laps_df(track_statuses=None, lap_numbers=None):
    """Build a minimal laps DataFrame for safety-car counting."""
    if track_statuses is None:
        return pd.DataFrame(columns=['TrackStatus', 'LapNumber'])
    return pd.DataFrame({
        'TrackStatus': track_statuses,
        'LapNumber': lap_numbers or list(range(1, len(track_statuses) + 1)),
    })


class ExtractDriverInfoTests(TestCase):
    @mock.patch('analytics.flows.import_drivers.get_run_logger')
    def test_extracts_driver_list_from_results(self, mock_logger):
        """Should extract all valid drivers from session results."""
        mock_session = mock.Mock()
        mock_session.results = _make_results_df([{}])
        mock_session.laps = pd.DataFrame()

        result = extract_driver_info.fn(mock_session)

        self.assertIsNotNone(result)
        self.assertEqual(len(result['drivers']), 1)
        self.assertEqual(result['drivers'][0]['full_name'], 'Max Verstappen')
        self.assertEqual(result['drivers'][0]['driver_number'], '1')

    @mock.patch('analytics.flows.import_drivers.get_run_logger')
    def test_skips_drivers_missing_critical_fields(self, mock_logger):
        """Should skip drivers missing full_name or driver_number."""
        mock_session = mock.Mock()
        mock_session.results = _make_results_df([
            {'FullName': '', 'DriverNumber': '1'},    # missing full_name
            {'FullName': 'Valid Driver', 'DriverNumber': '2'},
        ])
        mock_session.laps = pd.DataFrame()

        result = extract_driver_info.fn(mock_session)

        self.assertEqual(len(result['drivers']), 1)
        self.assertEqual(result['drivers'][0]['full_name'], 'Valid Driver')

    @mock.patch('analytics.flows.import_drivers.get_run_logger')
    def test_empty_results_returns_none(self, mock_logger):
        """Should return None when session results are empty."""
        mock_session = mock.Mock()
        mock_session.results = pd.DataFrame()

        result = extract_driver_info.fn(mock_session)

        self.assertIsNone(result)

    @mock.patch('analytics.flows.import_drivers.get_run_logger')
    def test_counts_safety_car_laps(self, mock_logger):
        """Should count unique laps with safety car / VSC track status."""
        mock_session = mock.Mock()
        mock_session.results = _make_results_df([{}])
        # Laps 3 and 5 under SC (status '4'), lap 7 under VSC ('6')
        mock_session.laps = _make_laps_df(
            track_statuses=['1', '1', '4', '1', '4', '1', '6'],
            lap_numbers=[1, 2, 3, 4, 5, 6, 7],
        )

        result = extract_driver_info.fn(mock_session)

        # 3 unique lap numbers: 3, 5, 7
        self.assertEqual(result['safety_car_laps'], 3)


class SaveDriverInfoToDbTests(TestCase):
    def setUp(self):
        season = make_season()
        race = make_race(season)
        self.session = make_session(race)
        self.driver = make_driver()

    @mock.patch('analytics.flows.import_drivers.get_run_logger')
    def test_creates_session_results_for_known_driver(self, mock_logger):
        """Should create a SessionResult for an existing driver."""
        driver_data = {
            'drivers': [{
                'full_name': 'Max Verstappen',
                'driver_number': '1',
                'abbreviation': 'VER',
                'team_name': '',
                'team_color': '',
                'position': 1,
                'grid_position': 1,
                'status': 'Finished',
                'points': 25.0,
                'time': '',
            }],
            'safety_car_laps': None,
        }

        result = save_driver_info_to_db.fn(self.session.id, driver_data)

        self.assertEqual(result['status'], 'success')
        self.assertEqual(SessionResult.objects.filter(session=self.session).count(), 1)

    @mock.patch('analytics.flows.import_drivers.get_run_logger')
    def test_creates_new_driver_when_not_found(self, mock_logger):
        """Should create a new Driver when no match is found."""
        driver_data = {
            'drivers': [{
                'full_name': 'New Driver',
                'driver_number': '99',
                'abbreviation': 'NEW',
                'team_name': '',
                'team_color': '',
                'position': None,
                'grid_position': None,
                'status': '',
                'points': None,
                'time': '',
            }],
            'safety_car_laps': None,
        }

        result = save_driver_info_to_db.fn(self.session.id, driver_data)

        self.assertEqual(result['status'], 'success')
        self.assertTrue(Driver.objects.filter(full_name='New Driver').exists())

    @mock.patch('analytics.flows.import_drivers.get_run_logger')
    def test_updates_driver_number_when_changed(self, mock_logger):
        """Should update driver_number when it differs from DB."""
        self.driver.driver_number = '99'
        self.driver.save()

        driver_data = {
            'drivers': [{
                'full_name': 'Max Verstappen',
                'driver_number': '1',
                'abbreviation': 'VER',
                'team_name': '',
                'team_color': '',
                'position': None,
                'grid_position': None,
                'status': '',
                'points': None,
                'time': '',
            }],
            'safety_car_laps': None,
        }

        save_driver_info_to_db.fn(self.session.id, driver_data)

        self.driver.refresh_from_db()
        self.assertEqual(self.driver.driver_number, '1')

    @mock.patch('analytics.flows.import_drivers.get_run_logger')
    def test_updates_driver_abbreviation_when_changed(self, mock_logger):
        """Should update abbreviation when it differs from DB."""
        self.driver.abbreviation = 'OLD'
        self.driver.save()

        driver_data = {
            'drivers': [{
                'full_name': 'Max Verstappen',
                'driver_number': '1',
                'abbreviation': 'VER',
                'team_name': '',
                'team_color': '',
                'position': None,
                'grid_position': None,
                'status': '',
                'points': None,
                'time': '',
            }],
            'safety_car_laps': None,
        }

        save_driver_info_to_db.fn(self.session.id, driver_data)

        self.driver.refresh_from_db()
        self.assertEqual(self.driver.abbreviation, 'VER')

    @mock.patch('analytics.flows.import_drivers.get_run_logger')
    def test_updates_session_safety_car_laps(self, mock_logger):
        """Should update session.safety_car_laps when provided."""
        self.assertIsNone(self.session.safety_car_laps)

        driver_data = {'drivers': [], 'safety_car_laps': 5}
        save_driver_info_to_db.fn(self.session.id, driver_data)

        self.session.refresh_from_db()
        self.assertEqual(self.session.safety_car_laps, 5)

    @mock.patch('analytics.flows.import_drivers.get_run_logger')
    def test_invalid_session_id_returns_failed(self, mock_logger):
        """Should return failed status for unknown session ID."""
        result = save_driver_info_to_db.fn(99999, {'drivers': [], 'safety_car_laps': None})

        self.assertEqual(result['status'], 'failed')
        self.assertIn('error', result)

    @mock.patch('analytics.flows.import_drivers.get_run_logger')
    def test_returns_counts_dict(self, mock_logger):
        """Returned dict should include driver and result counts."""
        result = save_driver_info_to_db.fn(self.session.id, {'drivers': [], 'safety_car_laps': None})

        self.assertIn('drivers_created', result)
        self.assertIn('drivers_updated', result)
        self.assertIn('drivers_skipped', result)
        self.assertIn('results_created', result)
