# F1 Fantasy ML Pipeline — Architecture & Implementation Plan

## Goal

Build a modular ML pipeline that:
1. Predicts driver/constructor race performance (finishing position, fantasy points)
2. Predicts driver price movements (buy-low/sell-high signals)
3. Optimizes lineup selection under budget constraints across multiple future races

The system is designed as swappable components — each layer has a clean interface so individual pieces can be upgraded from simple baselines to sophisticated models without rewriting the rest.

---

## F1 Official Fantasy — 2025 Rules & Scoring

Understanding the game constraints drives what we need to predict. Assume previous seasons follow the same rules for backtesting purposes.

### Team Composition
- **5 drivers + 2 constructors**
- **$100M budget cap** (can grow above $100M during season via price appreciation)
- **DRS Boost:** Choose one driver each week for 2x points (free, no penalty to change)
- **Chips:** Extra DRS (3x), Wildcard, Limitless, AutoPilot, No Negative, Final Fix — **deferred to post-MVP**

### Transfers
- **2 free transfers per race week** before lock (start of qualifying)
- Each additional transfer costs **-10 points**
- 1 unused transfer carries over to the next week (but carry-overs don't accumulate)

### Scoring — Drivers

**Qualifying (position-based):**
P1: 10, P2: 9, P3: 8, P4: 7, P5: 6, P6: 5, P7: 4, P8: 3, P9: 2, P10: 1, P11–P20: 0, No time set: -5, DSQ: -5

**Race (position-based):**
P1: 25, P2: 18, P3: 15, P4: 12, P5: 10, P6: 8, P7: 6, P8: 4, P9: 2, P10: 1, P11–P20: 0, DNF/no time: -20, DSQ: -20

**Race (dynamic):**
- Positions gained (start vs finish): +1 per position
- Positions lost: -1 per position
- Overtakes (on-track legal passes): +1 per overtake
- Fastest lap: +10
- Driver of the Day: +10

**Sprint (position-based):**
P1: 8, P2: 7, P3: 6, P4: 5, P5: 4, P6: 3, P7: 2, P8: 1, P9–P20: 0, No time: -10, DSQ: -10

**Sprint (dynamic):** Same as race (+1/-1 positions, +1 overtakes, +10 fastest lap, +10 DotD)

### Scoring — Constructors

- **Combined points of both drivers** (excluding Driver of the Day bonus)
- **Qualifying progression bonus:** Neither in Q2: -1, One in Q2: +1, Both in Q2: +3, One in Q3: +5, Both in Q3: +10
- **Pit stop points (race only, per constructor's fastest stop):**
  Under 2.0s: 20, 2.00–2.19s: 10, 2.20–2.49s: 5, 2.50–2.99s: 2, Over 3.0s: 0
  Fastest pit stop overall: +5 bonus. New world record (< 1.8s): +15 bonus
- **DSQ penalty:** -20 per disqualified driver (constructor absorbs this, not driver)

### Price Dynamics
- Prices update after each race based on **average fantasy performance from the last 3 GPs**
- Price floor: $3M, ceiling: $34M
- Key insight: since price changes are based on a 3-race rolling average, we can predict price movements with reasonable accuracy if we can predict performance
- **Data source:** Chrome extension exports `YYYY-MM-DD-drivers.csv` and `YYYY-MM-DD-constructors.csv` with current values and price changes. Performance CSVs contain per-race scoring breakdowns.

---

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     LAYER 0: DATA                           │
│  (Existing Django models — Session, Lap, WeatherSample,     │
│   SessionResult, Driver, Team, Circuit, Event)              │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  LAYER 1: FEATURE STORE                     │
│  Raw data → race-weekend feature vectors per driver         │
│  Interface: get_features(driver, event) → dict              │
└──────────────────────────┬──────────────────────────────────┘
                           │
                    ┌──────┴──────┐
                    ▼             ▼
┌──────────────────────┐ ┌──────────────────────┐
│  LAYER 2A: PERF      │ │  LAYER 2B: PRICE     │
│  PREDICTOR           │ │  PREDICTOR           │
│  predict race finish │ │  predict price Δ     │
│  + fantasy points    │ │  over next N races   │
└──────────┬───────────┘ └──────────┬───────────┘
           │                        │
           └──────────┬─────────────┘
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                  LAYER 3: OPTIMIZER                          │
│  Given predictions + budget + current prices →              │
│  optimal lineup (single race or multi-race horizon)         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  LAYER 4: EVALUATION                        │
│  Backtest over historical seasons                           │
│  Compare strategies, track cumulative fantasy score         │
└─────────────────────────────────────────────────────────────┘
```

Each layer communicates through defined interfaces (Python protocols/ABCs). Swap any layer's implementation without touching the others.

---

## Layer 1: Feature Store

**Purpose:** Transform raw Django ORM data into ML-ready feature vectors. One feature vector per driver per race weekend.

**Interface:**

```python
class FeatureStore(Protocol):
    def get_driver_features(self, driver_id: int, event_id: int) -> dict[str, float]: ...
    def get_all_driver_features(self, event_id: int) -> DataFrame: ...
    def get_constructor_features(self, team_id: int, event_id: int) -> dict[str, float]: ...
```

### Feature Categories

**Static context:**
- Circuit characteristics (length, corners, circuit_key one-hot or embedding)
- Event format (conventional vs sprint)
- Season stage (round_number / total_rounds)

**Recent form (rolling windows — last 3, 5, 10 races):**
- Mean finishing position
- Mean fantasy points scored
- Finishing position variance (consistency)
- Mean positions gained (race vs grid)
- DNF rate
- Points per race

**Qualifying signals (available pre-race):**
- Qualifying position
- Gap to pole (seconds)
- Q1/Q2/Q3 progression
- Qualifying delta vs teammate

**Practice signals (available pre-qualifying):**
- Best practice lap time rank
- Practice pace vs field (percentile)
- Long-run pace vs short-run pace (tire deg indicator)

**Weather:**
- Forecast conditions (rain probability, temperature)
- Driver's historical wet-weather performance delta

**Team strength:**
- Constructor standings position
- Constructor recent form
- Teammate's recent form (relative benchmark)

**Track-specific history:**
- Driver's historical performance at this circuit
- Mean finish position at this track (last 3 visits)

### Upgrade Path

| Version | Approach |
|---------|----------|
| **v1 (MVP)** | Pandas functions that query Django ORM, compute rolling aggregates, return flat dicts |
| **v2** | Precomputed feature table (Django model or parquet) refreshed by management command |
| **v3** | Learned embeddings for circuit/driver/team (from Phase 1 of bio-RL roadmap) |

---

## Layer 2A: Performance Predictor

**Purpose:** Given pre-race features, predict finishing position and fantasy points.

**Interface:**

```python
class PerformancePredictor(Protocol):
    def predict(self, features: DataFrame) -> DataFrame:
        """Input: all drivers' features for an event.
        Output: DataFrame with columns [driver_id, predicted_position,
                predicted_fantasy_points, confidence_lower, confidence_upper]
        """
        ...

    def fit(self, X: DataFrame, y: DataFrame) -> None: ...
```

### Upgrade Path

| Version | Model | Notes |
|---------|-------|-------|
| **v1 (MVP)** | XGBoost / LightGBM | Gradient-boosted trees. Train on historical features→results. Strong baseline, handles mixed feature types natively, fast to train. |
| **v2** | Quantile regression variant | Same model family but predict quantiles (10th, 50th, 90th) for uncertainty estimates. Feeds confidence intervals to optimizer. |
| **v3** | Neural net with attention | Small transformer-style model that attends to retrieved similar past races (bio-RL Phase 1 retrieval). |
| **v4** | Full bio-RL value estimator | Embedding retrieval + novelty-gated learning from the roadmap doc. |

**Targets to predict (multi-output):**
- Finishing position (primary)
- Fantasy points scored (directly optimizable)
- Positions gained/lost vs grid (captures overtaking ability)
- DNF probability (risk factor for lineup selection)

**Training approach (MVP):**
- Walk-forward validation: train on races 1..N, predict race N+1
- Never include future data in features
- Retrain weekly or after each race

---

## Layer 2B: Price Predictor

**Purpose:** Predict how driver prices will change over the next 1–5 races.

This is what enables the buy-low/sell-high strategy. If a driver is currently underpriced relative to expected upcoming performance, they're a buy signal.

**Interface:**

```python
class PricePredictor(Protocol):
    def predict_price_trajectory(
        self, driver_id: int, current_price: float, horizon: int = 5
    ) -> list[float]:
        """Predict price at each of the next `horizon` races."""
        ...
```

### Data Source

**Price data comes from the existing Chrome extension** that scrapes the F1 Fantasy website:
- `YYYY-MM-DD-drivers.csv` — name, team, % picked, season points, current value, price change
- `YYYY-MM-DD-constructors.csv` — same fields for constructors
- `YYYY-MM-DD-all-drivers-performance.csv` — per-race scoring breakdowns (event type, scoring item, points)
- `YYYY-MM-DD-all-constructors-performance.csv` — same for constructors

Store as Django models. Import via a management command that reads these CSVs from a directory. Historical price snapshots need to be collected before each race weekend (automate a reminder or pre-race Prefect/cron trigger).

Since prices change based on the **last 3 GPs' average fantasy performance**, the price predictor can be quite precise: if we predict a driver's next-race fantasy points, we can project what their 3-race rolling average will become, and thus estimate the direction and magnitude of their price change.

### Upgrade Path

| Version | Approach |
|---------|----------|
| **v1 (MVP)** | Analytical: compute predicted 3-race rolling average fantasy points → compare to current price tier → estimate direction. Since we know the mechanism, this can be surprisingly accurate. |
| **v2** | Regression on historical (rolling_avg_delta, current_price, pick_rate) → actual_price_change to learn the exact scaling |
| **v3** | Joint model: feed performance predictions directly into a learned price-change function calibrated per-season |

### Key Insight

Because we know the price mechanism (3-race rolling average), the price predictor isn't really a separate ML problem — it's a downstream computation from the performance predictor. If you can predict next race's fantasy points, you can compute the new rolling average and estimate price direction. The main uncertainty is the exact scaling function from rolling average to price delta.

---

## Layer 3: Optimizer

**Purpose:** Given performance predictions, price predictions, current prices, and budget → recommend the best lineup.

**Interface:**

```python
class LineupOptimizer(Protocol):
    def optimize_single_race(
        self,
        driver_predictions: DataFrame,  # driver_id, predicted_points, price
        constructor_predictions: DataFrame,  # team_id, predicted_points, price
        budget: float,
        constraints: dict,  # transfer limits, current lineup, etc.
    ) -> Lineup: ...

    def optimize_multi_race(
        self,
        driver_predictions: list[DataFrame],  # one per future race
        constructor_predictions: list[DataFrame],
        price_trajectories: DataFrame,  # driver_id, race_1_price, race_2_price, ...
        budget: float,
        current_lineup: Lineup,
        free_transfers: int = 2,
        horizon: int = 5,
    ) -> list[LineupAction]: ...
```

### Upgrade Path

| Version | Approach |
|---------|----------|
| **v1 (MVP)** | Greedy knapsack: rank drivers by predicted_points / price, fill 5 driver slots + 2 constructor slots greedily respecting budget. Pick DRS Boost as highest-expected-points driver. |
| **v2** | Integer linear programming (ILP) via PuLP or OR-Tools. Exact optimal single-race lineup under budget + 5-driver/2-constructor constraints. |
| **v3** | Multi-race ILP: maximize total points over N races, with price changes and transfer limits (2 free + 1 carryover, -10 per extra) as constraints. Decides what to buy/sell and when. |
| **v4** | Stochastic optimization: sample from prediction confidence intervals, optimize expected value + risk trade-off (e.g., maximize mean - λ·variance). |

### DRS Boost Selection

The DRS Boost (2x points) is the highest-leverage single decision each week. The optimizer should:
- Identify the driver in your lineup with the highest expected points
- Factor in upside variance — a volatile cheap driver with high ceiling may be a better DRS pick than a consistent mid-tier one
- Consider overtake potential (circuits with long straights/DRS zones) since +1 per overtake is doubled

---

## Layer 4: Evaluation / Backtesting

**Purpose:** Measure how well the full pipeline would have performed historically.

**Interface:**

```python
class Backtester:
    def run(
        self,
        seasons: list[int],
        feature_store: FeatureStore,
        predictor: PerformancePredictor,
        optimizer: LineupOptimizer,
    ) -> BacktestResult: ...
```

**Metrics:**
- Per-race: predicted vs actual fantasy points, lineup score vs optimal lineup, DRS Boost accuracy (did we pick the highest scorer?)
- Cumulative: total fantasy points over a season, rank among a simulated league
- Prediction quality: MAE on position predictions, calibration of confidence intervals
- Price strategy: ROI from buy-low/sell-high decisions vs static team
- Transfer efficiency: points gained per transfer used

**Walk-forward protocol:**
- For each race R in season:
  1. Train predictor on all races before R
  2. Generate features for race R
  3. Predict + optimize lineup
  4. Score against actual results
  5. Record everything

---

## New Django Models

Add to the existing `core` app or a new `predictions` app:

```python
# Fantasy price tracking (from Chrome extension CSVs)
class FantasyDriverPrice:
    driver: FK → Driver
    event: FK → Event
    snapshot_date: date
    price: Decimal  # e.g. 30.4 ($30.4M)
    price_change: Decimal  # e.g. -0.1
    pick_percentage: float  # e.g. 22.0
    season_fantasy_points: int
    unique_together: (driver, event)

class FantasyConstructorPrice:
    team: FK → Team
    event: FK → Event
    snapshot_date: date
    price: Decimal
    price_change: Decimal
    pick_percentage: float
    season_fantasy_points: int
    unique_together: (team, event)

# Per-race fantasy scoring breakdown (from performance CSVs)
class FantasyDriverScore:
    driver: FK → Driver
    event: FK → Event
    event_type: str  # "qualifying", "sprint", "race"
    scoring_item: str  # "Qualifying Position", "Race Overtake Bonus", etc.
    frequency: int (nullable)  # e.g. 5 for "5 overtakes"
    position: int (nullable)
    points: int
    race_total: int
    season_total: int
    unique_together: (driver, event, event_type, scoring_item)

class FantasyConstructorScore:
    team: FK → Team
    event: FK → Event
    event_type: str
    scoring_item: str
    frequency: int (nullable)
    position: int (nullable)
    points: int
    race_total: int
    season_total: int
    unique_together: (team, event, event_type, scoring_item)

# Scoring rules (for computing fantasy points from raw race data)
class ScoringRule:
    season: FK → Season
    rule_name: str  # "race_p1", "qualifying_p1", "position_gained", etc.
    points: float
    description: str

# Prediction tracking
class RacePrediction:
    event: FK → Event
    driver: FK → Driver
    predicted_position: float
    predicted_fantasy_points: float
    confidence_lower: float
    confidence_upper: float
    actual_position: int (nullable, filled post-race)
    actual_fantasy_points: float (nullable)
    model_version: str
    created_at: datetime

# Lineup recommendations
class LineupRecommendation:
    event: FK → Event
    driver_1 through driver_5: FK → Driver
    drs_boost_driver: FK → Driver
    constructor_1: FK → Team
    constructor_2: FK → Team
    total_cost: Decimal
    predicted_points: float
    actual_points: float (nullable)
    strategy_type: str  # "single_race", "multi_race_horizon_5", etc.
    model_version: str
    created_at: datetime
```

---

## Project Structure

```
core/
├── (existing models, tasks/, flows/, tests/)
│
predictions/
├── models.py              # DriverPrice, ScoringRule, RacePrediction, LineupRecommendation
├── migrations/
├── features/
│   ├── __init__.py
│   ├── base.py            # FeatureStore protocol
│   ├── v1_pandas.py       # MVP: ORM queries + pandas rolling calcs
│   └── feature_config.py  # which features to include, window sizes, etc.
├── predictors/
│   ├── __init__.py
│   ├── base.py            # PerformancePredictor / PricePredictor protocols
│   ├── xgboost_v1.py      # MVP performance predictor
│   └── price_heuristic.py # MVP price predictor
├── optimizers/
│   ├── __init__.py
│   ├── base.py            # LineupOptimizer protocol
│   ├── greedy_v1.py       # MVP greedy knapsack
│   └── ilp_v2.py          # Future: exact ILP solver
├── evaluation/
│   ├── __init__.py
│   ├── backtester.py
│   └── metrics.py
├── management/
│   └── commands/
│       ├── predict_race.py         # generate predictions for upcoming race
│       ├── optimize_lineup.py      # recommend lineup
│       ├── backtest.py             # run historical backtests
│       ├── import_fantasy_csv.py   # import Chrome extension CSVs (prices + performance)
│       └── compute_fantasy_points.py  # compute fantasy points from raw race data + scoring rules
└── tests/
    ├── test_features.py
    ├── test_predictors.py
    ├── test_optimizers.py
    ├── test_backtester.py
    └── factories.py
```

---

## Build Order

### Step 1: Predictions App Scaffolding + Models
- Create `predictions` app
- Implement models: `DriverPrice`, `ScoringRule`, `RacePrediction`, `LineupRecommendation`
- Migrations
- **Verify:** migrate succeeds, models in admin

### Step 2: Feature Store v1
- Implement `predictions/features/v1_pandas.py`
- Start with recent form features only (rolling position averages, points, DNF rate)
- Add qualifying and circuit features
- Tests with factory data
- **Verify:** can generate a feature vector for any driver+event in the DB

### Step 3: Performance Predictor v1 (XGBoost)
- Implement `predictions/predictors/xgboost_v1.py`
- Train/predict interface
- Walk-forward split utility
- Predict finishing position + fantasy points
- Tests with synthetic features
- **Verify:** model trains and produces reasonable predictions on held-out data

### Step 4: Greedy Optimizer v1
- Implement `predictions/optimizers/greedy_v1.py`
- Rank by points/price, fill 5 driver slots + 2 constructor slots under budget
- Handle DRS Boost selection (highest expected points driver in lineup)
- Tests
- **Verify:** produces valid lineups under budget with sensible DRS pick

### Step 5: Backtester
- Implement `predictions/evaluation/backtester.py`
- Walk-forward evaluation over 2023–2024 seasons
- Report per-race and cumulative metrics
- **Verify:** full backtest runs, produces metrics report

### Step 6: Management Commands
- `predict_race` — takes an event, runs feature extraction + prediction, stores results
- `optimize_lineup` — takes predictions + budget, outputs lineup
- `backtest` — runs evaluation, prints report
- **Verify:** end-to-end from command line

### Step 7: Fantasy Data Import
- `import_fantasy_csv` — reads Chrome extension CSVs from a directory, creates FantasyDriverPrice/FantasyConstructorPrice/FantasyDriverScore/FantasyConstructorScore records
- `compute_fantasy_points` — given raw race data (SessionResult, Lap) + ScoringRule table, compute what fantasy points each driver/constructor *should* have scored. This is critical for backtesting seasons where you didn't collect Chrome extension snapshots.
- **Verify:** prices + scores in DB, queryable. Fantasy points computed from raw data match actual Chrome extension scores.

### Step 8: Price Predictor v1
- Simple heuristic: performance residual → price direction
- Integrate into optimizer for multi-race mode
- **Verify:** backtest shows buy-low/sell-high adds value vs static team

### Step 9: Slack Integration
- Push pre-race predictions + lineup recommendation to Slack
- Push post-race actual vs predicted comparison
- **Verify:** messages appear in Slack channel

---

## Iteration Roadmap (Post-MVP)

Once MVP is validated, upgrade components independently:

### Near-term (component upgrades)
- Feature store v2: precomputed feature table, practice session signals, weather
- Predictor v2: quantile regression for confidence intervals
- Optimizer v2: ILP exact solver (PuLP) with transfer penalty constraints
- Better DRS Boost selection (variance-aware, circuit-specific)
- Constructor prediction model (pit stop speed, Q progression probability)

### Medium-term (new capabilities)
- Sprint race handling (separate predictions for sprint vs main race)
- **Chip optimization:** When to deploy Wildcard, Limitless, Extra DRS, etc. for maximum season-long value
- Opponent modeling (simulate league competition)
- Price predictor v2: learn exact price-change scaling function from historical data

### Long-term (bio-RL roadmap)
- Embedding encoder for race weekends (roadmap Phase 1)
- Retrieval-based prediction (similar past races)
- Novelty-gated learning (selective model updates)
- Pattern separation + complementary learning systems (roadmap Phase 2)
- Full world model + Monte Carlo simulation (roadmap Phase 3)

Each of these plugs into the existing pipeline by implementing the same interface. The greedy optimizer doesn't care whether predictions come from XGBoost or a bio-RL value estimator — it just needs `predicted_points` and `confidence`. The 5-driver/2-constructor/DRS-boost structure stays constant.

---

## Dependencies to Add

```
# requirements.txt additions
xgboost>=2.0
scikit-learn>=1.4
pandas>=2.0      # likely already pulled in by fastf1
```

Keep it minimal. No PyTorch until we reach the neural net predictor stage. No optimization libraries until we reach ILP stage.

---

## Open Questions

1. **Historical fantasy price data:** The Chrome extension captures current prices, but for backtesting older seasons we need historical prices. Check community datasets (Reddit r/FantasyF1, GitHub repos) or F1FantasyTools.com for archives. For any season without price data, `compute_fantasy_points` from raw race results gives us the scoring side, and we may need to approximate prices.

2. **Sprint weekends:** These add an extra scoring session. Feature store and fantasy points calculator both need to handle the sprint session type. Priority: get non-sprint weekends working first, then add sprint handling.

3. **Price change formula precision:** We know it's based on 3-race rolling average, but the exact mapping from rolling average → price delta is unclear. The fanamp article mentions "asset tiers and normalized points ratios" from 2024 — worth investigating. Even without the exact formula, directional accuracy (up/down/flat) is enough for the optimizer.

4. **Constructor scoring for optimizer:** Constructors score as sum-of-both-drivers plus pit stop points plus Q progression. The constructor prediction model needs to account for team-level factors (pit crew speed, likelihood of both cars in Q3) — not just individual driver predictions.

5. **DRS Boost as the biggest lever:** 2x on your best driver is easily 20-40+ points per week. Getting this right matters more than marginal lineup optimization. The predictor needs to be especially good at identifying the highest-ceiling driver for DRS.
