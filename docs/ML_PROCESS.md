# ML Pipeline — Implementation Log

This document tracks what has been built, the decisions made, and why. Updated as each step is completed.

For the full plan (architecture, upgrade paths, build order) see `ML_PIPELINE_PLAN.md`.

---

## Architecture

```
core app (existing)          predictions app (new)
─────────────────────        ─────────────────────────────────────────
Season                  →    Feature Store
Circuit                      ├── v1_pandas.V1FeatureStore  (15 features)
Team                         ├── v2_pandas.V2FeatureStore  (25 features — weather, car-circuit fit, driver intelligence)
Driver                       ├── v3_pandas.V3FeatureStore  (29 features — richer wet weather, pruned zero-importance)
SessionResult                └── v4.V4FeatureStore         (46 features — adds 8 FP telemetry + 7 form-direction + 2 weather) ← current best
Event                                      │
Session                                    ▼
SessionResult                Performance Predictor
Lap                          ├── XGBoostPredictor (v1)      MSE mean ± training residual std
WeatherSample                ├── XGBoostPredictorV2 (v2)    MSE mean + q10/q90 quantile bounds
                             ├── XGBoostPredictorV3 (v3)    V2 + exponential decay sample weights
                             └── XGBoostPredictorV4 (v4)    V3 + XGBRanker pairwise objective + linear calibration ← current best
                                           │
                                           ▼
                             Optimizer
                             ├── GreedyOptimizer (v1)       PPM greedy only
                             ├── GreedyOptimizerV2 (v2)     PPM greedy + upgrade pass + transfer constraints
                             ├── ILPOptimizer (v3)          Integer Linear Programming — provably optimal + transfer penalty in objective
                             └── MonteCarloOptimizer (v4)   Sample 500 scenarios from q10/q90 bounds; return highest average-scoring candidate
                                           │
                                           ▼
                             Backtester  (walk-forward, price-aware, shared oracle cache)
                                           │
                                           ▼
                             Management Commands
                             ├── next_race        (main weekly command — train + predict + optimize)
                             ├── backtest         (--feature-store v1/v2/v3, --predictor v1/v2/v3/v4, --optimizer v1/v2/v3/v4)
                             ├── tune_hyperparams (random search over XGBoost params)
                             ├── predict_race     (superseded by next_race)
                             └── optimize_lineup  (superseded by next_race)
```

The `predictions` app depends on `core` data but `core` knows nothing about predictions. Each layer talks to the next through a defined interface (Python Protocol), so implementations can be swapped independently.

---

## Running Experiments

### Backtesting

Walk-forward backtesting re-runs the full train→predict→optimize loop on historical data. For each race, it trains on all prior races, predicts the current race, and selects a lineup — exactly as the system would have operated live.

```bash
cd f1_data
python manage.py backtest --seasons 2022 2023 2024 2025 --min-train 5
```

Options:
- `--feature-store v1|v2|v3|v4` (default: v2) — one or more versions to sweep
- `--predictor v1|v2|v3|v4` (default: v2) — one or more versions to sweep
- `--optimizer v1|v2|v3|v4` (default: v2) — one or more versions to sweep
- `--min-train N` (default: 5) — minimum races before making the first prediction
- `--budget 100` (default: 100)
- `--verbose` — print each race's selected lineup and cost

Output columns: **MAE Pos**, **MAE Pts**, **Lineup** (actual points scored), **Optimal** (oracle ceiling), **Trades**.

**The oracle is always the same number regardless of optimizer.** It is computed once per race by running ILP with *actual* post-race points as inputs — the best lineup that was theoretically achievable given perfect knowledge. When passing multiple `--optimizer` values, this is pre-computed once and shared so the comparison is clean: both optimizers are measured against an identical ceiling.

Pass multiple values to `--feature-store`, `--predictor`, or `--optimizer` to run all combinations in one go:

