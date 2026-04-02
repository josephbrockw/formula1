# ML Pipeline Upgrade Plan

## The Core Problem

The current pipeline predicts a single `fantasy_points` number per driver per weekend using a regression model optimized for MSE. This has two fundamental issues:

1. **Wrong optimization target for lineup selection.** The optimizer needs to know *which 5 drivers will score the most*, not the exact point total for all 20 drivers. A model that correctly ranks the top 5 but is off by 3pts on each is far more valuable than one that nails midfield predictions but swaps P1 and P4. MSE treats all errors equally; lineup value doesn't.

2. **Weekend-total prediction hides learnable structure.** Qualifying points, sprint points, and race points are driven by different factors. Qualifying is mostly car speed + driver one-lap ability (highly predictable). Race outcomes add strategy, reliability, overtaking, weather, safety cars (lower predictability). Lumping them into one target forces the model to learn a blurry average.

---

## What Each Model Predicts (and Why)

This is the key architectural decision. The answer is: **predict positions (ranks), then map deterministically to fantasy points.**

### Why positions, not points directly

Fantasy points are a *deterministic function* of discrete events: finishing position, positions gained/lost, fastest lap, DNFs, etc. The stochastic part — the thing that's actually hard to predict — is **where drivers finish relative to each other**. Once you know the finishing order, you can compute most of the fantasy points mechanically using `ScoringRule`.

Predicting points directly asks the model to simultaneously learn two things: the competitive order AND the scoring rules. The scoring rules are already known — there's no reason to waste model capacity re-learning them from data.

Predicting positions also makes the model's errors more interpretable: "we predicted VER P1 but he finished P3" is actionable feedback. "We predicted 42pts but he scored 28pts" could mean many things.

### The exception: bonus points

Some fantasy scoring items can't be derived from finishing position alone: overtakes, fastest lap, Driver of the Day, pit stop bonuses (constructors). These are genuinely stochastic and need their own predictions. But they're a smaller, more volatile component — trying to predict them precisely adds noise. The better approach is to estimate them as a residual on top of position-based points.

### The architecture

```
                    ┌──────────────────────┐
                    │   Practice Telemetry  │
                    │   (v4 Feature Store)  │
                    └──────────┬───────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                 ▼
     ┌────────────────┐ ┌───────────┐  ┌──────────────────┐
     │ Qualifying      │ │  Sprint   │  │  Race Position   │
     │ Position Model  │ │  Position │  │  Model           │
     │                 │ │  Model    │  │                  │
     │ target: quali   │ │ target:   │  │ target: race     │
     │ position (1-20) │ │ sprint    │  │ finish position  │
     │                 │ │ position  │  │                  │
     │ loss: pairwise  │ │ loss:     │  │ features include │
     │ ranking         │ │ pairwise  │  │ predicted quali  │
     │                 │ │ ranking   │  │ position from ↙  │
     └───────┬────────┘ └─────┬─────┘  └────────┬─────────┘
             │                │                  │
             │     predicted positions           │
             ▼                ▼                  ▼
     ┌───────────────────────────────────────────────────┐
     │              Points Mapper                        │
     │                                                   │
     │  For each session, map predicted position to      │
     │  expected fantasy points using ScoringRule +      │
     │  historical bonus distributions:                  │
     │                                                   │
     │  expected_pts(pos) = position_pts(pos)            │
     │                    + E[positions_gained | quali,   │
     │                        race_pos prediction]       │
     │                    + E[bonuses | pos, driver       │
     │                        historical rates]          │
     │                                                   │
     │  Total = quali_pts + race_pts + sprint_pts        │
     │                                                   │
     │  Confidence bounds: sample from position          │
     │  prediction uncertainty → points distribution     │
     └───────────────────────┬───────────────────────────┘
                             │
                             ▼
                    ┌────────────────────┐
                    │     Optimizer       │
                    │  (ILP / Monte Carlo)│
                    └────────────────────┘
```

### Model-by-model specification

**Qualifying Position Model**
- Target: qualifying finishing position (1-20)
- Objective: `rank:pairwise` (XGBRanker) — optimizes for correct ordering within each race group
- Key features: practice short-run pace (best single lap), historical quali form, teammate quali delta, circuit type, car development trajectory
- Expected accuracy: highest of all models (qualifying is the most deterministic session)
- Output: predicted qualifying position per driver + uncertainty (position ± N)

**Race Position Model**
- Target: race finishing position (1-20, with DNF mapped to e.g. 18-20 range)
- Objective: `rank:pairwise`
- Key features: **predicted qualifying position** (from quali model), practice long-run pace, tyre degradation rank, historical race form, positions-gained tendency, reliability rate, weather forecast (wet/dry)
- This is where predicted quali position becomes a synthetic feature — it's the strongest single predictor of race position and it's available before lineup lock
- Output: predicted race position per driver + uncertainty

