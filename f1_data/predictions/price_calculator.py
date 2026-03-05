"""
Pure functions for the 2025 F1 Fantasy price change algorithm.

Algorithm (from f1fantasytools.com / PRICE_RULES.md):
  1. Compute AvgPPM = mean of (race_total / price_at_that_race) over last 1–3 races.
     No padding for early races — race 1 uses just 1 data point, race 2 uses 2.
  2. Classify AvgPPM into a performance band: Great / Good / Poor / Terrible.
  3. Look up price change from the (performance, asset_tier) table.
     A-Tier: current_price >= $19M. B-Tier: current_price < $19M.
  4. Clamp new price to [$3M, $34M].
"""

from __future__ import annotations

from decimal import Decimal

_A_TIER_THRESHOLD = Decimal("19.0")
_PRICE_FLOOR = Decimal("3.0")
_PRICE_CEILING = Decimal("34.0")

# (a_tier_change, b_tier_change) indexed by performance band
_CHANGES: dict[str, tuple[Decimal, Decimal]] = {
    "great":    (Decimal("0.3"),  Decimal("0.6")),
    "good":     (Decimal("0.1"),  Decimal("0.2")),
    "poor":     (Decimal("-0.1"), Decimal("-0.2")),
    "terrible": (Decimal("-0.3"), Decimal("-0.6")),
}


def classify_performance(avg_ppm: float) -> str:
    """Return the performance band name for a given AvgPPM value."""
    if avg_ppm > 1.2:
        return "great"
    if avg_ppm > 0.9:
        return "good"
    if avg_ppm > 0.6:
        return "poor"
    return "terrible"


def compute_price_change(avg_ppm: float, current_price: Decimal) -> Decimal:
    """
    Return the price change (in $M) for an asset given its AvgPPM and current price.

    The same thresholds apply to drivers and constructors, but because constructors
    score much higher points per $M on average, they tend to land in higher bands.
    """
    band = classify_performance(avg_ppm)
    a_change, b_change = _CHANGES[band]
    return a_change if current_price >= _A_TIER_THRESHOLD else b_change


def compute_avg_ppm(recent: list[tuple[float, Decimal]]) -> float:
    """
    Compute AvgPPM from a list of (fantasy_points, price_at_race) pairs.

    Uses only the last 3 entries (rolling window). If fewer than 3, uses all.
    Returns 0.0 for an empty list (no data = no change, treated as terrible).
    """
    if not recent:
        return 0.0
    window = recent[-3:]
    return sum(pts / float(price) for pts, price in window) / len(window)


def next_price(current_price: Decimal, avg_ppm: float) -> tuple[Decimal, Decimal]:
    """
    Given current price and AvgPPM, return (price_change, new_price).
    New price is clamped to [_PRICE_FLOOR, _PRICE_CEILING].
    """
    change = compute_price_change(avg_ppm, current_price)
    new = max(_PRICE_FLOOR, min(_PRICE_CEILING, current_price + change))
    return change, new
