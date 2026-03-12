from __future__ import annotations

from django.contrib import admin

from core.models import (
    Circuit,
    CollectionRun,
    Driver,
    Event,
    Lap,
    Season,
    Session,
    SessionCollectionStatus,
    SessionResult,
    Team,
    WeatherSample,
)


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ["year"]
    ordering = ["-year"]


@admin.register(Circuit)
class CircuitAdmin(admin.ModelAdmin):
    list_display = ["name", "country", "city", "circuit_length", "total_corners"]
    search_fields = ["name", "country", "city"]
    ordering = ["country", "name"]


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "season"]
    list_filter = ["season"]
    search_fields = ["name"]
    ordering = ["-season__year", "name"]


@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = ["code", "full_name", "driver_number", "team", "season"]
    list_filter = ["season", "team"]
    search_fields = ["code", "full_name"]
    ordering = ["-season__year", "code"]


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ["round_number", "event_name", "country", "event_date", "event_format", "season"]
    list_filter = ["season", "event_format"]
    search_fields = ["event_name", "country"]
    ordering = ["-season__year", "round_number"]


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ["__str__", "session_type", "date"]
    list_filter = ["session_type", "event__season"]
    search_fields = ["event__event_name"]
    ordering = ["-event__season__year", "event__round_number", "session_type"]


@admin.register(SessionResult)
class SessionResultAdmin(admin.ModelAdmin):
    list_display = ["driver", "session", "position", "grid_position", "points", "status", "fastest_lap_rank"]
    list_filter = ["session__event__season", "session__session_type", "team"]
    search_fields = ["driver__code", "driver__full_name", "session__event__event_name"]
    ordering = ["-session__event__season__year", "session__event__round_number", "position"]


@admin.register(Lap)
class LapAdmin(admin.ModelAdmin):
    list_display = ["driver", "session", "lap_number", "lap_time", "compound", "tyre_life", "is_pit_in_lap", "is_accurate"]
    list_filter = ["session__event__season", "session__session_type", "compound", "is_pit_in_lap", "is_accurate"]
    search_fields = ["driver__code", "session__event__event_name"]
    ordering = ["-session__event__season__year", "session__event__round_number", "driver__code", "lap_number"]


@admin.register(WeatherSample)
class WeatherSampleAdmin(admin.ModelAdmin):
    list_display = ["session", "timestamp", "air_temp", "track_temp", "humidity", "rainfall", "wind_speed"]
    list_filter = ["session__event__season", "rainfall"]
    search_fields = ["session__event__event_name"]
    ordering = ["-timestamp"]


@admin.register(CollectionRun)
class CollectionRunAdmin(admin.ModelAdmin):
    list_display = ["pk", "status", "started_at", "finished_at", "sessions_processed", "sessions_skipped"]
    list_filter = ["status"]
    ordering = ["-started_at"]


@admin.register(SessionCollectionStatus)
class SessionCollectionStatusAdmin(admin.ModelAdmin):
    list_display = ["session", "status", "collected_at", "lap_count", "result_count", "weather_sample_count", "retry_count"]
    list_filter = ["status", "session__event__season", "session__session_type"]
    search_fields = ["session__event__event_name", "error_message"]
    ordering = ["-session__event__season__year", "session__event__round_number", "session__session_type"]
