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
from .base import Driver, Team
from .events import Session


class Lap(models.Model):
    """
    Individual lap data for a driver in a session.
    
    Stores lap times, sector times, tire compound, and lap-specific
    telemetry from FastF1. Each lap represents one complete lap around
    the circuit by a specific driver.
    
    Key fields:
    - Lap time and sector times (1, 2, 3)
    - Tire compound and age
    - Track status (clean, yellow flag, etc.)
    - Position and pit status
    - Speed traps
    
    Data source: FastF1 Laps DataFrame
    """
    
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name='laps',
        help_text="The session this lap belongs to"
    )
    
    driver = models.ForeignKey(
        Driver,
        on_delete=models.CASCADE,
        related_name='laps',
        help_text="Driver who completed this lap"
    )
    
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name='laps',
        null=True,
        blank=True,
        help_text="Team the driver was racing for"
    )
    
    # Lap identification
    lap_number = models.IntegerField(
        help_text="Lap number in session (1, 2, 3, ...)"
    )
    
    driver_number = models.CharField(
        max_length=10,
        help_text="Driver's racing number"
    )
    
    # Lap times (stored as total seconds)
    lap_time = models.FloatField(
        null=True,
        blank=True,
        help_text="Total lap time in seconds (null if lap not completed)"
    )
    
    sector_1_time = models.FloatField(
        null=True,
        blank=True,
        help_text="Sector 1 time in seconds"
    )
    
    sector_2_time = models.FloatField(
        null=True,
        blank=True,
        help_text="Sector 2 time in seconds"
    )
    
    sector_3_time = models.FloatField(
        null=True,
        blank=True,
        help_text="Sector 3 time in seconds"
    )
    
    # Tire data
    compound = models.CharField(
        max_length=20,
        blank=True,
        help_text="Tire compound (SOFT, MEDIUM, HARD, INTERMEDIATE, WET)"
    )
    
    tire_life = models.IntegerField(
        null=True,
        blank=True,
        help_text="Number of laps on this set of tires"
    )
    
    fresh_tire = models.BooleanField(
        default=False,
        help_text="Whether this lap was on fresh tires"
    )
    
    # Track status
    track_status = models.CharField(
        max_length=10,
        blank=True,
        help_text="Track status code (1=clear, 2=yellow, 4=safety car, etc.)"
    )
    
    # Position and classification
    position = models.IntegerField(
        null=True,
        blank=True,
        help_text="Position at end of lap"
    )
    
    # Pit status
    pit_out_time = models.FloatField(
        null=True,
        blank=True,
        help_text="Time of pit exit in seconds since session start"
    )
    
    pit_in_time = models.FloatField(
        null=True,
        blank=True,
        help_text="Time of pit entry in seconds since session start"
    )
    
    # Speed traps
    speed_i1 = models.FloatField(
        null=True,
        blank=True,
        help_text="Speed at speed trap 1 (km/h)"
    )
    
    speed_i2 = models.FloatField(
        null=True,
        blank=True,
        help_text="Speed at speed trap 2 (km/h)"
    )
    
    speed_fl = models.FloatField(
        null=True,
        blank=True,
        help_text="Speed at finish line (km/h)"
    )
    
    speed_st = models.FloatField(
        null=True,
        blank=True,
        help_text="Speed trap (longest straight) in km/h"
    )
    
    # Lap validity
    is_personal_best = models.BooleanField(
        default=False,
        help_text="Whether this was driver's fastest lap in session"
    )
    
    is_accurate = models.BooleanField(
        default=True,
        help_text="Whether lap timing is accurate (from FastF1)"
    )
    
    # Lap start/end timestamps
    lap_start_time = models.FloatField(
        null=True,
        blank=True,
        help_text="Lap start time in seconds since session start"
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['session', 'lap_number', 'driver']
        unique_together = [['session', 'driver', 'lap_number']]
        indexes = [
            models.Index(fields=['session', 'driver']),
            models.Index(fields=['session', 'lap_number']),
            models.Index(fields=['compound']),
            models.Index(fields=['lap_time']),
            models.Index(fields=['is_personal_best']),
        ]
        verbose_name = 'Lap'
        verbose_name_plural = 'Laps'
    
    def __str__(self):
        time_str = f"{self.lap_time:.3f}s" if self.lap_time else "DNF"
        return f"{self.session} - {self.driver.full_name} - Lap {self.lap_number} - {time_str}"
    
    @property
    def lap_time_formatted(self):
        """Format lap time as MM:SS.mmm"""
        if not self.lap_time:
            return "--:--:---"
        
        minutes = int(self.lap_time // 60)
        seconds = self.lap_time % 60
        return f"{minutes:02d}:{seconds:06.3f}"


class Telemetry(models.Model):
    """
    High-frequency telemetry data for a lap.
    
    Stores detailed car telemetry including speed, throttle, brake,
    steering, gear, RPM, and DRS activation at specific distances
    around the track.
    
    Note: Telemetry is stored per-lap but contains many data points.
    For optimization, we aggregate key metrics rather than storing
    every single telemetry sample (which can be 100s per lap).
    
    Aggregated fields:
    - Max/min/avg speed
    - Throttle percentages
    - Brake usage
    - DRS activation
    
    For detailed analysis, use FastF1 directly and cache results.
    
    Data source: FastF1 Car Telemetry
    """
    
    lap = models.OneToOneField(
        Lap,
        on_delete=models.CASCADE,
        related_name='telemetry',
        help_text="The lap this telemetry belongs to"
    )
    
    # Speed metrics (km/h)
    max_speed = models.FloatField(
        null=True,
        blank=True,
        help_text="Maximum speed during lap (km/h)"
    )
    
    min_speed = models.FloatField(
        null=True,
        blank=True,
        help_text="Minimum speed during lap (km/h)"
    )
    
    avg_speed = models.FloatField(
        null=True,
        blank=True,
        help_text="Average speed during lap (km/h)"
    )
    
    # Throttle metrics (0-100%)
    throttle_pct_full = models.FloatField(
        null=True,
        blank=True,
        help_text="Percentage of lap at full throttle (100%)"
    )
    
    throttle_pct_avg = models.FloatField(
        null=True,
        blank=True,
        help_text="Average throttle application (%)"
    )
    
    # Brake metrics
    brake_pct = models.FloatField(
        null=True,
        blank=True,
        help_text="Percentage of lap spent braking"
    )
    
    # Gear usage
    max_gear = models.IntegerField(
        null=True,
        blank=True,
        help_text="Highest gear used during lap"
    )
    
    # RPM
    max_rpm = models.IntegerField(
        null=True,
        blank=True,
        help_text="Maximum engine RPM during lap"
    )
    
    avg_rpm = models.IntegerField(
        null=True,
        blank=True,
        help_text="Average engine RPM during lap"
    )
    
    # DRS
    drs_activations = models.IntegerField(
        default=0,
        help_text="Number of times DRS was activated during lap"
    )
    
    drs_distance = models.FloatField(
        null=True,
        blank=True,
        help_text="Total distance with DRS active (meters)"
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Telemetry'
        verbose_name_plural = 'Telemetry'
        indexes = [
            models.Index(fields=['max_speed']),
            models.Index(fields=['throttle_pct_full']),
        ]
    
    def __str__(self):
        return f"Telemetry for {self.lap}"


class PitStop(models.Model):
    """
    Pit stop data for a driver during a session.
    
    Records pit stop timing, duration, and tire changes from FastF1.
    Critical for race strategy analysis.
    
    Data source: FastF1 Pit Stops
    """
    
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name='pit_stops',
        help_text="The session this pit stop occurred in"
    )
    
    driver = models.ForeignKey(
        Driver,
        on_delete=models.CASCADE,
        related_name='pit_stops',
        help_text="Driver who made the pit stop"
    )
    
    lap = models.ForeignKey(
        Lap,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pit_stop',
        help_text="The lap on which the pit stop occurred"
    )
    
    # Stop identification
    stop_number = models.IntegerField(
        help_text="Stop number in session for this driver (1st stop, 2nd stop, etc.)"
    )
    
    lap_number = models.IntegerField(
        help_text="Lap number when pit stop occurred"
    )
    
    # Timing
    pit_in_time = models.FloatField(
        null=True,
        blank=True,
        help_text="Time of pit entry (seconds since session start)"
    )
    
    pit_out_time = models.FloatField(
        null=True,
        blank=True,
        help_text="Time of pit exit (seconds since session start)"
    )
    
    pit_duration = models.FloatField(
        null=True,
        blank=True,
        help_text="Total pit stop duration in seconds"
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['session', 'lap_number', 'driver']
        unique_together = [['session', 'driver', 'stop_number']]
        indexes = [
            models.Index(fields=['session', 'driver']),
            models.Index(fields=['lap_number']),
            models.Index(fields=['pit_duration']),
        ]
        verbose_name = 'Pit Stop'
        verbose_name_plural = 'Pit Stops'
    
    def __str__(self):
        duration_str = f"{self.pit_duration:.3f}s" if self.pit_duration else "N/A"
        return f"{self.session} - {self.driver.full_name} - Stop #{self.stop_number} - {duration_str}"
