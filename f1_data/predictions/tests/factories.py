from __future__ import annotations

from datetime import date, timedelta

from core.models import Circuit, Driver, Event, Lap, Season, Session, SessionResult, Team
from predictions.models import FantasyDriverScore


def make_season(year: int = 2024) -> Season:
    return Season.objects.create(year=year)


def make_circuit(key: str = "silverstone") -> Circuit:
    return Circuit.objects.create(
        circuit_key=key,
        name=f"Circuit {key}",
        country="UK",
        city="Silverstone",
        circuit_length=5.891,
        total_corners=18,
    )


def make_event(
    season: Season,
    round_number: int = 1,
    circuit: Circuit | None = None,
    event_format: str = "conventional",
    event_date: date | None = None,
) -> Event:
    circuit = circuit or make_circuit(key=f"circuit_{round_number}")
    return Event.objects.create(
        season=season,
        round_number=round_number,
        event_name=f"Grand Prix {round_number}",
        country="Country",
        circuit=circuit,
        event_date=event_date or date(2024, round_number, 1),
        event_format=event_format,
    )


def make_team(season: Season, name: str = "Red Bull Racing") -> Team:
    return Team.objects.create(season=season, name=name, full_name=name)


def make_driver(
    season: Season,
    team: Team,
    code: str = "VER",
    full_name: str = "Max Verstappen",
    driver_number: int = 1,
) -> Driver:
    return Driver.objects.create(
        season=season,
        code=code,
        full_name=full_name,
        driver_number=driver_number,
        team=team,
    )


def make_session(event: Event, session_type: str = "R") -> Session:
    return Session.objects.create(event=event, session_type=session_type)


def make_result(
    session: Session,
    driver: Driver,
    team: Team,
    position: int | None = 1,
    grid_position: int | None = 1,
    status: str = "Finished",
    points: float = 0.0,
) -> SessionResult:
    return SessionResult.objects.create(
        session=session,
        driver=driver,
        team=team,
        position=position,
        classified_position=str(position) if position else "R",
        grid_position=grid_position,
        status=status,
        points=points,
    )


def make_lap(
    session: Session,
    driver: Driver,
    lap_number: int = 1,
    lap_time_seconds: float = 90.0,
    is_accurate: bool = True,
) -> Lap:
    return Lap.objects.create(
        session=session,
        driver=driver,
        lap_number=lap_number,
        lap_time=timedelta(seconds=lap_time_seconds),
        is_accurate=is_accurate,
    )


def make_fantasy_score(
    driver: Driver,
    event: Event,
    race_total: int,
    event_type: str = "race",
    scoring_item: str = "Race Position",
    points: int = 1,
) -> FantasyDriverScore:
    return FantasyDriverScore.objects.create(
        driver=driver,
        event=event,
        event_type=event_type,
        scoring_item=scoring_item,
        points=points,
        race_total=race_total,
        season_total=race_total,
    )
