from __future__ import annotations

import datetime

from django.db.models import QuerySet
from django.test import TestCase

from core.models import Circuit, Event, Season, Session, SessionCollectionStatus
from core.tasks.gap_detector import find_uncollected_sessions, get_collection_summary


def _make_circuit() -> Circuit:
    circuit, _ = Circuit.objects.get_or_create(
        circuit_key="albert_park",
        defaults={"name": "Albert Park", "country": "Australia", "city": "Melbourne"},
    )
    return circuit


def _make_event(season: Season, round_number: int = 1) -> Event:
    return Event.objects.create(
        season=season,
        round_number=round_number,
        event_name="Australian Grand Prix",
        country="Australia",
        circuit=_make_circuit(),
        event_date=datetime.date(season.year, 3, 24),
        event_format="conventional",
    )


def _make_session(event: Event, session_type: str = "R") -> Session:
    return Session.objects.create(event=event, session_type=session_type)


class TestFindUncollectedSessions(TestCase):
    def setUp(self) -> None:
        self.season = Season.objects.create(year=2024)
        self.event = _make_event(self.season)

    def test_find_uncollected_returns_session_with_no_status(self) -> None:
        session = _make_session(self.event)
        self.assertIn(session, find_uncollected_sessions())

    def test_find_uncollected_returns_pending_session(self) -> None:
        session = _make_session(self.event)
        SessionCollectionStatus.objects.create(session=session, status="pending")
        self.assertIn(session, find_uncollected_sessions())

    def test_find_uncollected_excludes_failed_session_by_default(self) -> None:
        session = _make_session(self.event)
        SessionCollectionStatus.objects.create(session=session, status="failed")
        self.assertNotIn(session, find_uncollected_sessions())

    def test_find_uncollected_includes_failed_session_when_include_failed_true(self) -> None:
        session = _make_session(self.event)
        SessionCollectionStatus.objects.create(session=session, status="failed")
        self.assertIn(session, find_uncollected_sessions(include_failed=True))

    def test_find_uncollected_returns_collecting_session(self) -> None:
        session = _make_session(self.event)
        SessionCollectionStatus.objects.create(session=session, status="collecting")
        self.assertIn(session, find_uncollected_sessions())

    def test_find_uncollected_excludes_completed_session(self) -> None:
        session = _make_session(self.event)
        SessionCollectionStatus.objects.create(session=session, status="completed")
        self.assertNotIn(session, find_uncollected_sessions())

    def test_find_uncollected_mixed_statuses_returns_only_uncompleted(self) -> None:
        s_no_status = _make_session(self.event, "FP1")
        s_pending = _make_session(self.event, "FP2")
        s_completed = _make_session(self.event, "R")
        SessionCollectionStatus.objects.create(session=s_pending, status="pending")
        SessionCollectionStatus.objects.create(session=s_completed, status="completed")

        result = list(find_uncollected_sessions())
        self.assertIn(s_no_status, result)
        self.assertIn(s_pending, result)
        self.assertNotIn(s_completed, result)

    def test_find_uncollected_year_filter_includes_matching_year(self) -> None:
        session = _make_session(self.event)
        self.assertIn(session, find_uncollected_sessions(year=2024))

    def test_find_uncollected_year_filter_excludes_other_years(self) -> None:
        season_2023 = Season.objects.create(year=2023)
        session_2023 = _make_session(_make_event(season_2023))
        self.assertNotIn(session_2023, find_uncollected_sessions(year=2024))

    def test_find_uncollected_year_filter_excludes_completed_in_that_year(self) -> None:
        session = _make_session(self.event)
        SessionCollectionStatus.objects.create(session=session, status="completed")
        self.assertNotIn(session, find_uncollected_sessions(year=2024))

    def test_find_uncollected_no_year_spans_all_seasons(self) -> None:
        season_2023 = Season.objects.create(year=2023)
        session_2024 = _make_session(self.event)
        session_2023 = _make_session(_make_event(season_2023))

        result = list(find_uncollected_sessions())
        self.assertIn(session_2024, result)
        self.assertIn(session_2023, result)

    def test_find_uncollected_returns_queryset(self) -> None:
        self.assertIsInstance(find_uncollected_sessions(), QuerySet)


class TestGetCollectionSummary(TestCase):
    def setUp(self) -> None:
        self.season = Season.objects.create(year=2024)
        self.event = _make_event(self.season)
        self.session_types = ["FP1", "FP2", "FP3", "Q", "R"]
        self.sessions = [_make_session(self.event, t) for t in self.session_types]

    def test_get_summary_includes_season_key(self) -> None:
        summary = get_collection_summary()
        self.assertIn(2024, summary)

    def test_get_summary_total_reflects_all_sessions(self) -> None:
        summary = get_collection_summary()
        self.assertEqual(summary[2024]["total"], 5)

    def test_get_summary_all_pending_when_no_status_records(self) -> None:
        summary = get_collection_summary()
        self.assertEqual(summary[2024]["completed"], 0)
        self.assertEqual(summary[2024]["failed"], 0)
        self.assertEqual(summary[2024]["pending"], 5)

    def test_get_summary_counts_completed_correctly(self) -> None:
        SessionCollectionStatus.objects.create(session=self.sessions[0], status="completed")
        SessionCollectionStatus.objects.create(session=self.sessions[1], status="completed")
        summary = get_collection_summary()
        self.assertEqual(summary[2024]["completed"], 2)

    def test_get_summary_counts_failed_correctly(self) -> None:
        SessionCollectionStatus.objects.create(session=self.sessions[0], status="failed")
        summary = get_collection_summary()
        self.assertEqual(summary[2024]["failed"], 1)

    def test_get_summary_pending_is_remainder(self) -> None:
        SessionCollectionStatus.objects.create(session=self.sessions[0], status="completed")
        SessionCollectionStatus.objects.create(session=self.sessions[1], status="failed")
        summary = get_collection_summary()
        self.assertEqual(summary[2024]["pending"], 3)  # 5 total - 1 completed - 1 failed

    def test_get_summary_multiple_seasons_each_counted_independently(self) -> None:
        season_2023 = Season.objects.create(year=2023)
        event_2023 = _make_event(season_2023)
        sessions_2023 = [_make_session(event_2023, t) for t in ["Q", "R"]]
        SessionCollectionStatus.objects.create(session=sessions_2023[1], status="completed")

        summary = get_collection_summary()
        self.assertEqual(summary[2024]["total"], 5)
        self.assertEqual(summary[2023]["total"], 2)
        self.assertEqual(summary[2023]["completed"], 1)
        self.assertEqual(summary[2023]["pending"], 1)

    def test_get_summary_returns_seasons_in_year_order(self) -> None:
        Season.objects.create(year=2022)
        Season.objects.create(year=2023)
        keys = list(get_collection_summary().keys())
        self.assertEqual(keys, sorted(keys))
