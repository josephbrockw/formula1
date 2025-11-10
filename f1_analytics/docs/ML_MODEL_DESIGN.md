# ML/RL Model Design for F1 Fantasy Optimization

## Overview

The database schema is designed to support Machine Learning and Reinforcement Learning for optimizing F1 Fantasy team selection. This document explains the design decisions and how to leverage the schema for ML/RL.

## Database Schema Design

### 1. **Race Model** - Normalization & Track-Specific Analysis

```python
class Race(models.Model):
    season, name, round_number, race_date, circuit_name, country
```

**Why:**
- **Normalized**: Avoids storing race names as strings in every row
- **Track-specific features**: Enables analysis like "How does Hamilton perform at Monaco?"
- **Temporal ordering**: `round_number` provides sequential ordering for time-series
- **Small table**: ~24 races/season, very efficient

**ML Use Cases:**
- Track-specific performance prediction
- Circuit characteristics as features (street circuit vs. traditional)
- Temporal patterns (early season vs. late season)

---

### 2. **DriverRacePerformance Model** - Aggregated Performance

```python
class DriverRacePerformance(models.Model):
    driver, race, team, total_points, fantasy_price,
    season_points_cumulative, had_qualifying, had_sprint, had_race
```

**Why:**
- **One record per driver per race**: Clean aggregation level for ML features
- **Pre-aggregated points**: Avoids expensive sums across event scores
- **Price at time of race**: Enables "value over time" analysis
- **Event flags**: Quick filters for feature engineering

**ML Use Cases:**

#### Time-Series Features
```sql
-- Rolling average points (last 3 races)
SELECT driver_id, race_id, 
       AVG(total_points) OVER (
           PARTITION BY driver_id 
           ORDER BY race__round_number 
           ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING
       ) as avg_last_3_races
FROM analytics_driverraceperformance
```

#### Consistency Metrics
```sql
-- Standard deviation of points (consistency indicator)
SELECT driver_id,
       STDDEV(total_points) as consistency_score
FROM analytics_driverraceperformance
WHERE race__season_id = 1
GROUP BY driver_id
```

#### Value Metrics
```python
# Points per million over time
performance.points_per_million  # Already computed as property
```

---

### 3. **DriverEventScore Model** - Granular Details

```python
class DriverEventScore(models.Model):
    performance, event_type, scoring_item, points, position, frequency
```

**Why:**
- **Granular analysis**: Breakdown by event type (qualifying vs. race)
- **Pattern detection**: Find drivers who excel at specific scoring items
- **Small table size**: ~5,500 rows/season (manageable)
- **Flexibility**: Can aggregate up to DriverRacePerformance or analyze details

**ML Use Cases:**

#### Event-Specific Performance
```sql
-- Average qualifying points by driver
SELECT driver_id, AVG(points) as avg_qual_points
FROM analytics_driverevent score
WHERE event_type = 'qualifying'
GROUP BY performance__driver_id
```

#### Overtaking Analysis
```sql
-- Total overtakes by driver (proxy for aggressive driving style)
SELECT driver_id, SUM(frequency) as total_overtakes
FROM analytics_driveventscore
WHERE scoring_item LIKE '%Overtake%'
GROUP BY performance__driver_id
```

---

## Feature Engineering Examples

### 1. **Rolling Statistics** (Momentum)
```python
from django.db.models import Window, Avg
from django.db.models.functions import RowNumber

# Last 3 races average
performances = DriverRacePerformance.objects.annotate(
    rolling_avg_3=Window(
        expression=Avg('total_points'),
        partition_by=['driver'],
        order_by=['race__round_number'],
        frame=RowRange(start=-3, end=-1)
    )
)
```

### 2. **Team Performance** (Constructor Strength)
```python
# Average team performance (all drivers on team)
from django.db.models import Avg

team_avg = DriverRacePerformance.objects.filter(
    race=target_race
).values('team').annotate(
    team_avg_points=Avg('total_points')
)
```

### 3. **Track History** (Historical Performance)
```python
# Driver's historical performance at specific track
historical = DriverRacePerformance.objects.filter(
    driver=driver,
    race__name='Monaco'  # Same track name across seasons
).aggregate(
    avg_points=Avg('total_points'),
    consistency=StdDev('total_points')
)
```

