from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd

# (code, full_name, number, team)
_DRIVERS = [
    ("VER", "Max Verstappen", "1", "Red Bull Racing"),
    ("HAM", "Lewis Hamilton", "44", "Mercedes"),
    ("LEC", "Charles Leclerc", "16", "Ferrari"),
    ("NOR", "Lando Norris", "4", "McLaren"),
    ("SAI", "Carlos Sainz", "55", "Ferrari"),
]


def make_laps_dataframe(num_drivers: int = 3, num_laps: int = 5, **overrides) -> pd.DataFrame:
    rows = []
    for code, _, number, _ in _DRIVERS[:num_drivers]:
        for lap in range(1, num_laps + 1):
            rows.append(
                {
                    "Driver": code,
                    "DriverNumber": number,
                    "LapNumber": lap,
                    "LapTime": pd.Timedelta(seconds=90 + lap * 0.1),
                    "Sector1Time": pd.Timedelta(seconds=28),
                    "Sector2Time": pd.Timedelta(seconds=32),
                    "Sector3Time": pd.Timedelta(seconds=30),
                    "PitInTime": pd.NaT,
                    "PitOutTime": pd.NaT,
                    "Stint": 1,
                    "Compound": "SOFT",
                    "TyreLife": float(lap),
                    "Position": 1.0,
                    "TrackStatus": "1",
                    "IsPersonalBest": False,
                    "IsAccurate": True,
                }
            )
    df = pd.DataFrame(rows)
    for col, val in overrides.items():
        df[col] = val
    return df


def make_results_dataframe(
    num_drivers: int = 3, include_dnf: bool = False, **overrides
) -> pd.DataFrame:
    rows = []
    for i, (code, full_name, number, team) in enumerate(_DRIVERS[:num_drivers]):
        position = float(i + 1)
        classified = str(i + 1)
        status = "Finished"
        if include_dnf and i == num_drivers - 1:
            position = float("nan")
            classified = "R"
            status = "Engine"
        rows.append(
            {
                "DriverNumber": number,
                "Abbreviation": code,
                "FullName": full_name,
                "TeamName": team,
                "Position": position,
                "ClassifiedPosition": classified,
                "GridPosition": i + 1,
                "Status": status,
                "Points": max(0.0, 25.0 - i * 7),
                "Time": pd.NaT if i == 0 else pd.Timedelta(seconds=5 * i),
                "FastestLapTime": pd.NaT,
                "FastestLapRank": float("nan"),
            }
        )
    df = pd.DataFrame(rows)
    for col, val in overrides.items():
        df[col] = val
    return df


def make_weather_dataframe(
    num_samples: int = 5, include_rain: bool = False, **overrides
) -> pd.DataFrame:
    rows = []
    for i in range(num_samples):
        rows.append(
            {
                "Time": pd.Timedelta(minutes=i * 5),
                "AirTemp": 25.0 + i * 0.1,
                "TrackTemp": 35.0 + i * 0.2,
                "Humidity": 55.0,
                "Pressure": 1013.0,
                "WindSpeed": 2.5,
                "WindDirection": 180,
                "Rainfall": include_rain and i > 2,
            }
        )
    df = pd.DataFrame(rows)
    for col, val in overrides.items():
        df[col] = val
    return df


def make_session_mock(
    num_drivers: int = 1,
    num_laps: int = 5,
    session_date: datetime | None = None,
) -> MagicMock:
    mock = MagicMock()
    mock.laps = make_laps_dataframe(num_drivers=num_drivers, num_laps=num_laps)
    mock.results = make_results_dataframe(num_drivers=num_drivers)
    mock.weather_data = make_weather_dataframe()
    mock.date = session_date or datetime(2024, 3, 2, 14, 0, 0, tzinfo=timezone.utc)
    return mock


def make_schedule_dataframe(
    year: int = 2024,
    num_events: int = 1,
    sessions: list[str] | None = None,
    event_format: str = "conventional",
) -> pd.DataFrame:
    sessions = sessions or ["Practice 1", "Practice 2", "Practice 3", "Qualifying", "Race"]
    rows = []
    for i in range(num_events):
        row: dict = {
            "RoundNumber": i + 1,
            "EventName": f"Grand Prix {i + 1}",
            "Country": "Australia",
            "Location": f"City{i + 1}",
            "EventDate": pd.Timestamp(f"{year}-03-{24 + i}"),
            "EventFormat": event_format,
        }
        for slot in range(1, 6):
            idx = slot - 1
            if idx < len(sessions):
                row[f"Session{slot}"] = sessions[idx]
                row[f"Session{slot}Date"] = pd.Timestamp(f"{year}-03-{22 + i} {10 + slot}:00:00", tz="UTC")
            else:
                row[f"Session{slot}"] = ""
                row[f"Session{slot}Date"] = pd.NaT
        rows.append(row)
    return pd.DataFrame(rows)
