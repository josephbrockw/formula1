"""
Analytics models module.

This __init__.py imports all models to maintain backwards compatibility.
Existing code can still use: from analytics.models import Season, Driver, etc.

Model organization:
- base.py: Core entities (Season, Driver, Team, User, CurrentLineup)
- events.py: Race weekends and sessions (Race, Session, etc.)
- fantasy.py: F1 Fantasy game data from CSV imports (Snapshots, Performance, Scores)
- telemetry.py: FastF1 API data (Sessions, Laps, Telemetry, Weather) - to be implemented
"""

# Import base models
from .base import (
    User,
    Season,
    Team,
    Driver,
    CurrentLineup,
)

# Import event models
from .events import (
    Race,
)

# Import fantasy models
from .fantasy import (
    DriverSnapshot,
    ConstructorSnapshot,
    DriverRacePerformance,
    DriverEventScore,
    ConstructorRacePerformance,
    ConstructorEventScore,
)

# Import telemetry models (when implemented)
# from .telemetry import ...

# Explicit exports for clarity
__all__ = [
    # Base models (base.py)
    'User',
    'Season',
    'Team',
    'Driver',
    'CurrentLineup',
    # Event models (events.py)
    'Race',
    # Fantasy models (fantasy.py)
    'DriverSnapshot',
    'ConstructorSnapshot',
    'DriverRacePerformance',
    'DriverEventScore',
    'ConstructorRacePerformance',
    'ConstructorEventScore',
    # Telemetry models (telemetry.py - to be added)
]
