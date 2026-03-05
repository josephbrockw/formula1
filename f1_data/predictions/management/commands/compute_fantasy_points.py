"""
Reconstruct FantasyDriverScore and FantasyConstructorScore records from FastF1 data.

Uses the 2025 F1 Fantasy scoring rules applied uniformly across all seasons.
Scores are slightly lower than real fantasy scores (no Driver of the Day, no pit stop bonus)
because those data points are not available in FastF1.

Usage:
  python manage.py compute_fantasy_points --seasons 2024
  python manage.py compute_fantasy_points --seasons 2022 2023 2024 2025
"""

from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Driver, Event, Lap, Season, Session, SessionResult, Team
from predictions.fantasy_scorer import (
    score_constructor_q_progression,
    score_driver_qualifying,
    score_driver_race,
)
from predictions.models import FantasyConstructorScore, FantasyDriverScore


class Command(BaseCommand):
    help = "Reconstruct fantasy scores for historical seasons from FastF1 session data"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--seasons", type=int, nargs="+", required=True, help="Season years to process")

    def handle(self, *args, **options) -> None:
        for year in options["seasons"]:
            self._process_season(year)

    def _process_season(self, year: int) -> None:
        try:
            season = Season.objects.get(year=year)
        except Season.DoesNotExist:
            raise CommandError(f"No season found for year={year}")

        events = list(Event.objects.filter(season=season).order_by("round_number"))
        if not events:
            raise CommandError(f"No events found for {year}")

        self.stdout.write(f"Computing fantasy points for {year} — {len(events)} events")

        # Preload all sessions, results, laps for the season in bulk
        sessions = {
            (s.event_id, s.session_type): s
            for s in Session.objects.filter(event__season=season)
        }
        results_by_session: dict[int, list[SessionResult]] = defaultdict(list)
        for r in SessionResult.objects.filter(session__event__season=season).select_related("driver", "team"):
            results_by_session[r.session_id].append(r)

        laps_by_session_driver: dict[tuple[int, int], list[Lap]] = defaultdict(list)
        for lap in (
            Lap.objects.filter(session__event__season=season)
            .only("session_id", "driver_id", "lap_number", "position", "is_pit_in_lap", "is_pit_out_lap")
            .order_by("lap_number")
        ):
            laps_by_session_driver[(lap.session_id, lap.driver_id)].append(lap)

        driver_score_rows: list[FantasyDriverScore] = []
        constructor_score_rows: list[FantasyConstructorScore] = []

        # Track race_total per (driver_id, event_id) and (team_id, event_id)
        driver_race_totals: dict[tuple[int, int], int] = {}
        constructor_race_totals: dict[tuple[int, int], int] = {}

        for event in events:
            driver_score_rows_event, constructor_score_rows_event = _process_event(
                event, sessions, results_by_session, laps_by_session_driver
            )
            driver_score_rows.extend(driver_score_rows_event)
            constructor_score_rows.extend(constructor_score_rows_event)

            for row in driver_score_rows_event:
                key = (row.driver_id, row.event_id)
                driver_race_totals[key] = driver_race_totals.get(key, 0) + row.points
            for row in constructor_score_rows_event:
                key = (row.team_id, row.event_id)
                constructor_race_totals[key] = constructor_race_totals.get(key, 0) + row.points

        # Stamp race_total on every score row
        for row in driver_score_rows:
            row.race_total = driver_race_totals.get((row.driver_id, row.event_id), 0)
        for row in constructor_score_rows:
            row.race_total = constructor_race_totals.get((row.team_id, row.event_id), 0)

        # Compute season_total (running cumulative per driver, ordered by round_number)
        _stamp_driver_season_totals(driver_score_rows, events)
        _stamp_constructor_season_totals(constructor_score_rows, events)

        with transaction.atomic():
            FantasyDriverScore.objects.filter(driver__season=season).delete()
            FantasyDriverScore.objects.bulk_create(driver_score_rows)
            FantasyConstructorScore.objects.filter(team__season=season).delete()
            FantasyConstructorScore.objects.bulk_create(constructor_score_rows)

        self.stdout.write(
            f"  {year}: {len(driver_score_rows)} driver rows, {len(constructor_score_rows)} constructor rows"
        )


# ---------------------------------------------------------------------------
# Per-event processing
# ---------------------------------------------------------------------------


