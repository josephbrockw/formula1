from __future__ import annotations

from django.db import models

from core.models import Driver, Event, Season, Team


# ---------------------------------------------------------------------------
# Fantasy price snapshots (from Chrome extension CSV exports)
# ---------------------------------------------------------------------------


class FantasyDriverPrice(models.Model):
    """
    Price snapshot for a driver before a specific race weekend.

    Captured from the Chrome extension CSV export (YYYY-MM-DD-drivers.csv).
    One record per driver per event — the price at the time of that race.
    Used to reconstruct the buy/sell history and train the price predictor.
    """

    driver = models.ForeignKey(Driver, on_delete=models.CASCADE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    snapshot_date = models.DateField()
    price = models.DecimalField(max_digits=5, decimal_places=1, help_text="Price in $M, e.g. 30.4")
    price_change = models.DecimalField(max_digits=4, decimal_places=1, help_text="Change since last update, e.g. -0.1")
    pick_percentage = models.FloatField(help_text="% of fantasy teams that have selected this driver")
    season_fantasy_points = models.IntegerField(help_text="Cumulative season fantasy points at snapshot time")

    class Meta:
        unique_together = [("driver", "event")]

    def __str__(self) -> str:
        return f"{self.driver.code} @ {self.event} — ${self.price}M"


class FantasyConstructorPrice(models.Model):
    """
    Price snapshot for a constructor before a specific race weekend.
    Same structure as FantasyDriverPrice but for teams.
    """

    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    snapshot_date = models.DateField()
    price = models.DecimalField(max_digits=5, decimal_places=1, help_text="Price in $M")
    price_change = models.DecimalField(max_digits=4, decimal_places=1)
    pick_percentage = models.FloatField()
    season_fantasy_points = models.IntegerField()

    class Meta:
        unique_together = [("team", "event")]

    def __str__(self) -> str:
        return f"{self.team.name} @ {self.event} — ${self.price}M"


# ---------------------------------------------------------------------------
# Fantasy scoring breakdowns (from Chrome extension performance CSVs)
# ---------------------------------------------------------------------------


class FantasyDriverScore(models.Model):
    """
    Granular scoring line items for a driver in a race weekend.

    Captured from YYYY-MM-DD-all-drivers-performance.csv.
    Each row is one scoring action, e.g.:
      - event_type="race", scoring_item="Race Position", position=1, points=25
      - event_type="race", scoring_item="Race Overtake Bonus", frequency=3, points=3
      - event_type="qualifying", scoring_item="Qualifying Position", position=2, points=9

    This granularity lets us analyse which scoring categories each driver
    is strong or weak in — useful features for the ML model.
    """

    driver = models.ForeignKey(Driver, on_delete=models.CASCADE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    event_type = models.CharField(max_length=20, help_text="qualifying, sprint, or race")
    scoring_item = models.CharField(max_length=100, help_text="e.g. 'Qualifying Position', 'Race Overtake Bonus'")
    frequency = models.IntegerField(null=True, blank=True, help_text="Count for repeated actions, e.g. 5 for 5 overtakes")
    position = models.IntegerField(null=True, blank=True, help_text="Finishing position if applicable")
    points = models.IntegerField(help_text="Points earned (can be negative)")
    race_total = models.IntegerField(help_text="Driver's total points for this race weekend")
    season_total = models.IntegerField(help_text="Cumulative season total at this point")

    class Meta:
        unique_together = [("driver", "event", "event_type", "scoring_item")]

    def __str__(self) -> str:
        return f"{self.driver.code} @ {self.event} — {self.event_type}/{self.scoring_item}: {self.points}pts"


class FantasyConstructorScore(models.Model):
    """
    Granular scoring line items for a constructor in a race weekend.
    Same structure as FantasyDriverScore but for teams.
    Includes constructor-specific items like pit stop bonuses and Q progression.
    """

    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    event_type = models.CharField(max_length=20)
    scoring_item = models.CharField(max_length=100)
    frequency = models.IntegerField(null=True, blank=True)
    position = models.IntegerField(null=True, blank=True)
    points = models.IntegerField()
    race_total = models.IntegerField()
    season_total = models.IntegerField()

    class Meta:
        unique_together = [("team", "event", "event_type", "scoring_item")]

    def __str__(self) -> str:
        return f"{self.team.name} @ {self.event} — {self.event_type}/{self.scoring_item}: {self.points}pts"


# ---------------------------------------------------------------------------
# Scoring rules (for computing fantasy points from raw FastF1 data)
# ---------------------------------------------------------------------------


class ScoringRule(models.Model):
    """
    The fantasy points table for a given season.

    Stored per-season because the rules could change year to year.
    Used by compute_fantasy_points to calculate what a driver *should* have
    scored from raw FastF1 SessionResult data, without needing Chrome extension
    snapshots for every historical race.

    Examples of rule_name values:
      race_p1, race_p2, ..., race_p10
      qualifying_p1, ..., qualifying_p10
      race_dnf, race_dsq, qualifying_no_time
      position_gained, position_lost, overtake
      fastest_lap, driver_of_the_day
    """

    season = models.ForeignKey(Season, on_delete=models.CASCADE)
    rule_name = models.CharField(max_length=100)
    points = models.FloatField()
    description = models.CharField(max_length=200)

    class Meta:
        unique_together = [("season", "rule_name")]

    def __str__(self) -> str:
        return f"{self.season.year} — {self.rule_name}: {self.points}pts"


# ---------------------------------------------------------------------------
# ML prediction tracking
# ---------------------------------------------------------------------------


class RacePrediction(models.Model):
    """
    ML model predictions for a driver at an upcoming race.

    Created by predict_race management command before qualifying locks.
    After the race, actual_position and actual_fantasy_points are filled in
    so we can measure prediction accuracy over time.

    model_version lets us run multiple model variants and compare them.
    confidence_lower/upper are the 10th/90th percentile estimates — useful
    for the optimizer to balance expected value vs risk.
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE)
    predicted_position = models.FloatField()
    predicted_fantasy_points = models.FloatField()
    confidence_lower = models.FloatField(help_text="10th percentile fantasy points estimate")
    confidence_upper = models.FloatField(help_text="90th percentile fantasy points estimate")
    actual_position = models.IntegerField(null=True, blank=True)
    actual_fantasy_points = models.FloatField(null=True, blank=True)
    model_version = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("event", "driver", "model_version")]

    def __str__(self) -> str:
        return f"{self.driver.code} @ {self.event} — predicted {self.predicted_fantasy_points:.1f}pts ({self.model_version})"


# ---------------------------------------------------------------------------
# Lineup recommendations
# ---------------------------------------------------------------------------


class LineupRecommendation(models.Model):
    """
    Optimizer's recommended lineup for a race weekend.

    Created by optimize_lineup management command after predictions are made.
    Stores the full 5-driver + 2-constructor lineup with the DRS Boost pick.

    actual_points is filled in post-race to measure how well we did.
    strategy_type distinguishes single-race greedy picks from multi-race
    horizon strategies (e.g. "single_race", "multi_race_horizon_5").
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    driver_1 = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="lineup_slot_1")
    driver_2 = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="lineup_slot_2")
    driver_3 = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="lineup_slot_3")
    driver_4 = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="lineup_slot_4")
    driver_5 = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="lineup_slot_5")
    drs_boost_driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="lineup_drs_boost")
    constructor_1 = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="lineup_slot_1")
    constructor_2 = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="lineup_slot_2")
    total_cost = models.DecimalField(max_digits=6, decimal_places=1, help_text="Total lineup cost in $M")
    predicted_points = models.FloatField()
    actual_points = models.FloatField(null=True, blank=True)
    strategy_type = models.CharField(max_length=50, help_text="e.g. 'single_race', 'multi_race_horizon_5'")
    model_version = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("event", "strategy_type", "model_version")]

    def __str__(self) -> str:
        return f"{self.event} — {self.strategy_type} ({self.model_version}): {self.predicted_points:.1f} predicted pts"


