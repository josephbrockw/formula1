# ML Pipeline — Implementation Log

This document tracks what has been built, the decisions made, and why. Updated as each step is completed.

For the full plan (architecture, upgrade paths, build order) see `ML_PIPELINE_PLAN.md`.

---

## Architecture

```
core app (existing)          predictions app (new)
─────────────────────        ─────────────────────────────────────────
Season                  →    Feature Store
Circuit                      └── v1_pandas.V1FeatureStore
Team                              queries ORM, returns feature dicts
Driver                                     │
Event                                      ▼
Session                      Performance Predictor  [Step 3 — todo]
SessionResult                └── XGBoost v1
Lap                                        │
WeatherSample                              ▼
                             Optimizer  [Step 4 — todo]
                             └── Greedy knapsack v1
                                           │
                                           ▼
                             Backtester  [Step 5 — todo]
```

The `predictions` app depends on `core` data but `core` knows nothing about predictions. Each layer talks to the next through a defined interface (Python Protocol), so implementations can be swapped independently.

---

## Step 1 — Predictions App + Models

**Location:** `f1_data/predictions/`

**What was built:**

Created the `predictions` Django app with the following directory structure:

```
predictions/
├── models.py
├── admin.py
├── apps.py
├── migrations/
├── features/       ← feature store (Step 2)
├── predictors/     ← ML models (Step 3, todo)
├── optimizers/     ← lineup optimizer (Step 4, todo)
├── evaluation/     ← backtester (Step 5, todo)
├── management/commands/
└── tests/
```

**Models added** (`predictions/models.py`):

| Model | Purpose |
|-------|---------|
| `FantasyDriverPrice` | Price snapshot per driver per event. One record per (driver, event). Imported from Chrome extension CSV exports. |
| `FantasyConstructorPrice` | Same as above for constructors/teams. |
| `FantasyDriverScore` | Granular scoring breakdown per driver per race weekend — one row per scoring item (e.g. "Race Position: 25pts", "Overtake Bonus: 3pts"). Imported from Chrome extension performance CSVs. |
| `FantasyConstructorScore` | Same as above for constructors. Includes pit stop and Q progression items. |
| `ScoringRule` | The fantasy points table per season (e.g. P1=25, DNF=-20). Used by `compute_fantasy_points` to reconstruct historical fantasy scores from raw FastF1 data without needing Chrome extension snapshots. |
| `RacePrediction` | Stores ML model predictions for each driver at each event, including confidence bounds. Filled in with actual results post-race for accuracy tracking. |
| `LineupRecommendation` | Stores the optimizer's recommended 5-driver + 2-constructor lineup including DRS Boost pick. Filled in with actual points post-race. |

**Key decisions:**

- Fantasy data models (`FantasyDriverScore`, `FantasyDriverPrice`, etc.) live in `predictions` not `core`. `core` is FastF1 telemetry data; `predictions` is the fantasy game layer and everything built on top of it.
- `FantasyDriverScore` stores one row per scoring line item (not one row per race) so we can analyse which categories (overtakes, fastest lap, positions gained) each driver earns points from.
- `RacePrediction` has a `model_version` field so multiple model variants can run side-by-side for comparison.
- `LineupRecommendation.unique_together = (event, strategy_type, model_version)` — one recommendation per strategy per model version per race.

---

## Step 2 — Feature Store v1

**Location:** `f1_data/predictions/features/`

**What was built:**

- `predictions/features/base.py` — `FeatureStore` Protocol (interface definition)
- `predictions/features/v1_pandas.py` — `V1FeatureStore` implementation
- `predictions/tests/factories.py` — DB model factory functions for tests
- `predictions/tests/test_features_v1.py` — 33 tests

**What a feature vector is:**

For each (driver, event) pair, the feature store returns a flat `dict[str, float]`. Every key is a number — ML models only understand numbers. These features describe everything we know about a driver *before lineup lock*. The model learns which combinations are predictive of fantasy points.

**The 15 features:**

| Feature | Source model(s) | What it measures |
|---------|----------------|-----------------|
| `position_mean_last3` | `SessionResult` (Race) | Recent finishing form — last 3 races |
| `position_mean_last5` | `SessionResult` (Race) | Recent finishing form — last 5 races |
| `position_std_last5` | `SessionResult` (Race) | Consistency vs volatility (0 = always same position) |
| `dnf_rate_last10` | `SessionResult` (Race) | Reliability — fraction of races that ended in retirement |
| `positions_gained_mean_last5` | `SessionResult` (Race) | Overtaking tendency — mean (grid_pos − finish_pos) |
| `qualifying_position_mean_last3` | `SessionResult` (Qualifying) | Historical one-lap pace — last 3 qualifying sessions |
| `circuit_position_mean_last3` | `SessionResult` + `Circuit` | Track-specific affinity — last 3 visits to this circuit |
| `team_position_mean_last5` | `SessionResult` via `Team` | Car competitiveness — both drivers, last 5 races |
| `fantasy_points_mean_last3` | `FantasyDriverScore` | Direct target proxy — actual fantasy points, last 3 races |
| `practice_best_lap_rank` | `Lap` (FP1/FP2/FP3) | Qualifying pace proxy — rank by single best practice lap |
| `practice_avg_best_5_rank` | `Lap` (FP1/FP2/FP3) | Race pace proxy — rank by average of 5 best practice laps |
| `circuit_length` | `Circuit` | Track character — affects tyre wear, overtaking |
| `total_corners` | `Circuit` | Track character — high vs low downforce circuits |
| `round_number` | `Event` | Season stage — performance patterns shift across a season |
| `is_sprint_weekend` | `Event` | Sprint weekends have different scoring structures |

