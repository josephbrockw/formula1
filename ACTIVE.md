# Active Plan 
PR 5 — Form Direction + Car Separation Features

Context

The v4 feature store currently lives in f1_data/predictions/features/v4.py. Per ML_UPGRADE_INTEGRATION.md, f1_data/predictions/features/v4.py should contain
telemetry + form direction features. This PR adds 7 new features capturing the direction of a driver's form and relative strength vs their teammate.

The current feature store averages (e.g. position_mean_last3) lose information about trend: a driver who finished 5th, 4th, 3rd in recent races looks the same as
one who finished 3rd, 4th, 5th. Slope and recency features fix this.

 ---
Files to Change

┌─────────────────────────────────────────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────────────┐
│                          File                           │                                             Action                                              │
├─────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────┤
│ f1_data/predictions/features/v4.py                      │ add 7 new features                                                                              │
├─────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────┤

 ---
7 New Features

Race form direction

┌─────────────────────┬─────────────────────────────────────────────────────────────────────────────────────┬─────────────────────────────────────┐
│       Feature       │                                     Definition                                      │               Default               │
├─────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────────┤
│ position_last1      │ Finishing position in the most recent race                                          │ NEW_ENTRANT_POSITION_DEFAULT (18.0) │
├─────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────────┤
│ position_slope      │ OLS slope of finishing positions over last 5 races (negative = improving)           │ 0.0                                 │
├─────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────────┤
│ best_position_last5 │ Best (minimum) finishing position in last 5 races (DNFs → 20.0)                     │ NEW_ENTRANT_POSITION_DEFAULT        │
├─────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────────┤
│ teammate_delta      │ driver_position_last1 - teammate_position_last1 (negative = driver ahead last race) │ 0.0                                 │
├─────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────────┤
│ team_best_position  │ Minimum finishing position across both team cars in last 5 races                    │ NEW_ENTRANT_POSITION_DEFAULT        │
└─────────────────────┴─────────────────────────────────────────────────────────────────────────────────────┴─────────────────────────────────────┘

Qualifying trajectory

┌─────────────┬─────────────────────────────────────────────────────────────────────────────┬──────────────────────────────┐
│   Feature   │                                 Definition                                  │           Default            │
├─────────────┼─────────────────────────────────────────────────────────────────────────────┼──────────────────────────────┤
│ quali_last1 │ Qualifying position at the most recent event                                │ NEW_ENTRANT_POSITION_DEFAULT │
├─────────────┼─────────────────────────────────────────────────────────────────────────────┼──────────────────────────────┤
│ quali_slope │ OLS slope of qualifying positions over last 5 events (negative = improving) │ 0.0                          │
└─────────────┴─────────────────────────────────────────────────────────────────────────────┴──────────────────────────────┘

 ---
Implementation Approach

1. Add private helpers in f1_data/predictions/features/v4.py (after the existing FP helpers)

_driver_recent_race_positions(codes, event, n=5)
- Query SessionResult for session_type="R", ordered by event_date desc, limited to n races before this event
- Returns dict[str, list[float]] (driver code → positions chronologically ascending)
- Use driver__code__in=codes for batch query (same cross-season pattern as v3)
- DNF detection: same as v1 — not (status == "Finished" or status.startswith("+")) → assign 20.0

_driver_recent_quali_positions(codes, event, n=5)
- Same as above but session_type="Q"

_compute_form_features(driver_rows, event)
- Calls the two helpers above
- Computes per driver:
    - position_last1: last element of sorted positions (or default)
    - position_slope: np.polyfit(range(len(positions)), positions, 1)[0] when ≥2 points
    - best_position_last5: min of positions
    - quali_last1, quali_slope: same logic for quali
- Computes per team (group driver_rows by team_id):
    - teammate_delta: driver_last1 - teammate_last1 (0.0 if no teammate or insufficient data)
    - team_best_position: min across all drivers in team over last 5 races

2. Wire into get_all_driver_features()

After the FP telemetry block (existing v4 code), add:

```python
driver_rows = list(
Driver.objects.filter(id__in=driver_ids).values("id", "code", "team_id")
)
form_features = _compute_form_features(driver_rows, event)
for col, mapping in form_features.items():
df[col] = df["driver_id"].astype(int).map(mapping)
```

3. Update the class docstring

Add the 7 new features to the list.

 ---
Key design decisions

- Reuse driver__code cross-season pattern: Same as v3 — queries by driver.code so VER-2024 and VER-2025 are matched correctly. Avoids the "all rookies look the
  same" problem.
- DNF → 20.0: Consistent with v1's existing convention (NEW_ENTRANT_POSITION_DEFAULT = 18.0 is for true rookies; DNFs get 20 since they started the race and failed
  to finish — slightly worse than last place).
- teammate_delta uses last1 not last5: Provides a different signal from the existing driver_vs_teammate_gap_last5 in v3 (which is a rolling average). The last-race
  delta captures current form momentum vs teammate.
- team_best_position uses last5: The "car ceiling" signal — what's the best this car can do recently? Useful for detecting whether a team had one exceptional
  outlier (strategy, safety car) vs consistent pace.
- OLS slope: Reuses the same np.polyfit approach as fantasy_points_trend_last5 in v2. Requires ≥2 data points; defaults to 0.0 when insufficient.

 ---
Verification

# Run backtest comparing v3 vs v4 feature stores, same predictor and optimizer
python manage.py backtest --feature-store v3 v4 --predictor v4 --optimizer v4 --seasons 2024

# Quick sanity check: print feature vector for one driver at one event
```
python manage.py shell -c "
from predictions.features.v4 import V4FeatureStore
from core.models import Event
e = Event.objects.filter(season__year=2024).order_by('event_date')[5]
fs = V4FeatureStore()
df = fs.get_all_driver_features(e.id)
print(df[['driver_id','position_last1','position_slope','best_position_last5','teammate_delta','team_best_position','quali_last1','quali_slope']].to_string())
"
```

New features should be non-null for most drivers at any race from round 4 onwards (first 3 rounds have insufficient history for slopes).