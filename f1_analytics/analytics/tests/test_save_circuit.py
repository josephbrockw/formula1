"""
Tests for extract_circuit_data and save_circuit_to_db tasks.
"""

from unittest import mock
from django.test import TestCase
import pandas as pd
from analytics.models import Corner, MarshalLight, MarshalSector
from analytics.flows.import_circuit import extract_circuit_data, save_circuit_to_db
from analytics.tests.factories import make_season, make_circuit, make_race, make_session


def _make_geometry_df(count=2):
    """Build a minimal corners/lights/sectors DataFrame."""
    return pd.DataFrame([
        {'Number': i, 'Letter': '', 'X': float(i * 10), 'Y': float(i * 5),
         'Angle': 45.0, 'Distance': float(i * 100)}
        for i in range(1, count + 1)
    ])


class ExtractCircuitDataTests(TestCase):
    @mock.patch('analytics.flows.import_circuit.get_run_logger')
    def test_extracts_corners(self, mock_logger):
        """Should extract corners from circuit_info.corners DataFrame."""
        mock_session = mock.Mock()
        mock_circuit_info = mock.Mock()
        mock_circuit_info.circuit_key = 'test'
        mock_circuit_info.corners = _make_geometry_df(3)
        mock_circuit_info.marshal_lights = None
        mock_circuit_info.marshal_sectors = None
        mock_session.get_circuit_info.return_value = mock_circuit_info

        result = extract_circuit_data.fn(mock_session)

        self.assertIsNotNone(result)
        self.assertEqual(len(result['corners']), 3)
        self.assertEqual(result['corners'][0]['number'], 1)

    @mock.patch('analytics.flows.import_circuit.get_run_logger')
    def test_extracts_marshal_lights(self, mock_logger):
        """Should extract marshal lights from circuit_info.marshal_lights DataFrame."""
        mock_session = mock.Mock()
        mock_circuit_info = mock.Mock()
        mock_circuit_info.circuit_key = 'test'
        mock_circuit_info.corners = None
        mock_circuit_info.marshal_lights = _make_geometry_df(2)
        mock_circuit_info.marshal_sectors = None
        mock_session.get_circuit_info.return_value = mock_circuit_info

        result = extract_circuit_data.fn(mock_session)

        self.assertIsNotNone(result)
        self.assertEqual(len(result['marshal_lights']), 2)

    @mock.patch('analytics.flows.import_circuit.get_run_logger')
    def test_extracts_marshal_sectors(self, mock_logger):
        """Should extract marshal sectors from circuit_info.marshal_sectors DataFrame."""
        mock_session = mock.Mock()
        mock_circuit_info = mock.Mock()
        mock_circuit_info.circuit_key = 'test'
        mock_circuit_info.corners = None
        mock_circuit_info.marshal_lights = None
        mock_circuit_info.marshal_sectors = _make_geometry_df(4)
        mock_session.get_circuit_info.return_value = mock_circuit_info

        result = extract_circuit_data.fn(mock_session)

        self.assertIsNotNone(result)
        self.assertEqual(len(result['marshal_sectors']), 4)

    @mock.patch('analytics.flows.import_circuit.get_run_logger')
    def test_returns_none_when_all_empty(self, mock_logger):
        """Should return None when all geometry DataFrames are empty/None."""
        mock_session = mock.Mock()
        mock_circuit_info = mock.Mock()
        mock_circuit_info.circuit_key = 'test'
        mock_circuit_info.corners = None
        mock_circuit_info.marshal_lights = None
        mock_circuit_info.marshal_sectors = None
        mock_session.get_circuit_info.return_value = mock_circuit_info

        result = extract_circuit_data.fn(mock_session)

        self.assertIsNone(result)


class SaveCircuitToDbTests(TestCase):
    def setUp(self):
        season = make_season()
        self.circuit = make_circuit()
        race = make_race(season, circuit=self.circuit)
        self.session = make_session(race)

    def _circuit_data(self, corners=2, lights=2, sectors=2):
        def row(n):
            return {'number': n, 'letter': '', 'x': float(n), 'y': float(n),
                    'angle': 0.0, 'distance': float(n * 100)}
        return {
            'corners': [row(i) for i in range(1, corners + 1)],
            'marshal_lights': [row(i) for i in range(1, lights + 1)],
            'marshal_sectors': [row(i) for i in range(1, sectors + 1)],
        }

    @mock.patch('analytics.flows.import_circuit.get_run_logger')
    @mock.patch('analytics.flows.import_circuit.mark_data_loaded')
    def test_creates_corner_records(self, mock_mark, mock_logger):
        """Should create Corner records linked to the circuit."""
        result = save_circuit_to_db.fn(self.session.id, self._circuit_data(corners=3))

        self.assertEqual(result['status'], 'success')
        self.assertEqual(Corner.objects.filter(circuit=self.circuit).count(), 3)

    @mock.patch('analytics.flows.import_circuit.get_run_logger')
    @mock.patch('analytics.flows.import_circuit.mark_data_loaded')
    def test_creates_marshal_light_records(self, mock_mark, mock_logger):
        """Should create MarshalLight records linked to the circuit."""
        result = save_circuit_to_db.fn(self.session.id, self._circuit_data(lights=4))

        self.assertEqual(result['status'], 'success')
        self.assertEqual(MarshalLight.objects.filter(circuit=self.circuit).count(), 4)

    @mock.patch('analytics.flows.import_circuit.get_run_logger')
    @mock.patch('analytics.flows.import_circuit.mark_data_loaded')
    def test_creates_marshal_sector_records(self, mock_mark, mock_logger):
        """Should create MarshalSector records linked to the circuit."""
        result = save_circuit_to_db.fn(self.session.id, self._circuit_data(sectors=5))

        self.assertEqual(result['status'], 'success')
        self.assertEqual(MarshalSector.objects.filter(circuit=self.circuit).count(), 5)

    @mock.patch('analytics.flows.import_circuit.get_run_logger')
    @mock.patch('analytics.flows.import_circuit.mark_data_loaded')
    def test_returns_counts_dict(self, mock_mark, mock_logger):
        """Returned dict should include a counts sub-dict."""
        result = save_circuit_to_db.fn(self.session.id, self._circuit_data())

        self.assertEqual(result['status'], 'success')
        self.assertIn('counts', result)
        self.assertEqual(result['counts']['corners'], 2)
        self.assertEqual(result['counts']['marshal_lights'], 2)
        self.assertEqual(result['counts']['marshal_sectors'], 2)

    @mock.patch('analytics.flows.import_circuit.get_run_logger')
    def test_session_without_circuit_returns_failed(self, mock_logger):
        """Should return failed when the session's race has no circuit."""
        from analytics.models import Session, Race
        season = make_season(year=2099)
        race_no_circuit = make_race(season, round_number=1, circuit=None, name='No Circuit GP')
        session_no_circuit = make_session(race_no_circuit)

        result = save_circuit_to_db.fn(session_no_circuit.id, self._circuit_data())

        self.assertEqual(result['status'], 'failed')
