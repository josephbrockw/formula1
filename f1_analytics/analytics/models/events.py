"""
Event and session models for race weekends.

These models represent races, sessions, and event-specific data.
Used by both Fantasy CSV imports and FastF1 telemetry imports.

Structure:
- Race: Grand Prix events (shared by all data sources)
- Session: Individual sessions within a race weekend (FastF1)
- SessionResult: Session results and timing data (FastF1)
"""

from django.db import models
from .base import Season


class Circuit(models.Model):
    """
    Represents an F1 circuit/track.
    
    Circuits are reused across seasons and contain track-specific data
    from FastF1 CircuitInfo including corners, marshal positions, and rotation.
    
    Related models store detailed track geometry:
    - Corner: Turn positions and angles
    - MarshalLight: Flag/light positions
    - MarshalSector: Track segment definitions
    
    Data source: FastF1 CircuitInfo
    (http://docs.fastf1.dev/_modules/fastf1/mvapi/data.html#CircuitInfo)
    """
    name = models.CharField(
        max_length=200, 
        unique=True,
        help_text="Circuit name (e.g., 'Silverstone Circuit', 'Circuit de Monaco')"
    )
    
    rotation = models.FloatField(
        null=True,
        blank=True,
        help_text="Track rotation/orientation in degrees (for track map visualization)"
    )
    
    # TODO: Add additional fields when available from FastF1:
    # - track_length (in meters)
    # - lap_record (fastest lap time)
    # - coordinates (lat/long for circuit location)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
        verbose_name = 'Circuit'
        verbose_name_plural = 'Circuits'
    
    def __str__(self):
        return self.name


class Corner(models.Model):
    """
    Represents a corner/turn on a circuit.
    
    Stores geometric data for each numbered turn including position (X, Y),
    angle, and distance along the track. Used for track visualization and
    corner-specific performance analysis.
    
    Data source: FastF1 CircuitInfo.corners DataFrame
    """
    circuit = models.ForeignKey(
        Circuit,
        on_delete=models.CASCADE,
        related_name='corners',
        help_text="The circuit this corner belongs to"
    )
    
    number = models.IntegerField(
        help_text="Corner number (1, 2, 3, etc.)"
    )
    
    letter = models.CharField(
        max_length=10,
        blank=True,
        help_text="Corner letter designation (e.g., 'A', 'B' for chicanes)"
    )
    
    # Position coordinates
    x = models.FloatField(
        help_text="X coordinate in circuit coordinate system"
    )
    
    y = models.FloatField(
        help_text="Y coordinate in circuit coordinate system"
    )
    
    angle = models.FloatField(
        help_text="Corner angle in degrees"
    )
    
    distance = models.FloatField(
        help_text="Distance from start line in meters"
    )
    
    class Meta:
        ordering = ['circuit', 'number', 'letter']
        unique_together = [['circuit', 'number', 'letter']]
        indexes = [
            models.Index(fields=['circuit', 'number']),
        ]
        verbose_name = 'Corner'
        verbose_name_plural = 'Corners'
    
    def __str__(self):
        if self.letter:
            return f"{self.circuit.name} - Corner {self.number}{self.letter}"
        return f"{self.circuit.name} - Corner {self.number}"


class MarshalLight(models.Model):
    """
    Represents a marshal light/flag position on a circuit.
    
    Marshal lights are used to communicate track conditions to drivers.
    Positions are stored for track visualization and incident analysis.
    
    Data source: FastF1 CircuitInfo.marshal_lights DataFrame
    """
    circuit = models.ForeignKey(
        Circuit,
        on_delete=models.CASCADE,
        related_name='marshal_lights',
        help_text="The circuit this marshal light belongs to"
    )
    
    number = models.IntegerField(
        help_text="Marshal light number/ID"
    )
    
    letter = models.CharField(
        max_length=10,
        blank=True,
        help_text="Marshal light letter designation"
    )
    
    # Position coordinates
    x = models.FloatField(
        help_text="X coordinate in circuit coordinate system"
    )
    
    y = models.FloatField(
        help_text="Y coordinate in circuit coordinate system"
    )
    
    angle = models.FloatField(
        help_text="Light orientation angle in degrees"
    )
    
    distance = models.FloatField(
        help_text="Distance from start line in meters"
    )
    
    class Meta:
        ordering = ['circuit', 'number', 'letter']
        unique_together = [['circuit', 'number', 'letter']]
        indexes = [
            models.Index(fields=['circuit', 'distance']),
        ]
        verbose_name = 'Marshal Light'
        verbose_name_plural = 'Marshal Lights'
    
    def __str__(self):
        if self.letter:
            return f"{self.circuit.name} - Light {self.number}{self.letter}"
        return f"{self.circuit.name} - Light {self.number}"


