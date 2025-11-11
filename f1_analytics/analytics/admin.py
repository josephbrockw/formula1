from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import F
from .models import (
    User, Season, Team, Driver, DriverSnapshot, ConstructorSnapshot, CurrentLineup,
    Circuit, Corner, MarshalLight, MarshalSector, Race, Session, SessionWeather,
    DriverRacePerformance, DriverEventScore,
    ConstructorRacePerformance, ConstructorEventScore,
)


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ['year', 'name', 'is_active', 'start_date', 'end_date']
    list_filter = ['is_active', 'year']
    search_fields = ['year', 'name']


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ['name', 'short_name', 'created_at']
    search_fields = ['name', 'short_name']


@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'first_name', 'last_name', 'created_at']
    search_fields = ['full_name', 'first_name', 'last_name']
    list_filter = ['created_at']


@admin.register(DriverSnapshot)
class DriverSnapshotAdmin(admin.ModelAdmin):
    list_display = [
        'driver', 'team', 'snapshot_date', 'fantasy_price', 
        'price_change', 'season_points', 'percent_picked', 'points_per_million_display'
    ]
    list_filter = ['snapshot_date', 'season', 'team']
    search_fields = ['driver__full_name', 'driver__last_name']
    date_hierarchy = 'snapshot_date'
    readonly_fields = ['created_at', 'points_per_million_display', 'price_change_percentage_display']
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('driver', 'team', 'season', 'snapshot_date')
        }),
        ('Fantasy Metrics', {
            'fields': ('fantasy_price', 'price_change', 'season_points', 'percent_picked')
        }),
        ('Calculated Metrics', {
            'fields': ('points_per_million_display', 'price_change_percentage_display'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )
    
    def points_per_million_display(self, obj):
        return f"{obj.points_per_million:.2f}"
    points_per_million_display.short_description = 'Points per Million'
    
    def price_change_percentage_display(self, obj):
        return f"{obj.price_change_percentage:.2f}%"
    price_change_percentage_display.short_description = 'Price Change %'


@admin.register(ConstructorSnapshot)
class ConstructorSnapshotAdmin(admin.ModelAdmin):
    list_display = [
        'team', 'snapshot_date', 'fantasy_price', 
        'price_change', 'season_points', 'percent_picked', 'points_per_million_display'
    ]
    list_filter = ['snapshot_date', 'season', 'team']
    search_fields = ['team__name']
    date_hierarchy = 'snapshot_date'
    readonly_fields = ['created_at', 'points_per_million_display']
    
    def points_per_million_display(self, obj):
        return f"{obj.points_per_million:.2f}"
    points_per_million_display.short_description = 'Points per Million'


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Custom User admin"""
    pass


@admin.register(CurrentLineup)
class CurrentLineupAdmin(admin.ModelAdmin):
    list_display = ['user', 'updated_at', 'cap_space', 'total_budget_display']
    list_filter = ['user', 'updated_at']
    readonly_fields = ['created_at', 'updated_at', 'total_budget_display']
    
    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('Drivers', {
            'fields': ('driver1', 'driver2', 'driver3', 'driver4', 'driver5', 'drs_driver')
        }),
        ('Constructors', {
            'fields': ('team1', 'team2')
        }),
        ('Budget', {
            'fields': ('cap_space', 'total_budget_display')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def total_budget_display(self, obj):
        return f"${obj.total_budget}M"
    total_budget_display.short_description = 'Total Budget'


# Circuit-related inlines
class CornerInline(admin.TabularInline):
    """Inline display of corners within a circuit"""
    model = Corner
    extra = 0
    fields = ['number', 'letter', 'x', 'y', 'angle', 'distance']
    readonly_fields = ['number', 'letter', 'x', 'y', 'angle', 'distance']
    can_delete = False
    ordering = ['number', 'letter']
    
    def has_add_permission(self, request, obj=None):
        return False


class MarshalLightInline(admin.TabularInline):
    """Inline display of marshal lights within a circuit"""
    model = MarshalLight
    extra = 0
    fields = ['number', 'letter', 'distance', 'angle']
    readonly_fields = ['number', 'letter', 'distance', 'angle']
    can_delete = False
    ordering = ['number', 'letter']
    
    def has_add_permission(self, request, obj=None):
        return False


class MarshalSectorInline(admin.TabularInline):
    """Inline display of marshal sectors within a circuit"""
    model = MarshalSector
    extra = 0
    fields = ['number', 'letter', 'distance', 'angle']
    readonly_fields = ['number', 'letter', 'distance', 'angle']
    can_delete = False
    ordering = ['number', 'letter']
    
    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Circuit)
class CircuitAdmin(admin.ModelAdmin):
    list_display = ['name', 'rotation', 'corner_count', 'race_count', 'created_at']
    search_fields = ['name']
    readonly_fields = ['created_at', 'updated_at', 'corner_count', 'light_count', 'sector_count']
    inlines = [CornerInline, MarshalLightInline, MarshalSectorInline]
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('name', 'rotation')
        }),
        ('Statistics', {
            'fields': ('corner_count', 'light_count', 'sector_count'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def corner_count(self, obj):
        return obj.corners.count()
    corner_count.short_description = 'Corners'
    
    def light_count(self, obj):
        return obj.marshal_lights.count()
    light_count.short_description = 'Marshal Lights'
    
    def sector_count(self, obj):
        return obj.marshal_sectors.count()
    sector_count.short_description = 'Marshal Sectors'
    
    def race_count(self, obj):
        return obj.races.count()
    race_count.short_description = 'Races'


@admin.register(Corner)
class CornerAdmin(admin.ModelAdmin):
    list_display = ['circuit', 'number', 'letter', 'angle', 'distance', 'x', 'y']
    list_filter = ['circuit']
    search_fields = ['circuit__name']
    ordering = ['circuit', 'number', 'letter']
    
    fieldsets = (
        ('Corner Info', {
            'fields': ('circuit', 'number', 'letter')
        }),
        ('Position', {
            'fields': ('x', 'y', 'distance')
        }),
        ('Geometry', {
            'fields': ('angle',)
        }),
    )


@admin.register(MarshalLight)
class MarshalLightAdmin(admin.ModelAdmin):
    list_display = ['circuit', 'number', 'letter', 'distance', 'angle']
    list_filter = ['circuit']
    search_fields = ['circuit__name']
    ordering = ['circuit', 'number', 'letter']
    
    fieldsets = (
        ('Light Info', {
            'fields': ('circuit', 'number', 'letter')
        }),
        ('Position', {
            'fields': ('x', 'y', 'distance', 'angle')
        }),
    )


@admin.register(MarshalSector)
class MarshalSectorAdmin(admin.ModelAdmin):
    list_display = ['circuit', 'number', 'letter', 'distance', 'angle']
    list_filter = ['circuit']
    search_fields = ['circuit__name']
    ordering = ['circuit', 'number', 'letter']
    
    fieldsets = (
        ('Sector Info', {
            'fields': ('circuit', 'number', 'letter')
        }),
        ('Position', {
            'fields': ('x', 'y', 'distance', 'angle')
        }),
    )


class SessionWeatherInline(admin.StackedInline):
    """Inline display of weather data within a session"""
    model = SessionWeather
    extra = 0
    fields = [
        'air_temperature', 'track_temperature', 'humidity', 'pressure',
        'wind_speed', 'wind_direction', 'rainfall', 'data_source'
    ]
    readonly_fields = fields
    can_delete = False
    
    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = [
        'race', 'session_number', 'session_type', 'session_date_utc', 
        'get_season', 'get_round', 'has_weather'
    ]
    list_filter = ['session_type', 'race__season', 'race__event_format']
    search_fields = ['race__name', 'session_type']
    ordering = ['race__season', 'race__round_number', 'session_number']
    readonly_fields = ['created_at', 'updated_at']
    date_hierarchy = 'session_date_utc'
    inlines = [SessionWeatherInline]
    
    fieldsets = (
        ('Session Info', {
            'fields': ('race', 'session_number', 'session_type')
        }),
        ('Timing', {
            'fields': ('session_date_utc', 'session_date_local')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_season(self, obj):
        return obj.race.season.year
    get_season.short_description = 'Season'
    get_season.admin_order_field = 'race__season__year'
    
    def get_round(self, obj):
        return obj.race.round_number
    get_round.short_description = 'Round'
    get_round.admin_order_field = 'race__round_number'
    
    def has_weather(self, obj):
        return hasattr(obj, 'weather')
    has_weather.short_description = 'Weather'
    has_weather.boolean = True


@admin.register(SessionWeather)
class SessionWeatherAdmin(admin.ModelAdmin):
    list_display = [
        'session', 'air_temperature', 'track_temperature', 'humidity',
        'wind_speed', 'rainfall', 'data_source'
    ]
    list_filter = ['rainfall', 'data_source', 'session__race__season']
    search_fields = ['session__race__name', 'session__session_type']
    ordering = ['session__race__season', 'session__race__round_number', 'session__session_number']
    readonly_fields = ['created_at', 'updated_at', 'weather_summary']
    
    fieldsets = (
        ('Session', {
            'fields': ('session',)
        }),
        ('Temperature', {
            'fields': ('air_temperature', 'track_temperature')
        }),
        ('Atmospheric', {
            'fields': ('humidity', 'pressure')
        }),
        ('Wind', {
            'fields': ('wind_speed', 'wind_direction')
        }),
        ('Precipitation', {
            'fields': ('rainfall',)
        }),
        ('Summary', {
            'fields': ('weather_summary',)
        }),
        ('Metadata', {
            'fields': ('data_source', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def weather_summary(self, obj):
        return obj.weather_summary
    weather_summary.short_description = 'Weather Summary'


class SessionInline(admin.TabularInline):
    """Inline display of sessions within a race"""
    model = Session
    extra = 0
    fields = ['session_number', 'session_type', 'session_date_utc', 'session_date_local']
    readonly_fields = ['session_number', 'session_type', 'session_date_utc', 'session_date_local']
    can_delete = False
    ordering = ['session_number']
    
    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Race)
class RaceAdmin(admin.ModelAdmin):
    list_display = [
        'round_number', 'name', 'season', 'location', 'country', 
        'circuit', 'event_format', 'race_date', 'f1_api_support'
    ]
    list_filter = ['season', 'event_format', 'f1_api_support', 'country']
    search_fields = ['name', 'location', 'country', 'official_event_name', 'circuit__name']
    ordering = ['season', 'round_number']
    inlines = [SessionInline]
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('season', 'name', 'round_number')
        }),
        ('Location', {
            'fields': ('circuit', 'location', 'country')
        }),
        ('Dates', {
            'fields': ('race_date', 'event_date')
        }),
        ('FastF1 Metadata', {
            'fields': ('official_event_name', 'event_format', 'f1_api_support'),
            'classes': ('collapse',)
        }),
    )


class DriverEventScoreInline(admin.TabularInline):
    """Inline display of event scores within a race performance"""
    model = DriverEventScore
    extra = 0
    fields = ['event_type', 'scoring_item', 'points', 'position', 'frequency']
    readonly_fields = ['event_type', 'scoring_item', 'points', 'position', 'frequency']
    can_delete = False
    
    def has_add_permission(self, request, obj=None):
        return False


@admin.register(DriverRacePerformance)
class DriverRacePerformanceAdmin(admin.ModelAdmin):
    list_display = [
        'driver', 'race', 'team', 'total_points', 'fantasy_price',
        'points_per_million_display', 'season_points_cumulative'
    ]
    list_filter = ['race__season', 'team', 'had_sprint', 'race__round_number']
    search_fields = ['driver__full_name', 'driver__last_name', 'race__name']
    readonly_fields = ['created_at', 'updated_at', 'points_per_million_display']
    inlines = [DriverEventScoreInline]
    
    fieldsets = (
        ('Race & Driver', {
            'fields': ('driver', 'race', 'team')
        }),
        ('Performance', {
            'fields': ('total_points', 'fantasy_price', 'season_points_cumulative', 'points_per_million_display')
        }),
        ('Event Participation', {
            'fields': ('had_qualifying', 'had_sprint', 'had_race'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def points_per_million_display(self, obj):
        return f"{obj.points_per_million:.2f}"
    points_per_million_display.short_description = 'Points/M'


@admin.register(DriverEventScore)
class DriverEventScoreAdmin(admin.ModelAdmin):
    list_display = [
        'get_driver', 'get_race', 'event_type', 'scoring_item', 
        'points', 'position', 'frequency'
    ]
    list_filter = ['event_type', 'scoring_item', 'performance__race__season']
    search_fields = [
        'performance__driver__full_name', 
        'performance__driver__last_name',
        'performance__race__name',
        'scoring_item'
    ]
    readonly_fields = ['created_at']
    
    fieldsets = (
        ('Performance Link', {
            'fields': ('performance',)
        }),
        ('Event Details', {
            'fields': ('event_type', 'scoring_item')
        }),
        ('Scoring', {
            'fields': ('points', 'position', 'frequency')
        }),
        ('Metadata', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )
    
    def get_driver(self, obj):
        return obj.performance.driver.full_name
    get_driver.short_description = 'Driver'
    get_driver.admin_order_field = 'performance__driver__full_name'
    
    def get_race(self, obj):
        return obj.performance.race.name
    get_race.short_description = 'Race'
    get_race.admin_order_field = 'performance__race__name'


class ConstructorEventScoreInline(admin.TabularInline):
    """Inline display of event scores within a constructor race performance"""
    model = ConstructorEventScore
    extra = 0
    fields = ['event_type', 'scoring_item', 'points', 'position', 'frequency']
    readonly_fields = ['event_type', 'scoring_item', 'points', 'position', 'frequency']
    can_delete = False
    
    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ConstructorRacePerformance)
class ConstructorRacePerformanceAdmin(admin.ModelAdmin):
    list_display = [
        'team', 'race', 'total_points', 'fantasy_price',
        'points_per_million_display', 'season_points_cumulative'
    ]
    list_filter = ['race__season', 'team', 'had_sprint', 'race__round_number']
    search_fields = ['team__name', 'race__name']
    readonly_fields = ['created_at', 'updated_at', 'points_per_million_display']
    inlines = [ConstructorEventScoreInline]
    
    fieldsets = (
        ('Race & Team', {
            'fields': ('team', 'race')
        }),
        ('Performance', {
            'fields': ('total_points', 'fantasy_price', 'season_points_cumulative', 'points_per_million_display')
        }),
        ('Event Participation', {
            'fields': ('had_qualifying', 'had_sprint', 'had_race'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def points_per_million_display(self, obj):
        return f"{obj.points_per_million:.2f}"
    points_per_million_display.short_description = 'Points/M'


@admin.register(ConstructorEventScore)
class ConstructorEventScoreAdmin(admin.ModelAdmin):
    list_display = [
        'get_team', 'get_race', 'event_type', 'scoring_item', 
        'points', 'position', 'frequency'
    ]
    list_filter = ['event_type', 'scoring_item', 'performance__race__season']
    search_fields = [
        'performance__team__name',
        'performance__race__name',
        'scoring_item'
    ]
    readonly_fields = ['created_at']
    
    fieldsets = (
        ('Performance Link', {
            'fields': ('performance',)
        }),
        ('Event Details', {
            'fields': ('event_type', 'scoring_item')
        }),
        ('Scoring', {
            'fields': ('points', 'position', 'frequency')
        }),
        ('Metadata', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )
    
    def get_team(self, obj):
        return obj.performance.team.name
    get_team.short_description = 'Team'
    get_team.admin_order_field = 'performance__team__name'
    
    def get_race(self, obj):
        return obj.performance.race.name
    get_race.short_description = 'Race'
    get_race.admin_order_field = 'performance__race__name'

