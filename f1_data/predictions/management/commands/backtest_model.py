"""
backtest_model — pure model-quality evaluation over historical seasons.

Unlike the `backtest` command, this command evaluates *only* the predictor:
no optimizer, no lineup selection, no budget constraint, no oracle, no transfer
penalty. The output is a table of ranking and error metrics per race, which
tells us "how well does the model rank drivers?" independent of how well the
optimizer converts those rankings into a good fantasy team.

This distinction matters because a model can rank drivers accurately but still
produce a bad lineup if the optimizer is poorly calibrated — and vice versa.
Separating the two makes it easier to diagnose which component to improve.

The positional <family> argument selects which predictor family to evaluate.
It matches the directory names under predictions/predictors/:
  xgboost          → race predictors v1–v4, evaluates against R session results
  race_ranker      → placeholder, raises CommandError until versions are added
  qualifying_ranker → placeholder, evaluates against Q session results
  sprint_ranker    → placeholder, evaluates against S session results

Usage:
  python manage.py backtest_model xgboost --seasons 2024
  python manage.py backtest_model xgboost --seasons 2023 2024 --predictor v2 v3
"""
from __future__ import annotations

import itertools

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from core.models import Event, SessionResult
from predictions.evaluation.backtester import _compute_mae
from predictions.evaluation.metrics import compute_rank_metrics
from predictions.features.v1_pandas import V1FeatureStore
from predictions.features.v2_pandas import V2FeatureStore
from predictions.features.v3_pandas import V3FeatureStore
from predictions.features.v4 import V4FeatureStore
from predictions.models import FantasyDriverScore
from predictions.predictors.xgboost.v1 import XGBoostPredictor
from predictions.predictors.xgboost.v2 import XGBoostPredictorV2
from predictions.predictors.xgboost.v3 import XGBoostPredictorV3
from predictions.predictors.xgboost.v4 import XGBoostPredictorV4
from predictions.features.qualifying.v1_qualify import build_qualifying_training_dataset
from predictions.features.race.v1_race import RaceV1FeatureStore
from predictions.predictors.qualifying_ranker.v1_qualify import QualifyingRankerV1
from predictions.predictors.race_ranker.v1_race import RaceRankerV1
from predictions.predictors.xgboost.shared import build_training_dataset, walk_forward_splits

# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

_FEATURE_STORE_REGISTRY = {
    "v1": V1FeatureStore,
    "v2": V2FeatureStore,
    "v3": V3FeatureStore,
    "v4": V4FeatureStore,
}

# Each family has its own predictor registry. Placeholder families have empty
# dicts — the command raises CommandError before attempting to instantiate.
_FAMILY_PREDICTOR_REGISTRY: dict[str, dict[str, type]] = {
    "xgboost": {
        "v1": XGBoostPredictor,
        "v2": XGBoostPredictorV2,
        "v3": XGBoostPredictorV3,
        "v4": XGBoostPredictorV4,
    },
    "race_ranker": {"v1": RaceRankerV1},
    "qualifying_ranker": {"v1": QualifyingRankerV1},
    "sprint_ranker": {},
    "price_heuristic": {},
}

# Maps family → DB session_type used when fetching actual results.
# None means the family is not a driver-ranking predictor (e.g. price_heuristic).
_FAMILY_SESSION_TYPE: dict[str, str | None] = {
    "xgboost": "R",
    "race_ranker": "R",
    "qualifying_ranker": "Q",
    "sprint_ranker": "S",
    "price_heuristic": None,
}

# FantasyDriverScore.event_type uses English strings, not the session type codes.
_SESSION_TYPE_TO_EVENT_TYPE = {"R": "race", "Q": "qualifying", "S": "sprint"}

# Maps family → the function used to build (X, y) training DataFrames.
# Each family targets a different session type, so each needs its own builder.
# sprint_ranker falls back to the race builder as a safe default — it raises
# CommandError before _run_combo is called anyway (empty predictor registry).
_FAMILY_BUILD_DATASET = {
    "xgboost": build_training_dataset,
    "race_ranker": build_training_dataset,
    "qualifying_ranker": build_qualifying_training_dataset,
    "sprint_ranker": build_training_dataset,        # placeholder, sprint builder TBD
    "price_heuristic": build_training_dataset,      # unreachable (raises before _run_combo)
}

