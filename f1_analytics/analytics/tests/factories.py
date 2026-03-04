"""
Shared DB object factories for tests.

Usage:
    from analytics.tests.factories import make_season, make_race, make_session, make_driver, make_circuit
"""

from datetime import timedelta
from django.utils import timezone
from analytics.models import Season, Race, Session, Driver, Circuit


def make_season(year=2025):
    return Season.objects.create(year=year, name=f'{year} Season')


def make_circuit(name='Test Circuit'):
    return Circuit.objects.create(name=name)


def make_race(season, round_number=1, circuit=None, name='Test Grand Prix'):
    return Race.objects.create(
        season=season, round_number=round_number,
        name=name, circuit=circuit,
    )


def make_session(race, session_type='Race', session_number=5, past=True):
    now = timezone.now()
    date = now.replace(hour=12) if past else now + timedelta(days=30)
    return Session.objects.create(
        race=race, session_number=session_number,
        session_type=session_type, session_date_utc=date,
    )


def make_driver(full_name='Max Verstappen', driver_number='1', abbreviation='VER'):
    parts = full_name.split()
    return Driver.objects.create(
        full_name=full_name,
        first_name=parts[0],
        last_name=parts[-1],
        driver_number=driver_number,
        abbreviation=abbreviation,
    )
