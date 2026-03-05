from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from django.db.models import Max

from core.models import Driver, Event, SessionResult
from predictions.features.base import FeatureStore
from predictions.models import (
    FantasyConstructorPrice,
    FantasyConstructorScore,
    FantasyDriverPrice,
    FantasyDriverScore,
)
from predictions.optimizers.base import Lineup, LineupOptimizer
from predictions.predictors.base import PerformancePredictor
from predictions.predictors.xgboost_v1 import build_training_dataset, walk_forward_splits


@dataclass
class RaceBacktestResult:
    """
    Metrics for a single race in the backtest.

    lineup_* and optimal_* are None when FantasyDriverPrice /
    FantasyConstructorPrice records are absent for that event.
    """

    event_id: int
    event_name: str
    n_train: int
    mae_position: float
    mae_fantasy_points: float
    lineup_predicted_points: float | None
    lineup_actual_points: float | None
    optimal_actual_points: float | None


@dataclass
class BacktestResult:
    """Aggregated backtest output across all evaluated races."""

    race_results: list[RaceBacktestResult]

    @property
    def mean_mae_position(self) -> float:
        vals = [r.mae_position for r in self.race_results]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def mean_mae_fantasy_points(self) -> float:
        vals = [r.mae_fantasy_points for r in self.race_results]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def total_lineup_points(self) -> float | None:
        pts = [r.lineup_actual_points for r in self.race_results if r.lineup_actual_points is not None]
        return sum(pts) if pts else None

    @property
    def total_optimal_points(self) -> float | None:
        pts = [r.optimal_actual_points for r in self.race_results if r.optimal_actual_points is not None]
        return sum(pts) if pts else None


class Backtester:
    def run(
        self,
        events: list[Event],
        feature_store: FeatureStore,
        predictor: PerformancePredictor,
        optimizer: LineupOptimizer,
        min_train: int = 5,
        budget: float = 100.0,
    ) -> BacktestResult:
        """
        Walk-forward evaluation over a list of chronologically ordered events.

        For each split, trains the predictor on past races, predicts the next
        race, then evaluates MAE and (if price data is available) lineup quality.
        """
        race_results = []
        for train_events, test_event in walk_forward_splits(events, min_train):
            X, y = build_training_dataset(train_events, feature_store)
            if X.empty:
                continue
            predictor.fit(X, y)
            features = feature_store.get_all_driver_features(test_event.id)
            if features.empty:
                continue
            predictions = predictor.predict(features)
            actuals = _actual_driver_results(test_event)
            if not actuals:
                continue
            mae_pos, mae_pts = _compute_mae(predictions, actuals)
            lineup_predicted, lineup_actual, optimal = _optimize_and_score(
                test_event, predictions, actuals, optimizer, budget
            )
            race_results.append(
                RaceBacktestResult(
                    event_id=test_event.id,
                    event_name=test_event.event_name,
                    n_train=len(train_events),
                    mae_position=mae_pos,
                    mae_fantasy_points=mae_pts,
                    lineup_predicted_points=lineup_predicted,
                    lineup_actual_points=lineup_actual,
                    optimal_actual_points=optimal,
                )
            )
        return BacktestResult(race_results=race_results)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _actual_driver_results(event: Event) -> dict[int, tuple[float, float]]:
    """Return {driver_id: (position, fantasy_pts)} for drivers with both values."""
    positions = dict(
        SessionResult.objects.filter(
            session__event=event, session__session_type="R"
        ).values_list("driver_id", "position")
    )
    fantasy_pts = dict(
        FantasyDriverScore.objects.filter(event=event)
        .values("driver_id")
        .annotate(total=Max("race_total"))
        .values_list("driver_id", "total")
    )
    return {
        did: (float(positions[did]), float(fantasy_pts[did]))
        for did in positions
        if did in fantasy_pts and positions[did] is not None
    }


def _compute_mae(
    predictions: pd.DataFrame,
    actuals: dict[int, tuple[float, float]],
) -> tuple[float, float]:
    """Return (mae_position, mae_fantasy_points) for drivers present in both."""
    pos_errors: list[float] = []
    pts_errors: list[float] = []
    for _, row in predictions.iterrows():
        did = int(row["driver_id"])
        if did not in actuals:
            continue
        actual_pos, actual_pts = actuals[did]
        pos_errors.append(abs(float(row["predicted_position"]) - actual_pos))
        pts_errors.append(abs(float(row["predicted_fantasy_points"]) - actual_pts))
    if not pos_errors:
        return 0.0, 0.0
    n = len(pos_errors)
    return sum(pos_errors) / n, sum(pts_errors) / n


