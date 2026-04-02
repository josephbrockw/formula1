# Active Plan 

 Plan: PR 4 — Feature Store v4 (Practice Telemetry)

 Context

 V1–V3 feature stores use no practice lap data beyond a single-number "best lap rank" and "avg best-5 rank" (already in V1). This PR adds eight new features derived from the Lap model — long-run race pace, tyre degradation, sector times, lap
  counts, session availability — giving the model signal that's genuinely predictive of race results and entirely unavailable before. V4 extends V3 in the same chain pattern as all prior versions.

 ---
 New features

 ┌─────────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┬───────────────────────────────────────────────┐
 │         Feature         │                                                                    What it captures                                                                     │                    Source                     │
 ├─────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────┤
 │ fp_long_run_pace_rank   │ Median lap time in stints ≥5 laps (same compound, accurate laps only). Rank 1=fastest. Best single proxy for race pace.                                 │ FP2 preferred; FP1 fallback                   │
 ├─────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────┤
 │ fp_tyre_deg_rank        │ Slope of lap_time vs tyre_life within long-run stints. Rank 1=lowest degradation.                                                                       │ FP2 preferred                                 │
 ├─────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────┤
 │ fp_short_vs_long_delta  │ practice_best_lap_rank - fp_long_run_pace_rank. Positive = stronger in quali than race, negative = stronger in race than quali.                         │ Derived from existing V3 column + new feature │
 ├─────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────┤
 │ fp_sector1_rank         │ Best sector 1 time rank across FP1–FP3. Accurate, non-pit laps only.                                                                                    │ FP1+FP2+FP3                                   │
 ├─────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────┤
 │ fp_sector2_rank         │ Best sector 2 time rank.                                                                                                                                │ Same                                          │
 ├─────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────┤
 │ fp_sector3_rank         │ Best sector 3 time rank.                                                                                                                                │ Same                                          │
 ├─────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────┤
 │ fp_total_laps           │ Total FP laps completed. Low count signals setup problems or mechanical issues.                                                                         │ FP1+FP2+FP3                                   │
 ├─────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────┤
 │ fp_session_availability │ Count of FP sessions with any lap data (1 for sprint weekends, up to 3 for conventional). Lets the model discount telemetry features when data is thin. │ Session existence                             │
 └─────────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┴───────────────────────────────────────────────┘

 ---
 Filter logic (shared across all lap-based features)

 Lap.objects.filter(
     session__event=event,
     session__session_type__in=["FP1", "FP2", "FP3"],
     is_accurate=True,
     is_pit_in_lap=False,
     is_pit_out_lap=False,
     lap_time__isnull=False,
 )

 For long-run features: additionally require compound__isnull=False, stint__isnull=False, tyre_life__isnull=False.

 Load all filtered laps for the event in one query using .values(...) — don't query per driver. Process in Python/pandas.

 "Long run" definition: a group of laps sharing the same (driver_id, session_type, stint, compound) with count ≥ 5. This avoids treating outlap/inlap bursts as meaningful pace data.

 ---
 Implementation

 1. Extend make_lap factory — predictions/tests/factories.py

 Add optional kwargs so tests can build realistic lap data:
```python
 def make_lap(
     session, driver, lap_number=1, lap_time_seconds=90.0, is_accurate=True,
     compound=None, tyre_life=None, stint=None,
     sector1_seconds=None, sector2_seconds=None, sector3_seconds=None,
     is_pit_in_lap=False, is_pit_out_lap=False,
 )
```
 Convert sector_seconds → timedelta the same way lap_time_seconds is handled.

 2. Create predictions/features/v4.py

 Structure:

