from __future__ import annotations

import numpy as np
import pandas as pd
from django.conf import settings
from scipy.optimize import Bounds, LinearConstraint, milp

from predictions.optimizers.base import Lineup

_N_DRIVERS = 5
_N_CONSTRUCTORS = 2


class ILPOptimizer:
    """
    Integer Linear Programming optimizer. Provably finds the globally optimal lineup.

    Variable layout: [x_0..x_{n_d-1} | y_0..y_{n_c-1} | z_0..z_{n_d-1} | e]

        x[i]  — driver i is selected            (binary)
        y[j]  — constructor j is selected       (binary)
        z[i]  — driver i receives DRS boost     (binary)
        e     — excess transfers beyond free allowance (continuous, ≥ 0)
                only present when constraints["current_lineup"] is not None

    Objective (minimise negated score + transfer penalty):
        -(Σ pts[i]*x[i] + Σ pts[j]*y[j] + Σ pts[i]*z[i])  +  transfer_penalty * e

    The DRS driver appears in both x and z so their points count twice.
    Transfer penalty only applies to players not already in the current lineup.
    Players retained from the previous race have a coefficient of 0 in the transfer sum.

    Constraints:
        (1) Σ price[i]*x[i] + Σ price[j]*y[j]  ≤  budget
        (2) Σ x[i] = 5
        (3) Σ y[j] = 2
        (4) Σ z[i] = 1
        (5) z[i] - x[i] ≤ 0  for each i  (DRS driver must be in lineup)
        (6) Σ_{new} x[i] + Σ_{new} y[j] - e ≤ free_transfers  (when current_lineup given)
    """

    def optimize_single_race(
        self,
        driver_predictions: pd.DataFrame,
        constructor_predictions: pd.DataFrame,
        budget: float,
        constraints: dict | None = None,
    ) -> Lineup:
        current_lineup = (constraints or {}).get("current_lineup")
        free_transfers = int((constraints or {}).get("free_transfers", 2))
        transfer_penalty = float((constraints or {}).get("transfer_penalty", 10.0))

        drivers = driver_predictions.reset_index(drop=True)
        constructors = constructor_predictions.reset_index(drop=True)
        n_d = len(drivers)
        n_c = len(constructors)

        d_pts = drivers["predicted_fantasy_points"].to_numpy(dtype=float)
        c_pts = constructors["predicted_fantasy_points"].to_numpy(dtype=float)
        d_price = drivers["price"].to_numpy(dtype=float)
        c_price = constructors["price"].to_numpy(dtype=float)

        has_e = current_lineup is not None
        n_vars = 2 * n_d + n_c + (1 if has_e else 0)

        # Build new-player masks (1.0 = new, 0.0 = retained). Only when we have a prior lineup.
        new_d = np.zeros(n_d)
        new_c = np.zeros(n_c)
        if has_e:
            current_driver_ids = set(current_lineup.driver_ids)
            current_constructor_ids = set(current_lineup.constructor_ids)
            new_d = np.array([
                0.0 if int(drivers.loc[i, "driver_id"]) in current_driver_ids else 1.0
                for i in range(n_d)
            ])
            new_c = np.array([
                0.0 if int(constructors.loc[j, "team_id"]) in current_constructor_ids else 1.0
                for j in range(n_c)
            ])

        # Objective: negate score + threshold cost for new players (not applied to z/DRS variables).
        threshold = getattr(settings, "ILP_TRANSFER_THRESHOLD", 0.0)
        d_obj = -d_pts + threshold * new_d
        c_obj = -c_pts + threshold * new_c
        obj = np.concatenate([d_obj, c_obj, -d_pts])  # z uses raw -d_pts (DRS is always free)
        if has_e:
            obj = np.append(obj, transfer_penalty)

        # --- Constraints ---
        A_rows, lb_rows, ub_rows = [], [], []

        def _row(*parts: np.ndarray) -> np.ndarray:
            r = np.concatenate(parts)
            return np.append(r, 0.0) if has_e else r

        # (1) Budget
        A_rows.append(_row(d_price, c_price, np.zeros(n_d)))
        lb_rows.append(-np.inf)
        ub_rows.append(budget)

        # (2) Exactly 5 drivers
        A_rows.append(_row(np.ones(n_d), np.zeros(n_c), np.zeros(n_d)))
        lb_rows.append(_N_DRIVERS)
        ub_rows.append(_N_DRIVERS)

        # (3) Exactly 2 constructors
        A_rows.append(_row(np.zeros(n_d), np.ones(n_c), np.zeros(n_d)))
        lb_rows.append(_N_CONSTRUCTORS)
        ub_rows.append(_N_CONSTRUCTORS)

        # (4) Exactly 1 DRS
        A_rows.append(_row(np.zeros(n_d), np.zeros(n_c), np.ones(n_d)))
        lb_rows.append(1)
        ub_rows.append(1)

        # (5) z[i] ≤ x[i] for each driver
        for i in range(n_d):
            row = np.zeros(n_vars)
            row[i] = -1              # -x[i]
            row[n_d + n_c + i] = 1  # +z[i]
            A_rows.append(row)
            lb_rows.append(-np.inf)
            ub_rows.append(0.0)

        # (6) Transfer budget: Σ_{new} x[i] + Σ_{new} y[j] - e ≤ free_transfers
        if has_e:
            A_rows.append(np.concatenate([new_d, new_c, np.zeros(n_d), [-1.0]]))
            lb_rows.append(-np.inf)
            ub_rows.append(float(free_transfers))

        lin_constraints = LinearConstraint(np.vstack(A_rows), lb_rows, ub_rows)

        # x, y, z are binary (integrality=1); e is continuous (integrality=0)
        integrality = np.ones(n_vars)
        if has_e:
            integrality[-1] = 0

        lb_bounds = np.zeros(n_vars)
        ub_bounds = np.ones(n_vars)
        if has_e:
            ub_bounds[-1] = float(n_d + n_c)  # max possible excess transfers

        sol = milp(
            obj,
            constraints=lin_constraints,
            integrality=integrality,
            bounds=Bounds(lb_bounds, ub_bounds),
        )

        if not sol.success:
            raise ValueError(f"ILP solver found no feasible lineup: {sol.message}")

        x = sol.x
        x_vars = np.round(x[:n_d]).astype(int)
        y_vars = np.round(x[n_d : n_d + n_c]).astype(int)
        z_vars = np.round(x[n_d + n_c : 2 * n_d + n_c]).astype(int)

        driver_ids = [int(drivers.loc[i, "driver_id"]) for i in range(n_d) if x_vars[i] == 1]
        constructor_ids = [int(constructors.loc[j, "team_id"]) for j in range(n_c) if y_vars[j] == 1]
        drs_idx = int(np.argmax(z_vars))
        drs_boost_driver_id = int(drivers.loc[drs_idx, "driver_id"])

        total_cost = float(d_price[x_vars == 1].sum() + c_price[y_vars == 1].sum())
        # predicted_points does NOT subtract the transfer penalty — the backtester
        # applies that deduction separately from actual post-race scores.
        predicted_points = (
            float(d_pts[x_vars == 1].sum())
            + float(c_pts[y_vars == 1].sum())
            + float(d_pts[drs_idx])
        )

        return Lineup(
            driver_ids=driver_ids,
            constructor_ids=constructor_ids,
            drs_boost_driver_id=drs_boost_driver_id,
            total_cost=total_cost,
            predicted_points=predicted_points,
        )
