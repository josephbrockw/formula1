"""
Analytics models module.

This __init__.py imports all models to maintain backwards compatibility.
Existing code can still use: from analytics.models import Season, Driver, etc.

Model organization:
- base.py: Core entities (Season, Driver, Team, User, CurrentLineup)
- events.py: Circuits, races, sessions, weather (Circuit, Corner, MarshalLight, MarshalSector, Race, Session, SessionWeather)
- fantasy.py: F1 Fantasy game data from CSV imports (Snapshots, Performance, Scores)
- pipeline.py: Internal pipeline infrastructure (SessionLoadStatus)
- telemetry.py: FastF1 API data (Laps, Telemetry, PitStops) - to be implemented

Note: api_logs.py was removed - we now use reactive rate limiting instead of tracking HTTP requests
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
    Circuit,
    Corner,
    MarshalLight,
    MarshalSector,
    Race,
    Session,
    SessionWeather,
    SessionResult,
)

# Import pipeline models
from .pipeline import (
    SessionLoadStatus,
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

# Import telemetry models
from .telemetry import (
    Lap,
    Telemetry,
    PitStop,
)

# Explicit exports for clarity
__all__ = [
    # Base models (base.py)
    'User',
    'Season',
    'Team',
    'Driver',
    'CurrentLineup',
    # Event models (events.py)
    'Circuit',
    'Corner',
    'MarshalLight',
    'MarshalSector',
    'Race',
    'Session',
    'SessionWeather',
    'SessionResult',
    # Pipeline models (pipeline.py)
    'SessionLoadStatus',
    # Fantasy models (fantasy.py)
    'DriverSnapshot',
    'ConstructorSnapshot',
    'DriverRacePerformance',
    'DriverEventScore',
    'ConstructorRacePerformance',
    'ConstructorEventScore',
    # Telemetry models (telemetry.py)
    'Lap',
    'Telemetry',
    'PitStop',
]
