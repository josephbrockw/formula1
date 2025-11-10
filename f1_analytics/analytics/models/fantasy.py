"""
Fantasy F1 game models.

These models store data from F1 Fantasy game (formula1.com), imported from CSV files.
Includes pricing snapshots, race performance data, and scoring details.
"""

from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from .base import User, Season, Team, Driver
from .events import Race


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


class DriverRacePerformance(models.Model):
    """
    Aggregated performance for a driver in a specific race.
    This is the main table for ML/RL feature engineering.
    
    Design rationale:
    - One record per driver per race for efficient aggregations
    - Stores total points and fantasy price for value calculations
    - Links to Race for track-specific analysis
    - Supports time-series features (rolling averages, trends)
    """
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='race_performances')
    race = models.ForeignKey(Race, on_delete=models.CASCADE, related_name='driver_performances')
    team = models.ForeignKey(
        Team, 
        on_delete=models.SET_NULL, 
        null=True, 
        help_text="Team at time of race (handles mid-season transfers)"
    )
    
    # Aggregate metrics for this race
    total_points = models.IntegerField(
        help_text="Total fantasy points earned in this race weekend"
    )
    fantasy_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Driver's fantasy price at time of race (for value calculations)"
    )
    season_points_cumulative = models.IntegerField(
        help_text="Cumulative season points after this race"
    )
    
    # Event participation flags (for feature engineering)
    had_qualifying = models.BooleanField(default=False)
    had_sprint = models.BooleanField(default=False)
    had_race = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['race__season', 'race__round_number', 'driver']
        unique_together = [['driver', 'race']]
        indexes = [
            models.Index(fields=['driver', 'race']),
            models.Index(fields=['race', '-total_points']),
            models.Index(fields=['team', 'race']),
        ]
        verbose_name = 'Driver Race Performance'
        verbose_name_plural = 'Driver Race Performances'
    
    def __str__(self):
        return f"{self.driver.full_name} - {self.race.name} ({self.total_points} pts)"
    
    @property
    def points_per_million(self):
        """Value metric: points earned per million spent"""
        if self.fantasy_price > 0:
            return float(self.total_points) / float(self.fantasy_price)
        return 0