# ---------------------------------------------------------------------------
# My actual submitted lineups
# ---------------------------------------------------------------------------


class MyLineup(models.Model):
    """
    The lineup you actually submitted to F1 Fantasy for a race weekend.

    Distinct from LineupRecommendation (what the optimizer suggested). They
    will often differ — you might ignore a recommendation, make a judgment
    call, or have constraints the optimizer doesn't know about.

    This is the source of truth for transfer constraint tracking: the
    next_race command reads the most recent MyLineup to know your current
    team and how many free transfers you have banked.

    actual_points is null until after the race — fill it in via
    record_my_lineup --actual-points N once results are published.
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    driver_1 = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="+")
    driver_2 = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="+")
    driver_3 = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="+")
    driver_4 = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="+")
    driver_5 = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="+")
    drs_boost_driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name="+")
    constructor_1 = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="+")
    constructor_2 = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="+")
    team_cost = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True,
        help_text="Total cost of this lineup at submission time ($M)",
    )
    budget_cap = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True,
        help_text="Total available budget at time of submission ($M)",
    )
    actual_points = models.FloatField(null=True, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("event",)]

    def __str__(self) -> str:
        drivers = ", ".join(
            d.code
            for d in [self.driver_1, self.driver_2, self.driver_3, self.driver_4, self.driver_5]
        )
        return f"{self.event} — {drivers}"
