from __future__ import annotations

import datetime
import time
import traceback

import pandas as pd
from django.db import transaction
from django.utils import timezone

from core.models import (
    Circuit,
    CollectionRun,
    Driver,
    Event,
    Lap,
    Season,
    Session,
    SessionCollectionStatus,
    SessionResult,
    Team,
    WeatherSample,
)
from core.tasks.data_mappers import map_laps, map_session_results, map_weather
from core.tasks.fastf1_loader import get_event_schedule, load_session
from core.tasks.gap_detector import find_uncollected_sessions, get_collection_summary
from core.tasks.notifier import send_slack_notification

_SESSION_NAME_MAP = {
    "Practice 1": "FP1",
    "Practice 2": "FP2",
    "Practice 3": "FP3",
    "Qualifying": "Q",
    "Sprint Qualifying": "SQ",
    "Sprint Shootout": "SQ",
    "Sprint": "S",
    "Race": "R",
}


def collect_all(
    years: list[int] | None,
    force_recollect: bool,
    stdout,
    round_number: int | None = None,
) -> None:
    if years is None:
        years = list(range(2018, datetime.date.today().year + 1))

    run = CollectionRun.objects.create(status="running")

    for year in years:
        _sync_schedule(year)

    sessions = _sessions_to_collect(years, force_recollect, round_number).order_by(
        "event__season__year", "event__round_number", "session_type"
    )
    total = sessions.count()
    stdout.write(f"Starting collection. {total} sessions to process.")

    for i, session in enumerate(sessions):
        stdout.write(f"[{i + 1}/{total}] {session.event.event_name} — {session.session_type}")
        _process_session(run, session, i, total, stdout)

    run.status = "completed"
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "finished_at"])
    send_slack_notification(
        f"Collection complete. {run.sessions_processed} processed, {run.sessions_skipped} failed.",
        level="info",
    )


def collect_single_session(session_model: Session) -> None:
    scs, _ = SessionCollectionStatus.objects.get_or_create(session=session_model)
    scs.status = "collecting"
    scs.save(update_fields=["status"])

    event = session_model.event
    ff1_session = load_session(event.season.year, event.round_number, session_model.session_type)

    driver_lookup, team_lookup = _sync_drivers_teams(ff1_session.results, event.season)
    results, results_skipped = map_session_results(
        ff1_session.results, session_model, driver_lookup, team_lookup
    )
    laps, laps_skipped = map_laps(ff1_session.laps, session_model, driver_lookup)
    weather = map_weather(ff1_session.weather_data, session_model, ff1_session.date)

    with transaction.atomic():
        SessionResult.objects.filter(session=session_model).delete()
        Lap.objects.filter(session=session_model).delete()
        WeatherSample.objects.filter(session=session_model).delete()
        SessionResult.objects.bulk_create(results)
        Lap.objects.bulk_create(laps, batch_size=500)
        WeatherSample.objects.bulk_create(weather)

    skipped = sorted(set(results_skipped + laps_skipped))
    scs.status = "completed"
    scs.collected_at = timezone.now()
    scs.result_count = len(results)
    scs.lap_count = len(laps)
    scs.weather_sample_count = len(weather)
    scs.error_message = f"Skipped drivers: {skipped}" if skipped else None
    scs.save()


def _sync_schedule(year: int) -> None:
    schedule = get_event_schedule(year)
    schedule = schedule[schedule["EventFormat"] != "testing"]
    season, _ = Season.objects.get_or_create(year=year)

    for _, row in schedule.iterrows():
        circuit, _ = Circuit.objects.get_or_create(
            circuit_key=row["Location"],
            defaults={"name": row["EventName"], "country": row["Country"], "city": row["Location"]},
        )
        event, _ = Event.objects.get_or_create(
            season=season,
            round_number=int(row["RoundNumber"]),
            defaults={
                "event_name": row["EventName"],
                "country": row["Country"],
                "circuit": circuit,
                "event_date": row["EventDate"].date(),
                "event_format": row["EventFormat"],
            },
        )
        for slot in range(1, 6):
            name = row.get(f"Session{slot}", "")
            if not name or pd.isna(name):
                continue
            session_type = _SESSION_NAME_MAP.get(str(name))
            if session_type is None:
                continue
            date_val = row.get(f"Session{slot}Date")
            Session.objects.get_or_create(
                event=event,
                session_type=session_type,
                defaults={"date": date_val if not pd.isna(date_val) else None},
            )


