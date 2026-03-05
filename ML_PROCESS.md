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
Session                      Performance Predictor  [Step 3 — done]
SessionResult                └── XGBoost v1
Lap                                        │
WeatherSample                              ▼
                             Optimizer  [Step 4 — done]
                             └── Greedy knapsack v1
                                           │
                                           ▼
                             Backtester  [Step 5 — done]
                                           │
                                           ▼
                             Management Commands  [Step 6 — done]
                             ├── predict_race
                             ├── optimize_lineup
                             └── backtest
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

---

## Step 4 — Greedy Lineup Optimizer v1

**Location:** `f1_data/predictions/optimizers/`

**What was built:**

- `predictions/optimizers/base.py` — `Lineup` frozen dataclass + `LineupOptimizer` Protocol
- `predictions/optimizers/greedy_v1.py` — `GreedyOptimizer` + `_pick_greedily` helper
- `predictions/tests/test_greedy_v1.py` — 17 tests

**The optimisation problem:**

Given ~20 drivers and ~10 constructors with predicted fantasy scores and prices, pick 5 drivers + 2 constructors within a $100M budget to maximise total fantasy points. This is a variant of the **0/1 knapsack problem** (each player can only be picked once). Exact solution requires checking C(20,5) × C(10,2) = 697,680 combinations — feasible but overkill for v1.

**Greedy approach:**

1. Score every player by `value = predicted_fantasy_points / price` (points per dollar)
2. Sort by value descending
3. Pick greedily with **budget lookahead**: before picking player i, verify that `player.price + cheapest(slots_left − 1 remaining players)` fits within the remaining budget. This prevents committing to an expensive early pick that makes remaining slots unaffordable.

**DRS Boost rule:**

The DRS Boost driver scores double their points. Total formula: `sum(driver pts) + sum(constructor pts) + drs_driver_pts`. Always assign DRS to the highest-scoring driver in the lineup.

**Constructor budget reservation:**

Before picking drivers, reserve `sum(cheapest 2 constructor prices)` from the budget. This prevents spending too much on drivers and having nothing left for constructors.

**Key decisions:**

- **Greedy not brute-force:** Greedy is O(n log n). For a weekly tool where we run it once, brute force would also work, but greedy is easier to reason about and fast enough. v2 can use integer linear programming (PuLP/scipy) for optimality guarantees.
- **Two-phase picking:** Drivers first (with constructor budget reserved), then constructors from remaining spend. The two categories don't overlap so they can be picked independently.
- **Budget lookahead uses future candidates only:** Once we pass a candidate in value-sorted order, we never revisit it. So "cheapest remaining options" = cheapest candidates after the current one in the sorted list.
- **Value = pts/price not just pts:** A driver with 40pts at $10M is better value than 50pts at $20M if other slots need filling. Value ranking finds efficient combinations.

---

---

## Step 5 — Walk-forward Backtester

**Location:** `f1_data/predictions/evaluation/`

**What was built:**

- `predictions/evaluation/backtester.py` — `Backtester`, `BacktestResult`, `RaceBacktestResult`, and helpers
- New factories in `predictions/tests/factories.py` — `make_driver_price`, `make_constructor_price`, `make_constructor_score`
- `predictions/tests/test_backtester.py` — 15 tests

**What the backtester does:**

For each race in a walk-forward split (train on races 1..N, test on N+1):
1. Build training dataset from train events
2. Fit the predictor
3. Get features for test event → predict
4. Compare predictions against actual results (MAE)
5. If price data is available: optimize lineup → score it → compute the oracle (optimal) lineup

**Metrics per race:**

| Metric | Description |
|--------|-------------|
| `mae_position` | Mean absolute error on finishing position predictions |
| `mae_fantasy_points` | Mean absolute error on fantasy points predictions |
| `lineup_predicted_points` | Total score the optimizer expected (from predictions) |
| `lineup_actual_points` | What that lineup actually scored post-race |
| `optimal_actual_points` | Best possible lineup score with perfect knowledge |

**The oracle ceiling:**

To compute `optimal_actual_points`, we run the optimizer a second time but with actual points as the "predictions." This tells us: if we had a perfect model, what's the most we could score? The gap between `lineup_actual_points` and `optimal_actual_points` shows how much prediction error is costing us in fantasy points.

