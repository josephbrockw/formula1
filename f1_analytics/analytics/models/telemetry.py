"""
FastF1 telemetry and session models.

These models store detailed race data from the FastF1 API including:
- Session information (Practice, Qualifying, Race)
- Lap data and sector times
- Telemetry data (speed, throttle, brake, gear, RPM, DRS)
- Weather conditions
- Pit stops and driver status

Data source: FastF1 Python API (https://docs.fastf1.dev/)
"""

from django.db import models
from .base import Season, Driver, Team
from .events import Race

# TODO: Add FastF1 telemetry models here
# Examples:
# - Session (FP1, FP2, FP3, Qualifying, Sprint, Race)
#   - Links to Race model in events.py
# - LapData (lap times, sector times, tire compounds)
# - TelemetryData (speed, throttle, brake, gear per distance)
# - WeatherData (track temp, air temp, humidity, pressure)
# - PitStop (duration, lap number, tire change)
# - DriverStatus (on track, in pit, out, DNF)
