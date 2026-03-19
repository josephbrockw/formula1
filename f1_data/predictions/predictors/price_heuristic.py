"""
Heuristic price trajectory predictor (v1).

Since F1 Fantasy prices are fully determined by the 3-race rolling AvgPPM formula,
we can predict future prices analytically — no ML model needed. The only source of
error is in the predicted fantasy points themselves.

The function takes:
  - current_price: the driver's price going into the next race (already known)
  - recent_scores: (pts, price) pairs from the last 1–3 actual races (the rolling
    window seed). Pass [] for the start of a season.
  - predicted_points: predicted fantasy points for each future race (from XGBoost)

Returns the predicted price *after* each future race, i.e. the price the driver
would cost going into the race that follows.
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd

from predictions.models import FantasyDriverPrice
from predictions.price_calculator import compute_avg_ppm, next_price


def predict_price_trajectory(
    current_price: Decimal,
    recent_scores: list[tuple[float, Decimal]],
    predicted_points: list[float],
) -> list[Decimal]:
    """
    Simulate the price formula forward using predicted fantasy points.

    Each entry in the returned list is the price after that race completes —
    i.e. what the driver will cost at the following race weekend.
    """
    window = list(recent_scores)
    price = current_price
    result = []

    for pts in predicted_points:
        avg_ppm = compute_avg_ppm(window)
        _, new_price = next_price(price, avg_ppm)
        result.append(new_price)
        window = (window + [(pts, price)])[-3:]
        price = new_price

    return result


def price_adjust_predictions(
    predictions: pd.DataFrame,
    event,
    rolling_scores: dict[int, list[tuple[float, Decimal]]],
    price_sensitivity: float,
) -> pd.DataFrame:
    """
    Boost each driver's predicted_fantasy_points by their expected price change
    multiplied by price_sensitivity.

    A driver rising $2M next race is worth price_sensitivity * 2 extra points to
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
            adjusted.at[i, "predicted_fantasy_points"] += price_change * price_sensitivity
    return adjusted


def update_rolling_scores(
    rolling_scores: dict[int, list[tuple[float, Decimal]]],
    event,
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
