from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd


@dataclass(frozen=True)
class Lineup:
    """
    A recommended fantasy lineup for one race weekend.

    driver_ids:           5 driver PKs from the Driver model
    constructor_ids:      2 team PKs from the Team model
    drs_boost_driver_id:  one of the 5 drivers (the one with highest expected points)
    total_cost:           sum of all prices in $M
    predicted_points:     expected total fantasy score including DRS bonus
                          = sum(all driver pts) + sum(constructor pts) + drs_driver_pts
                            (DRS driver counted twice because they score 2x)
    """

    driver_ids: list[int]
    constructor_ids: list[int]
    drs_boost_driver_id: int
    total_cost: float
    predicted_points: float


class LineupOptimizer(Protocol):
    """
    Interface for lineup selection under budget and composition constraints.

    Implementations:
        greedy_v1.GreedyOptimizer — value-sorted greedy knapsack (baseline)
        greedy_v2.GreedyOptimizerV2 — adds budget-maximising upgrade pass
    """

    def optimize_single_race(
        self,
        driver_predictions: pd.DataFrame,
        constructor_predictions: pd.DataFrame,
        budget: float,
        constraints: dict | None,
    ) -> Lineup:
        """
        Select the best lineup for a single race.

        driver_predictions must have columns:
            driver_id, predicted_fantasy_points, price

        constructor_predictions must have columns:
            team_id, predicted_fantasy_points, price
        """
        ...