**Sprint Position Model**
- Target: sprint race finishing position
- Objective: `rank:pairwise` or simple regression (small sample)
- Key features: predicted sprint qualifying position (if available), practice pace, historical sprint results
- Challenge: only ~6 sprints/year since 2021, ~30 total. May need to be a simpler model or a variant of the race model with a sprint flag
- Output: predicted sprint position per driver

**Points Mapper (not ML — deterministic + lookup)**
- Converts predicted positions to expected fantasy points
- Position-based points: direct lookup from `ScoringRule` (P1=25pts, P2=18pts, etc.)
- Positions gained: `predicted_quali_pos - predicted_race_pos` → lookup from ScoringRule
- Bonus expectations: historical rates per driver. E.g., if VER gets fastest lap 30% of the time when finishing P1-P3, add `0.30 * fastest_lap_points` to his expected score
- Confidence bounds: instead of quantile regression, sample N scenarios from the position models' uncertainty distributions, map each to points, take the 10th/90th percentile of the resulting points distribution

---

## Feature Store v4: Telemetry + Form Direction

### New practice telemetry features

| Feature | Source | What it measures |
|---------|--------|-----------------|
| `fp_long_run_pace_rank` | `Lap` (FP2 preferred, FP1 fallback) | Median lap time across stints ≥5 laps, same compound, `is_accurate=True`. Rank 1=fastest. Best single predictor of race pace. |
| `fp_tyre_deg_rank` | `Lap` (FP2 preferred) | Slope of lap_time vs tyre_life within long-run stints. Rank 1=lowest degradation. Predicts who gains positions in stint 2+. |
| `fp_short_vs_long_delta` | Derived | `practice_best_lap_rank - fp_long_run_pace_rank`. Positive = qualifies better than races. Negative = strong race pace, weak quali. |
| `fp_sector1_rank` | `Lap` (best of FP1-FP3) | Best sector 1 time rank. Circuit-specific pace signal. |
| `fp_sector2_rank` | `Lap` | Best sector 2 time rank. |
| `fp_sector3_rank` | `Lap` | Best sector 3 time rank. |
| `fp_total_laps` | `Lap` (all FP sessions) | Total laps completed. Low count = setup problems or mechanical issues. |
| `fp_session_availability` | `Session` existence | Bitmask or count: which FP sessions have data. Sprint weekends only have FP1. |

### New form-direction features

| Feature | Source | What it measures |
|---------|--------|-----------------|
| `position_slope_last5` | `SessionResult` (Race) | Linear regression slope through last 5 finishing positions. Negative = improving. Captures trajectory that means miss. |
| `position_last1` | `SessionResult` (Race) | Most recent race finishing position. Single most recency-weighted signal. |
| `best_position_last5` | `SessionResult` (Race) | Best finish in last 5 races. Captures upside ceiling. |
| `teammate_position_delta_last3` | `SessionResult` (Race) | Driver's mean pos minus teammate's mean pos. Isolates driver skill from car quality. |
| `team_best_position_last3` | `SessionResult` (Race) | Best position by either team driver in last 3 races. Car quality ceiling. |
| `quali_position_slope_last5` | `SessionResult` (Qualifying) | Qualifying form trajectory. |
| `quali_position_last1` | `SessionResult` (Qualifying) | Most recent qualifying position. |

### Weather features (for race model)

| Feature | Source | What it measures |
|---------|--------|-----------------|
| `practice_rainfall_any` | `WeatherSample` (FP sessions) | Was there rain in any practice session? Signals possible wet race. |
| `driver_wet_performance_rank` | `SessionResult` + `WeatherSample` | Historical finishing position in wet races vs dry. Some drivers (e.g. VER, HAM) are dramatically better in rain. |

---

## Evaluation Metrics (add to backtester)

Current metrics: MAE position, MAE points, lineup points, oracle points.

**Add these rank-based metrics:**

| Metric | What it measures | Why it matters |
|--------|-----------------|---------------|
| Spearman ρ per race | Rank correlation between predicted and actual finishing order | Overall ranking quality — are we getting the order right? |
| Top-5 precision | Of the 5 highest-predicted fantasy scorers, how many were in the actual top 5? | Directly measures lineup-relevant accuracy |
| Top-5 recall | Of the actual top 5 scorers, how many did we predict in the top 5? | Measures whether we're missing high scorers |
| NDCG@5 | Normalized discounted cumulative gain for top 5 | Penalizes ranking errors at the top more than at the bottom |

These metrics should be tracked per race and aggregated. They'll likely reveal that model changes that barely move MAE can dramatically change lineup quality.

---

## Implementation Plan: PR Breakdown

Estimated timeline: roughly one feature-complete PR every 1-2 working sessions. Total: 10 PRs across 4 phases.

### Phase 1: Foundations (PRs 1-3)

**PR 1 — Rank-based evaluation metrics**
- Add Spearman ρ, top-5 precision/recall, NDCG@5 to `BacktestResult` / `RaceBacktestResult`
- Update `backtest` command to display new columns
- No model changes — just measure the current system better
- *Why first:* every subsequent PR's impact is measured by these metrics