class DriverEventScore(models.Model):
    """
    Granular scoring details for individual events within a race weekend.
    Links to DriverRacePerformance for aggregation.
    
    Design rationale:
    - Stores individual scoring items (qualifying position, overtakes, etc.)
    - Enables detailed breakdowns and pattern analysis
    - Supports calculating consistency, variance, specific strengths
    - Small table size (23 drivers × 24 races × ~10 items each = ~5500 rows/season)
    """
    
    # Event type choices
    EVENT_TYPE_CHOICES = [
        ('qualifying', 'Qualifying'),
        ('sprint', 'Sprint Race'),
        ('race', 'Main Race'),
        ('weekend', 'Weekend Bonus'),
    ]
    
    performance = models.ForeignKey(
        DriverRacePerformance, 
        on_delete=models.CASCADE, 
        related_name='event_scores'
    )
    
    event_type = models.CharField(
        max_length=20,
        choices=EVENT_TYPE_CHOICES,
        db_index=True,
        help_text="Type of event (qualifying, sprint, race, weekend)"
    )
    
    scoring_item = models.CharField(
        max_length=100,
        help_text="Specific scoring action (e.g., 'Qualifying Position', 'Race Overtake Bonus')"
    )
    
    # Scoring details
    points = models.IntegerField(help_text="Points earned for this item (can be negative)")
    position = models.IntegerField(
        null=True,
        blank=True,
        help_text="Final position if applicable (1st, 2nd, etc.)"
    )
    frequency = models.IntegerField(
        null=True,
        blank=True,
        help_text="Count for frequency-based items (overtakes, positions gained)"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['performance__race', 'event_type', 'scoring_item']
        indexes = [
            models.Index(fields=['performance', 'event_type']),
            models.Index(fields=['event_type', 'scoring_item']),
        ]
        verbose_name = 'Driver Event Score'
        verbose_name_plural = 'Driver Event Scores'
    
    def __str__(self):
        driver_name = self.performance.driver.full_name
        race_name = self.performance.race.name
        return f"{driver_name} - {race_name} - {self.event_type}: {self.scoring_item} ({self.points} pts)"


class ConstructorRacePerformance(models.Model):
    """
    Aggregated performance for a constructor in a specific race.
    Similar to DriverRacePerformance but for teams/constructors.
    
    Design rationale:
    - One record per constructor per race for efficient aggregations
    - Stores total points and fantasy price for value calculations
    - Links to Race for track-specific analysis
    - Supports time-series features for constructor performance trends
    """
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='race_performances')
    race = models.ForeignKey(Race, on_delete=models.CASCADE, related_name='constructor_performances')
    
    # Aggregate metrics for this race
    total_points = models.IntegerField(
        help_text="Total fantasy points earned in this race weekend"
    )
    fantasy_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Constructor's fantasy price at time of race"
    )
    season_points_cumulative = models.IntegerField(
        help_text="Cumulative season points after this race"
    )
    
    # Event participation flags
    had_qualifying = models.BooleanField(default=False)
    had_sprint = models.BooleanField(default=False)
    had_race = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['race__season', 'race__round_number', 'team']
        unique_together = [['team', 'race']]
        indexes = [
            models.Index(fields=['team', 'race']),
            models.Index(fields=['race', '-total_points']),
        ]
        verbose_name = 'Constructor Race Performance'
        verbose_name_plural = 'Constructor Race Performances'
    
    def __str__(self):
        return f"{self.team.name} - {self.race.name} ({self.total_points} pts)"
    
    @property
    def points_per_million(self):
        """Value metric: points earned per million spent"""
        if self.fantasy_price > 0:
            return float(self.total_points) / float(self.fantasy_price)
        return 0


class ConstructorEventScore(models.Model):
    """
    Granular scoring details for individual events within a constructor's race weekend.
    Links to ConstructorRacePerformance for aggregation.
    
    Design rationale:
    - Stores individual scoring items (qualifying position, pitstop bonuses, etc.)
    - Enables detailed breakdowns and pattern analysis for constructors
    - Similar structure to DriverEventScore for consistency
    """
    
    # Event type choices (same as driver)
    EVENT_TYPE_CHOICES = [
        ('qualifying', 'Qualifying'),
        ('sprint', 'Sprint Race'),
        ('race', 'Main Race'),
        ('weekend', 'Weekend Bonus'),
    ]
    
    performance = models.ForeignKey(
        ConstructorRacePerformance, 
        on_delete=models.CASCADE, 
        related_name='event_scores'
    )
    
    event_type = models.CharField(
        max_length=20,
        choices=EVENT_TYPE_CHOICES,
        db_index=True,
        help_text="Type of event (qualifying, sprint, race, weekend)"
    )
    
    scoring_item = models.CharField(
        max_length=100,
        help_text="Specific scoring action (e.g., 'Qualifying Position', 'Pitstop Bonus')"
    )
    
    # Scoring details
    points = models.IntegerField(help_text="Points earned for this item (can be negative)")
    position = models.IntegerField(
        null=True,
        blank=True,
        help_text="Final position if applicable"
    )
    frequency = models.IntegerField(
        null=True,
        blank=True,
        help_text="Count for frequency-based items (pitstops, positions gained)"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['performance__race', 'event_type', 'scoring_item']
        indexes = [
            models.Index(fields=['performance', 'event_type']),
            models.Index(fields=['event_type', 'scoring_item']),
        ]
        verbose_name = 'Constructor Event Score'
        verbose_name_plural = 'Constructor Event Scores'
    
    def __str__(self):
        team_name = self.performance.team.name
        race_name = self.performance.race.name
        return f"{team_name} - {race_name} - {self.event_type}: {self.scoring_item} ({self.points} pts)"
