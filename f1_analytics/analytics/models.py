
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """
    Custom User model - extends Django's AbstractUser.
    Add any custom fields or methods here as needed.
    """
    class Meta:
        db_table = 'auth_user'
        verbose_name = 'User'
        verbose_name_plural = 'Users'
    
    def __str__(self):
        return self.username


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


class Race(models.Model):
    """
    Represents an individual Grand Prix event in a season.
    Used to normalize race references and support track-specific analysis.
    """
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name='races')
    name = models.CharField(max_length=100, help_text="e.g., 'Bahrain', 'Australia', 'Monaco'")
    round_number = models.IntegerField(
        help_text="Race number in season (1 for first race, 2 for second, etc.)"
    )
    race_date = models.DateField(null=True, blank=True)
    circuit_name = models.CharField(max_length=200, blank=True)
    country = models.CharField(max_length=100, blank=True)
    
    class Meta:
        ordering = ['season', 'round_number']
        unique_together = [['season', 'name'], ['season', 'round_number']]
        indexes = [
            models.Index(fields=['season', 'round_number']),
            models.Index(fields=['name']),
        ]
    
    def __str__(self):
        return f"{self.season.year} {self.name} GP (Round {self.round_number})"


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


class CurrentLineup(models.Model):
    """
    Represents my current lineup of drivers and teams
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    driver1 = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='driver1')
    driver2 = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='driver2')
    driver3 = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='driver3')
    driver4 = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='driver4')
    driver5 = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='driver5')
    drs_driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='drs_driver')
    team1 = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='team1')
    team2 = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='team2')
    cap_space = models.DecimalField(max_digits=10, decimal_places=2, help_text="Remaining cap space in millions (M)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    @property
    def total_budget(self):
        """
        Calculate total budget spent on current lineup.
        Returns the sum of most recent fantasy prices for all drivers and teams, plus remaining cap space.
        Note: DRS driver is always one of the 5 drivers (just flagged for DRS bonus), so we only count 5 drivers.
        """
        from django.db.models import Max
        
        # Get the most recent snapshot date
        latest_date = DriverSnapshot.objects.aggregate(Max('snapshot_date'))['snapshot_date__max']
        if not latest_date:
            return self.cap_space
        
        # Get the 5 driver IDs (DRS driver is one of these 5)
        driver_ids = [
            self.driver1_id, self.driver2_id, self.driver3_id, 
            self.driver4_id, self.driver5_id
        ]
        
        # Fetch all driver snapshots in one query
        driver_snapshots = DriverSnapshot.objects.filter(
            driver_id__in=driver_ids,
            snapshot_date=latest_date
        ).values('fantasy_price')
        
        # Sum driver prices
        driver_total = sum(snap['fantasy_price'] for snap in driver_snapshots)
        
        # Fetch team snapshots in one query
        team_ids = [self.team1_id, self.team2_id]
        team_snapshots = ConstructorSnapshot.objects.filter(
            team_id__in=team_ids,
            snapshot_date=latest_date
        ).values('team_id', 'fantasy_price')
        
        # Sum team prices
        team_total = sum(snap['fantasy_price'] for snap in team_snapshots)
        
        # Total budget = driver costs + team costs + remaining cap space
        total = driver_total + team_total
        if self.cap_space:
            total += self.cap_space
        return total
    
    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'Current Lineup'
        verbose_name_plural = 'Current Lineups'
    
    def __str__(self):
        return f"{self.user.username}'s Lineup (updated {self.updated_at.strftime('%Y-%m-%d %H:%M')})"
    