```bash
# Compare ILP (v3) vs Monte Carlo (v4) with the v4 predictor's confidence bounds
python manage.py backtest --feature-store v3 --predictor v4 --optimizer v3 v4 --seasons 2024

# Compare v2 and v3 predictor with v3 feature store
python manage.py backtest --feature-store v3 --predictor v2 v3 --optimizer v2 --seasons 2022 2023 2024 2025
```

Sweep shortcuts:
```bash
# All optimizer versions (v1/v2/v3/v4) with fixed fs=v2, pred=v2
python manage.py backtest --seasons 2024 2025 --all-optimizers

# All combinations across all registered versions of every component
python manage.py backtest --seasons 2024 2025 --all
```

Both sweep flags send a Slack summary when complete.

---

### Hyperparameter tuning

Random search over the XGBoost hyperparameter space using TimeSeriesSplit cross-validation. Searches ~972 combinations; `--n-iter` controls how many are sampled. Run this when the predictor parameters in V2/V3 might need updating.

```bash
python manage.py tune_hyperparams \
  --seasons 2022 2023 2024 2025 \
  --feature-store v3 \
  --n-iter 50 \
  --top-n 10 \
  --n-splits 4
```

Options:
- `--seasons` — year(s) to build the training dataset from
- `--feature-store v1|v2|v3` (default: v3)
- `--n-iter` (default: 50) — random combinations to evaluate
- `--top-n` (default: 10) — top results to show in the ranked table
- `--n-splits` (default: 4) — CV folds

Output: ranked table of param combinations with mean CV MAE, alongside the V2 defaults as a baseline.

---

## Current Best Configuration

```bash
python manage.py backtest --feature-store v3 --predictor v2 v3 --optimizer v2 --seasons 2022 2023 2024 2025
```

| Config | MAE Pos | MAE Pts | Total Lineup Pts | Pts Left on Table |
|--------|---------|---------|-----------------|-------------------|
| fs=v3, pred=v2, opt=v2 | 3.56 | 8.43 | 14,037 | 5,486 |
| **fs=v3, pred=v3, opt=v2** | **3.55** | **8.43** | **14,490** | **5,033** |

Tested over 2022–2025 seasons, 87 predictions, `--min-train 5`.

Pred=v3 (recency weighting) achieves **+453 lineup points (+3.2%)** with identical MAE. The improvement comes from better relative driver ranking rather than better average accuracy — recency weighting causes the model to focus on modern F1 patterns. See `DECISIONS.md` entry 2026-03-19 for full analysis including feature importance shifts.

**v4 predictor + v4 optimizer (pending results):**

```bash
# Recommended comparison run — v4 predictor is required for MC because it produces
# the q10/q90 confidence bounds that MC samples from. v3 opt is the control.
python manage.py backtest --feature-store v3 --predictor v4 --optimizer v3 v4 --seasons 2024 --min-train 5
```

The oracle column will be identical for both optimizer rows (shared ILP cache). A higher lineup score for v4 vs v3 confirms that MC's scenario sampling finds better lineups than ILP-on-mean when the predictor's confidence bounds reflect real outcome uncertainty.

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

## Feature Store v4

**Location:** `f1_data/predictions/features/v4.py`

**What was built:**

- `predictions/features/v4.py` — `V4FeatureStore` extending V3 with 15 new features
- `predictions/tests/test_features_v4.py` — 45 tests (telemetry + form direction)

V4 adds two groups of features on top of V3's 29:

### Group 1 — Practice Telemetry (8 features)

These come from FP lap data — signals that weren't available in V1–V3 at all.