```python
 class V4FeatureStore:
     def get_all_driver_features(self, event_id: int) -> pd.DataFrame:
         df = V3FeatureStore().get_all_driver_features(event_id)
         # Load all FP laps once
         fp_laps = _load_fp_laps(event)
         driver_ids = df["driver_id"].tolist()

         long_run_ranks = _fp_long_run_pace_ranks(fp_laps, driver_ids)
         deg_ranks = _fp_tyre_deg_ranks(fp_laps, driver_ids)
         sector_ranks = _fp_sector_ranks(fp_laps, driver_ids)

         df["fp_long_run_pace_rank"]  = df["driver_id"].map(long_run_ranks)
         df["fp_tyre_deg_rank"]       = df["driver_id"].map(deg_ranks)
         df["fp_sector1_rank"]        = df["driver_id"].map(lambda d: sector_ranks[d][0])
         df["fp_sector2_rank"]        = df["driver_id"].map(lambda d: sector_ranks[d][1])
         df["fp_sector3_rank"]        = df["driver_id"].map(lambda d: sector_ranks[d][2])
         df["fp_total_laps"]          = df["driver_id"].map(_fp_total_laps(fp_laps, driver_ids))
         df["fp_session_availability"] = _fp_session_availability(event)
         # Derived — uses existing V3 column
         df["fp_short_vs_long_delta"] = df["practice_best_lap_rank"] - df["fp_long_run_pace_rank"]
         return df
```

 Private helpers (all take the pre-loaded fp_laps DataFrame):

 _load_fp_laps(event) — single DB query, returns pandas DataFrame with columns: driver_id, session_type, lap_time_seconds (converted from timedelta), tyre_life, stint, compound, sector1_seconds, sector2_seconds, sector3_seconds.

 _fp_long_run_pace_ranks(fp_laps, driver_ids) → {driver_id: float}:
 - Filter to FP2 laps first; if fewer than N drivers have ≥1 qualifying long-run stint, add FP1
 - Group by (driver_id, stint, compound), keep groups with count ≥ 5
 - Median lap_time_seconds per driver across all qualifying stints
 - Rank ascending (1=fastest); default 10.5 for missing drivers

 _fp_tyre_deg_ranks(fp_laps, driver_ids) → {driver_id: float}:
 - Same long-run stints as above (FP2 preferred)
 - For each qualifying stint: slope, _ = np.polyfit(tyre_life_array, lap_time_array, 1)
 - Average slope per driver across stints (more stints = more reliable)
 - Rank ascending (1=lowest slope = least degradation); default 10.5

 _fp_sector_ranks(fp_laps, driver_ids) → {driver_id: (s1_rank, s2_rank, s3_rank)}:
 - Use all FP1+FP2+FP3 laps (sectors are usually set in qualifying simulation runs)
 - Filter: sector time not null
 - Best (minimum) sector time per driver
 - Rank each sector independently; default 10.5

 _fp_total_laps(fp_laps, driver_ids) → {driver_id: float}:
 - Count rows per driver in all FP sessions; default 0.0

 _fp_session_availability(event) → float:
 - Session.objects.filter(event=event, session_type__in=["FP1","FP2","FP3"]).values_list("session_type", flat=True).distinct()
 - Return count as float (0.0–3.0)

 3. Register v4 — two places

 predictions/management/commands/backtest.py:
 from predictions.features.v4 import V4FeatureStore
 _FEATURE_STORE_REGISTRY = {"v1": ..., "v2": ..., "v3": ..., "v4": V4FeatureStore}

 f1_data/settings.py:
 ML_FEATURE_STORE_VERSIONS: list[str] = ["v1", "v2", "v3", "v4"]

 ---
 Default values (when a driver has no practice data)

 ┌─────────────────────────┬─────────┬───────────────────────────────────┐
 │         Feature         │ Default │             Rationale             │
 ├─────────────────────────┼─────────┼───────────────────────────────────┤
 │ fp_long_run_pace_rank   │ 10.5    │ Midfield; no signal = average     │
 ├─────────────────────────┼─────────┼───────────────────────────────────┤
 │ fp_tyre_deg_rank        │ 10.5    │ Same                              │
 ├─────────────────────────┼─────────┼───────────────────────────────────┤
 │ fp_sector[1-3]_rank     │ 10.5    │ Same                              │
 ├─────────────────────────┼─────────┼───────────────────────────────────┤
 │ fp_short_vs_long_delta  │ 0.0     │ No information = no delta         │
 ├─────────────────────────┼─────────┼───────────────────────────────────┤
 │ fp_total_laps           │ 0.0     │ Explicitly no data                │
 ├─────────────────────────┼─────────┼───────────────────────────────────┤
 │ fp_session_availability │ 0.0     │ Computable from session existence │
 └─────────────────────────┴─────────┴───────────────────────────────────┘

 ---
 DurationField → seconds

 Django returns Python timedelta for DurationField. In the _load_fp_laps query, convert immediately:
```python
 laps_qs = Lap.objects.filter(...).values(
     "driver_id", "session__session_type",
     "lap_time", "tyre_life", "stint", "compound",
     "sector1_time", "sector2_time", "sector3_time",
 )
 df = pd.DataFrame(laps_qs)
 df["lap_time_seconds"] = df["lap_time"].apply(
     lambda t: t.total_seconds() if t is not None else None
 )
 # Same for sector columns
```

 ---
 Tests — predictions/tests/test_features_v4.py

 Follow the V3 test structure. Test each helper in isolation first, then integration.

 Required test cases:
 - _fp_long_run_pace_ranks: fastest driver ranked 1, slower ranked 2+; stints <5 laps excluded; pit in/out laps excluded; inaccurate laps excluded; missing driver gets 10.5
 - _fp_tyre_deg_ranks: highest slope gets worst rank; flat pace (slope≈0) gets rank 1; missing driver gets 10.5
 - _fp_sector_ranks: best sector time per session; null sector times ignored; missing driver gets 10.5
 - _fp_total_laps: counts all accurate non-pit FP laps; missing driver gets 0.0
 - _fp_session_availability: returns 1.0 for event with only FP1, 3.0 for event with FP1+FP2+FP3
 - fp_short_vs_long_delta: practice_best_lap_rank - fp_long_run_pace_rank computed correctly
 - Feature count: assert V4 produces exactly V3_count + 8 features
 - Sprint weekend: all features have sensible defaults when only FP1 available

 ---
 Critical files

 - Create: predictions/features/v4.py
 - Modify: predictions/tests/factories.py — extend make_lap with compound/tyre_life/stint/sector kwargs
 - Modify: predictions/management/commands/backtest.py — add v4 to registry + import
 - Modify: f1_data/settings.py — add "v4" to ML_FEATURE_STORE_VERSIONS
 - Create: predictions/tests/test_features_v4.py
 - Reference: predictions/features/v3_pandas.py — template for structure and patterns
 - Reference: predictions/features/v1_pandas.py — practice_best_lap_rank column name confirmed here (line 263)

 ---
 Verification

 # Unit tests
 /Users/joewilkinson/Projects/formula1/venv/bin/python manage.py test predictions.tests.test_features_v4 -v 2

 # Backtest comparison — v3 vs v4 with same predictor
 /Users/joewilkinson/Projects/formula1/venv/bin/python manage.py backtest \
   --seasons 2024 2025 \
   --feature-store v3 v4 \
   --predictor v3 \
   --optimizer v2

 Expected: v4 shows improved rank metrics (Spearman ρ, NDCG@10) vs v3. MAE may not improve much — the new features help with ordering, not point estimation.