def _sync_drivers_teams(
    results_df: pd.DataFrame, season: Season
) -> tuple[dict[str, Driver], dict[str, Team]]:
    team_lookup: dict[str, Team] = {}
    driver_lookup: dict[str, Driver] = {}

    for _, row in results_df.iterrows():
        team_name = str(row["TeamName"])
        if team_name not in team_lookup:
            team, _ = Team.objects.get_or_create(
                season=season,
                name=team_name,
                defaults={"full_name": team_name},
            )
            team_lookup[team_name] = team

        code = str(row["Abbreviation"])
        driver, _ = Driver.objects.get_or_create(
            season=season,
            code=code,
            defaults={
                "full_name": str(row["FullName"]),
                "driver_number": int(row["DriverNumber"]),
                "team": team_lookup[team_name],
            },
        )
        driver_lookup[code] = driver

    return driver_lookup, team_lookup


def _sessions_to_collect(
    years: list[int], force_recollect: bool, round_number: int | None = None
):
    if force_recollect:
        qs = Session.objects.filter(event__season__year__in=years)
    else:
        qs = find_uncollected_sessions().filter(event__season__year__in=years)
    if round_number is not None:
        qs = qs.filter(event__round_number=round_number)
    return qs


def _is_rate_limit(exc: Exception) -> bool:
    from requests.exceptions import HTTPError

    return (
        isinstance(exc, HTTPError)
        and getattr(getattr(exc, "response", None), "status_code", None) == 429
    )


def _process_session(
    run: CollectionRun, session: Session, i: int, total: int, stdout
) -> None:
    try:
        collect_single_session(session)
        run.sessions_processed += 1
        run.save(update_fields=["sessions_processed"])
    except Exception as exc:
        if _is_rate_limit(exc):
            _pause_for_rate_limit(run, session, i, total, stdout)
            collect_single_session(session)
            run.sessions_processed += 1
            run.save(update_fields=["sessions_processed"])
        else:
            _handle_session_error(run, session, exc, stdout)


def _pause_for_rate_limit(
    run: CollectionRun, session: Session, i: int, total: int, stdout
) -> None:
    resume_at = timezone.now() + datetime.timedelta(minutes=61)
    run.status = "paused_rate_limit"
    run.save(update_fields=["status"])
    remaining = total - (i + 1)
    summary = get_collection_summary()
    season_lines = "\n".join(
        f"  {year}: {counts['completed']}/{counts['total']} done, {counts['failed']} failed"
        for year, counts in sorted(summary.items())
    )
    msg = (
        f"Rate limited at [{i + 1}/{total}] {session.event.event_name} — {session.session_type}. "
        f"Pausing until {resume_at.strftime('%H:%M UTC')}.\n"
        f"Progress: {run.sessions_processed} processed, {run.sessions_skipped} failed, "
        f"{remaining} remaining.\n\n"
        f"Season status:\n{season_lines}"
    )
    stdout.write(f"⚠️  Rate limited. Slack notified. Pausing until {resume_at.strftime('%H:%M UTC')}.")
    send_slack_notification(msg, level="warning")
    time.sleep(61 * 60)
    run.status = "running"
    run.save(update_fields=["status"])


def _handle_session_error(
    run: CollectionRun, session: Session, exc: Exception, stdout
) -> None:
    traceback.print_exc()
    scs, _ = SessionCollectionStatus.objects.get_or_create(session=session)
    scs.status = "failed"
    scs.error_message = str(exc)
    scs.retry_count += 1
    scs.save(update_fields=["status", "error_message", "retry_count"])
    run.sessions_skipped += 1
    run.save(update_fields=["sessions_skipped"])
    send_slack_notification(
        f"Error collecting {session.event.event_name} — {session.session_type}: {exc}",
        level="error",
    )