# Some predictor families require a family-specific feature store that is NOT
# interchangeable with the generic v1/v2/v3/v4 stores. For these families the
# --feature-store argument is ignored and the override class is always used.
#
# race_ranker must always use RaceV1FeatureStore because it adds the
# `predicted_quali_position` column that RaceRankerV1 expects in its feature
# matrix. Using a generic store would cause a KeyError at fit/predict time.
_FAMILY_FEATURE_STORE_OVERRIDE: dict[str, type] = {
    "race_ranker": RaceV1FeatureStore,
}

_ALL_FAMILIES = sorted(_FAMILY_PREDICTOR_REGISTRY)


class Command(BaseCommand):
    help = "Evaluate predictor quality (MAE + ranking metrics) without running the optimizer"

    def add_arguments(self, parser) -> None:
        # Positional: names the predictor family to evaluate.
        # `choices` makes Django print valid options in --help and reject unknown values.
        parser.add_argument(
            "family",
            choices=_ALL_FAMILIES,
            help=(
                "Predictor family to evaluate. Must match a directory name under "
                "predictions/predictors/. Options: " + ", ".join(_ALL_FAMILIES)
            ),
        )
        parser.add_argument(
            "--seasons",
            type=int,
            nargs="+",
            required=True,
            help="Season year(s) to backtest, e.g. --seasons 2023 2024",
        )
        parser.add_argument(
            "--min-train",
            type=int,
            default=5,
            help="Minimum events to train on before first prediction (default: 5)",
        )
        parser.add_argument(
            "--feature-store",
            choices=settings.ML_FEATURE_STORE_VERSIONS,
            default=["v2"],
            nargs="+",
            help="Feature store version(s) (default: v2). Pass multiple to sweep.",
        )
        parser.add_argument(
            "--predictor",
            choices=settings.ML_PREDICTOR_VERSIONS,
            default=["v2"],
            nargs="+",
            help="Predictor version(s) within the family (default: v2). Pass multiple to sweep.",
        )

    def handle(self, *args, **options) -> None:
        family = options["family"]
        seasons = options["seasons"]
        min_train = options["min_train"]
        fs_versions = options["feature_store"]
        pred_versions = options["predictor"]

        # Validate: family must have at least one registered predictor.
        predictor_registry = _FAMILY_PREDICTOR_REGISTRY[family]
        if not predictor_registry:
            raise CommandError(
                f"No predictors registered for family '{family}'. "
                f"Add version classes to _FAMILY_PREDICTOR_REGISTRY['{family}'] "
                f"when the first {family} predictor is implemented."
            )

        # Validate: family must map to a rankable session type (price_heuristic doesn't).
        session_type = _FAMILY_SESSION_TYPE[family]
        if session_type is None:
            raise CommandError(
                f"Family '{family}' is not a driver-ranking predictor and cannot be "
                f"evaluated with backtest_model. Use a dedicated evaluation command instead."
            )

        # Validate: requested predictor versions must exist in this family's registry.
        unknown = [v for v in pred_versions if v not in predictor_registry]
        if unknown:
            available = sorted(predictor_registry)
            raise CommandError(
                f"Unknown predictor version(s) for '{family}': {unknown}. "
                f"Available: {available}"
            )

        # Warn if --feature-store was passed for a family that mandates its own store.
        # The default value is ["v2"], so any other value means the user explicitly
        # passed something that will be silently ignored — better to tell them.
        if family in _FAMILY_FEATURE_STORE_OVERRIDE and fs_versions != ["v2"]:
            override_cls = _FAMILY_FEATURE_STORE_OVERRIDE[family]
            self.stdout.write(
                f"Note: --feature-store {fs_versions} ignored for '{family}'. "
                f"This family always uses {override_cls.__name__}."
            )

        events = list(
            Event.objects.filter(season__year__in=seasons)
            .select_related("season", "circuit")
            .order_by("event_date")
        )
        if len(events) < min_train + 1:
            raise CommandError(
                f"Only {len(events)} events for seasons {seasons} — "
                f"need at least {min_train + 1}."
            )

        combos = list(itertools.product(fs_versions, pred_versions))
        combo_results: list[tuple[str, str, _ComboSummary]] = []

        for fs_version, pred_version in combos:
            self.stdout.write(
                f"\n── {family}  fs={fs_version}  pred={pred_version} ──"
            )
            summary = _run_combo(
                family=family,
                fs_version=fs_version,
                pred_version=pred_version,
                predictor_registry=predictor_registry,
                events=events,
                min_train=min_train,
                session_type=session_type,
                stdout=self.stdout,
            )
            combo_results.append((fs_version, pred_version, summary))

        if len(combos) > 1:
            _print_comparison_table(self.stdout, combo_results)


# ---------------------------------------------------------------------------
# Per-combo evaluation
# ---------------------------------------------------------------------------


