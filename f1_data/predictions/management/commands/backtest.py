from __future__ import annotations

import itertools

from django.core.management.base import BaseCommand, CommandError

from core.models import Event
from core.tasks.notifier import send_slack_notification
from predictions.evaluation.backtester import Backtester, RaceBacktestResult
from predictions.features.v1_pandas import V1FeatureStore
from predictions.features.v2_pandas import V2FeatureStore
from predictions.models import BacktestRaceResult, BacktestRun
from predictions.optimizers.greedy_v1 import GreedyOptimizer as GreedyOptimizerV1
from predictions.optimizers.greedy_v2 import GreedyOptimizerV2
from predictions.optimizers.ilp_v3 import ILPOptimizer
from predictions.predictors.xgboost_v1 import XGBoostPredictor
from predictions.predictors.xgboost_v2 import XGBoostPredictorV2

_VERSIONS = ["v1", "v2"]
_OPT_VERSIONS = ["v1", "v2", "v3"]


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
        parser.add_argument(
            "--feature-store",
            choices=_VERSIONS,
            default="v2",
            help="Feature store version (default: v2). Ignored when --all is set.",
        )
        parser.add_argument(
            "--predictor",
            choices=_VERSIONS,
            default="v2",
            help="Predictor version (default: v2). Ignored when --all is set.",
        )
        parser.add_argument(
            "--optimizer",
            choices=_OPT_VERSIONS,
            default="v2",
            help="Optimizer version (default: v2). Ignored when --all or --all-optimizers is set.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            default=False,
            help="Run all 8 combinations of feature-store/predictor/optimizer (v1/v2 only) and send a Slack summary.",
        )
        parser.add_argument(
            "--all-optimizers",
            action="store_true",
            default=False,
            help="Run v1/v2/v3 optimizers with fixed feature-store=v2, predictor=v2 and send a Slack summary.",
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

        if options["all"]:
            combos = list(itertools.product(_VERSIONS, _VERSIONS, _VERSIONS))
            self.stdout.write(f"Running all {len(combos)} combinations — seasons {seasons}")
            runs = []
            for fs, pred, opt in combos:
                self.stdout.write(f"\n── fs={fs} pred={pred} opt={opt} ──")
                run = self._run_single(fs, pred, opt, events, seasons, min_train, budget)
                if run:
                    runs.append(run)
            _send_all_done_notification(runs, seasons)
        elif options["all_optimizers"]:
            self.stdout.write(f"Running optimizer comparison (fs=v2, pred=v2, opt=v1/v2/v3) — seasons {seasons}")
            runs = []
            for opt in _OPT_VERSIONS:
                self.stdout.write(f"\n── fs=v2 pred=v2 opt={opt} ──")
                run = self._run_single("v2", "v2", opt, events, seasons, min_train, budget)
                if run:
                    runs.append(run)
            _send_all_done_notification(runs, seasons)
        else:
            self._run_single(
                options["feature_store"], options["predictor"], options["optimizer"],
                events, seasons, min_train, budget,
            )

    def _run_single(
        self,
        fs_version: str,
        pred_version: str,
        opt_version: str,
        events: list,
        seasons: list[int],
        min_train: int,
        budget: float,
    ) -> BacktestRun | None:
        feature_store = V2FeatureStore() if fs_version == "v2" else V1FeatureStore()
        predictor = XGBoostPredictorV2() if pred_version == "v2" else XGBoostPredictor()
        if opt_version == "v3":
            optimizer = ILPOptimizer()
        elif opt_version == "v2":
            optimizer = GreedyOptimizerV2()
        else:
            optimizer = GreedyOptimizerV1()

        run = BacktestRun.objects.create(
            feature_store_version=fs_version,
            predictor_version=pred_version,
            optimizer_version=opt_version,
            seasons=",".join(str(s) for s in sorted(seasons)),
            min_train=min_train,
            budget=budget,
        )
        n_splits = len(events) - min_train
        self.stdout.write(
            f"Backtest run #{run.pk} — {len(events)} events, {n_splits} predictions, min_train={min_train}"
        )

        header = (
            f"{'':>10}  {'Event':<35}  {'Train':>5}  {'MAE Pos':>7}  {'MAE Pts':>7}"
            f"  {'Trades':>6}  {'Lineup':>7}  {'Optimal':>7}"
        )
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        def on_race_done(r: RaceBacktestResult, n: int, total: int) -> None:
            BacktestRaceResult.objects.create(
                run=run,
                event_id=r.event_id,
                n_train=r.n_train,
                mae_position=r.mae_position,
                mae_fantasy_points=r.mae_fantasy_points,
                lineup_predicted_points=r.lineup_predicted_points,
                lineup_actual_points=r.lineup_actual_points,
                optimal_actual_points=r.optimal_actual_points,
                n_transfers=r.n_transfers,
            )
            lineup_str = f"{r.lineup_actual_points:.1f}" if r.lineup_actual_points is not None else "—"
            optimal_str = f"{r.optimal_actual_points:.1f}" if r.optimal_actual_points is not None else "—"
            self.stdout.write(
                f"[{n:>3}/{total}]  {r.event_name:<35}  {r.n_train:>5}  {r.mae_position:>7.2f}"
                f"  {r.mae_fantasy_points:>7.2f}  {r.n_transfers:>6}  {lineup_str:>7}  {optimal_str:>7}"
            )

        result = Backtester().run(
            events=events,
            feature_store=feature_store,
            predictor=predictor,
            optimizer=optimizer,
            min_train=min_train,
            budget=budget,
            on_race_done=on_race_done,
        )

        if not result.race_results:
            run.delete()
            self.stdout.write("No results produced — run deleted.")
            return None

        run.mean_mae_position = result.mean_mae_position
        run.mean_mae_fantasy_points = result.mean_mae_fantasy_points
        run.total_lineup_points = result.total_lineup_points
        run.total_optimal_points = result.total_optimal_points
        run.save()

        self.stdout.write("")
        self.stdout.write(f"Races evaluated:        {len(result.race_results)}")
        self.stdout.write(f"Mean MAE (position):    {result.mean_mae_position:.2f}")
        self.stdout.write(f"Mean MAE (fantasy pts): {result.mean_mae_fantasy_points:.2f}")
        if result.total_lineup_points is not None:
            self.stdout.write(f"Total lineup points:    {result.total_lineup_points:.1f}")
        if result.total_optimal_points is not None:
            self.stdout.write(f"Total optimal points:   {result.total_optimal_points:.1f}")
            if result.total_lineup_points is not None:
                self.stdout.write(f"Points left on table:   {result.total_optimal_points - result.total_lineup_points:.1f}")

        return run


def _send_all_done_notification(runs: list[BacktestRun], seasons: list[int]) -> None:
    if not runs:
        return

    seasons_str = ",".join(str(s) for s in sorted(seasons))
    lines = [f"*Backtest sweep complete* — seasons {seasons_str} — {len(runs)} runs\n"]
    lines.append(f"{'Config':<22}  {'MAE Pos':>7}  {'MAE Pts':>7}  {'Lineup':>7}  {'Oracle':>7}  {'Left':>7}")
    lines.append("─" * 65)

    for run in runs:
        config = f"fs={run.feature_store_version} p={run.predictor_version} o={run.optimizer_version}"
        mae_pos = f"{run.mean_mae_position:.2f}" if run.mean_mae_position is not None else "—"
        mae_pts = f"{run.mean_mae_fantasy_points:.2f}" if run.mean_mae_fantasy_points is not None else "—"
        lineup = f"{run.total_lineup_points:.0f}" if run.total_lineup_points is not None else "—"
        oracle = f"{run.total_optimal_points:.0f}" if run.total_optimal_points is not None else "—"
        left = (
            f"{run.total_optimal_points - run.total_lineup_points:.0f}"
            if run.total_lineup_points is not None and run.total_optimal_points is not None
            else "—"
        )
        lines.append(f"{config:<22}  {mae_pos:>7}  {mae_pts:>7}  {lineup:>7}  {oracle:>7}  {left:>7}")

    runs_with_mae = [r for r in runs if r.mean_mae_position is not None]
    runs_with_pts = [r for r in runs if r.total_lineup_points is not None]
    if runs_with_mae:
        best = min(runs_with_mae, key=lambda r: r.mean_mae_position)
        lines.append(f"\n:trophy: Best MAE pos: fs={best.feature_store_version} p={best.predictor_version} o={best.optimizer_version} ({best.mean_mae_position:.2f})")
    if runs_with_pts:
        best = max(runs_with_pts, key=lambda r: r.total_lineup_points)
        lines.append(f":moneybag: Most lineup pts: fs={best.feature_store_version} p={best.predictor_version} o={best.optimizer_version} ({best.total_lineup_points:.0f})")

    send_slack_notification("```\n" + "\n".join(lines) + "\n```")