| Feature | What it measures |
|---------|-----------------|
| `fp_long_run_pace_rank` | Median lap time in qualifying long-run stints (≥5 laps), ranked 1=fastest. Prefers FP2; falls back to FP1+FP2 if fewer than half the field have FP2 long-run data. |
| `fp_tyre_deg_rank` | Average OLS slope of lap time vs tyre age in FP2 long runs, ranked 1=lowest degradation. |
| `fp_sector1_rank` | Best sector 1 time across all FP sessions, ranked 1=fastest. |
| `fp_sector2_rank` | Same for sector 2. |
| `fp_sector3_rank` | Same for sector 3. |
| `fp_total_laps` | Total FP laps (all sessions, passing quality filters). Low count signals setup problems. |
| `fp_session_availability` | Number of FP sessions with any lap data (0–3). Sprint weekends give 1.0. |
| `fp_short_vs_long_delta` | `practice_best_lap_rank - fp_long_run_pace_rank`. Positive = stronger in short bursts (quali specialist); negative = stronger in sustained pace (race specialist). |

All rank features default to 10.5 (mid-field) when data is absent.

### Group 2 — Form Direction (7 features)

These capture the *trend* in a driver's recent results, not just the average. A driver going 5→4→3 and one going 3→4→5 look identical to V3's `position_mean_last3` but are very different situations.

| Feature | Definition | Default |
|---------|-----------|---------|
| `position_last1` | Finishing position in the most recent race | 18.0 (NEW_ENTRANT_POSITION_DEFAULT) |
| `position_slope` | OLS slope of finishing positions over last 5 races. Negative = improving. | 0.0 |
| `best_position_last5` | Best (minimum) finishing position in last 5 races. DNFs → 20.0. | 18.0 |
| `teammate_delta` | `driver_position_last1 - teammate_position_last1`. Negative = driver beat teammate. | 0.0 |
| `team_best_position` | Minimum finishing position across both team cars over last 5 races. Car ceiling signal. | 18.0 |
| `quali_last1` | Qualifying position at the most recent event. | 18.0 |
| `quali_slope` | OLS slope of qualifying positions over last 5 events. Negative = improving. | 0.0 |

**Key decisions:**

- **Cross-season query by `driver__code`:** Same pattern as V3 — `VER-2024` and `VER-2025` are different DB records but the same driver. Querying by code gives correct multi-season history.
- **DNF → 20.0, rookie → 18.0:** A driver who retires started the race and failed — slightly worse than last place. A true rookie has never raced — mid-field default of 18.0 is more neutral. Consistent with V1's convention.
- **`teammate_delta` uses last1:** Provides a different signal from V3's rolling `driver_vs_teammate_gap_last5`. The single last-race delta captures current form momentum vs teammate, not the rolling average.
- **`team_best_position` uses last5:** The "car ceiling" — what's the best this car can do recently? Useful for distinguishing a team with one lucky outlier race vs consistently strong pace.
- **OLS slope via `np.polyfit`:** Same approach as `fantasy_points_trend_last5` in V2. Requires ≥2 data points; defaults to 0.0 when insufficient (first 3 rounds of a season).

**Verification:**

```bash
python manage.py test predictions.tests.test_features_v4

# Sanity check: print form features for one event
python manage.py shell -c "
from predictions.features.v4 import V4FeatureStore
from core.models import Event
e = Event.objects.filter(season__year=2024).order_by('event_date')[5]
fs = V4FeatureStore()
df = fs.get_all_driver_features(e.id)
print(df[['driver_id','position_last1','position_slope','best_position_last5','teammate_delta','team_best_position','quali_last1','quali_slope']].to_string())
"

# Backtest comparing V3 vs V4 feature stores
python manage.py backtest --feature-store v3 v4 --predictor v4 --optimizer v4 --seasons 2024
```

New features are non-null for most drivers from round 4 onwards (first 3 rounds lack enough history for slopes).

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

## XGBoost Performance Predictor v2 (Hybrid)

**Location:** `f1_data/predictions/predictors/xgboost_v2.py`

**What was built:**

`XGBoostPredictorV2` — satisfies the same `PerformancePredictor` Protocol as V1 (same `fit()`/`predict()` signature, same output columns). Internally it trains **four** models instead of two.