class MarshalSector(models.Model):
    """
    Represents a marshal sector (track segment) on a circuit.
    
    Marshal sectors divide the track into segments for race control and
    safety management. Each sector has assigned marshals who monitor
    that portion of the track.
    
    Data source: FastF1 CircuitInfo.marshal_sectors DataFrame
    """
    circuit = models.ForeignKey(
        Circuit,
        on_delete=models.CASCADE,
        related_name='marshal_sectors',
        help_text="The circuit this marshal sector belongs to"
    )
    
    number = models.IntegerField(
        help_text="Marshal sector number/ID"
    )
    
    letter = models.CharField(
        max_length=10,
        blank=True,
        help_text="Marshal sector letter designation"
    )
    
    # Position coordinates (sector start/reference point)
    x = models.FloatField(
        help_text="X coordinate in circuit coordinate system"
    )
    
    y = models.FloatField(
        help_text="Y coordinate in circuit coordinate system"
    )
    
    angle = models.FloatField(
        help_text="Sector orientation angle in degrees"
    )
    
    distance = models.FloatField(
        help_text="Distance from start line in meters"
    )
    
    class Meta:
        ordering = ['circuit', 'number', 'letter']
        unique_together = [['circuit', 'number', 'letter']]
        indexes = [
            models.Index(fields=['circuit', 'distance']),
        ]
        verbose_name = 'Marshal Sector'
        verbose_name_plural = 'Marshal Sectors'
    
    def __str__(self):
        if self.letter:
            return f"{self.circuit.name} - Sector {self.number}{self.letter}"
        return f"{self.circuit.name} - Sector {self.number}"


class Race(models.Model):
    """
    Represents an individual Grand Prix event in a season.
    Used to normalize race references and support track-specific analysis.
    
    Shared by:
    - Fantasy CSV imports (performance data)
    - FastF1 imports (telemetry, lap times, weather)
    """
    
    # Event format choices
    FORMAT_CONVENTIONAL = 'conventional'
    FORMAT_SPRINT = 'sprint'
    FORMAT_TESTING = 'testing'
    
    EVENT_FORMAT_CHOICES = [
        (FORMAT_CONVENTIONAL, 'Conventional Weekend'),
        (FORMAT_SPRINT, 'Sprint Weekend'),
        (FORMAT_TESTING, 'Testing'),
    ]
    
    # Core fields (used by Fantasy CSV imports)
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name='races')
    name = models.CharField(max_length=100, help_text="e.g., 'Bahrain', 'Australia', 'Monaco'")
    round_number = models.IntegerField(
        help_text="Race number in season (1 for first race, 2 for second, etc.)"
    )
    race_date = models.DateField(null=True, blank=True, help_text="Main race date")
    circuit = models.ForeignKey(
        Circuit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='races',
        help_text="The circuit where this race takes place"
    )
    country = models.CharField(max_length=100, blank=True)
    
    # FastF1 additional fields
    location = models.CharField(
        max_length=100, 
        blank=True,
        help_text="City/location name (e.g., 'Melbourne', 'Silverstone')"
    )
    official_event_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Full official event name from FIA"
    )
    event_format = models.CharField(
        max_length=20,
        choices=EVENT_FORMAT_CHOICES,
        default=FORMAT_CONVENTIONAL,
        help_text="Weekend format (conventional, sprint, testing)"
    )
    event_date = models.DateField(
        null=True,
        blank=True,
        help_text="Official event date from FastF1 (usually race day)"
    )
    f1_api_support = models.BooleanField(
        default=True,
        help_text="Whether FastF1 API has data for this event"
    )
    
    class Meta:
        ordering = ['season', 'round_number']
        unique_together = [['season', 'name'], ['season', 'round_number']]
        indexes = [
            models.Index(fields=['season', 'round_number']),
            models.Index(fields=['name']),
            models.Index(fields=['event_format']),
            models.Index(fields=['circuit']),
        ]
    
    def __str__(self):
        return f"{self.season.year} {self.name} GP (Round {self.round_number})"


