# F1 Analytics

A project for tracking F1 performance and optimising a Fantasy Formula 1 lineup. The goal is to derive algorithms that make decisions on which constructors and drivers to pick each race week.

---

## Repository layout

```
f1_data/          New data collection pipeline (active development)
f1_analytics/     Original analytics app (web UI, lineup optimiser, fantasy imports)
chrome_extension/ Chrome extension for exporting F1 Fantasy CSV data
```

---

## f1_data — Data collection pipeline

A focused Django + SQLite + FastF1 pipeline that collects historical and live session data (laps, results, weather) for downstream ML/RL use.

### Setup

```bash
cd f1_data
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
```

### Data models

| Model | Description |
|-------|-------------|
| `Season` | A championship year |
| `Circuit` | Track metadata |
| `Event` | A race weekend (round within a season) |
| `Session` | A single session within an event (FP1–FP3, Q, SQ, S, R) |
| `Driver` | Driver per season |
| `Team` | Constructor per season |
| `Lap` | Per-lap data: lap time, sectors, compound, stint, pit, position |
| `SessionResult` | Finishing position, points, grid, status per driver per session |
| `WeatherSample` | Weather readings at 5-minute intervals per session |
| `CollectionRun` | Audit record for each `collect_data` invocation |
| `SessionCollectionStatus` | Per-session collection state: pending / collecting / completed / failed |

### Data flow

```
FastF1 API
  └── fastf1_loader.py   (thin wrapper — only place FastF1 is imported)
        └── data_mappers.py    (transform DataFrames → model instances)
              └── collect_season.py  (orchestrate, write to DB, track status)
```

### Management commands

#### Data collection (`core/`)

| Command | What it does |
|---------|-------------|
| `collect_data` | Pull session data (laps, results, weather) from the FastF1 API |
| `collection_status` | Print a coverage table showing which sessions have been collected |
| `seed_season_reference` | Seed driver and team reference records from a roster JSON file |

```bash
python manage.py collect_data                          # all seasons (2018–present)
python manage.py collect_data --year 2025              # one season
python manage.py collect_data --year 2025 --round 5   # one round
python manage.py collect_data --retry-failed           # also retry previously failed sessions
python manage.py collect_data --force                  # re-collect completed sessions too

python manage.py collection_status                     # summary table across all seasons
python manage.py collection_status --year 2025         # per-session breakdown for one season
python manage.py collection_status --gaps              # show only incomplete sessions
```

Rate limits are handled automatically with exponential backoff (1 min → 5 min → 60 min). A Slack notification is sent on completion and when rate-limited.

#### Fantasy data (`predictions/`)

| Command | What it does |
|---------|-------------|
| `import_fantasy_csv` | Import Chrome extension CSVs: actual fantasy scores and market prices |
| `compute_fantasy_points` | Reconstruct approximate fantasy scores from FastF1 data (no Driver of the Day / pit stop bonus). Use for historical seasons where you don't have Chrome extension data. |
| `compute_fantasy_prices` | Simulate the F1 Fantasy price formula forward across all events in a season |

#### Race week operations (`predictions/`)

| Command | What it does |
|---------|-------------|
| `next_race` | **Main weekly command.** Trains on all available data, generates predictions, recommends lineup changes. Also auto-scores the previous round if actual score data is available. |
| `record_my_lineup` | Record the lineup you actually submitted for a race |
| `score_lineup` | Score a past lineup against actual results (useful if you want to score without running `next_race`) |

#### Model research (`predictions/`)

| Command | What it does |
|---------|-------------|
| `backtest` | Walk-forward backtest over historical seasons; prints per-race MAE and lineup quality |
| `tune_hyperparams` | Random search over XGBoost hyperparameters using TimeSeriesSplit CV |

#### Interpreting backtest output

Each race in the backtest prints a row with MAE and lineup quality columns. After all races, a summary block shows:

