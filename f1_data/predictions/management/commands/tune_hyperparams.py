from __future__ import annotations

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit, cross_val_score
from xgboost import XGBRegressor

from core.models import Event
from predictions.features.v1_pandas import V1FeatureStore
from predictions.features.v2_pandas import V2FeatureStore
from predictions.features.v3_pandas import V3FeatureStore
from predictions.predictors.xgboost_v1 import (
    TARGET_POSITION,
    _NON_FEATURE_COLS,
    build_training_dataset,
)

# The parameter space described in the plan.
# 3 × 3 × 3 × 3 × 2 × 2 × 3 = 972 total combinations.
# RandomizedSearchCV samples n_iter of these at random.
PARAM_GRID = {
    "n_estimators":     [20, 50, 100],
    "max_depth":        [2, 3, 4],
    "learning_rate":    [0.05, 0.1, 0.2],
    "min_child_weight": [3, 5, 10],
    "subsample":        [0.7, 0.8],
    "colsample_bytree": [0.7, 0.8],
    "reg_lambda":       [1, 5, 10],
}

# V2's current hardcoded defaults — used as the baseline for comparison.
V2_DEFAULTS = {
    "n_estimators": 100,
    "max_depth": 4,
    "learning_rate": 0.1,
    "min_child_weight": 1,   # XGBoost default (not explicitly set in V2)
    "subsample": 1.0,        # XGBoost default
    "colsample_bytree": 1.0, # XGBoost default
    "reg_lambda": 1,         # XGBoost default
}


def _make_feature_store(version: str):
    if version == "v3":
        return V3FeatureStore()
    if version == "v2":
        return V2FeatureStore()
    return V1FeatureStore()


def _cv_mae_with_params(X: pd.DataFrame, y: pd.Series, cv: TimeSeriesSplit, params: dict) -> float:
    """Run CV with a fixed param set and return the mean MAE across folds."""
    model = XGBRegressor(
        objective="reg:squarederror",
        random_state=42,
        verbosity=0,
        **params,
    )
    scores = cross_val_score(model, X, y, cv=cv, scoring="neg_mean_absolute_error")
    return float(-scores.mean())


class Command(BaseCommand):
    help = "Random search over XGBoost hyperparameters using TimeSeriesSplit CV on historical data."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--seasons",
            nargs="+",
            type=int,
            required=True,
            help="Season year(s) to include, e.g. --seasons 2022 2023 2024 2025",
        )
        parser.add_argument(
            "--feature-store",
            choices=["v1", "v2", "v3"],
            default="v3",
            help="Feature store version to use (default: v3)",
        )
        parser.add_argument(
            "--n-iter",
            type=int,
            default=50,
            help="Number of random parameter combinations to try (default: 50)",
        )
        parser.add_argument(
            "--top-n",
            type=int,
            default=10,
            help="How many top results to print in the ranked table (default: 10)",
        )
        parser.add_argument(
            "--n-splits",
            type=int,
            default=4,
            help="Number of TimeSeriesSplit folds (default: 4)",
        )

    def handle(self, *args, **options) -> None:
        seasons = options["seasons"]
        n_iter = options["n_iter"]
        top_n = options["top_n"]
        n_splits = options["n_splits"]

        # ── 1. Load events ──────────────────────────────────────────────────
        events = list(
            Event.objects.filter(season__year__in=seasons)
            .select_related("season", "circuit")
            .order_by("event_date")
        )
        if not events:
            raise CommandError(f"No events found for seasons {seasons}.")

        # ── 2. Build training dataset ────────────────────────────────────────
        # build_training_dataset is the same function the backtester uses —
        # so we're evaluating on the exact data the production model trains on.
        feature_store = _make_feature_store(options["feature_store"])
        self.stdout.write(f"Building training dataset from {len(events)} events …")
        X, y = build_training_dataset(events, feature_store)

        if X.empty:
            raise CommandError("Training dataset is empty — no features/targets could be built.")

        feat_cols = [c for c in X.columns if c not in _NON_FEATURE_COLS]
        X_feat = X[feat_cols]
        y_pos = y[TARGET_POSITION]

        self.stdout.write(
            f"Dataset: {len(X_feat)} rows, {len(feat_cols)} features — {feat_cols}"
        )

        # ── 3. TimeSeriesSplit cross-validation ──────────────────────────────
        # Why TimeSeriesSplit, not random KFold?
        # Our rows are ordered chronologically (earliest race first).
        # If we shuffled, fold 3 might train on 2025 data to predict 2023 races —
        # that's data leakage. TimeSeriesSplit ensures validation rows always come
        # *after* training rows, mirroring walk-forward backtesting.
        cv = TimeSeriesSplit(n_splits=n_splits)

        self.stdout.write(
            f"\nRunning RandomizedSearchCV: n_iter={n_iter}, TimeSeriesSplit(n_splits={n_splits})"
        )
        self.stdout.write("(this may take a minute …)\n")

        search = RandomizedSearchCV(
            XGBRegressor(objective="reg:squarederror", random_state=42, verbosity=0),
            param_distributions=PARAM_GRID,
            n_iter=n_iter,
            cv=cv,
            scoring="neg_mean_absolute_error",
            random_state=42,
            n_jobs=-1,       # parallelise across all CPU cores
            refit=False,     # we don't need a fitted model — just the scores
            return_train_score=False,
        )
        search.fit(X_feat, y_pos)

        # ── 4. Print ranked table ────────────────────────────────────────────
        results = pd.DataFrame(search.cv_results_)
        results["mae"] = -results["mean_test_score"]
        results = results.sort_values("mae").head(top_n).reset_index(drop=True)

        header = f"{'Rank':>4}  {'MAE(pos)':>8}  {'n_est':>5}  {'depth':>5}  {'lr':>5}  {'min_cw':>6}  {'sub':>4}  {'col':>4}  {'lambda':>6}"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        for i, row in results.iterrows():
            p = row["params"]
            self.stdout.write(
                f"{i + 1:>4}  {row['mae']:>8.3f}"
                f"  {p['n_estimators']:>5}"
                f"  {p['max_depth']:>5}"
                f"  {p['learning_rate']:>5}"
                f"  {p['min_child_weight']:>6}"
                f"  {p['subsample']:>4}"
                f"  {p['colsample_bytree']:>4}"
                f"  {p['reg_lambda']:>6}"
            )

        # ── 5. Best params + baseline comparison ─────────────────────────────
        best_params = results.iloc[0]["params"]
        best_mae = results.iloc[0]["mae"]

        self.stdout.write(f"\nBest params: {best_params}")

        # Run one extra CV pass at V2's defaults for a direct comparison.
        # This answers: "how much better is the tuned config vs. what we ship today?"
        self.stdout.write("Computing V2 baseline CV MAE …")
        baseline_mae = _cv_mae_with_params(X_feat, y_pos, cv, V2_DEFAULTS)
        delta = best_mae - baseline_mae

        self.stdout.write(
            f"\nV2 baseline CV MAE:  {baseline_mae:.3f}"
            f"\nBest tuned CV MAE:   {best_mae:.3f}"
            f"\nDelta:               {delta:+.3f}"
        )

        if delta < 0:
            self.stdout.write(
                "\nAction: update XGBoostPredictorV2.__init__() with the params above "
                "and re-run the backtest to confirm end-to-end improvement."
            )
        else:
            self.stdout.write(
                "\nNote: tuned params did not improve on the V2 baseline in CV. "
                "Consider more iterations (--n-iter) or a wider grid."
            )