class _ComboSummary:
    """Aggregated metrics for one (feature_store, predictor) combination."""

    def __init__(self) -> None:
        self.n_races = 0
        self.mae_pos_total = 0.0
        self.mae_pts_total = 0.0
        self.spearman_total = 0.0
        self.top10_prec_total = 0.0
        self.top10_rec_total = 0.0
        self.ndcg_total = 0.0

    def add(
        self,
        mae_pos: float,
        mae_pts: float,
        spearman: float,
        top10_prec: float,
        top10_rec: float,
        ndcg: float,
    ) -> None:
        self.n_races += 1
        self.mae_pos_total += mae_pos
        self.mae_pts_total += mae_pts
        self.spearman_total += spearman
        self.top10_prec_total += top10_prec
        self.top10_rec_total += top10_rec
        self.ndcg_total += ndcg

    def _mean(self, total: float) -> float:
        return total / self.n_races if self.n_races else 0.0

    @property
    def mean_mae_pos(self) -> float:
        return self._mean(self.mae_pos_total)

    @property
    def mean_mae_pts(self) -> float:
        return self._mean(self.mae_pts_total)

    @property
    def mean_spearman(self) -> float:
        return self._mean(self.spearman_total)

    @property
    def mean_top10_prec(self) -> float:
        return self._mean(self.top10_prec_total)

    @property
    def mean_top10_rec(self) -> float:
        return self._mean(self.top10_rec_total)

    @property
    def mean_ndcg(self) -> float:
        return self._mean(self.ndcg_total)


def _run_combo(
    family: str,
    fs_version: str,
    pred_version: str,
    predictor_registry: dict[str, type],
    events: list[Event],
    min_train: int,
    session_type: str,
    stdout,
) -> _ComboSummary:
    # Some families mandate their own feature store (e.g. race_ranker requires
    # RaceV1FeatureStore for the predicted_quali_position column). Use the override
    # when present; otherwise use the generic store selected by --feature-store.
    if family in _FAMILY_FEATURE_STORE_OVERRIDE:
        feature_store = _FAMILY_FEATURE_STORE_OVERRIDE[family]()
    else:
        feature_store = _FEATURE_STORE_REGISTRY[fs_version]()
    predictor = predictor_registry[pred_version]()

    header = (
        f"{'':>10}  {'Event':<38}  {'Train':>5}  {'MAE Pos':>7}  {'MAE Pts':>7}"
        f"  {'Sρ':>5}  {'P@10':>5}  {'R@10':>5}  {'NDCG@10':>7}"
    )
    stdout.write(header)
    stdout.write("─" * len(header))

    splits = list(walk_forward_splits(events, min_train))
    total = len(splits)
    summary = _ComboSummary()

    for n, (train_events, test_event) in enumerate(splits, start=1):
        # 1. Build training data and fit the predictor on all past events.
        # Each predictor family targets a different session type, so we dispatch
        # to the appropriate dataset builder (e.g. qualifying builder for qualifying_ranker).
        build_fn = _FAMILY_BUILD_DATASET[family]
        X, y = build_fn(train_events, feature_store)
        if X.empty:
            continue

        predictor.fit(X, y)

        # 2. Generate features for the test event and predict driver performance.
        features = feature_store.get_all_driver_features(test_event.id)
        if features.empty:
            continue

        predictions = predictor.predict(features)

        # 3. Load actual results for the session type we're evaluating against.
        actuals = _actuals_for_session(test_event, session_type)
        if not actuals:
            # No actual results available for this event/session — skip silently.
            # This is expected for qualifying/sprint when data hasn't been collected.
            continue

        # 4. Compute error metrics (MAE) and ranking metrics (Spearman ρ, NDCG@10…).
        # _compute_mae: for each driver present in both predictions and actuals,
        #   computes |predicted - actual| for position and fantasy points, then averages.
        # compute_rank_metrics: builds two ranked lists (predicted vs actual) and
        #   computes correlation and precision/recall at the top 10.
        mae_pos, mae_pts = _compute_mae(predictions, actuals)
        rank_metrics = compute_rank_metrics(predictions, actuals)

        summary.add(
            mae_pos=mae_pos,
            mae_pts=mae_pts,
            spearman=rank_metrics.spearman_rho,
            top10_prec=rank_metrics.top10_precision,
            top10_rec=rank_metrics.top10_recall,
            ndcg=rank_metrics.ndcg_at_10,
        )

        stdout.write(
            f"[{n:>3}/{total}]  {test_event.event_name:<38}  {len(train_events):>5}"
            f"  {mae_pos:>7.2f}  {mae_pts:>7.2f}"
            f"  {rank_metrics.spearman_rho:>5.2f}"
            f"  {rank_metrics.top10_precision:>5.2f}"
            f"  {rank_metrics.top10_recall:>5.2f}"
            f"  {rank_metrics.ndcg_at_10:>7.2f}"
        )

    stdout.write("")
    if summary.n_races == 0:
        stdout.write("No events evaluated — check that session data exists for these seasons.")
        return summary

    stdout.write(f"Races evaluated:        {summary.n_races}")
    stdout.write(f"Mean MAE (position):    {summary.mean_mae_pos:.2f}")
    stdout.write(f"Mean MAE (fantasy pts): {summary.mean_mae_pts:.2f}")
    stdout.write("")
    stdout.write(f"Rank metrics (mean across {summary.n_races} races):")
    stdout.write(f"  Spearman ρ:       {summary.mean_spearman:.2f}")
    stdout.write(
        f"  Top-10 precision: {summary.mean_top10_prec:.2f}"
        f"  ({summary.mean_top10_prec * 10:.1f} of 10 correct on average)"
    )
    stdout.write(f"  Top-10 recall:    {summary.mean_top10_rec:.2f}")
    stdout.write(f"  NDCG@10:          {summary.mean_ndcg:.2f}")

    # Feature importances tell us which input signals the model relied on most.
    # We read them from the final trained model (most training data = most reliable).
    if hasattr(predictor, "get_feature_importances"):
        importances = predictor.get_feature_importances() or {}
        if importances:
            stdout.write("")
            stdout.write("Feature importances (fantasy pts model, final trained model):")
            for feat, imp in importances.items():
                stdout.write(f"  {feat:<45} {imp:.4f}")

    return summary


