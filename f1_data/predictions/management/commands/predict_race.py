from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import Driver, Event
from predictions.features.v1_pandas import V1FeatureStore
from predictions.features.v2_pandas import V2FeatureStore
from predictions.features.v3_pandas import V3FeatureStore
from predictions.models import RacePrediction
from predictions.predictors.xgboost.shared import build_training_dataset
from predictions.predictors.xgboost.v1 import XGBoostPredictor

_DEFAULT_MODEL_VERSION = "xgboost_v1"


class Command(BaseCommand):
    help = "Generate ML predictions for a race weekend and store them in the DB"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--year", type=int, required=True, help="Season year, e.g. 2024")
        parser.add_argument("--round", type=int, required=True, help="Round number within the season")
        parser.add_argument(
            "--model-version",
            default=_DEFAULT_MODEL_VERSION,
            help=f"Model version tag (default: {_DEFAULT_MODEL_VERSION})",
        )
        parser.add_argument(
            "--feature-store",
            choices=settings.ML_FEATURE_STORE_VERSIONS,
            default=settings.ML_FEATURE_STORE,
            help=f"Feature store version (default: {settings.ML_FEATURE_STORE})",
        )

    def handle(self, *args, **options) -> None:
        year = options["year"]
        round_number = options["round"]
        model_version = options["model_version"]

        try:
            event = Event.objects.select_related("season").get(
                season__year=year, round_number=round_number
            )
        except Event.DoesNotExist:
            raise CommandError(f"No event found for year={year}, round={round_number}")

        train_events = list(
            Event.objects.filter(event_date__lt=event.event_date)
            .select_related("season", "circuit")
            .order_by("event_date")
        )
        self.stdout.write(f"Predicting: {event} — training on {len(train_events)} past events")

        fs_version = options["feature_store"]
        if fs_version == "v3":
            feature_store = V3FeatureStore()
        elif fs_version == "v2":
            feature_store = V2FeatureStore()
        else:
            feature_store = V1FeatureStore()
        predictor = XGBoostPredictor()

        X, y = build_training_dataset(train_events, feature_store)
        if X.empty:
            raise CommandError(
                "No training data available — need at least a few past events with results and fantasy scores."
            )

        predictor.fit(X, y)

        features = feature_store.get_all_driver_features(event.id)
        if features.empty:
            raise CommandError(
                f"No features for {event}. Make sure race data has been collected."
            )

        predictions = predictor.predict(features)
        drivers_by_id = {d.id: d for d in Driver.objects.filter(season=event.season)}

        saved = 0
        for _, row in predictions.iterrows():
            driver = drivers_by_id.get(int(row["driver_id"]))
            if driver is None:
                continue
            RacePrediction.objects.update_or_create(
                event=event,
                driver=driver,
                model_version=model_version,
                defaults={
                    "predicted_position": float(row["predicted_position"]),
                    "predicted_fantasy_points": float(row["predicted_fantasy_points"]),
                    "confidence_lower": float(row["confidence_lower"]),
                    "confidence_upper": float(row["confidence_upper"]),
                },
            )
            saved += 1

        self.stdout.write(f"Saved {saved} predictions\n")
        self.stdout.write(f"{'Driver':<8}  {'Pos':>5}  {'Points':>7}  {'Range':>16}")
        self.stdout.write("-" * 42)
        for _, row in predictions.sort_values("predicted_fantasy_points", ascending=False).iterrows():
            driver = drivers_by_id.get(int(row["driver_id"]))
            code = driver.code if driver else str(int(row["driver_id"]))
            lo = row["confidence_lower"]
            hi = row["confidence_upper"]
            self.stdout.write(
                f"{code:<8}  {row['predicted_position']:>5.1f}  {row['predicted_fantasy_points']:>7.1f}"
                f"  [{lo:>5.1f}, {hi:>5.1f}]"
            )
