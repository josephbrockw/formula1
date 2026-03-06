from __future__ import annotations

import pandas as pd

from predictions.optimizers.base import Lineup
from predictions.optimizers.greedy_v1 import _pick_greedily

# Fantasy rules: 5 drivers, 2 constructors
_N_DRIVERS = 5
_N_CONSTRUCTORS = 2


class GreedyOptimizerV2:
    """
    Budget-maximising greedy optimizer (v2).

    Extends v1's value-sorted greedy knapsack with an upgrade pass: after the
    initial greedy selection, any picked player is swapped for a higher-scoring
    unpicked player if the swap fits within the remaining budget. This repeats
    until no further improvement is possible.

    This fixes the main weakness of v1: PPM ranking finds the best *value*
    lineup but ignores leftover budget. If you have $30M unspent, you should
    upgrade picks to higher-scoring options even if their PPM is lower.
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

        constructor_reserve = float(
            constructors["price"].nsmallest(_N_CONSTRUCTORS).sum()
        )
        driver_budget = budget - constructor_reserve

        picked_driver_ids = _upgrade_picks(
            _pick_greedily(drivers, "driver_id", _N_DRIVERS, driver_budget),
            drivers, "driver_id", driver_budget,
        )

        driver_spend = float(
            drivers.loc[drivers["driver_id"].isin(picked_driver_ids), "price"].sum()
        )
        constructor_budget = budget - driver_spend

        picked_constructor_ids = _upgrade_picks(
            _pick_greedily(constructors, "team_id", _N_CONSTRUCTORS, constructor_budget),
            constructors, "team_id", constructor_budget,
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

        ideal = _build_lineup_from_ids(
            picked_driver_ids, picked_constructor_ids, drivers, constructors
        )

        current_lineup = (constraints or {}).get("current_lineup")
        if current_lineup is None:
            return ideal

        return _apply_transfer_constraints(
            ideal=ideal,
            current=current_lineup,
            free_transfers=(constraints or {}).get("free_transfers", 2),
            transfer_penalty=(constraints or {}).get("transfer_penalty", 10.0),
            drivers_df=drivers,
            constructors_df=constructors,
            budget=budget,
        )


def _build_lineup_from_ids(
    driver_ids: list[int],
    constructor_ids: list[int],
    drivers_df: pd.DataFrame,
    constructors_df: pd.DataFrame,
) -> Lineup:
    """Construct a Lineup dataclass from id lists, computing DRS and predicted points."""
    d_pts_map = dict(zip(drivers_df["driver_id"].astype(int), drivers_df["predicted_fantasy_points"].astype(float)))
    c_pts_map = dict(zip(constructors_df["team_id"].astype(int), constructors_df["predicted_fantasy_points"].astype(float)))
    d_price_map = dict(zip(drivers_df["driver_id"].astype(int), drivers_df["price"].astype(float)))
    c_price_map = dict(zip(constructors_df["team_id"].astype(int), constructors_df["price"].astype(float)))

    d_pts = [d_pts_map.get(d, 0.0) for d in driver_ids]
    drs_driver_id = driver_ids[d_pts.index(max(d_pts))]
    drs_bonus = max(d_pts)
    predicted_points = sum(d_pts) + sum(c_pts_map.get(c, 0.0) for c in constructor_ids) + drs_bonus
    total_cost = sum(d_price_map.get(d, 0.0) for d in driver_ids) + sum(c_price_map.get(c, 0.0) for c in constructor_ids)

    return Lineup(
        driver_ids=driver_ids,
        constructor_ids=constructor_ids,
        drs_boost_driver_id=drs_driver_id,
        total_cost=total_cost,
        predicted_points=predicted_points,
    )


def _apply_transfer_constraints(
    ideal: Lineup,
    current: Lineup,
    free_transfers: int,
    transfer_penalty: float,
    drivers_df: pd.DataFrame,
    constructors_df: pd.DataFrame,
    budget: float,
) -> Lineup:
    """
    Given the unconstrained ideal lineup and the previous race's lineup, return the
    best reachable lineup under transfer limits.

    Algorithm:
    1. Find which players changed between current and ideal.
    2. Pair each outgoing player with an incoming player, sorting so the highest-gain
       swap is paired first (drop lowest scorer, bring in highest scorer).
    3. Take all free transfers, then any paid transfers where gain > transfer_penalty.
    4. Budget-check: if over budget, drop the last paid transfer.
    5. Return the resulting lineup.
    """
    d_pts = dict(zip(drivers_df["driver_id"].astype(int), drivers_df["predicted_fantasy_points"].astype(float)))
    c_pts = dict(zip(constructors_df["team_id"].astype(int), constructors_df["predicted_fantasy_points"].astype(float)))
    d_price = dict(zip(drivers_df["driver_id"].astype(int), drivers_df["price"].astype(float)))
    c_price = dict(zip(constructors_df["team_id"].astype(int), constructors_df["price"].astype(float)))

    # Drivers being dropped (in current, not in ideal), sorted worst-first
    drivers_out = sorted(set(current.driver_ids) - set(ideal.driver_ids), key=lambda d: d_pts.get(d, 0.0))
    # Drivers being brought in (in ideal, not in current), sorted best-first
    drivers_in = sorted(set(ideal.driver_ids) - set(current.driver_ids), key=lambda d: d_pts.get(d, 0.0), reverse=True)
    constructors_out = sorted(set(current.constructor_ids) - set(ideal.constructor_ids), key=lambda c: c_pts.get(c, 0.0))
    constructors_in = sorted(set(ideal.constructor_ids) - set(current.constructor_ids), key=lambda c: c_pts.get(c, 0.0), reverse=True)

    # Build (old_id, new_id, gain, is_driver) tuples and sort by gain descending
    changes: list[tuple[int, int, float, bool]] = []
    for old, new in zip(drivers_out, drivers_in):
        changes.append((old, new, d_pts.get(new, 0.0) - d_pts.get(old, 0.0), True))
    for old, new in zip(constructors_out, constructors_in):
        changes.append((old, new, c_pts.get(new, 0.0) - c_pts.get(old, 0.0), False))
    changes.sort(key=lambda c: c[2], reverse=True)

    # Accept free transfers unconditionally; paid transfers only if gain > penalty
    selected = [c for i, c in enumerate(changes) if i < free_transfers or c[2] > transfer_penalty]

    # Apply selected changes to current lineup
    final_drivers = list(current.driver_ids)
    final_constructors = list(current.constructor_ids)
    for old_id, new_id, _, is_driver in selected:
        if is_driver:
            final_drivers.remove(old_id)
            final_drivers.append(new_id)
        else:
            final_constructors.remove(old_id)
            final_constructors.append(new_id)

    # If over budget, revert the last (lowest-gain) paid change
    cost = sum(d_price.get(d, 0.0) for d in final_drivers) + sum(c_price.get(c, 0.0) for c in final_constructors)
    if cost > budget and len(selected) > free_transfers:
        old_id, new_id, _, is_driver = selected[-1]
        if is_driver:
            final_drivers.remove(new_id)
            final_drivers.append(old_id)
        else:
            final_constructors.remove(new_id)
            final_constructors.append(old_id)

    return _build_lineup_from_ids(final_drivers, final_constructors, drivers_df, constructors_df)


def _upgrade_picks(
    picked_ids: list[int],
    candidates: pd.DataFrame,
    id_col: str,
    budget: float,
) -> list[int]:
    """
    After greedy selection, swap any picked player for a higher-scoring unpicked
    player that fits in the remaining budget. Repeats until no improvement is possible.
    """
    picked = set(picked_ids)
    improved = True
    while improved:
        improved = False
        current_cost = float(candidates.loc[candidates[id_col].isin(picked), "price"].sum())
        for pid in list(picked):
            row = candidates.loc[candidates[id_col] == pid].iloc[0]
            swap_budget = budget - current_cost + float(row["price"])
            unpicked = candidates.loc[~candidates[id_col].isin(picked)]
            better = unpicked.loc[
                (unpicked["price"] <= swap_budget)
                & (unpicked["predicted_fantasy_points"] > float(row["predicted_fantasy_points"]))
            ]
            if better.empty:
                continue
            best = better.sort_values("predicted_fantasy_points", ascending=False).iloc[0]
            picked.remove(pid)
            picked.add(int(best[id_col]))
            improved = True
            break
    return list(picked)