| Model | Objective | Output column |
|-------|-----------|---------------|
| `_position_model` | MSE (standard) | `predicted_position` |
| `_points_mean_model` | MSE (standard) | `predicted_fantasy_points` |
| `_points_q10` | `reg:quantileerror`, α=0.1 | `confidence_lower` |
| `_points_q90` | `reg:quantileerror`, α=0.9 | `confidence_upper` |

**Why hybrid (MSE mean + quantile bounds), not pure quantile?**

The first V2 iteration used the q50 (median) for `predicted_fantasy_points`. Backtest over 2024–2025 (43 races) showed:

| Approach | MAE Pts | Total lineup pts |
|----------|---------|-----------------|
| V1: MSE mean ± residual std | 8.50 | **8374** |
| Pure quantile (q50 as point estimate) | 8.20 | 7771 |

Lower MAE but 603 fewer lineup points. The problem: F1 fantasy points are right-skewed. A driver who usually scores 8pts but occasionally hits 50pts has mean ~15pts and median ~8pts. The greedy optimizer ranks by `predicted_fantasy_points / price`, so systematically undervaluing high-upside drivers produces worse lineups. **The mean is the correct optimization target for a greedy expected-value maximizer.**

The hybrid keeps MSE for the point estimate (good optimizer signal) and adds quantile regression only for the bounds (calibrated uncertainty).

**What `confidence_lower`/`confidence_upper` actually mean:**

V1's ±std bounds were symmetric and measured in-sample error — always an underestimate and the same width for every driver. V2's q10/q90 bounds are:
- **Asymmetric**: a right-skewed driver has a wider upper gap (q90 - mean) than lower gap (mean - q10)
- **Driver-specific width**: high-variance drivers (inconsistent results) get wider bands than consistent midfielders
- **Calibrated**: trained to hit the actual 10th/90th percentile of outcomes, not a post-hoc approximation

**Quantile crossing:**

Three independently trained quantile models can produce out-of-order predictions on unseen data (e.g. q10 > q90 on a specific row). The `predict()` method enforces ordering via `np.minimum(q10, q90)` and `np.maximum(q10, q90)`.

**Future use of confidence bounds:**

The current greedy optimizer ignores the bounds entirely — it only uses `predicted_fantasy_points`. The bounds become useful when the optimizer is upgraded to risk-adjusted selection:

```python
# Future optimizer objective (not yet built):
score = predicted_fantasy_points - λ * (confidence_upper - confidence_lower)
```

Wide interval → high-risk/high-reward pick. λ controls risk appetite. This is the planned "stochastic optimization" upgrade.

**Key decisions:**

- **No q50 model:** A median model adds training cost but the q50 prediction is not needed anywhere in the current pipeline. If the future optimizer needs the median it can be added then.
- **Shared utilities with V1:** `build_training_dataset` and `walk_forward_splits` live in `xgboost_v1.py` and are imported directly — no duplication.
- **`backtest` defaults updated:** `--feature-store` and `--predictor` both default to `v2`. Pass `--predictor v1` to compare against the baseline.

Tests: `predictions/tests/test_xgboost_v2.py` — 9 tests (`SimpleTestCase`, no DB)

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

---

## Optimizer + Backtester Improvements (post-Step 8)

Three successive improvements to the optimizer and backtester, each verified to improve backtest scores.

### A — Budget Maximisation (`greedy_v2.GreedyOptimizerV2`)

**Location:** `f1_data/predictions/optimizers/greedy_v2.py`

`GreedyOptimizerV1` picks by PPM (points-per-dollar) and stops. If the budget isn't tight, unspent money could have been used to upgrade picks to higher-scoring alternatives.

`GreedyOptimizerV2` adds an **upgrade pass** after greedy selection: iterates over every picked player and swaps them for the best unpicked player who scores more and fits in the remaining budget. Repeats until stable. This ensures the full budget is spent optimally.

`greedy_v1.py` is preserved unchanged as a baseline. `greedy_v2.py` imports `_pick_greedily` from v1 and adds `_upgrade_picks` on top. All commands (`backtest`, `optimize_lineup`) updated to use v2.

