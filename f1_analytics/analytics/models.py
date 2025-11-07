
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


class Season(models.Model):
    """
    Represents an F1 season
    """
    year = models.IntegerField(unique=True)
    name = models.CharField(max_length=100, help_text="e.g., '2025 Formula 1 Season'")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['-year']
        indexes = [
            models.Index(fields=['year']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return f"{self.year} Season"


class Team(models.Model):
    """
    Constructor/Team - relatively stable entity across seasons
    """
    name = models.CharField(max_length=100, unique=True)
    short_name = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['name']
    
    def __str__(self):
        return self.name


class Driver(models.Model):
    """
    Driver - relatively stable entity across seasons
    """
    full_name = models.CharField(max_length=200, unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    current_team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, related_name='drivers')
    
    class Meta:
        ordering = ['last_name', 'first_name']
        indexes = [
            models.Index(fields=['last_name', 'first_name']),
        ]
    
    def __str__(self):
        if self.current_team:
            return f"{self.full_name} ({self.current_team.name})"
        return self.full_name


class DriverSnapshot(models.Model):
    """
    Time-series data: Driver statistics at a specific date
    This is the main model for tracking how drivers change over time
    """
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='snapshots')
    team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, related_name='driver_snapshots')
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name='driver_snapshots')
    
    # Date of this snapshot
    snapshot_date = models.DateField(db_index=True)
    
    # Fantasy game metrics
    fantasy_price = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        help_text="Fantasy price in millions (e.g., 30.4 for $30.4M)"
    )
    price_change = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        help_text="Price change, negative for decreases"
    )
    season_points = models.IntegerField(
        validators=[MinValueValidator(0)]
    )
    percent_picked = models.DecimalField(
        max_digits=5, 
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Percentage of teams that have picked this driver"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-snapshot_date', 'driver__last_name']
        unique_together = [['driver', 'snapshot_date']]
        indexes = [
            models.Index(fields=['snapshot_date']),
            models.Index(fields=['driver', 'snapshot_date']),
            models.Index(fields=['season', 'snapshot_date']),
            models.Index(fields=['-fantasy_price']),
            models.Index(fields=['-season_points']),
        ]
    
    def __str__(self):
        return f"{self.driver.full_name} @ ${self.fantasy_price}M - {self.snapshot_date}"
    
    @property
    def points_per_million(self):
        """Calculate value metric: points per million spent"""
        if self.fantasy_price > 0:
            return float(self.season_points) / float(self.fantasy_price)
        return 0
    
    @property
    def price_change_percentage(self):
        """Calculate price change as percentage"""
        if self.fantasy_price > 0:
            return (float(self.price_change) / float(self.fantasy_price)) * 100
        return 0


class ConstructorSnapshot(models.Model):
    """
    Time-series data: Constructor statistics at a specific date
    """
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='snapshots')
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name='constructor_snapshots')
    
    # Date of this snapshot
    snapshot_date = models.DateField(db_index=True)
    
    # Fantasy game metrics
    fantasy_price = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        help_text="Fantasy price in millions"
    )
    price_change = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        help_text="Price change, negative for decreases"
    )
    season_points = models.IntegerField(
        validators=[MinValueValidator(0)]
    )
    percent_picked = models.DecimalField(
        max_digits=5, 
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Percentage of teams that have picked this constructor"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-snapshot_date', 'team__name']
        unique_together = [['team', 'snapshot_date']]
        indexes = [
            models.Index(fields=['snapshot_date']),
            models.Index(fields=['team', 'snapshot_date']),
            models.Index(fields=['season', 'snapshot_date']),
            models.Index(fields=['-fantasy_price']),
            models.Index(fields=['-season_points']),
        ]
    
    def __str__(self):
        return f"{self.team.name} @ ${self.fantasy_price}M - {self.snapshot_date}"
    
    @property
    def points_per_million(self):
        """Calculate value metric: points per million spent"""
        if self.fantasy_price > 0:
            return float(self.season_points) / float(self.fantasy_price)
        return 0