### 4. **Price Efficiency** (Value Metric)
```python
# Best value drivers (points per million)
from django.db.models import F, Avg

recent_value = DriverRacePerformance.objects.filter(
    race__season=current_season,
    race__round_number__gte=current_round - 3
).annotate(
    ppm=F('total_points') / F('fantasy_price')
).values('driver').annotate(
    avg_ppm=Avg('ppm')
).order_by('-avg_ppm')
```

---

## Reinforcement Learning Design

### State Space

The state for each decision point (before a race) includes:

1. **Driver Features** (from DriverRacePerformance):
   - Rolling avg points (last 3, 5 races)
   - Points variance (consistency)
   - Current fantasy price
   - Points per million trend
   - Team strength (avg of teammates)

2. **Context Features** (from Race):
   - Track type (street, traditional, etc.)
   - Historical driver performance at this track
   - Round number (season progression)

3. **Portfolio Features** (from CurrentLineup):
   - Current cap space
   - Budget allocation
   - Team diversity

### Action Space

- **Discrete actions**: Binary for each driver (pick/don't pick)
- **Constraint**: Budget ≤ $100M, exactly 5 drivers, 2 teams
- **DRS driver**: Additional binary choice from selected 5

### Reward Signal

```python
# Reward = points earned - opportunity cost
reward = actual_lineup_points - avg_points_available_within_budget
```

---

## Indexes for Performance

All models include strategic indexes for common ML queries:

```python
# DriverRacePerformance indexes
indexes = [
    models.Index(fields=['driver', 'race']),  # Lookup specific performance
    models.Index(fields=['race', '-total_points']),  # Leaderboard by race
    models.Index(fields=['driver', 'race__season', 'race__round_number']),  # Time-series
    models.Index(fields=['team', 'race']),  # Team-based aggregations
]

# DriverEventScore indexes
indexes = [
    models.Index(fields=['performance', 'event_type']),  # Event breakdowns
    models.Index(fields=['event_type', 'scoring_item']),  # Scoring patterns
    models.Index(fields=['performance__driver', 'event_type']),  # Driver event performance
]
```

---

## Query Optimization Tips

### 1. Use `select_related` for Foreign Keys
```python
# Efficient: One query
performances = DriverRacePerformance.objects.select_related(
    'driver', 'race', 'team'
).filter(race__season=season)
```

### 2. Use `prefetch_related` for Reverse Relations
```python
# Efficient: Two queries instead of N+1
performances = DriverRacePerformance.objects.prefetch_related(
    'event_scores'
).filter(race=target_race)
```

### 3. Aggregate at Database Level
```python
# Good: Database does the work
from django.db.models import Sum, Avg

stats = DriverRacePerformance.objects.filter(
    driver=driver
).aggregate(
    total=Sum('total_points'),
    average=Avg('total_points')
)
```

---

## Next Steps for ML Implementation

1. **Feature Engineering Script**: Create `features.py` to compute all ML features
2. **Training Data Export**: Export to pandas DataFrame for sklearn/torch
3. **Model Training**: Train models on historical data (cross-validation by season)
4. **Prediction Pipeline**: Real-time prediction before each race
5. **RL Environment**: Gym-style environment for reinforcement learning

---

## Summary of Design Rationale

| Decision | Rationale | ML Benefit |
|----------|-----------|------------|
| Separate Race table | Normalize race data | Track-specific features, temporal ordering |
| DriverRacePerformance aggregation | One record per driver-race | Efficient time-series queries, clean aggregation |
| DriverEventScore details | Granular scoring data | Pattern detection, event-specific analysis |
| Multiple indexes | Optimize common queries | Fast feature computation |
| Event participation flags | Quick filtering | Avoid unnecessary joins |
| Price snapshot in performance | Temporal accuracy | Value-over-time analysis |
| Cumulative season points | Already computed | No expensive sums needed |

This design balances **normalization** (avoiding duplication) with **denormalization** (pre-computing aggregates) to enable both efficient queries and flexible analysis for ML/RL optimization.