**Constructor predictions without a constructor predictor:**

We don't have a dedicated constructor model yet. Instead, predicted constructor points = sum of the two team drivers' predicted fantasy points. This is a reasonable proxy since constructors score the combined points of their drivers. A proper constructor model (pit stop speed, Q3 progression) is a future upgrade.

**Key decisions:**

- **`run()` takes events, not seasons:** The backtester is a pure evaluation function. The management command translates seasons → events and passes them in. This keeps the backtester testable without a full season in the DB.
- **Lineup metrics optional:** When `FantasyDriverPrice` / `FantasyConstructorPrice` are missing for an event (e.g. historical seasons we didn't scrape), the backtester skips lineup metrics but still computes MAE metrics. Fields are `None` rather than silently defaulting to 0.
- **`BacktestResult` as a dataclass with properties:** `mean_mae_position`, `total_lineup_points`, etc. are computed lazily from the list of `RaceBacktestResult` objects rather than stored separately. This keeps the data and the derived aggregates in one place.

---

---

## Step 6 — Management Commands

**Location:** `f1_data/predictions/management/commands/`

**What was built:**

Three thin management commands that wire the existing pipeline layers together for real-world use.

### `predict_race` — `python manage.py predict_race --year 2024 --round 5`

Trains XGBoost on all past events (by `event_date`), generates predictions for the target event, saves `RacePrediction` records to the DB, then prints a table sorted by predicted fantasy points.

Key behaviour:
- Training events = all events with `event_date < target event_date` (cross-season, chronological)
- Uses `update_or_create` so re-running overwrites stale predictions rather than duplicating
- Skips drivers with no DB record in the target event's season (guards against data gaps)

### `optimize_lineup` — `python manage.py optimize_lineup --year 2024 --round 5 --budget 100`

Reads stored `RacePrediction` + `FantasyDriverPrice` / `FantasyConstructorPrice` records, runs `GreedyOptimizer`, saves a `LineupRecommendation`, and prints the team with DRS Boost marked.

Key behaviour:
- Deliberately depends on `predict_race` and `import_fantasy_csv` running first — separation of concerns
- Constructor predicted points = sum of both team drivers' predicted points (no dedicated constructor model yet)
- Saves with `strategy_type="single_race"` — multi-race horizon strategies will have different tags

### `backtest` — `python manage.py backtest --seasons 2023 2024 --min-train 5`

Chains all layers (feature store → predictor → optimizer → backtester) over the specified seasons and prints a per-race table with MAE and lineup scores, plus aggregate summary statistics.

Output columns:
- **MAE Pos** — mean absolute error on predicted finishing position
- **MAE Pts** — mean absolute error on predicted fantasy points
- **Lineup** — actual points scored by the optimizer's chosen lineup
- **Optimal** — best achievable lineup score with perfect knowledge (oracle ceiling)

**Key design principle across all three commands:**

All logic lives in the layer modules already built (Steps 2–5). The commands are pure plumbing: parse args → instantiate layers → call them → format output. This means the commands are easy to test manually but don't need unit tests themselves (Django's management command testing infrastructure is heavy and the logic being tested is already covered).

---

---

## Step 7 — Fantasy Data Import

**Location:** `f1_data/predictions/management/commands/`, `f1_data/predictions/price_calculator.py`

**What was built:**

### `predictions/price_calculator.py` — Pure price formula functions

Implements the 2025 F1 Fantasy price change algorithm as pure, testable functions:

| Function | Description |
|----------|-------------|
| `classify_performance(avg_ppm)` | Maps AvgPPM to "great"/"good"/"poor"/"terrible" |
| `compute_price_change(avg_ppm, current_price)` | Returns price change in $M (A-Tier ≥$19M, B-Tier <$19M) |
| `compute_avg_ppm(recent)` | Computes rolling average PPM from last 1–3 races |
| `next_price(current_price, avg_ppm)` | Returns (price_change, new_price) with $3M–$34M clamp |

**The 2025 algorithm:**
- AvgPPM = mean of (race_total / price_at_that_race) over last 1-3 races (no zero-padding for early rounds)
- >1.2: Great, >0.9: Good, >0.6: Poor, ≤0.6: Terrible
- A-Tier (≥$19M): ±0.3/±0.1. B-Tier (<$19M): ±0.6/±0.2

