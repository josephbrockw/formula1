from django.contrib import admin
from django.db.models import F
from .models import (
    Season, Team, Driver, DriverSnapshot, ConstructorSnapshot,
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

