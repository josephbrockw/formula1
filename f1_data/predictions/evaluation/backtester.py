from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

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
from predictions.predictors.price_heuristic import predict_price_trajectory
from predictions.predictors.xgboost_v1 import build_training_dataset, walk_forward_splits

# Points of future lineup value attributed to each $1M of predicted price appreciation.
# A driver rising $2M is treated as scoring PRICE_SENSITIVITY * 2 extra points this race.
PRICE_SENSITIVITY = 5.0


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
    n_transfers: int = 0


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
        on_race_done: Callable[[RaceBacktestResult, int, int], None] | None = None,
    ) -> BacktestResult:
        """
        Walk-forward evaluation over a list of chronologically ordered events.

        For each split, trains the predictor on past races, predicts the next
        race, then evaluates MAE and (if price data is available) lineup quality.

        on_race_done(result, n, total) is called after each race completes,
        where n is the 1-based index and total is the number of splits.
        """
        race_results = []
        rolling_scores: dict[int, list[tuple[float, Decimal]]] = {}
        current_lineup: Lineup | None = None
        splits = list(walk_forward_splits(events, min_train))
        total = len(splits)
        for n, (train_events, test_event) in enumerate(splits, start=1):
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
            adjusted = _price_adjust_predictions(predictions, test_event, rolling_scores)
            constraints = {
                "current_lineup": current_lineup,
                "free_transfers": 2,
                "transfer_penalty": 10.0,
            }
            lineup_predicted, lineup_actual, optimal, new_lineup = _optimize_and_score(
                test_event, adjusted, actuals, optimizer, budget, constraints
            )
            n_transfers = _count_transfers(current_lineup, new_lineup)
            if lineup_actual is not None:
                lineup_actual -= max(0, n_transfers - 2) * 10.0
            current_lineup = new_lineup
            race_result = RaceBacktestResult(
                event_id=test_event.id,
                event_name=test_event.event_name,
                n_train=len(train_events),
                mae_position=mae_pos,
                mae_fantasy_points=mae_pts,
                lineup_predicted_points=lineup_predicted,
                lineup_actual_points=lineup_actual,
                optimal_actual_points=optimal,
                n_transfers=n_transfers,
            )
            race_results.append(race_result)
            if on_race_done:
                on_race_done(race_result, n, total)
            _update_rolling_scores(rolling_scores, test_event, actuals)
        return BacktestResult(race_results=race_results)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_transfers(old: Lineup | None, new: Lineup | None) -> int:
    """Count how many players changed between two lineups."""
    if old is None or new is None:
        return 0
    return (
        len(set(new.driver_ids) - set(old.driver_ids))
        + len(set(new.constructor_ids) - set(old.constructor_ids))
    )


def _price_adjust_predictions(
    predictions: pd.DataFrame,
    event: Event,
    rolling_scores: dict[int, list[tuple[float, Decimal]]],
) -> pd.DataFrame:
    """
    Boost each driver's predicted_fantasy_points by their expected price change
    multiplied by PRICE_SENSITIVITY.

    A driver rising $2M next race is worth PRICE_SENSITIVITY * 2 extra points to
    the optimizer — that money expands future lineup budgets. Uses horizon=1 (next
    race only) since uncertainty compounds quickly beyond one race.

    Returns predictions unchanged when no price data exists for this event.
    """
    driver_prices = dict(
        FantasyDriverPrice.objects.filter(event=event).values_list("driver_id", "price")
    )
    if not driver_prices:
        return predictions
    adjusted = predictions.copy()
    for i, row in adjusted.iterrows():
        did = int(row["driver_id"])
        price = driver_prices.get(did)
        if price is None:
            continue
        recent = rolling_scores.get(did, [])
        trajectory = predict_price_trajectory(price, recent, [float(row["predicted_fantasy_points"])])
        if trajectory:
            price_change = float(trajectory[0] - price)
            adjusted.at[i, "predicted_fantasy_points"] += price_change * PRICE_SENSITIVITY
    return adjusted


def _update_rolling_scores(
    rolling_scores: dict[int, list[tuple[float, Decimal]]],
    event: Event,
    actuals: dict[int, tuple[float, float]],
) -> None:
    """Append this race's actual (pts, price) to each driver's rolling window."""
    driver_prices = dict(
        FantasyDriverPrice.objects.filter(event=event).values_list("driver_id", "price")
    )
    for did, (_, actual_pts) in actuals.items():
        price = driver_prices.get(did)
        if price is None:
            continue
        history = rolling_scores.get(did, [])
        rolling_scores[did] = (history + [(actual_pts, price)])[-3:]


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
    constraints: dict | None = None,
) -> tuple[float | None, float | None, float | None, Lineup | None]:
    """
    Return (lineup_predicted_pts, lineup_actual_pts, optimal_actual_pts, lineup).
    Returns (None, None, None, None) when price data is unavailable for the event.
    """
    driver_prices = dict(
        FantasyDriverPrice.objects.filter(event=event).values_list("driver_id", "price")
    )
    constructor_prices = dict(
        FantasyConstructorPrice.objects.filter(event=event).values_list("team_id", "price")
    )
    if not driver_prices or not constructor_prices:
        return None, None, None, None

    driver_preds_df = _build_driver_preds_df(predictions, driver_prices)
    constructor_preds_df = _build_constructor_preds_df(event, predictions, constructor_prices)
    if driver_preds_df.empty or constructor_preds_df.empty:
        return None, None, None, None

    lineup = optimizer.optimize_single_race(driver_preds_df, constructor_preds_df, budget, constraints)
    actual_driver_pts = {did: pts for did, (_, pts) in actuals.items()}
    actual_constructor_pts = dict(
        FantasyConstructorScore.objects.filter(event=event)
        .values("team_id")
        .annotate(total=Max("race_total"))
        .values_list("team_id", "total")
    )
    lineup_actual = _score_lineup(lineup, actual_driver_pts, actual_constructor_pts)
    # Oracle optimal is always unconstrained — it's the ceiling with perfect knowledge.
    optimal = _optimal_score(
        driver_preds_df, constructor_preds_df, actual_driver_pts, actual_constructor_pts, optimizer, budget
    )
    return lineup.predicted_points, lineup_actual, optimal, lineup


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