class Session(models.Model):
    """
    Individual session within a race weekend.
    
    Stores session timing data from FastF1 including:
    - Practice sessions (FP1, FP2, FP3)
    - Qualifying
    - Sprint Qualifying (Sprint Shootout)
    - Sprint Race
    - Main Race
    
    Each Race can have 3-5 sessions depending on the weekend format.
    """
    
    # Session type choices (matching FastF1 naming)
    TYPE_PRACTICE_1 = 'Practice 1'
    TYPE_PRACTICE_2 = 'Practice 2'
    TYPE_PRACTICE_3 = 'Practice 3'
    TYPE_QUALIFYING = 'Qualifying'
    TYPE_SPRINT_QUALIFYING = 'Sprint Qualifying'
    TYPE_SPRINT = 'Sprint'
    TYPE_RACE = 'Race'
    
    SESSION_TYPE_CHOICES = [
        (TYPE_PRACTICE_1, 'Free Practice 1'),
        (TYPE_PRACTICE_2, 'Free Practice 2'),
        (TYPE_PRACTICE_3, 'Free Practice 3'),
        (TYPE_QUALIFYING, 'Qualifying'),
        (TYPE_SPRINT_QUALIFYING, 'Sprint Qualifying'),
        (TYPE_SPRINT, 'Sprint Race'),
        (TYPE_RACE, 'Race'),
    ]
    
    race = models.ForeignKey(
        Race, 
        on_delete=models.CASCADE, 
        related_name='sessions',
        help_text="The race weekend this session belongs to"
    )
    
    session_type = models.CharField(
        max_length=30,
        choices=SESSION_TYPE_CHOICES,
        help_text="Type of session (Practice 1, Qualifying, Race, etc.)"
    )
    
    session_number = models.IntegerField(
        help_text="Session number in weekend (1-5, matching FastF1 Session1-Session5)"
    )
    
    # Session timing (stored in both local and UTC)
    session_date_local = models.CharField(
        max_length=100,
        blank=True,
        help_text="Session date/time in local timezone (as string from FastF1)"
    )
    
    session_date_utc = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Session date/time in UTC"
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['race', 'session_number']
        unique_together = [['race', 'session_number']]
        indexes = [
            models.Index(fields=['race', 'session_number']),
            models.Index(fields=['session_type']),
            models.Index(fields=['session_date_utc']),
        ]
        verbose_name = 'Session'
        verbose_name_plural = 'Sessions'
    
    def __str__(self):
        return f"{self.race.name} - {self.session_type}"
    
    @property
    def is_practice(self):
        """Check if this is a practice session"""
        return self.session_type in [
            self.TYPE_PRACTICE_1, 
            self.TYPE_PRACTICE_2, 
            self.TYPE_PRACTICE_3
        ]
    
    @property
    def is_qualifying(self):
        """Check if this is qualifying (main or sprint)"""
        return self.session_type in [
            self.TYPE_QUALIFYING,
            self.TYPE_SPRINT_QUALIFYING
        ]
    
    @property
    def is_race(self):
        """Check if this is a race (main or sprint)"""
        return self.session_type in [
            self.TYPE_SPRINT,
            self.TYPE_RACE
        ]


class SessionWeather(models.Model):
    """
    Weather conditions for a session.
    
    Stores weather data from FastF1 (primary) or Open-Meteo (fallback).
    Weather can significantly impact lap times, tire strategy, and race outcomes.
    
    FastF1 provides:
    - Air temperature (°C)
    - Track temperature (°C)
    - Humidity (%)
    - Wind speed (m/s)
    - Wind direction (degrees)
    - Rainfall (boolean)
    - Pressure (mbar)
    
    Data sources:
    1. FastF1 Session.weather_data (preferred)
    2. Open-Meteo API (fallback for missing data)
    """
    
    session = models.OneToOneField(
        Session,
        on_delete=models.CASCADE,
        related_name='weather',
        help_text="The session this weather data belongs to"
    )
    
    # Temperature measurements
    air_temperature = models.FloatField(
        null=True,
        blank=True,
        help_text="Air temperature in Celsius"
    )
    
    track_temperature = models.FloatField(
        null=True,
        blank=True,
        help_text="Track surface temperature in Celsius (from FastF1 only)"
    )
    
    # Atmospheric conditions
    humidity = models.FloatField(
        null=True,
        blank=True,
        help_text="Relative humidity percentage (0-100)"
    )
    
    pressure = models.FloatField(
        null=True,
        blank=True,
        help_text="Atmospheric pressure in millibars (mbar)"
    )
    
    # Wind conditions
    wind_speed = models.FloatField(
        null=True,
        blank=True,
        help_text="Wind speed in meters per second (m/s)"
    )
    
    wind_direction = models.IntegerField(
        null=True,
        blank=True,
        help_text="Wind direction in degrees (0-360, where 0/360=North, 90=East, 180=South, 270=West)"
    )
    
    # Precipitation
    rainfall = models.BooleanField(
        default=False,
        help_text="Whether it rained during the session"
    )
    
    # Data source tracking
    data_source = models.CharField(
        max_length=50,
        default='fastf1',
        help_text="Source of weather data (fastf1, open-meteo, manual)"
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['session__race__season', 'session__race__round_number', 'session__session_number']
        verbose_name = 'Session Weather'
        verbose_name_plural = 'Session Weather'
        indexes = [
            models.Index(fields=['rainfall']),
            models.Index(fields=['air_temperature']),
        ]
    
    def __str__(self):
        temp_str = f"{self.air_temperature}°C" if self.air_temperature else "N/A"
        rain_str = " (Rain)" if self.rainfall else ""
        return f"{self.session} - {temp_str}{rain_str}"
    
    @property
    def weather_summary(self):
        """Human-readable weather summary"""
        parts = []
        
        if self.air_temperature is not None:
            parts.append(f"Air: {self.air_temperature:.1f}°C")
        
        if self.track_temperature is not None:
            parts.append(f"Track: {self.track_temperature:.1f}°C")
        
        if self.humidity is not None:
            parts.append(f"Humidity: {self.humidity:.0f}%")
        
        if self.wind_speed is not None:
            parts.append(f"Wind: {self.wind_speed:.1f} m/s")
        
        if self.rainfall:
            parts.append("RAIN")
        
        return ", ".join(parts) if parts else "No weather data"
    
    @property
    def is_wet(self):
        """Check if session had wet conditions"""
        return self.rainfall
    
    @property
    def is_hot(self):
        """Check if session had hot conditions (air temp > 30°C)"""
        return self.air_temperature is not None and self.air_temperature > 30
