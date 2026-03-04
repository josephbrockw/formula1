from __future__ import annotations

import fastf1


def get_event_schedule(year: int) -> fastf1.events.EventSchedule:
    return fastf1.get_event_schedule(year)


def load_session(year: int, round_number: int, session_type: str) -> fastf1.core.Session:
    session = fastf1.get_session(year, round_number, session_type)
    session.load(laps=True, telemetry=False, weather=True, messages=False)
    return session
