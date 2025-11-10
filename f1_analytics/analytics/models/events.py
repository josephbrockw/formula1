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


class Race(models.Model):
    """
    Represents an individual Grand Prix event in a season.
    Used to normalize race references and support track-specific analysis.
    
    Shared by:
    - Fantasy CSV imports (performance data)
    - FastF1 imports (telemetry, lap times, weather)
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


# TODO: Add FastF1 session models here
# class Session(models.Model):
#     """Individual session within a race weekend (FP1, FP2, FP3, Qualifying, Sprint, Race)"""
#     race = models.ForeignKey(Race, on_delete=models.CASCADE, related_name='sessions')
#     session_type = models.CharField(max_length=20)  # 'FP1', 'FP2', 'FP3', 'Q', 'S', 'R'
#     date = models.DateTimeField()
#     ...
