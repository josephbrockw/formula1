from __future__ import annotations

import pandas as pd

from predictions.optimizers.base import Lineup

# Fantasy rules: 5 drivers, 2 constructors
_N_DRIVERS = 5
_N_CONSTRUCTORS = 2


class GreedyOptimizer:
    """
    Value-sorted greedy knapsack optimizer.

    Ranks drivers and constructors by predicted_fantasy_points / price
    (points per dollar), then picks greedily. Before each pick, a budget
    lookahead check ensures enough budget remains to fill all remaining slots
    with the cheapest available options — this prevents spending too much early
    and getting stuck.

    DRS Boost is assigned to the picked driver with the highest predicted
    fantasy points (they score double, so picking the highest scorer maximises
    the bonus).
    """

    def optimize_single_race(
        self,
        driver_predictions: pd.DataFrame,
        constructor_predictions: pd.DataFrame,
        budget: float,
        constraints: dict | None = None,
    ) -> Lineup:
        """
        Select the best lineup for a single race.

        driver_predictions must have columns:
            driver_id (int), predicted_fantasy_points (float), price (float)

        constructor_predictions must have columns:
            team_id (int), predicted_fantasy_points (float), price (float)
        """
        drivers = driver_predictions.copy()
        drivers["value"] = drivers["predicted_fantasy_points"] / drivers["price"]
        drivers = drivers.sort_values("value", ascending=False).reset_index(drop=True)

        constructors = constructor_predictions.copy()
        constructors["value"] = constructors["predicted_fantasy_points"] / constructors["price"]
        constructors = constructors.sort_values("value", ascending=False).reset_index(drop=True)

        # Reserve budget for the 2 cheapest constructors before picking drivers,
        # so we don't spend everything on drivers and have nothing left for constructors.
        constructor_reserve = float(
            constructors["price"].nsmallest(_N_CONSTRUCTORS).sum()
        )
        driver_budget = budget - constructor_reserve

        picked_driver_ids = _pick_greedily(drivers, "driver_id", _N_DRIVERS, driver_budget)

        driver_spend = float(
            drivers.loc[drivers["driver_id"].isin(picked_driver_ids), "price"].sum()
        )
        constructor_budget = budget - driver_spend

        picked_constructor_ids = _pick_greedily(
            constructors, "team_id", _N_CONSTRUCTORS, constructor_budget
        )

        total_cost = driver_spend + float(
            constructors.loc[constructors["team_id"].isin(picked_constructor_ids), "price"].sum()
        )

        driver_pts = drivers.loc[
            drivers["driver_id"].isin(picked_driver_ids), ["driver_id", "predicted_fantasy_points"]
        ]
        constructor_pts = float(
            constructors.loc[
                constructors["team_id"].isin(picked_constructor_ids), "predicted_fantasy_points"
            ].sum()
        )

        best_driver_row = driver_pts.sort_values("predicted_fantasy_points", ascending=False).iloc[0]
        drs_driver_id = int(best_driver_row["driver_id"])
        drs_bonus = float(best_driver_row["predicted_fantasy_points"])

        predicted_points = float(driver_pts["predicted_fantasy_points"].sum()) + constructor_pts + drs_bonus

        return Lineup(
            driver_ids=picked_driver_ids,
            constructor_ids=picked_constructor_ids,
            drs_boost_driver_id=drs_driver_id,
            total_cost=total_cost,
            predicted_points=predicted_points,
        )


def _pick_greedily(
    candidates: pd.DataFrame,
    id_col: str,
    n_to_pick: int,
    budget: float,
) -> list[int]:
    """
    Greedily pick n_to_pick candidates by value (descending) within budget.

    Before picking candidate at index i, checks that:
        candidate.price + cheapest(slots_left - 1 candidates after index i) <= remaining_budget

    This lookahead ensures we never commit to a pick that makes it impossible
    to fill the remaining slots. Candidates are processed in value order and
    never revisited.
    """
    prices = candidates["price"].tolist()
    picked: list[int] = []
    remaining = budget

    for i, row in candidates.iterrows():
        if len(picked) == n_to_pick:
            break

        slots_left = n_to_pick - len(picked)

        # Prices of all candidates after position i (not yet considered)
        future_prices = sorted(prices[i + 1 :])
        cheapest_to_fill_rest = sum(future_prices[: slots_left - 1])

        if row["price"] + cheapest_to_fill_rest <= remaining:
            picked.append(int(row[id_col]))
            remaining -= float(row["price"])

    return picked
