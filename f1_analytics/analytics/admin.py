from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import F
from .models import (
    User, Season, Team, Driver, DriverSnapshot, ConstructorSnapshot, CurrentLineup,
    Race, DriverRacePerformance, DriverEventScore,
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


@admin.register(Race)
class RaceAdmin(admin.ModelAdmin):
    list_display = ['name', 'season', 'round_number', 'race_date', 'country']
    list_filter = ['season', 'country']
    search_fields = ['name', 'circuit_name', 'country']
    ordering = ['season', 'round_number']
    
    fieldsets = (
        ('Race Info', {
            'fields': ('season', 'name', 'round_number')
        }),
        ('Location', {
            'fields': ('circuit_name', 'country', 'race_date')
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