### `import_fantasy_csv` — `python manage.py import_fantasy_csv --dir data/2025/`

Scans a directory for Chrome extension CSV exports and imports them. Detects file type by filename suffix:

| File pattern | Creates |
|---|---|
| `YYYY-MM-DD-drivers.csv` | `FantasyDriverPrice` (price snapshot per driver per event) |
| `YYYY-MM-DD-constructors.csv` | `FantasyConstructorPrice` |
| `YYYY-MM-DD-all-drivers-performance.csv` | `FantasyDriverScore` (one row per scoring line item) |
| `YYYY-MM-DD-all-constructors-performance.csv` | `FantasyConstructorScore` |

**Key decisions:**
- Snapshot date → event: finds the next upcoming event after the snapshot date (prices are taken before the race). Falls back to most recent past event if no upcoming event exists.
- Driver name matching: `Driver.full_name__iexact` — exact match on "Lando Norris" format.
- Race name matching for performance CSVs: `event_name__icontains` — "Australia" matches "Australian Grand Prix", "Saudi Arabia" matches "Saudi Arabian Grand Prix".
- `update_or_create` throughout — safe to re-run; reruns update stale records.

### `compute_fantasy_prices` — `python manage.py compute_fantasy_prices --year 2024 --driver-prices data/2024/starting_driver_prices.csv`

Computes all historical `FantasyDriverPrice` / `FantasyConstructorPrice` records from starting prices + existing `FantasyDriverScore` data using the 2025 formula.

**Starting prices CSV format** (no header, just code and price):
```
NOR,28.0
VER,30.0
HAM,24.5
```

The command chains prices forward race-by-race: price at race N uses AvgPPM computed from races 1..N-1. Uses `bulk_create` after deleting existing records for the season (idempotent). `pick_percentage` and `season_fantasy_points` are set to 0 (not available for computed prices — only real Chrome extension snapshots have them).

**compute_fantasy_points (from raw FastF1 data) is NOT yet built** — this requires computing qualifying positions, fastest lap detection, overtake counting, etc. from FastF1 lap data. Deferred as it's significantly more complex.

---

---

## Step 8 — Price Predictor v1 (Heuristic)

**Location:** `f1_data/predictions/predictors/price_heuristic.py`

**What was built:**

A single pure function `predict_price_trajectory` that simulates the F1 Fantasy price formula forward using predicted fantasy points from XGBoost.

```
predict_price_trajectory(current_price, recent_scores, predicted_points) -> list[Decimal]
```

- `current_price`: driver's price going into the next race (already known)
- `recent_scores`: `(pts, price)` pairs from last 1–3 actual races (rolling window seed)
- `predicted_points`: predicted fantasy points for each future race (from XGBoost)
- Returns: predicted price *after* each future race (= price going into the race after)

**How it works:**

This isn't an ML model — it's an analytical simulation of the known F1 Fantasy price formula. For each future race:
1. Compute AvgPPM from the rolling window (actual history + any previously predicted races)
2. Apply `next_price()` → get the new price
3. Slide the window: drop the oldest entry, add `(predicted_pts, current_price)`
4. Advance `current_price` to the new price

The only source of error is in the predicted fantasy points. If the performance predictor is accurate, the price trajectory will be accurate.

**Key insight:**

Because we know the exact price mechanism (3-race rolling AvgPPM → tier → delta), this heuristic can be surprisingly precise. It's not a proxy — it's the actual formula run forward. The buy-low/sell-high signal quality is limited only by XGBoost's prediction accuracy, not by the price model.

**What this enables:**

The price trajectory lets the optimizer answer: "is this driver worth picking up now even if they score fewer points this race, because their price will rise and give us more budget in future races?" This is the core of the transfer strategy.

Tests: `predictions/tests/test_price_heuristic.py` — 9 tests

---

## What Is Not Yet Built

| Step | What | Status |
|------|------|--------|
| Step 7 | Fantasy data import (import_fantasy_csv, compute_fantasy_prices) | done |
| Step 7b | compute_fantasy_points (from raw FastF1 data) | deferred |
| Step 8 | Price predictor v1 (heuristic) | done |
| Step 9 | Slack integration | todo |