def _optimize_and_score(
    event: Event,
    predictions: pd.DataFrame,
    actuals: dict[int, tuple[float, float]],
    optimizer: LineupOptimizer,
    budget: float,
) -> tuple[float | None, float | None, float | None]:
    """
    Return (lineup_predicted_pts, lineup_actual_pts, optimal_actual_pts).
    Returns (None, None, None) when price data is unavailable for the event.
    """
    driver_prices = dict(
        FantasyDriverPrice.objects.filter(event=event).values_list("driver_id", "price")
    )
    constructor_prices = dict(
        FantasyConstructorPrice.objects.filter(event=event).values_list("team_id", "price")
    )
    if not driver_prices or not constructor_prices:
        return None, None, None

    driver_preds_df = _build_driver_preds_df(predictions, driver_prices)
    constructor_preds_df = _build_constructor_preds_df(event, predictions, constructor_prices)
    if driver_preds_df.empty or constructor_preds_df.empty:
        return None, None, None

    lineup = optimizer.optimize_single_race(driver_preds_df, constructor_preds_df, budget)
    actual_driver_pts = {did: pts for did, (_, pts) in actuals.items()}
    actual_constructor_pts = dict(
        FantasyConstructorScore.objects.filter(event=event)
        .values("team_id")
        .annotate(total=Max("race_total"))
        .values_list("team_id", "total")
    )
    lineup_actual = _score_lineup(lineup, actual_driver_pts, actual_constructor_pts)
    optimal = _optimal_score(
        driver_preds_df, constructor_preds_df, actual_driver_pts, actual_constructor_pts, optimizer, budget
    )
    return lineup.predicted_points, lineup_actual, optimal


def _build_driver_preds_df(
    predictions: pd.DataFrame, driver_prices: dict[int, float]
) -> pd.DataFrame:
    """Merge predictions with price data, dropping drivers without a price."""
    rows = [
        {
            "driver_id": int(row["driver_id"]),
            "predicted_fantasy_points": float(row["predicted_fantasy_points"]),
            "price": float(driver_prices[int(row["driver_id"])]),
        }
        for _, row in predictions.iterrows()
        if int(row["driver_id"]) in driver_prices
    ]
    return pd.DataFrame(rows)


def _build_constructor_preds_df(
    event: Event,
    predictions: pd.DataFrame,
    constructor_prices: dict[int, float],
) -> pd.DataFrame:
    """
    Build constructor predictions by summing both team drivers' predicted points.

    Without a dedicated constructor predictor, this is our best proxy for
    constructor performance: if both drivers score well, the constructor scores well.
    """
    pred_lookup = dict(
        zip(predictions["driver_id"].astype(int), predictions["predicted_fantasy_points"].astype(float))
    )
    team_drivers: dict[int, list[int]] = {}
    for driver in Driver.objects.filter(season=event.season).select_related("team"):
        team_drivers.setdefault(driver.team_id, []).append(driver.id)

    rows = [
        {
            "team_id": team_id,
            "predicted_fantasy_points": sum(pred_lookup.get(did, 0.0) for did in team_drivers.get(team_id, [])),
            "price": float(price),
        }
        for team_id, price in constructor_prices.items()
    ]
    return pd.DataFrame(rows)


def _score_lineup(
    lineup: Lineup,
    actual_driver_pts: dict[int, float],
    actual_constructor_pts: dict[int, float],
) -> float:
    """Score a lineup using actual post-race points. DRS driver counts double."""
    driver_total = sum(actual_driver_pts.get(did, 0.0) for did in lineup.driver_ids)
    constructor_total = sum(actual_constructor_pts.get(cid, 0.0) for cid in lineup.constructor_ids)
    drs_bonus = actual_driver_pts.get(lineup.drs_boost_driver_id, 0.0)
    return driver_total + constructor_total + drs_bonus


def _optimal_score(
    driver_preds_df: pd.DataFrame,
    constructor_preds_df: pd.DataFrame,
    actual_driver_pts: dict[int, float],
    actual_constructor_pts: dict[int, float],
    optimizer: LineupOptimizer,
    budget: float,
) -> float:
    """
    Run the optimizer with actual points as predictions to find the best
    achievable lineup. This is the oracle ceiling — the score we'd get with
    perfect predictions. The gap between this and lineup_actual_points shows
    how much accuracy matters.
    """
    oracle_drivers = driver_preds_df.copy()
    oracle_drivers["predicted_fantasy_points"] = oracle_drivers["driver_id"].map(
        lambda did: actual_driver_pts.get(int(did), 0.0)
    )
    oracle_constructors = constructor_preds_df.copy()
    oracle_constructors["predicted_fantasy_points"] = oracle_constructors["team_id"].map(
        lambda tid: actual_constructor_pts.get(int(tid), 0.0)
    )
    oracle_lineup = optimizer.optimize_single_race(oracle_drivers, oracle_constructors, budget)
    return _score_lineup(oracle_lineup, actual_driver_pts, actual_constructor_pts)
