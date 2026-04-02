# Active Plan 

PR 6 — Weather Features: practice_rainfall_any + driver_wet_performance_rank

Context

V4 already inherits V3's continuous weather features (weather_practice_rain_fraction, driver_wet_vs_dry_position_delta, driver_wet_session_count). This PR adds two
derived features on top of those:

1. practice_rainfall_any — a binary conditioning signal: "was practice wet this weekend?" The ranker benefits from this as a discrete wet/dry switch, even though
   the continuous fraction exists.
2. driver_wet_performance_rank — converts the raw delta (positions gained/lost in wet vs dry) into a rank (1-20.5), matching V4's rank-based idiom for driver
   comparisons.

Both features are derived from columns already computed by V3 — no additional DB queries.

 ---
Files to change

┌───────────────────────────────────────────────┬────────────────────────────────────────────────────┐
│                     File                      │                       Change                       │
├───────────────────────────────────────────────┼────────────────────────────────────────────────────┤
│ f1_data/predictions/features/v4.py            │ Add 2 feature computations + update docstring      │
├───────────────────────────────────────────────┼────────────────────────────────────────────────────┤
│ f1_data/predictions/tests/test_features_v4.py │ Update feature count constant + add 2 test classes │
└───────────────────────────────────────────────┴────────────────────────────────────────────────────┘

 ---
Implementation

v4.py — get_all_driver_features

Add after the fp_short_vs_long_delta line (line 83):

# Binary wet-weekend flag. The ranker uses this as a conditioning signal to
# weight wet-specialist features more heavily. Derived from V3's continuous
# fraction — no extra DB query needed.
df["practice_rainfall_any"] = (df["weather_practice_rain_fraction"] > 0.0).astype(float)

# Rank drivers by wet vs dry position delta, ascending (rank 1 = best wet performer).
# Drivers with insufficient wet history receive V3's default +2.0 penalty, which
# naturally places them near the bottom when ranked — encoding "unknown wet ability".
# When all drivers are on the same default (no wet races in history), method="average"
# assigns them all equal rank (~10.5 for a 20-driver field).
df["driver_wet_performance_rank"] = df["driver_wet_vs_dry_position_delta"].rank(
method="average", ascending=True
)

Update the class docstring to add the two new features under a "Weather features" section.

test_features_v4.py

1. Update feature count constant:
   V4_NEW_FEATURE_COUNT = 17  # was 15

2. Add PracticeRainfallAnyTest — tests via the get_all_driver_features integration path, creating WeatherSample records using make_weather_sample:

- test_dry_practice_returns_zero: FP1 with rainfall=False → practice_rainfall_any = 0.0
- test_any_wet_practice_returns_one: FP1 dry + FP2 wet → practice_rainfall_any = 1.0
- test_race_session_rain_excluded: rain only in race session, no FP samples → practice_rainfall_any = 0.0

3. Add DriverWetPerformanceRankTest — tests via _wet_vs_dry_position_deltas + ranking logic, using the V3 test helper pattern (_past_race with is_wet=True/False):

- test_wet_specialist_ranks_first: driver A finishes better in wet than dry (negative delta), driver B worse → driver A gets rank 1
- test_all_default_deltas_get_equal_rank: two drivers with no wet history both have +2.0 delta → method="average" assigns them both the same rank (1.5 for a
  2-driver field), confirming no artificial differentiation

These tests call V4FeatureStore().get_all_driver_features(event_id) and check the resulting df columns.

 ---
Verification

# Run just the V4 tests
python manage.py test predictions.tests.test_features_v4

# Run full feature test suite
python manage.py test predictions.tests

# Quick smoke test: check feature count for a known event
python manage.py shell -c "
from predictions.features.v4 import V4FeatureStore
df = V4FeatureStore().get_all_driver_features(<event_id>)
print(df.columns.tolist())
print(df[['driver_id','practice_rainfall_any','driver_wet_performance_rank']].head())
"