from __future__ import annotations

import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from core.models import Driver, Event, Team
from predictions.features.v2_pandas import V2FeatureStore
from decimal import Decimal

from predictions.models import FantasyConstructorPrice, FantasyDriverPrice, MyLineup
from predictions.optimizers.base import Lineup
from predictions.optimizers.greedy_v2 import GreedyOptimizerV2
from predictions.predictors.xgboost_v1 import build_training_dataset
from predictions.predictors.xgboost_v2 import XGBoostPredictorV2


class Command(BaseCommand):
    help = "Predict the next race and recommend lineup changes from your current team."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--year", type=int, required=True)
        parser.add_argument("--round", type=int, required=True)
        parser.add_argument("--budget", type=float, default=None,
                            help="Budget cap in $M (auto-detected from last lineup if not given)")

    def handle(self, *args, **options) -> None:
        year = options["year"]
        round_number = options["round"]

        try:
            event = Event.objects.select_related("season", "circuit").get(
                season__year=year, round_number=round_number
            )
        except Event.DoesNotExist:
            raise CommandError(f"No event found for year={year}, round={round_number}")

        train_events = list(
            Event.objects.filter(event_date__lt=event.event_date)
            .select_related("season", "circuit")
            .order_by("event_date")
        )

        self.stdout.write(f"\nNext Race: Round {round_number} — {event.event_name} ({event.event_date})")
        self.stdout.write(f"Training on {len(train_events)} past events\n")

        feature_store = V2FeatureStore()
        predictor = XGBoostPredictorV2()

        X, y = build_training_dataset(train_events, feature_store)
        if X.empty:
            raise CommandError(
                "No training data — need past events with race results and fantasy scores."
            )
        predictor.fit(X, y)

        features = feature_store.get_all_driver_features(event.id)
        if features.empty:
            raise CommandError(
                f"No features for {event}. Run collect_data --year {year} to pull session data."
            )

        predictions = predictor.predict(features)
        drivers_by_id = {d.id: d for d in Driver.objects.filter(season=event.season)}
        teams_by_id = {t.id: t for t in Team.objects.filter(season=event.season)}

        _print_predictions(self.stdout, predictions, drivers_by_id)

        driver_prices = dict(
            FantasyDriverPrice.objects.filter(event=event).values_list("driver_id", "price")
        )
        constructor_prices = dict(
            FantasyConstructorPrice.objects.filter(event=event).values_list("team_id", "price")
        )
        if not driver_prices or not constructor_prices:
            raise CommandError(
                "No price data for this event. Run compute_fantasy_prices or import_fantasy_csv first."
            )

        driver_preds_df = _build_driver_preds_df(predictions, driver_prices)
        constructor_preds_df = _build_constructor_preds_df(event, predictions, constructor_prices)

        current_lineup, banked_transfers = _current_state(event)
        last_mylineup = (
            MyLineup.objects
            .filter(event__season=event.season, event__event_date__lt=event.event_date)
            .order_by("-event__event_date")
            .first()
        )

        auto_budget = _compute_available_budget(last_mylineup, event) if last_mylineup else None
        if auto_budget is not None:
            budget = auto_budget
            self.stdout.write(f"Budget: ${budget:.1f}M (auto-detected)")
        elif options["budget"] is not None:
            budget = options["budget"]
            self.stdout.write(f"Budget: ${budget:.1f}M (manual override)")
        else:
            raise CommandError(
                "Could not auto-detect budget (prices missing or no prior lineup). "
                "Pass --budget to override."
            )

        self.stdout.write("\n--- CURRENT TEAM ---")
        if current_lineup is None:
            self.stdout.write("  No lineup recorded yet (first race — no transfer constraints)")
        else:
            driver_codes = " ".join(drivers_by_id[did].code for did in current_lineup.driver_ids if did in drivers_by_id)
            team_names = " / ".join(teams_by_id[cid].name for cid in current_lineup.constructor_ids if cid in teams_by_id)
            drs_code = drivers_by_id[current_lineup.drs_boost_driver_id].code if current_lineup.drs_boost_driver_id in drivers_by_id else "?"
            self.stdout.write(f"  Drivers: {driver_codes}  (DRS: {drs_code})")
            self.stdout.write(f"  Constructors: {team_names}")
            self.stdout.write(f"  Banked transfers: {banked_transfers}")

        constraints = {
            "current_lineup": current_lineup,
            "free_transfers": banked_transfers,
            "transfer_penalty": 10.0,
        }
        lineup = GreedyOptimizerV2().optimize_single_race(
            driver_preds_df, constructor_preds_df, budget, constraints
        )

        if current_lineup is not None:
            _print_transfers(self.stdout, current_lineup, lineup, drivers_by_id, teams_by_id, banked_transfers)

        _print_lineup(self.stdout, lineup, drivers_by_id, teams_by_id, driver_prices, constructor_prices, predictions)


# ---------------------------------------------------------------------------
# Current team state
# ---------------------------------------------------------------------------


def _mylineup_to_lineup(my_lineup: MyLineup) -> Lineup:
    return Lineup(
        driver_ids=[
            my_lineup.driver_1_id, my_lineup.driver_2_id, my_lineup.driver_3_id,
            my_lineup.driver_4_id, my_lineup.driver_5_id,
        ],
        constructor_ids=[my_lineup.constructor_1_id, my_lineup.constructor_2_id],
        drs_boost_driver_id=my_lineup.drs_boost_driver_id,
        total_cost=0.0,
        predicted_points=0.0,
    )


