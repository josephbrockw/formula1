from __future__ import annotations

import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from core.models import Driver, Event, Team
from predictions.models import (
    FantasyConstructorPrice,
    FantasyDriverPrice,
    LineupRecommendation,
    RacePrediction,
)
from predictions.optimizers.greedy_v1 import GreedyOptimizer

_DEFAULT_MODEL_VERSION = "xgboost_v1"
_STRATEGY_TYPE = "single_race"


class Command(BaseCommand):
    help = "Optimize a fantasy lineup for a race weekend using stored predictions and prices"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--year", type=int, required=True, help="Season year, e.g. 2024")
        parser.add_argument("--round", type=int, required=True, help="Round number within the season")
        parser.add_argument("--budget", type=float, default=100.0, help="Budget cap in $M (default: 100)")
        parser.add_argument(
            "--model-version",
            default=_DEFAULT_MODEL_VERSION,
            help=f"Model version tag to use for predictions (default: {_DEFAULT_MODEL_VERSION})",
        )

    def handle(self, *args, **options) -> None:
        year = options["year"]
        round_number = options["round"]
        budget = options["budget"]
        model_version = options["model_version"]

        try:
            event = Event.objects.select_related("season").get(
                season__year=year, round_number=round_number
            )
        except Event.DoesNotExist:
            raise CommandError(f"No event found for year={year}, round={round_number}")

        preds = list(
            RacePrediction.objects.filter(event=event, model_version=model_version).select_related("driver")
        )
        if not preds:
            raise CommandError(
                f"No predictions for {event} (model_version={model_version}). Run predict_race first."
            )

        driver_prices = dict(
            FantasyDriverPrice.objects.filter(event=event).values_list("driver_id", "price")
        )
        constructor_prices = dict(
            FantasyConstructorPrice.objects.filter(event=event).values_list("team_id", "price")
        )
        if not driver_prices:
            raise CommandError(f"No driver prices for {event}. Run import_fantasy_csv first.")
        if not constructor_prices:
            raise CommandError(f"No constructor prices for {event}. Run import_fantasy_csv first.")

        pred_lookup = {p.driver_id: p.predicted_fantasy_points for p in preds}

        driver_rows = [
            {
                "driver_id": p.driver_id,
                "predicted_fantasy_points": p.predicted_fantasy_points,
                "price": float(driver_prices[p.driver_id]),
            }
            for p in preds
            if p.driver_id in driver_prices
        ]
        driver_preds_df = pd.DataFrame(driver_rows)
        if driver_preds_df.empty:
            raise CommandError("No drivers have both predictions and prices.")

        driver_team_map = dict(Driver.objects.filter(season=event.season).values_list("id", "team_id"))
        team_drivers: dict[int, list[int]] = {}
        for driver_id, team_id in driver_team_map.items():
            team_drivers.setdefault(team_id, []).append(driver_id)

        constructor_rows = [
            {
                "team_id": team_id,
                "predicted_fantasy_points": sum(pred_lookup.get(did, 0.0) for did in team_drivers.get(team_id, [])),
                "price": float(price),
            }
            for team_id, price in constructor_prices.items()
        ]
        constructor_preds_df = pd.DataFrame(constructor_rows)

        lineup = GreedyOptimizer().optimize_single_race(driver_preds_df, constructor_preds_df, budget)

        drivers_by_id = {d.id: d for d in Driver.objects.filter(season=event.season)}
        teams_by_id = {t.id: t for t in Team.objects.filter(season=event.season)}

        driver_objs = [drivers_by_id[did] for did in lineup.driver_ids if did in drivers_by_id]
        constructor_objs = [teams_by_id[cid] for cid in lineup.constructor_ids if cid in teams_by_id]
        drs_driver = drivers_by_id.get(lineup.drs_boost_driver_id)

        if len(driver_objs) == 5 and len(constructor_objs) == 2 and drs_driver is not None:
            LineupRecommendation.objects.update_or_create(
                event=event,
                strategy_type=_STRATEGY_TYPE,
                model_version=model_version,
                defaults={
                    "driver_1": driver_objs[0],
                    "driver_2": driver_objs[1],
                    "driver_3": driver_objs[2],
                    "driver_4": driver_objs[3],
                    "driver_5": driver_objs[4],
                    "drs_boost_driver": drs_driver,
                    "constructor_1": constructor_objs[0],
                    "constructor_2": constructor_objs[1],
                    "total_cost": lineup.total_cost,
                    "predicted_points": lineup.predicted_points,
                },
            )

        self.stdout.write(f"\nOptimized lineup for {event}  (budget: ${budget:.1f}M)\n")
        self.stdout.write("Drivers:")
        for did in lineup.driver_ids:
            driver = drivers_by_id.get(did)
            pts = pred_lookup.get(did, 0.0)
            price = float(driver_prices.get(did, 0))
            drs_marker = "  ← DRS Boost" if did == lineup.drs_boost_driver_id else ""
            self.stdout.write(f"  {driver.code if driver else did:<8}  ${price:.1f}M  {pts:.1f} pts{drs_marker}")

        self.stdout.write("Constructors:")
        for cid in lineup.constructor_ids:
            team = teams_by_id.get(cid)
            price = float(constructor_prices.get(cid, 0))
            pts = sum(pred_lookup.get(did, 0.0) for did in team_drivers.get(cid, []))
            self.stdout.write(f"  {team.name if team else cid:<20}  ${price:.1f}M  {pts:.1f} pts")

        self.stdout.write(f"\nTotal cost:       ${lineup.total_cost:.1f}M")
        self.stdout.write(f"Predicted points:  {lineup.predicted_points:.1f}")
