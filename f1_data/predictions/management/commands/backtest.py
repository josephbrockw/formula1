from __future__ import annotations

import itertools

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import Driver, Event, Team
from core.tasks.notifier import send_slack_blocks
from predictions.evaluation.backtester import BacktestResult, Backtester, RaceBacktestResult
from predictions.features.v1_pandas import V1FeatureStore
from predictions.features.v2_pandas import V2FeatureStore
from predictions.features.v3_pandas import V3FeatureStore
from predictions.models import BacktestRaceResult, BacktestRun
from predictions.optimizers.greedy_v1 import GreedyOptimizer as GreedyOptimizerV1
from predictions.optimizers.greedy_v2 import GreedyOptimizerV2
from predictions.optimizers.ilp_v3 import ILPOptimizer
from predictions.predictors.xgboost_v1 import XGBoostPredictor
from predictions.predictors.xgboost_v2 import XGBoostPredictorV2
from predictions.predictors.xgboost_v3 import XGBoostPredictorV3

_VERSIONS = settings.ML_FEATURE_STORE_VERSIONS
_OPT_VERSIONS = settings.ML_OPTIMIZER_VERSIONS


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
            default=["v2"],
            nargs="+",
            help="Feature store version(s) to run (default: v2). Pass multiple to sweep, e.g. --feature-store v2 v3.",
        )
        parser.add_argument(
            "--predictor",
            choices=settings.ML_PREDICTOR_VERSIONS,
            default=["v2"],
            nargs="+",
            help="Predictor version(s) to run (default: v2). Pass multiple to sweep.",
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
        parser.add_argument(
            "--verbose",
            action="store_true",
            default=False,
            help="Print each race's selected lineup and total cost (useful for diagnosing budget bugs).",
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

        fs_versions = options["feature_store"]
        pred_versions = options["predictor"]
        opt_version = options["optimizer"]
        verbose = options["verbose"]

        if options["all"]:
            combos = list(itertools.product(_VERSIONS, settings.ML_PREDICTOR_VERSIONS, _OPT_VERSIONS))
            self.stdout.write(f"Running all {len(combos)} combinations — seasons {seasons}")
            run_pairs: list[tuple[BacktestRun, BacktestResult]] = []
            for fs, pred, opt in combos:
                self.stdout.write(f"\n── fs={fs} pred={pred} opt={opt} ──")
                run, result = self._run_single(fs, pred, opt, events, seasons, min_train, budget, verbose)
                if run and result:
                    run_pairs.append((run, result))
            _send_all_done_notification(run_pairs, seasons)
        elif options["all_optimizers"]:
            self.stdout.write(f"Running optimizer comparison (fs=v2, pred=v2, opt=v1/v2/v3) — seasons {seasons}")
            run_pairs = []
            for opt in _OPT_VERSIONS:
                self.stdout.write(f"\n── fs=v2 pred=v2 opt={opt} ──")
                run, result = self._run_single("v2", "v2", opt, events, seasons, min_train, budget, verbose)
                if run and result:
                    run_pairs.append((run, result))
            _send_all_done_notification(run_pairs, seasons)
        elif len(fs_versions) > 1 or len(pred_versions) > 1:
            combos = list(itertools.product(fs_versions, pred_versions, [opt_version]))
            self.stdout.write(f"Running {len(combos)} combination(s) — seasons {seasons}")
            run_pairs = []
            for fs, pred, opt in combos:
                self.stdout.write(f"\n── fs={fs} pred={pred} opt={opt} ──")
                run, result = self._run_single(fs, pred, opt, events, seasons, min_train, budget, verbose)
                if run and result:
                    run_pairs.append((run, result))
            _send_all_done_notification(run_pairs, seasons)
        else:
            self._run_single(
                fs_versions[0], pred_versions[0], opt_version,
                events, seasons, min_train, budget, verbose,
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
        verbose: bool = False,
    ) -> tuple[BacktestRun | None, BacktestResult | None]:
        if fs_version == "v3":
            feature_store = V3FeatureStore()
        elif fs_version == "v2":
            feature_store = V2FeatureStore()
        else:
            feature_store = V1FeatureStore()
        if pred_version == "v3":
            predictor = XGBoostPredictorV3()
        elif pred_version == "v2":
            predictor = XGBoostPredictorV2()
        else:
            predictor = XGBoostPredictor()
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

        # Build id→code/name lookups used by verbose lineup printing
        driver_code_map: dict[int, str] = {}
        team_name_map: dict[int, str] = {}
        if verbose:
            driver_code_map = dict(Driver.objects.filter(season__year__in=seasons).values_list("id", "code"))
            team_name_map = dict(Team.objects.filter(season__year__in=seasons).values_list("id", "name"))

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
            if verbose and r.lineup is not None:
                drivers_str = " ".join(driver_code_map.get(d, f"#{d}") for d in r.lineup.driver_ids)
                teams_str = " ".join(team_name_map.get(c, f"#{c}") for c in r.lineup.constructor_ids)
                self.stdout.write(
                    f"           Lineup: {drivers_str} | {teams_str}  (${r.lineup.total_cost:.1f}M / ${budget:.1f}M)"
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
            return None, None

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

        # Per-season breakdown
        season_summaries = result.by_season
        if len(season_summaries) > 1:
            self.stdout.write("")
            self.stdout.write(
                f"{'Season':>6}  {'Races':>5}  {'MAE Pos':>7}  {'MAE Pts':>7}  {'Lineup':>8}  {'Oracle':>8}  {'Left':>7}"
            )
            self.stdout.write("-" * 62)
            for s in season_summaries:
                lineup_str = f"{s.lineup_points:.0f}" if s.lineup_points is not None else "—"
                oracle_str = f"{s.optimal_points:.0f}" if s.optimal_points is not None else "—"
                left_str = f"{s.left_on_table:.0f}" if s.left_on_table is not None else "—"
                self.stdout.write(
                    f"{s.year:>6}  {s.n_races:>5}  {s.mae_position:>7.2f}  {s.mae_fantasy_points:>7.2f}"
                    f"  {lineup_str:>8}  {oracle_str:>8}  {left_str:>7}"
                )

        # Feature importances (top 10 from the fantasy-points model)
        if result.feature_importances:
            self.stdout.write("")
            self.stdout.write("Feature importances (fantasy pts model, final trained model):")
            for feat, imp in result.feature_importances.items():
                self.stdout.write(f"  {feat:<45} {imp:.4f}")

        return run, result


def _send_all_done_notification(
    run_pairs: list[tuple[BacktestRun, BacktestResult]],
    seasons: list[int],
) -> None:
    if not run_pairs:
        return

    seasons_str = "–".join(str(s) for s in [min(seasons), max(seasons)]) if len(seasons) > 1 else str(seasons[0])
    blocks: list[dict] = []

    # Header
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f":checkered_flag: *Backtest complete* — {seasons_str} — {len(run_pairs)} run(s)"},
    })
    blocks.append({"type": "divider"})

    # One card per run
    for run, result in run_pairs:
        config = f"fs={run.feature_store_version} p={run.predictor_version} o={run.optimizer_version}"
        mae_pos = f"{run.mean_mae_position:.2f}" if run.mean_mae_position is not None else "—"
        mae_pts = f"{run.mean_mae_fantasy_points:.2f}" if run.mean_mae_fantasy_points is not None else "—"
        lineup = f"{run.total_lineup_points:,.0f}" if run.total_lineup_points is not None else "—"
        oracle = f"{run.total_optimal_points:,.0f}" if run.total_optimal_points is not None else "—"
        left = (
            f"{run.total_optimal_points - run.total_lineup_points:,.0f}"
            if run.total_lineup_points is not None and run.total_optimal_points is not None
            else "—"
        )

        summary_lines = [
            f"*`{config}`*",
            f"MAE: *{mae_pos}* pos  /  *{mae_pts}* pts",
            f"Lineup: *{lineup}*  ·  Oracle: {oracle}  ·  Left: {left}",
        ]

        # Per-season breakdown
        season_summaries = result.by_season
        if season_summaries:
            summary_lines.append("")
            for s in season_summaries:
                lu = f"{s.lineup_points:,.0f}" if s.lineup_points is not None else "—"
                left_s = f"{s.left_on_table:,.0f}" if s.left_on_table is not None else "—"
                summary_lines.append(
                    f"  *{s.year}*  {s.n_races} races  ·  MAE {s.mae_position:.2f}  ·  {lu} pts  ·  left {left_s}"
                )

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(summary_lines)},
        })

    blocks.append({"type": "divider"})

    # Winners
    runs_list = [r for r, _ in run_pairs]
    winner_lines = []
    runs_with_mae = [r for r in runs_list if r.mean_mae_position is not None]
    runs_with_pts = [r for r in runs_list if r.total_lineup_points is not None]
    if runs_with_mae:
        best = min(runs_with_mae, key=lambda r: r.mean_mae_position)
        winner_lines.append(
            f":trophy: *Best MAE:* `fs={best.feature_store_version} p={best.predictor_version} o={best.optimizer_version}` ({best.mean_mae_position:.2f})"
        )
    if runs_with_pts:
        best = max(runs_with_pts, key=lambda r: r.total_lineup_points)
        winner_lines.append(
            f":moneybag: *Most pts:* `fs={best.feature_store_version} p={best.predictor_version} o={best.optimizer_version}` ({best.total_lineup_points:,.0f})"
        )
    if winner_lines:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(winner_lines)},
        })

    # Feature importances — use the last run's model (most data = most representative)
    _, last_result = run_pairs[-1]
    if last_result.feature_importances:
        top = list(last_result.feature_importances.items())[:8]
        imp_lines = [":bar_chart: *Top features* (fantasy pts, final model)"]
        for i, (feat, imp) in enumerate(top, 1):
            imp_lines.append(f"  {i}. `{feat}` — {imp:.3f}")
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(imp_lines)},
        })

    send_slack_blocks(blocks)