**PR 2 — MyLineup tracking + model-vs-human comparison**
- Add `record_my_lineup` management command (create `MyLineup` records from CLI)
- Add comparison output to `backtest` or new `compare_lineups` command showing MyLineup vs LineupRecommendation vs oracle for overlapping races
- Backfill any historical picks you remember
- *Why second:* establishes the human baseline you're trying to beat

**PR 3 — Early stopping + training window experiments**
- Add `early_stopping_rounds=20` with validation split to predictor `fit()` (use most recent N races as validation)
- Add `--max-seasons-back N` flag to `backtest` to limit training window
- Run experiments: 1 season vs 2 vs 4, document results
- *Small, safe change that might immediately improve results*

### Phase 2: Feature Store v4 (PRs 4-6)

**PR 4 — Practice telemetry features**
- New `v4.py` feature store with long-run pace rank, tyre deg rank, short-vs-long delta, sector ranks, lap count, session availability
- Filter logic: `is_accurate=True`, exclude pit in/out laps, compound grouping, minimum stint length
- Tests with factory data
- Run backtest comparison: v3 vs v4 features with existing predictor

**PR 5 — Form direction + car separation features**
- Add position_slope, position_last1, best_position_last5, teammate_delta, team_best_position to v4 feature store
- Add qualifying trajectory features (quali_slope, quali_last1)
- Run backtest comparison

**PR 6 — Weather features**
- Add practice_rainfall_any, driver_wet_performance_rank to v4 feature store
- Requires joining `WeatherSample` → `Session` → `Event` to identify wet sessions historically
- May have limited data for wet race history — track how many wet races exist in your dataset

### Phase 3: Separate Models (PRs 7-9)

**PR 7 — Qualifying position model**
- New predictor: `qualifying_ranker.py` using `XGBRanker` with `objective='rank:pairwise'`
- Training target: qualifying position per race (from `SessionResult` where `session_type='Q'`)
- Group parameter: race event (ranking is within-race)
- `build_training_dataset` variant that produces qualifying-specific targets
- Backtest: measure Spearman ρ on qualifying predictions separately

**PR 8 — Race position model with quali feature**
- New predictor: `race_ranker.py` using `XGBRanker`
- Feature set includes `predicted_quali_position` from PR 7's model as a synthetic feature
- Pipeline change: train quali model first → predict quali for current event → feed into race model features → predict race
- Training: during walk-forward, for historical events use *actual* qualifying positions as the feature (since they're known). Only at prediction time use the quali model's output. This avoids compounding training-time errors.
- Backtest: measure Spearman ρ on race predictions, compare to current approach

**PR 9 — Points mapper + sprint model + integration**
- Deterministic points mapper: position → fantasy points using `ScoringRule` lookup
- Bonus estimator: historical rates for fastest lap, overtakes, DotD per driver per position bucket
- Confidence bounds via scenario sampling: sample from position uncertainty → map each to points → take percentiles
- Sprint model: either simple ranker (if enough data) or heuristic (sprint pos ≈ quali pos with noise)
- Wire everything together: quali model → race model → points mapper → optimizer
- Full backtest comparison: old pipeline vs new pipeline on all metrics

### Phase 4: External Signals (PR 10)

**PR 10 — Betting odds integration (if feasible)**
- Research and implement odds data source (The Odds API, Betfair, or scraping)
- New feature: `implied_win_probability`, `implied_top3_probability`, `implied_top6_probability`
- Add to v4 feature store
- Backtest comparison with and without odds features
- *This is optional / stretch — do it if the data source is accessible, skip if it's a rabbit hole*

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| XGBRanker produces poor calibrated position estimates | Medium | Keep MSE regressor as fallback; ranker output is relative scores, may need calibration layer to map back to positions 1-20 |
| Practice telemetry features are noisy on sprint weekends (FP1 only) | High | Session availability flag + sensible defaults; possibly train separate sprint-weekend feature logic |
| Qualifying model errors compound into race model | Medium | Use actual quali positions during training (not predicted); only use predicted at inference time |
| Too few wet races to learn wet-weather features | High | Start with simple binary feature; consider treating wet races as a separate regime rather than a feature |
| Sprint model has too little data (<30 races) | High | Fall back to heuristic (sprint ≈ quali ordering with compressed points) rather than full ML model |
| Betting odds API is unreliable or requires paid tier | Medium | Defer to Phase 4; entire plan works without it |

---

## Success Criteria

After full implementation, the new pipeline should show improvement on these metrics vs the current best (fs=v3, pred=v3, opt=v2):

- **Top-5 precision ≥ 0.50** (at least 2-3 of the predicted top 5 are actually in the top 5)
- **Spearman ρ ≥ 0.70 per race** (strong rank correlation)
- **Total lineup points improvement ≥ 5%** over 2024-2025 backtest
- **Consistently competitive with your manual picks** on races where both are tracked

The north star metric is lineup points, not MAE. A model that achieves all of the above but has *higher* MAE than today would still be a success — it means we're ranking the top correctly even if midfield predictions got noisier.
