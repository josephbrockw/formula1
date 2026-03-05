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