Tests: `predictions/tests/test_greedy_v2.py`

### B — Price-Aware Lineup Selection (`backtester.py`)

**Location:** `f1_data/predictions/evaluation/backtester.py`

A driver whose price is predicted to rise $2M next race gives you $2M more budget to spend in future races. That future value should influence which lineup you pick now.

Before each race's lineup optimisation, `_price_adjust_predictions` boosts each driver's `predicted_fantasy_points` by `expected_price_change * PRICE_SENSITIVITY` (default: 5.0 — meaning $1M of predicted appreciation = 5 bonus points). Uses `predict_price_trajectory` with horizon=1.

The backtester maintains a `rolling_scores` dict tracking the last 3 `(actual_pts, price)` pairs per driver, updated after each race. This seeds the price trajectory calculation with real history.

**Key decision:** MAE is computed on *raw* predictions. Price adjustment only affects lineup selection, not accuracy measurement.

### C — Transfer Constraints (`greedy_v2.py` + `backtester.py`)

Real F1 Fantasy: 2 free transfers per race, extras cost 10 points each, 1 unused transfer banks to the next race (max 2 banked).

**Backtester** now tracks `current_lineup` across races, passes it as a constraint to the optimizer, and deducts `max(0, n_transfers - 2) * 10` from `lineup_actual_points`. The oracle optimal is still computed without constraints (it's the theoretical ceiling).

**Optimizer** (`_apply_transfer_constraints`) finds the diff between ideal and current lineup, pairs swaps by gain (drop worst, bring best), and applies: all free transfers + any paid ones where `gain > transfer_penalty`. Budget-checks and reverts the last paid change if over.

`RaceBacktestResult` gains an `n_transfers` field. The `backtest` command shows a `Trades` column.

**Why constraints improved scores:** The 10-point penalty gate acts as a confidence filter — the optimizer only acts on large predicted differences, ignoring noise. Without constraints the optimizer was "overfitting" to noisy predictions by rebuilding from scratch each race. This is analogous to regularisation in ML: adding a penalty for complexity improves out-of-sample performance.

Tests: `TestApplyTransferConstraints` in `predictions/tests/test_greedy_v2.py`

---

---

## ILP Optimizer v3

**Location:** `f1_data/predictions/optimizers/ilp_v3.py`

**What was built:**

- `predictions/optimizers/ilp_v3.py` — `ILPOptimizer` using `scipy.optimize.milp`
- `predictions/tests/test_ilp_v3.py` — 16 tests (shape, budget, DRS, optimality, infeasibility, transfer penalty)

**Why ILP over greedy:**

Greedy optimizers pick one player at a time and never reconsider. They can get trapped: a driver with a great points/price ratio gets picked early, consuming budget that could have funded two cheaper drivers who together score more. ILP frames the entire problem as a single mathematical question and proves its answer cannot be beaten by any other valid lineup.

**Variables (all binary except `e`):**

| Variable | Meaning |
|----------|---------|
| `x[i]` | Driver i is in the lineup (binary) |
| `y[j]` | Constructor j is in the lineup (binary) |
| `z[i]` | Driver i receives the DRS boost (binary) |
| `e` | Excess transfers beyond free allowance (continuous, ≥ 0) |

**Objective (minimise):**
```
-(Σ pts[i]·x[i]  +  Σ pts[j]·y[j]  +  Σ pts[i]·z[i])  +  transfer_penalty · e
```
The DRS driver appears in both x and z so their points count twice. The penalty term `transfer_penalty · e` is the ILP equivalent of v2's post-hoc transfer constraint logic.

**Constraints:**
1. `Σ price[i]·x[i] + Σ price[j]·y[j] ≤ budget`
2. `Σ x[i] = 5`
3. `Σ y[j] = 2`
4. `Σ z[i] = 1`
5. `z[i] - x[i] ≤ 0` for each driver (DRS driver must be selected)
6. `Σ_{new} x[i] + Σ_{new} y[j] - e ≤ free_transfers` (when current_lineup provided)

**How the transfer penalty works in ILP:**

Constraint (6) defines `e = max(0, total_new_players - free_transfers)` — the number of paid transfers. The solver minimises `penalty · e` as part of the objective. It never makes a transfer unless the predicted points gain exceeds the penalty. Players retained from the previous race have coefficient 0 in the transfer sum (keeping them is always free).

This is preferable to greedy v2's post-hoc approach (find ideal lineup, then prune transfers). ILP considers the penalty *during* optimisation, meaning it can find lineups that are globally better when accounting for cost — e.g. a combination of two cheap retained drivers that beats one expensive new driver minus the penalty.

**Why `predicted_points` in the returned Lineup does not include the penalty:**

The backtester deducts transfer penalties from `lineup_actual_points` separately (based on real post-race counts). `predicted_points` is the raw lineup score used for comparison and reporting. Subtracting the penalty from it would cause double-counting.

**Noise sensitivity and the transfer threshold:**

ILP v3 is provably optimal given perfect predictions. In practice, with MAE = 8.5 pts,
small predicted gains are often noise rather than signal. With 2 free transfers, the ILP
will freely make a 2nd transfer for any predicted gain > 0 pts — including gains that are
well within the noise margin. Backtesting showed this caused v3 to underperform v2 greedy
(6554 vs 7432 total pts over 2024–2025) despite having a higher oracle ceiling (10324 vs 9243).

The fix is `ILP_TRANSFER_THRESHOLD` in settings.py. This adds a per-transfer cost T to new
players in the ILP objective — effectively requiring predicted gain > T before a transfer is
made. Set to 8.5 (current MAE) initially. As prediction accuracy improves and MAE falls,
lower this value to let the ILP be more aggressive. A perfectly accurate model would use T=0.

**Key decisions:**

- **scipy.optimize.milp, not PuLP:** scipy is already a transitive dependency (via FastF1→matplotlib→numpy). No new packages needed. `milp` requires scipy ≥ 1.7.0 (2021).
- **Slack variable `e` (not auxiliary binary variables):** Transfer count is a linear expression (sum of x[i] for new drivers). Introducing `e ≥ transfers - free` with `e ≥ 0` and minimising `penalty·e` handles `max(0, ...)` without branching. Clean and efficient.
- **`e` is continuous, not integer:** Transfer counts are always integers since x/y are binary, so `e` settles at an integer anyway. Leaving it continuous avoids constraining the solver unnecessarily.
- **`--all-optimizers` flag (not `--all`):** The existing `--all` sweeps 8 combos of feature-store × predictor × optimizer (v1/v2 only). `--all-optimizers` is a separate flag that fixes fs=v2, pred=v2 and sweeps v1/v2/v3 — isolating the optimizer dimension for a clean comparison.
- **`ILP_TRANSFER_THRESHOLD` in settings.py:** Calibrate to current MAE. Prevents the ILP acting on noise. Revisit whenever the predictor is significantly improved.

**Verification:**

```bash
python manage.py test predictions.tests.test_ilp_v3
python manage.py backtest --seasons 2024 --optimizer v3 --min-train 10
python manage.py backtest --seasons 2024 --all-optimizers   # compare v1/v2/v3 side-by-side
```

---

## What Is Not Yet Built

| Step | What | Status |
|------|------|--------|
| Step 7 | Fantasy data import (import_fantasy_csv, compute_fantasy_prices) | done |
| Step 7b | compute_fantasy_points (from raw FastF1 data) | deferred |
| Step 8 | Price predictor v1 (heuristic) | done |
| Optimizer improvements | Budget maximisation, price-aware selection, transfer constraints | done |
| Predictor v2 | Hybrid MSE mean + quantile bounds | done |
| Step 9 | Slack integration | todo |
| Future | Risk-aware optimizer: `score = mean - λ·(upper - lower)` | deferred — needs stronger baseline first |