**Key decisions:**

- **Lineup lock timing:** Lineups must be submitted before qualifying starts (or before Sprint Qualifying on sprint weekends). This means the current event's qualifying position is NOT a valid feature — we don't know it yet. Historical qualifying positions are fine.
- **Cross-season queries use `driver__code`:** The `Driver` model is per-season (`unique_together = (season, code)`), so "VER 2023" and "VER 2024" are different DB records with different PKs. All cross-season queries (race form, qualifying form, circuit history, fantasy points) match on `driver__code` ("VER") to correctly pull multi-season history. Only practice pace queries use `driver_id` since practice laps are always for the current event's season.
- **Ranks not raw times for practice:** `practice_best_lap_rank` and `practice_avg_best_5_rank` are ranks (1=fastest) rather than lap times in seconds, so they're comparable across circuits. Monaco laps are ~75s, Monza ~82s — P1 at both means the same thing.
- **`practice_best_lap_rank` vs `practice_avg_best_5_rank`:** Best single lap correlates with qualifying pace (maximum effort, fresh tyres, low fuel). Average of 5 best laps correlates with race pace (sustainable effort across stints). A driver with a big gap between these two ranks may be fast in bursts but degrades.
- **Defaults for missing data:** Every feature has a sensible mid-field default (position 10, rank 10, rate 0.0) so new drivers or events with incomplete data still produce a valid feature vector. ML models require a number for every feature.
- **`team_position_mean_last5` stays same-season:** Team is also a per-season model. For constructor form we use the current season's team record, which is correct — we want to know how the current car is performing, not how a driver's historical team performed.

---

## Step 3 — XGBoost Performance Predictor v1

**Location:** `f1_data/predictions/predictors/`

**What was built:**

- `predictions/predictors/base.py` — `PerformancePredictor` Protocol
- `predictions/predictors/xgboost_v1.py` — `XGBoostPredictor`, `build_training_dataset`, `walk_forward_splits`
- `predictions/tests/test_xgboost_v1.py` — 16 tests

**New dependencies:** `xgboost==3.2.0`, `scikit-learn>=1.4` added to `requirements.txt`. macOS also requires `brew install libomp` for XGBoost's OpenMP runtime.

**How XGBoost works:**

Gradient boosting builds an ensemble of decision trees sequentially. Each new tree is trained to correct the errors of the combined previous trees. After 100 trees (our default), the prediction is their weighted sum. This handles non-linear relationships well — e.g. the fantasy point difference between P1 and P2 (7 pts) vs P11 and P12 (0 pts) is non-linear and a linear model would struggle.

**What `XGBoostPredictor` does:**

Trains two separate `XGBRegressor` models — one predicting `finishing_position`, one predicting `fantasy_points`. Separate models are simpler and work well because the targets, while correlated, diverge when bonuses (fastest lap, Driver of the Day) are involved.

| Method | Description |
|--------|-------------|
| `fit(X, y)` | Trains both models. `X` is the features DataFrame (including `driver_id`). `y` has `finishing_position` and `fantasy_points` columns. After fitting, computes residual std dev for confidence bounds. |
| `predict(features)` | Returns a DataFrame with `driver_id`, `predicted_position`, `predicted_fantasy_points`, `confidence_lower`, `confidence_upper`. |

**`build_training_dataset(events, feature_store)`**

Iterates over historical events, calls `get_all_driver_features` for each, and pairs features with actual `SessionResult.position` and `FantasyDriverScore.race_total`. Rows are skipped when either target is missing. Returns `(X, y)` DataFrames.

**`walk_forward_splits(events, min_train=5)`**

Generator that yields `(train_events, test_event)` pairs. With 10 events and `min_train=5`, yields 5 splits where training data grows by one race each time and the test event is always in the future. The backtester (Step 5) uses this to drive evaluation.

**Confidence bounds (v1 simplification):**

After `fit()`, computes the standard deviation of prediction errors on the training set. Uses `predicted_points ± 1 std dev` as confidence bounds. This is a lower bound on real error (training data is easier than test data) but gives the optimizer a rough uncertainty signal. v2 will use proper quantile regression (`objective='reg:quantileerror'`).

**Key decisions:**

- **`XGBRegressor` via scikit-learn API:** XGBoost has a native API and a scikit-learn compatible API. We use the sklearn API (`XGBRegressor`) because it follows the familiar `fit/predict` pattern and integrates with sklearn utilities in future steps.
- **`verbosity=0`:** Suppresses XGBoost's training output. By default it prints progress to stdout on every fit call, which clutters management command output.
- **`driver_id` stripped before training:** The feature DataFrame includes a `driver_id` column for identification, but it must not be fed to the model as a feature (it's an arbitrary integer PK, not a meaningful signal). The predictor learns which columns are features at `fit()` time and uses those same columns at `predict()` time.
- **Two models, not one multi-output model:** Sklearn's `MultiOutputRegressor` wraps a single model for multiple targets. Two separate models are equivalent but easier to inspect and tune independently.

---

## What Is Not Yet Built

| Step | What | Status |
|------|------|--------|
| Step 3 | XGBoost performance predictor | done |
| Step 4 | Greedy knapsack optimizer | todo |
| Step 5 | Walk-forward backtester | todo |
| Step 6 | Management commands (predict_race, optimize_lineup, backtest) | todo |
| Step 7 | import_fantasy_csv management command | todo |
| Step 8 | Price predictor v1 (heuristic) | todo |
| Step 9 | Slack integration | todo |
