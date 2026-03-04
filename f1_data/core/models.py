from __future__ import annotations

from django.db import models


# ---------------------------------------------------------------------------
# Reference models
# ---------------------------------------------------------------------------


class Season(models.Model):
    year = models.IntegerField(unique=True)

    def __str__(self) -> str:
        return str(self.year)


class Circuit(models.Model):
    circuit_key = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    country = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    circuit_length = models.FloatField(null=True, blank=True)
    total_corners = models.IntegerField(null=True, blank=True)

    def __str__(self) -> str:
        return self.name


class Team(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    full_name = models.CharField(max_length=200)

    class Meta:
        unique_together = [('season', 'name')]

    def __str__(self) -> str:
        return f"{self.season.year} {self.name}"


class Driver(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE)
    code = models.CharField(max_length=10)
    full_name = models.CharField(max_length=200)
    driver_number = models.IntegerField()
    team = models.ForeignKey(Team, on_delete=models.CASCADE)

    class Meta:
        unique_together = [('season', 'code')]

    def __str__(self) -> str:
        return f"{self.season.year} {self.code}"


# ---------------------------------------------------------------------------
# Event models
# ---------------------------------------------------------------------------


class Event(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE)
    round_number = models.IntegerField()
    event_name = models.CharField(max_length=200)
    country = models.CharField(max_length=100)
    circuit = models.ForeignKey(Circuit, on_delete=models.CASCADE)
    event_date = models.DateField()
    event_format = models.CharField(max_length=50)

    class Meta:
        unique_together = [('season', 'round_number')]

    def __str__(self) -> str:
        return f"{self.season.year} {self.event_name}"


class Session(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    session_type = models.CharField(max_length=10)
    date = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('event', 'session_type')]

    def __str__(self) -> str:
        return f"{self.event} — {self.session_type}"


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class SessionResult(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE)
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    position = models.IntegerField(null=True, blank=True)
    classified_position = models.CharField(max_length=10)
    grid_position = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=100)
    points = models.FloatField(default=0)
    time = models.DurationField(null=True, blank=True)
    fastest_lap_rank = models.IntegerField(null=True, blank=True)

    class Meta:
        unique_together = [('session', 'driver')]

    def __str__(self) -> str:
        return f"{self.session} — {self.driver.code} P{self.position}"


# ---------------------------------------------------------------------------
# Lap models
# ---------------------------------------------------------------------------


class Lap(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE)
    lap_number = models.IntegerField()
    lap_time = models.DurationField(null=True, blank=True)
    sector1_time = models.DurationField(null=True, blank=True)
    sector2_time = models.DurationField(null=True, blank=True)
    sector3_time = models.DurationField(null=True, blank=True)
    pit_in_time = models.DurationField(null=True, blank=True)
    pit_out_time = models.DurationField(null=True, blank=True)
    is_pit_in_lap = models.BooleanField(default=False)
    is_pit_out_lap = models.BooleanField(default=False)
    stint = models.IntegerField(null=True, blank=True)
    compound = models.CharField(max_length=20, null=True, blank=True)
    tyre_life = models.IntegerField(null=True, blank=True)
    track_status = models.CharField(max_length=10, null=True, blank=True)
    position = models.IntegerField(null=True, blank=True)
    is_personal_best = models.BooleanField(default=False)
    is_accurate = models.BooleanField(default=False)

    class Meta:
        unique_together = [('session', 'driver', 'lap_number')]

    def __str__(self) -> str:
        return f"{self.session} — {self.driver.code} L{self.lap_number}"


# ---------------------------------------------------------------------------
# Weather models
# ---------------------------------------------------------------------------


class WeatherSample(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    timestamp = models.DateTimeField()
    air_temp = models.FloatField()
    track_temp = models.FloatField()
    humidity = models.FloatField()
    pressure = models.FloatField()
    wind_speed = models.FloatField()
    wind_direction = models.IntegerField()
    rainfall = models.BooleanField()

    class Meta:
        unique_together = [('session', 'timestamp')]

    def __str__(self) -> str:
        return f"{self.session} @ {self.timestamp}"


# ---------------------------------------------------------------------------
# Collection tracking models
# ---------------------------------------------------------------------------


class CollectionRun(models.Model):
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=30, default='running')
    sessions_processed = models.IntegerField(default=0)
    sessions_skipped = models.IntegerField(default=0)
    error_message = models.TextField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Run {self.pk} [{self.status}] @ {self.started_at}"


class SessionCollectionStatus(models.Model):
    session = models.OneToOneField(Session, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, default='pending')
    collected_at = models.DateTimeField(null=True, blank=True)
    lap_count = models.IntegerField(default=0)
    weather_sample_count = models.IntegerField(default=0)
    result_count = models.IntegerField(default=0)
    error_message = models.TextField(null=True, blank=True)
    retry_count = models.IntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.session} [{self.status}]"
