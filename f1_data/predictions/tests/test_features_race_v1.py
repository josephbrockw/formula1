"""
Tests for RaceV1FeatureStore.

The only behaviour this store adds on top of V4FeatureStore is appending a
`predicted_quali_position` column derived from the event's qualifying session
results (SessionResult session_type="Q").

We test this new behaviour in isolation by patching V4FeatureStore's
get_all_driver_features() to return a minimal DataFrame. This avoids recreating
the extensive DB setup that V4 features require (practice laps, weather, full
race history) — that's already covered in test_features_v4.py.

DB is still needed for qualifying SessionResult objects, so we use TestCase.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd
from django.test import TestCase

from predictions.features.race.v1_race import RaceV1FeatureStore, _QUALI_POSITION_FALLBACK
from predictions.tests.factories import (
    make_circuit,
    make_driver,
    make_event,
    make_result,
    make_season,
    make_session,
    make_team,
)


def _base_df(driver_ids: list[int]) -> pd.DataFrame:
    """Minimal V4-style DataFrame as if returned by super().get_all_driver_features()."""
    return pd.DataFrame({
        "driver_id": driver_ids,
        "some_v4_feature": [1.0] * len(driver_ids),
    })


class TestRaceV1FeatureStoreQualiPosition(TestCase):
    """predicted_quali_position is appended correctly from qualifying session results."""

    def setUp(self) -> None:
        self.season = make_season(2024)
        self.circuit = make_circuit(key="silverstone")
        self.team = make_team(self.season, name="Ferrari", code="FER")
        self.d1 = make_driver(self.season, self.team, code="LEC", driver_number=16)
        self.d2 = make_driver(self.season, self.team, code="SAI", driver_number=55)
        self.event = make_event(
            self.season, round_number=1, circuit=self.circuit, event_date=date(2024, 5, 1)
        )

    def _get_features(self, event_id: int) -> pd.DataFrame:
        """Run get_all_driver_features with V4 parent mocked out."""
        driver_ids = [self.d1.id, self.d2.id]
        store = RaceV1FeatureStore()
        with patch.object(
            store.__class__.__bases__[0],   # V4FeatureStore
            "get_all_driver_features",
            return_value=_base_df(driver_ids),
        ):
            return store.get_all_driver_features(event_id)

    def test_predicted_quali_position_column_added(self) -> None:
        """The column must be present in the returned DataFrame."""
        df = self._get_features(self.event.id)
        self.assertIn("predicted_quali_position", df.columns)

    def test_qualifying_positions_mapped_correctly(self) -> None:
        """Drivers are mapped to their actual qualifying positions (P1, P2)."""
        q_session = make_session(self.event, session_type="Q")
        make_result(q_session, self.d1, self.team, position=1)
        make_result(q_session, self.d2, self.team, position=2)

        df = self._get_features(self.event.id)
        df = df.set_index("driver_id")

        self.assertEqual(df.loc[self.d1.id, "predicted_quali_position"], 1.0)
        self.assertEqual(df.loc[self.d2.id, "predicted_quali_position"], 2.0)

    def test_fallback_when_no_qualifying_session(self) -> None:
        """If no qualifying session exists, all drivers get the midfield fallback."""
        df = self._get_features(self.event.id)
        self.assertTrue(
            (df["predicted_quali_position"] == _QUALI_POSITION_FALLBACK).all()
        )

    def test_fallback_for_dns_driver_with_null_position(self) -> None:
        """
        A driver who DNSed qualifying has position=None in the DB.
        dict.get(key, default) does NOT fire for None values — only for missing keys.
        Our `is not None` check must correctly fall back to the midfield value.
        """
        q_session = make_session(self.event, session_type="Q")
        make_result(q_session, self.d1, self.team, position=1)
        make_result(q_session, self.d2, self.team, position=None)  # DNS — null position

        df = self._get_features(self.event.id)
        df = df.set_index("driver_id")

        self.assertEqual(df.loc[self.d1.id, "predicted_quali_position"], 1.0)
        self.assertEqual(df.loc[self.d2.id, "predicted_quali_position"], _QUALI_POSITION_FALLBACK)

    def test_empty_base_df_returns_empty(self) -> None:
        """If V4 returns empty (no feature data), we return empty without crashing."""
        store = RaceV1FeatureStore()
        with patch.object(
            store.__class__.__bases__[0],
            "get_all_driver_features",
            return_value=pd.DataFrame(),
        ):
            result = store.get_all_driver_features(self.event.id)
        self.assertTrue(result.empty)

    def test_race_session_results_not_used(self) -> None:
        """Only qualifying (Q) session results should feed predicted_quali_position.
        A race session result must not affect the qualifying position column."""
        r_session = make_session(self.event, session_type="R")
        make_result(r_session, self.d1, self.team, position=5)
        make_result(r_session, self.d2, self.team, position=10)

        df = self._get_features(self.event.id)
        # No Q session → should still be fallback despite R session existing
        self.assertTrue(
            (df["predicted_quali_position"] == _QUALI_POSITION_FALLBACK).all()
        )