**Error metrics** — how accurate the predictions are across all 20 drivers:
- `Mean MAE (position)` — average absolute error on finishing position predictions. If this is 3.2, we're off by about 3 positions per driver on average.
- `Mean MAE (fantasy pts)` — same idea but for predicted fantasy points.

**Lineup quality metrics** — how good the selected lineup actually was:
- `Total lineup points` — cumulative fantasy points scored by the ML-recommended lineup over all backtested races.
- `Total optimal points` — what the ILP oracle would have scored with perfect knowledge. The ceiling.
- `Points left on table` — the gap between oracle and lineup. Ideally as small as possible.

**Rank metrics** — whether the model is identifying the *right drivers* at the top, which is what drives lineup quality:
- `Spearman ρ` — rank correlation between predicted and actual finishing order across all 20 drivers. 1.0 = perfect order. 0.0 = uncorrelated. A high MAE model can still have a good Spearman ρ if it gets the order right even when point estimates are off.
- `Top-10 precision` — of the 10 drivers we predicted would score most fantasy points, what fraction actually were in the top 10? 0.6 means 6 of 10 correct. Top 10 = the F1 points-scoring positions, where almost all fantasy value concentrates.
- `Top-10 recall` — of the actual top 10 scorers, what fraction did we correctly identify?
- `NDCG@10` — like precision, but weighted by rank position: correctly identifying the highest scorer matters more than correctly identifying the 10th scorer. 1.0 = perfect.

The north star metric is lineup points. MAE is useful for debugging but a model with lower MAE can produce a worse lineup if it's mis-ranking the top drivers. The rank metrics bridge that gap.

#### Lower-level / superseded (`predictions/`)

These commands are thin building blocks that `next_race` now wraps end-to-end. They remain useful for debugging specific steps in isolation.

| Command | Superseded by |
|---------|--------------|
| `predict_race` | `next_race` (trains + predicts in one step) |
| `optimize_lineup` | `next_race` (predicts + optimizes in one step) |

### Running tests

```bash
cd f1_data
python manage.py test                                          # full suite
python manage.py test core.tests.test_gap_detector            # one module
```

---

## ML Pipeline

The prediction system is built from three independent, versioned components that are combined at runtime. Think of them as a pipeline: raw data → **feature store** → **predictor** → **optimizer** → lineup.

The active version of each is configured in `f1_data/settings.py` (`ML_FEATURE_STORE`, `ML_PREDICTOR`, `ML_OPTIMIZER`) and read by `next_race`. Backtesting accepts any combination via CLI flags so you can compare versions side-by-side.

---

### Feature Stores

Responsible for querying raw race data and computing a flat feature vector for each driver before a race. All features must be available before lineup lock (no current-race qualifying position).

| Version | Class | Features | Key changes |
|---------|-------|----------|-------------|
| **v1** | `V1FeatureStore` | 15 | MVP. Rolling race form (last 3/5/10), recent qualifying position, circuit history, team form, fantasy points history, practice pace, static circuit info. |
| **v2** | `V2FeatureStore` | 25 | +10 vs v1. Adds practice-session weather (rainfall flag, track temp), car-circuit fit (constructor standing, downforce-split ratings), driver vs teammate gap, pick percentage, price change direction, fantasy points trend. |
| **v3** | `V3FeatureStore` | 29 | +9 richer features, –6 zero-importance features vs v2. Replaces the binary rainfall flag with a continuous rain fraction. Adds wet-weather driver specialisation, driver experience, circuit historical rain rate, temperature deviation from circuit mean, team qualifying form, driver vs teammate championship gap. Drops circuit geometry and downforce-split ratings (zero walk-forward importance). |

---

### Predictors

Responsible for training XGBoost models on historical (features, outcomes) pairs and generating per-driver predictions for an upcoming race.

