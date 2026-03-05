from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from core.models import Event
from predictions.evaluation.backtester import Backtester, RaceBacktestResult
from predictions.features.v1_pandas import V1FeatureStore
from predictions.optimizers.greedy_v1 import GreedyOptimizer
from predictions.predictors.xgboost_v1 import XGBoostPredictor


class Command(BaseCommand):
    help = "Run walk-forward backtesting over historical seasons and print a performance report"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--seasons",
            type=int,
            nargs="+",
            required=True,
            help="Season year(s) to include, e.g. --seasons 2023 2024",
        )
        parser.add_argument(
            "--min-train",
            type=int,
            default=5,
            help="Minimum events to train on before first prediction (default: 5)",
        )
        parser.add_argument(
            "--budget",
            type=float,
            default=100.0,
            help="Lineup budget cap in $M (default: 100)",
        )

    def handle(self, *args, **options) -> None:
        seasons = options["seasons"]
        min_train = options["min_train"]
        budget = options["budget"]

        events = list(
            Event.objects.filter(season__year__in=seasons)
            .select_related("season", "circuit")
            .order_by("event_date")
        )
        if len(events) < min_train + 1:
            raise CommandError(
                f"Only {len(events)} events for seasons {seasons} — need at least {min_train + 1}."
            )

        n_splits = len(events) - min_train
        self.stdout.write(
            f"Backtesting seasons {seasons} — {len(events)} events, "
            f"{n_splits} predictions, min_train={min_train}"
        )

        header = (
            f"{'':>10}  {'Event':<35}  {'Train':>5}  {'MAE Pos':>7}  {'MAE Pts':>7}"
            f"  {'Lineup':>7}  {'Optimal':>7}"
        )
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        def on_race_done(r: RaceBacktestResult, n: int, total: int) -> None:
            lineup_str = f"{r.lineup_actual_points:.1f}" if r.lineup_actual_points is not None else "—"
            optimal_str = f"{r.optimal_actual_points:.1f}" if r.optimal_actual_points is not None else "—"
            self.stdout.write(
                f"[{n:>3}/{total}]  {r.event_name:<35}  {r.n_train:>5}  {r.mae_position:>7.2f}"
                f"  {r.mae_fantasy_points:>7.2f}  {lineup_str:>7}  {optimal_str:>7}"
            )

        result = Backtester().run(
            events=events,
            feature_store=V1FeatureStore(),
            predictor=XGBoostPredictor(),
            optimizer=GreedyOptimizer(),
            min_train=min_train,
            budget=budget,
            on_race_done=on_race_done,
        )

        if not result.race_results:
            raise CommandError(
                "No backtest results produced. Check that events have results and fantasy score data."
            )

        self.stdout.write("")
        self.stdout.write(f"Races evaluated:        {len(result.race_results)}")
        self.stdout.write(f"Mean MAE (position):    {result.mean_mae_position:.2f}")
        self.stdout.write(f"Mean MAE (fantasy pts): {result.mean_mae_fantasy_points:.2f}")
        if result.total_lineup_points is not None:
            self.stdout.write(f"Total lineup points:    {result.total_lineup_points:.1f}")
        if result.total_optimal_points is not None:
            self.stdout.write(f"Total optimal points:   {result.total_optimal_points:.1f}")
            if result.total_lineup_points is not None:
                gap = result.total_optimal_points - result.total_lineup_points
                self.stdout.write(f"Points left on table:   {gap:.1f}")
