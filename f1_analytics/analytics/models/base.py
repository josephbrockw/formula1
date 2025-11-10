"""
Base models shared across all data sources.

These models represent core F1 entities that are referenced by both
Fantasy data (CSV imports) and FastF1 telemetry data (API imports).
"""

from django.db import models
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


class CurrentLineup(models.Model):
    """
    Represents user's current lineup of drivers and teams.
    This is user-created data, not imported from external sources.
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
        # Import here to avoid circular dependency
        from .fantasy import DriverSnapshot, ConstructorSnapshot
        
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
