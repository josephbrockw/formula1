# ML Upgrade Plan — Integration Guide

How the upgrade plan maps onto the existing codebase. Read `ML_PROCESS.md` for what's already built and `ML_UPGRADE_PLAN.md` for the full rationale.

---

## What doesn't change

The `core` app, all existing models in `predictions/models.py`, the optimizer layer, the backtester orchestration, and the management command structure all stay as-is. The upgrade is scoped entirely to the feature store, predictor layer, and evaluation metrics.

The Protocol interfaces (`FeatureStore`, `PerformancePredictor`, `LineupOptimizer`) also don't change — new implementations satisfy the same contracts. Old versions remain in place for backtest comparisons.

---

## Predictor directory reorganization

The current flat `predictors/` directory doesn't scale to multiple model types with multiple versions each. Reorganize into subdirectories per model type:

```
predictions/predictors/
├── base.py                          ← PerformancePredictor Protocol (unchanged)
├── points_mapper.py                 ← NEW — deterministic position → fantasy points
├── xgboost/
│   ├── __init__.py
│   ├── shared.py                    ← build_training_dataset, walk_forward_splits (moved from xgboost_v1.py)
│   ├── v1.py
│   ├── v2.py
│   ├── v3.py
│   └── v4.py
├── qualifying_ranker/
│   ├── __init__.py
│   └── v1.py                        ← XGBRanker, target: qualifying position
├── race_ranker/
│   ├── __init__.py
│   └── v1.py                        ← XGBRanker, target: race position
├── sprint_ranker/
│   ├── __init__.py
│   └── v1.py                        ← XGBRanker or heuristic
└── price_heuristic/
    ├── __init__.py
    └── v1.py                         ← moved from price_heuristic.py
```

This should happen as a standalone refactor PR before any new models are added. Update all imports in management commands, backtester, and tests. Extract `build_training_dataset` and `walk_forward_splits` from `xgboost_v1.py` into `xgboost/shared.py` since they're already imported by multiple predictor versions.

---

## How the ranker pipeline fits the existing Protocol

The current predictors output `predicted_fantasy_points` directly. The new ranker models output `predicted_position` per session, then `points_mapper` converts to fantasy points.

The `PerformancePredictor` Protocol still works — `predict()` still returns a DataFrame with `driver_id`, `predicted_fantasy_points`, `confidence_lower`, `confidence_upper`. The ranker pipeline wraps three models + mapper behind this same interface and registers as predictor `v5` in the backtest CLI. The optimizer sees no change.

### Intra-predictor dependency

The race ranker depends on the qualifying ranker's output (predicted qualifying position is the race model's strongest feature). This is the one new structural concept — a predictor that internally chains two models. It's hidden behind the single `predict()` call:

```
1. qualifying_ranker.predict(features)  → predicted_quali_positions
2. Inject into feature vectors
3. race_ranker.predict(augmented_features) → predicted_race_positions
4. sprint_ranker.predict(features) → predicted_sprint_positions  (sprint weekends only)
5. points_mapper.map(positions, driver_history) → fantasy_points + bounds
```

**Training-time detail:** During walk-forward training, the race ranker trains with *actual* qualifying positions (known for historical events). Only at inference does it use the qualifying ranker's predictions. This prevents compounding training-time errors.

---

## New files outside of predictors

```
predictions/features/
└── v4_pandas.py             ← NEW — telemetry + form direction features

predictions/evaluation/
├── backtester.py            ← MODIFIED — add rank-based metrics, MyLineup comparison
└── metrics.py               ← NEW — Spearman ρ, top-5 precision/recall, NDCG@5
```

Rank metrics are added to `RaceBacktestResult` and `BacktestResult` (existing dataclasses). The `backtest` command gains new output columns and loads `MyLineup` records for overlapping events to show a comparison.

---

## Versioning convention

```bash
# Old pipeline vs new pipeline, same feature store and optimizer
python manage.py backtest --feature-store v3 v4 --predictor v3 v5 --optimizer v2 --seasons 2024 2025
```

`v5` instantiates the full ranker pipeline. Feature store v4 is designed for it but v3 also works (the ranker just won't have telemetry features). This lets you isolate whether gains come from better features, better model architecture, or both.
