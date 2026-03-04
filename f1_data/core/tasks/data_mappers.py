from __future__ import annotations

from datetime import datetime

import pandas as pd

from core.models import Driver, Lap, Session, SessionResult, Team, WeatherSample


def _to_duration(value) -> object:
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _to_int_or_none(value) -> int | None:
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return int(value)


def _to_str_or_none(value) -> str | None:
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def map_laps(
    laps_df: pd.DataFrame,
    session_model: Session,
    driver_lookup: dict[str, Driver],
) -> tuple[list[Lap], list[str]]:
    laps = []
    skipped: set[str] = set()
    for _, row in laps_df.iterrows():
        driver_code = row["Driver"]
        if driver_code not in driver_lookup:
            skipped.add(driver_code)
            continue
        pit_in = _to_duration(row["PitInTime"])
        pit_out = _to_duration(row["PitOutTime"])
        laps.append(
            Lap(
                session=session_model,
                driver=driver_lookup[driver_code],
                lap_number=int(row["LapNumber"]),
                lap_time=_to_duration(row["LapTime"]),
                sector1_time=_to_duration(row["Sector1Time"]),
                sector2_time=_to_duration(row["Sector2Time"]),
                sector3_time=_to_duration(row["Sector3Time"]),
                pit_in_time=pit_in,
                pit_out_time=pit_out,
                is_pit_in_lap=pit_in is not None,
                is_pit_out_lap=pit_out is not None,
                stint=_to_int_or_none(row["Stint"]),
                compound=_to_str_or_none(row["Compound"]),
                tyre_life=_to_int_or_none(row["TyreLife"]),
                track_status=_to_str_or_none(row["TrackStatus"]),
                position=_to_int_or_none(row["Position"]),
                is_personal_best=bool(row["IsPersonalBest"]),
                is_accurate=bool(row["IsAccurate"]),
            )
        )
    return laps, sorted(skipped)


def map_session_results(
    results_df: pd.DataFrame,
    session_model: Session,
    driver_lookup: dict[str, Driver],
    team_lookup: dict[str, Team],
) -> tuple[list[SessionResult], list[str]]:
    results = []
    skipped: set[str] = set()
    for _, row in results_df.iterrows():
        driver_code = row["Abbreviation"]
        team_name = row["TeamName"]
        if driver_code not in driver_lookup or team_name not in team_lookup:
            skipped.add(driver_code)
            continue
        results.append(
            SessionResult(
                session=session_model,
                driver=driver_lookup[driver_code],
                team=team_lookup[team_name],
                position=_to_int_or_none(row["Position"]),
                classified_position=str(row["ClassifiedPosition"]),
                grid_position=_to_int_or_none(row["GridPosition"]),
                status=str(row["Status"]),
                points=0.0 if pd.isna(row["Points"]) else float(row["Points"]),
                time=_to_duration(row["Time"]),
                fastest_lap_rank=_to_int_or_none(row.get("FastestLapRank", float("nan"))),
            )
        )
    return results, sorted(skipped)


def map_weather(
    weather_df: pd.DataFrame | None,
    session_model: Session,
    session_date: datetime,
) -> list[WeatherSample]:
    if weather_df is None or weather_df.empty:
        return []
    samples = []
    for _, row in weather_df.iterrows():
        samples.append(
            WeatherSample(
                session=session_model,
                timestamp=session_date + row["Time"],
                air_temp=float(row["AirTemp"]),
                track_temp=float(row["TrackTemp"]),
                humidity=float(row["Humidity"]),
                pressure=float(row["Pressure"]),
                wind_speed=float(row["WindSpeed"]),
                wind_direction=int(row["WindDirection"]),
                rainfall=bool(row["Rainfall"]),
            )
        )
    return samples
