from __future__ import annotations

from datetime import date, timedelta
from datetime import datetime as dt
from datetime import timezone as tz

from core.models import Circuit, Driver, Event, Lap, Season, Session, SessionResult, Team, WeatherSample
from predictions.models import (
    FantasyConstructorPrice,
    FantasyConstructorScore,
    FantasyDriverPrice,
    FantasyDriverScore,
    LineupRecommendation,
    MyLineup,
)


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


def make_team(season: Season, name: str = "Red Bull Racing", code: str = "") -> Team:
    return Team.objects.create(season=season, name=name, full_name=name, code=code)


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
    compound: str | None = None,
    tyre_life: int | None = None,
    stint: int | None = None,
    sector1_seconds: float | None = None,
    sector2_seconds: float | None = None,
    sector3_seconds: float | None = None,
    is_pit_in_lap: bool = False,
    is_pit_out_lap: bool = False,
) -> Lap:
    return Lap.objects.create(
        session=session,
        driver=driver,
        lap_number=lap_number,
        lap_time=timedelta(seconds=lap_time_seconds),
        is_accurate=is_accurate,
        compound=compound,
        tyre_life=tyre_life,
        stint=stint,
        sector1_time=timedelta(seconds=sector1_seconds) if sector1_seconds is not None else None,
        sector2_time=timedelta(seconds=sector2_seconds) if sector2_seconds is not None else None,
        sector3_time=timedelta(seconds=sector3_seconds) if sector3_seconds is not None else None,
        is_pit_in_lap=is_pit_in_lap,
        is_pit_out_lap=is_pit_out_lap,
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


def make_driver_price(
    driver: Driver,
    event: Event,
    price: float = 10.0,
) -> FantasyDriverPrice:
    return FantasyDriverPrice.objects.create(
        driver=driver,
        event=event,
        snapshot_date=event.event_date,
        price=price,
        price_change=0.0,
        pick_percentage=10.0,
        season_fantasy_points=0,
    )


def make_constructor_price(
    team: Team,
    event: Event,
    price: float = 15.0,
) -> FantasyConstructorPrice:
    return FantasyConstructorPrice.objects.create(
        team=team,
        event=event,
        snapshot_date=event.event_date,
        price=price,
        price_change=0.0,
        pick_percentage=10.0,
        season_fantasy_points=0,
    )


_WEATHER_TS_BASE = dt(2024, 1, 1, 12, 0, 0, tzinfo=tz.utc)


def make_weather_sample(
    session: Session,
    rainfall: bool = False,
    track_temp: float = 35.0,
) -> WeatherSample:
    seq = WeatherSample.objects.filter(session=session).count()
    return WeatherSample.objects.create(
        session=session,
        timestamp=_WEATHER_TS_BASE + timedelta(minutes=seq),
        air_temp=25.0,
        track_temp=track_temp,
        humidity=50.0,
        pressure=1013.0,
        wind_speed=5.0,
        wind_direction=180,
        rainfall=rainfall,
    )


def make_constructor_score(
    team: Team,
    event: Event,
    race_total: int,
    event_type: str = "race",
    scoring_item: str = "Race Position",
    points: int = 1,
) -> FantasyConstructorScore:
    return FantasyConstructorScore.objects.create(
        team=team,
        event=event,
        event_type=event_type,
        scoring_item=scoring_item,
        points=points,
        race_total=race_total,
        season_total=race_total,
    )


def make_my_lineup(
    event: Event,
    drivers: list[Driver],
    constructors: list[Team],
    drs_driver: Driver | None = None,
    actual_points: float | None = None,
) -> MyLineup:
    assert len(drivers) == 5
    assert len(constructors) == 2
    return MyLineup.objects.create(
        event=event,
        driver_1=drivers[0],
        driver_2=drivers[1],
        driver_3=drivers[2],
        driver_4=drivers[3],
        driver_5=drivers[4],
        drs_boost_driver=drs_driver or drivers[0],
        constructor_1=constructors[0],
        constructor_2=constructors[1],
        actual_points=actual_points,
    )


def make_lineup_recommendation(
    event: Event,
    drivers: list[Driver],
    constructors: list[Team],
    drs_driver: Driver | None = None,
    predicted_points: float = 100.0,
    actual_points: float | None = None,
    oracle_actual_points: float | None = None,
    strategy_type: str = "single_race",
    model_version: str = "xgb_v2",
) -> LineupRecommendation:
    assert len(drivers) == 5
    assert len(constructors) == 2
    from decimal import Decimal
    return LineupRecommendation.objects.create(
        event=event,
        driver_1=drivers[0],
        driver_2=drivers[1],
        driver_3=drivers[2],
        driver_4=drivers[3],
        driver_5=drivers[4],
        drs_boost_driver=drs_driver or drivers[0],
        constructor_1=constructors[0],
        constructor_2=constructors[1],
        total_cost=Decimal("95.0"),
        predicted_points=predicted_points,
        actual_points=actual_points,
        oracle_actual_points=oracle_actual_points,
        strategy_type=strategy_type,
        model_version=model_version,
    )