| Version | Class | What it predicts | Key changes |
|---------|-------|-----------------|-------------|
| **v1** | `XGBoostPredictor` | Position (MSE), points (MSE) | MVP. Single model per target. Confidence bounds are ±1 std dev of training residuals — approximate. |
| **v2** | `XGBoostPredictorV2` | Position (MSE), points (MSE), points q10, points q90 | 4-model ensemble. Replaces residual bounds with proper quantile regression (`reg:quantileerror`). Wide q10/q90 gap = high-variance driver; narrow = consistent. |
| **v3** | `XGBoostPredictorV3` | Same as v2 | V2 + exponential decay sample weights. Races `n` events ago get weight `exp(−λn)`, where `λ = ln(2) / half_life`. Default `half_life = 10` ≈ half a season. Configured via `ML_PREDICTOR_V3_HALF_LIFE`. |

---

### Optimizers

Responsible for selecting a valid lineup (5 drivers + 2 constructors, 1 DRS boost driver) under the $100M budget constraint, using the predictor's point estimates.

| Version | Class | Strategy | Key changes |
|---------|-------|---------|-------------|
| **v1** | `GreedyOptimizer` | Greedy knapsack | Sorts all drivers and constructors by points-per-dollar, picks greedily until budget is exhausted. Simple and fast, but ignores leftover budget. |
| **v2** | `GreedyOptimizerV2` | Greedy + upgrade pass | After greedy selection, iterates through unpicked players and swaps in any higher-scoring option that fits in the remaining budget. Repeats until no improvement is possible. |
| **v3** | `ILPOptimizer` | Integer Linear Programming | Provably finds the globally optimal lineup. Formulates selection as a binary ILP and solves it exactly. Handles transfer limits as a hard constraint when a current lineup is provided. |

---

### Current production configuration

```python
# f1_data/f1_data/settings.py
ML_FEATURE_STORE = "v2"   # used by next_race
ML_PREDICTOR     = "v2"   # used by next_race
ML_OPTIMIZER     = "v2"   # used by next_race
```

> **Note:** `next_race` currently supports feature stores v1/v2 and predictors v1/v2. The v3 feature store and v3 predictor are available for backtesting but not yet wired into `next_race`.

---

## f1_analytics — Analytics app

The original Django app with a web UI, lineup optimiser, fantasy data imports, and backup tooling. See `f1_analytics/` for its own setup and commands (documented in the old README history).

---

## chrome_extension — F1 Fantasy exporter

A Chrome extension (v1.3) that scrapes the F1 Fantasy website and exports data as CSV files.

### Installation

1. Go to `chrome://extensions/` and enable **Developer mode**
2. Click **Load unpacked** and select `chrome_extension/f1-fantasy-exporter/`
3. Pin the extension icon to your toolbar

### Exports

**Prices snapshot** — navigate to the F1 Fantasy driver/constructor list, click the extension icon, then:
- **Export Drivers** — downloads `YYYY-MM-DD-drivers.csv`
- **Export Constructors** — switch to the Constructors tab first, then **Export Constructors** — downloads `YYYY-MM-DD-constructors.csv`

Fields: name, team, % picked, season points, current value, price change.

**Performance data** — navigate to the Drivers tab, click **Export Performance Data**. The extension automatically clicks through every driver card, then every constructor card (~2–3 minutes), and downloads:
- `YYYY-MM-DD-all-drivers-performance.csv`
- `YYYY-MM-DD-all-constructors-performance.csv`

Fields: driver/constructor, race, event type (qualifying / sprint / race), scoring item, frequency, position, points, race total, season total.

### CSV samples

```
# Prices
Driver Name,Team,% Picked,Season Points,Current Value,Price Change
Lando Norris,McLaren,22.00,614,$30.4M,-$0.1M

# Performance
Driver Name,Team,Driver Value,Race,Event Type,Scoring Item,Frequency,Position,Points,Race Total,Season Total
Lando Norris,McLaren,$30.4M,Australia,qualifying,Qualifying Position,,1,10,59,614
Lando Norris,McLaren,$30.4M,Australia,race,Race Overtake Bonus,5,,5,59,614
```