# ---------------------------------------------------------------------------
# Multi-combo comparison table
# ---------------------------------------------------------------------------


def _print_comparison_table(
    stdout,
    combo_results: list[tuple[str, str, _ComboSummary]],
) -> None:
    stdout.write("")
    stdout.write("Comparison")
    stdout.write("─" * 62)
    stdout.write(
        f"  {'fs':<4}  {'pred':<4}  {'MAE Pos':>7}  {'MAE Pts':>7}  {'Sρ':>5}  {'P@10':>5}  {'NDCG@10':>7}"
    )
    stdout.write("  " + "─" * 58)
    for fs_v, pred_v, s in combo_results:
        if s.n_races == 0:
            stdout.write(f"  {fs_v:<4}  {pred_v:<4}  {'—':>7}  {'—':>7}  {'—':>5}  {'—':>5}  {'—':>7}")
        else:
            stdout.write(
                f"  {fs_v:<4}  {pred_v:<4}"
                f"  {s.mean_mae_pos:>7.2f}  {s.mean_mae_pts:>7.2f}"
                f"  {s.mean_spearman:>5.2f}  {s.mean_top10_prec:>5.2f}  {s.mean_ndcg:>7.2f}"
            )
    stdout.write("")


# ---------------------------------------------------------------------------
# Actuals helper
# ---------------------------------------------------------------------------


def _actuals_for_session(
    event: Event,
    session_type: str,
) -> dict[int, tuple[float, float]]:
    """
    Return {driver_id: (position, fantasy_pts)} for a given session type.

    `session_type` is the DB value: "R" for race, "Q" for qualifying, "S" for sprint.

    Fantasy points are summed from FantasyDriverScore line items filtered by
    event_type (the English string: "race", "qualifying", "sprint"). We sum the
    individual `points` rows rather than using `race_total` because `race_total`
    is the full-weekend aggregate — qualifying + sprint + race combined. For
    session-specific evaluation we only want the points earned in that session.

    Drivers without a recorded position are excluded (DNF/DNS/DSQ etc.).
    Drivers without fantasy point data default to 0.0 (common for qualifying
    sessions where the CSV import hasn't been run yet).
    """
    positions = dict(
        SessionResult.objects.filter(
            session__event=event,
            session__session_type=session_type,
        ).values_list("driver_id", "position")
    )
    if not positions:
        return {}

    event_type = _SESSION_TYPE_TO_EVENT_TYPE[session_type]
    fantasy_pts_lookup = dict(
        FantasyDriverScore.objects.filter(event=event, event_type=event_type)
        .values("driver_id")
        .annotate(total=Sum("points"))
        .values_list("driver_id", "total")
    )

    return {
        did: (float(positions[did]), float(fantasy_pts_lookup.get(did, 0.0)))
        for did in positions
        if positions[did] is not None
    }