def _current_state(event: Event) -> tuple[Lineup | None, int]:
    """Return (current lineup as Lineup, banked transfers) based on saved MyLineup records.

    Replays the transfer credit logic forward through every saved lineup for this
    season up to (but not including) the target event, so the banked transfer count
    is accurate even if the user has been saving lineups every race.
    """
    saved = list(
        MyLineup.objects.filter(
            event__season=event.season,
            event__event_date__lt=event.event_date,
        ).select_related("event").order_by("event__event_date")
    )

    banked = 2  # season-start credit
    prev: Lineup | None = None

    for my_lineup in saved:
        current = _mylineup_to_lineup(my_lineup)
        n_transfers = _count_transfers(prev, current)
        banked = min(2, banked - min(n_transfers, banked) + 1)
        prev = current

    return prev, banked


def _compute_available_budget(last: MyLineup, target_event: Event) -> float | None:
    if last.budget_cap is None or last.team_cost is None:
        return None
    bank = float(last.budget_cap) - float(last.team_cost)
    driver_ids = [last.driver_1_id, last.driver_2_id, last.driver_3_id,
                  last.driver_4_id, last.driver_5_id]
    team_ids = [last.constructor_1_id, last.constructor_2_id]
    driver_prices = dict(
        FantasyDriverPrice.objects.filter(event=target_event, driver_id__in=driver_ids)
        .values_list("driver_id", "price")
    )
    constructor_prices = dict(
        FantasyConstructorPrice.objects.filter(event=target_event, team_id__in=team_ids)
        .values_list("team_id", "price")
    )
    if len(driver_prices) != 5 or len(constructor_prices) != 2:
        return None
    current_team_value = sum(float(p) for p in driver_prices.values()) + \
                         sum(float(p) for p in constructor_prices.values())
    return bank + current_team_value


def _count_transfers(old: Lineup | None, new: Lineup | None) -> int:
    if old is None or new is None:
        return 0
    return (
        len(set(new.driver_ids) - set(old.driver_ids))
        + len(set(new.constructor_ids) - set(old.constructor_ids))
    )


# ---------------------------------------------------------------------------
# DataFrame builders
# ---------------------------------------------------------------------------


def _build_driver_preds_df(predictions: pd.DataFrame, driver_prices: dict) -> pd.DataFrame:
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


def _build_constructor_preds_df(event: Event, predictions: pd.DataFrame, constructor_prices: dict) -> pd.DataFrame:
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


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _print_predictions(stdout, predictions: pd.DataFrame, drivers_by_id: dict) -> None:
    stdout.write("--- PREDICTIONS ---")
    stdout.write(f"{'Driver':<8}  {'Pts':>6}  {'Range':>18}  {'Conf':>6}")
    stdout.write("-" * 46)
    for _, row in predictions.sort_values("predicted_fantasy_points", ascending=False).iterrows():
        driver = drivers_by_id.get(int(row["driver_id"]))
        code = driver.code if driver else "???"
        lo, hi = row["confidence_lower"], row["confidence_upper"]
        width = hi - lo
        stdout.write(
            f"{code:<8}  {row['predicted_fantasy_points']:>6.1f}"
            f"  [{lo:>5.1f} – {hi:>5.1f}]"
            f"  ±{width / 2:>4.1f}"
        )


def _print_transfers(stdout, old: Lineup, new: Lineup, drivers_by_id: dict, teams_by_id: dict, banked: int) -> None:
    dropped_drivers = set(old.driver_ids) - set(new.driver_ids)
    added_drivers = set(new.driver_ids) - set(old.driver_ids)
    dropped_constructors = set(old.constructor_ids) - set(new.constructor_ids)
    added_constructors = set(new.constructor_ids) - set(old.constructor_ids)
    n_transfers = len(dropped_drivers) + len(dropped_constructors)

    stdout.write("\n--- RECOMMENDED CHANGES ---")
    if n_transfers == 0:
        stdout.write("  No changes — keep your current lineup.")
        return

    for did in dropped_drivers:
        d = drivers_by_id.get(did)
        stdout.write(f"  Drop: {d.code if d else did}")
    for did in added_drivers:
        d = drivers_by_id.get(did)
        stdout.write(f"  Add:  {d.code if d else did}")
    for cid in dropped_constructors:
        t = teams_by_id.get(cid)
        stdout.write(f"  Drop: {t.name if t else cid}")
    for cid in added_constructors:
        t = teams_by_id.get(cid)
        stdout.write(f"  Add:  {t.name if t else cid}")

    free = min(n_transfers, banked)
    paid = max(0, n_transfers - banked)
    penalty = paid * 10
    summary = f"  {n_transfers} transfer(s): {free} free"
    if paid:
        summary += f", {paid} paid (−{penalty} pts penalty)"
    stdout.write(summary)


def _print_lineup(stdout, lineup: Lineup, drivers_by_id: dict, teams_by_id: dict,
                  driver_prices: dict, constructor_prices: dict, predictions: pd.DataFrame) -> None:
    pred_lookup = dict(
        zip(predictions["driver_id"].astype(int), predictions["predicted_fantasy_points"].astype(float))
    )
    stdout.write("\n--- RECOMMENDED LINEUP ---")
    for did in lineup.driver_ids:
        driver = drivers_by_id.get(did)
        price = float(driver_prices.get(did, 0))
        pts = pred_lookup.get(did, 0.0)
        drs = "  ← DRS Boost" if did == lineup.drs_boost_driver_id else ""
        stdout.write(f"  {driver.code if driver else did:<8}  ${price:.1f}M  {pts:.1f} pts{drs}")
    for cid in lineup.constructor_ids:
        team = teams_by_id.get(cid)
        price = float(constructor_prices.get(cid, 0))
        stdout.write(f"  {team.name if team else cid:<20}  ${price:.1f}M")
    stdout.write(f"\n  Total cost:       ${lineup.total_cost:.1f}M")
    stdout.write(f"  Predicted points:  {lineup.predicted_points:.1f}")