def _process_event(
    event: Event,
    sessions: dict[tuple[int, str], Session],
    results_by_session: dict[int, list[SessionResult]],
    laps_by_session_driver: dict[tuple[int, int], list[Lap]],
) -> tuple[list[FantasyDriverScore], list[FantasyConstructorScore]]:
    driver_rows: list[FantasyDriverScore] = []
    constructor_qual_positions: dict[int, list[int | None]] = defaultdict(list)  # team_id → [positions]
    constructor_race_rows: dict[int, list[FantasyConstructorScore]] = defaultdict(list)  # team_id → rows

    for session_type in ("Q", "SQ", "S", "R"):
        session = sessions.get((event.id, session_type))
        if session is None:
            continue
        results = results_by_session.get(session.id, [])
        is_qual = session_type in ("Q", "SQ")

        for result in results:
            driver_id = result.driver_id
            team_id = result.team_id

            if is_qual:
                score_rows = score_driver_qualifying(
                    position=result.position,
                    status=result.status,
                    classified_position=result.classified_position,
                    session_type=session_type,
                )
                constructor_qual_positions[team_id].append(result.position)
            else:
                lap_data = [
                    (lap.position, lap.is_pit_in_lap, lap.is_pit_out_lap)
                    for lap in laps_by_session_driver.get((session.id, driver_id), [])
                ]
                score_rows = score_driver_race(
                    position=result.position,
                    grid_position=result.grid_position,
                    status=result.status,
                    classified_position=result.classified_position,
                    fastest_lap_rank=result.fastest_lap_rank,
                    laps=lap_data,
                    session_type=session_type,
                )

            for event_type, scoring_item, frequency, position, points in score_rows:
                driver_rows.append(
                    FantasyDriverScore(
                        driver_id=driver_id,
                        event_id=event.id,
                        event_type=event_type,
                        scoring_item=scoring_item,
                        frequency=frequency,
                        position=position,
                        points=points,
                        race_total=0,   # stamped later
                        season_total=0, # stamped later
                    )
                )

            if not is_qual:
                # Constructor gets driver's race/sprint points; include driver code to keep rows unique
                driver_code = result.driver.code
                for event_type, scoring_item, frequency, position, points in score_rows:
                    constructor_race_rows[team_id].append(
                        FantasyConstructorScore(
                            team_id=team_id,
                            event_id=event.id,
                            event_type=event_type,
                            scoring_item=f"{driver_code}: {scoring_item}",
                            frequency=frequency,
                            position=position,
                            points=points,
                            race_total=0,
                            season_total=0,
                        )
                    )

    # Q progression bonus — only for the primary qualifying session (Q or SQ)
    primary_qual_type = "Q" if sessions.get((event.id, "Q")) else "SQ" if sessions.get((event.id, "SQ")) else None
    if primary_qual_type:
        seen_teams: set[int] = set()
        for result in results_by_session.get(sessions[(event.id, primary_qual_type)].id, []):
            team_id = result.team_id
            if team_id in seen_teams:
                continue
            seen_teams.add(team_id)

        for team_id, positions in constructor_qual_positions.items():
            q_row = score_constructor_q_progression(positions)
            event_type, scoring_item, frequency, position, points = q_row
            constructor_race_rows[team_id].append(
                FantasyConstructorScore(
                    team_id=team_id,
                    event_id=event.id,
                    event_type=event_type,
                    scoring_item=scoring_item,
                    frequency=frequency,
                    position=position,
                    points=points,
                    race_total=0,
                    season_total=0,
                )
            )

    constructor_rows = [row for rows in constructor_race_rows.values() for row in rows]
    return driver_rows, constructor_rows


# ---------------------------------------------------------------------------
# Cumulative totals
# ---------------------------------------------------------------------------


def _stamp_driver_season_totals(rows: list[FantasyDriverScore], events: list[Event]) -> None:
    event_order = {e.id: i for i, e in enumerate(events)}
    # Group rows by driver, then sort events by round_number
    by_driver: dict[int, list[FantasyDriverScore]] = defaultdict(list)
    for row in rows:
        by_driver[row.driver_id].append(row)

    for driver_rows in by_driver.values():
        # Compute race_total per event (already set), then running sum in round order
        by_event: dict[int, list[FantasyDriverScore]] = defaultdict(list)
        for row in driver_rows:
            by_event[row.event_id].append(row)

        running = 0
        for event_id in sorted(by_event, key=lambda eid: event_order.get(eid, 9999)):
            race_total = by_event[event_id][0].race_total
            running += race_total
            for row in by_event[event_id]:
                row.season_total = running


def _stamp_constructor_season_totals(rows: list[FantasyConstructorScore], events: list[Event]) -> None:
    event_order = {e.id: i for i, e in enumerate(events)}
    by_team: dict[int, list[FantasyConstructorScore]] = defaultdict(list)
    for row in rows:
        by_team[row.team_id].append(row)

    for team_rows in by_team.values():
        by_event: dict[int, list[FantasyConstructorScore]] = defaultdict(list)
        for row in team_rows:
            by_event[row.event_id].append(row)

        running = 0
        for event_id in sorted(by_event, key=lambda eid: event_order.get(eid, 9999)):
            race_total = by_event[event_id][0].race_total
            running += race_total
            for row in by_event[event_id]:
                row.season_total = running
