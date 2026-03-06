"""
Post-race scoring command.

After a race, run this to automatically fill in actual scores for:
  - LineupRecommendation.actual_points  (what the ML recommendation scored)
  - LineupRecommendation.oracle_actual_points  (max achievable with perfect knowledge)
  - MyLineup.actual_points  (what your submitted lineup scored)

All three are derived from FantasyDriverScore and FantasyConstructorScore records
that were imported from the Chrome extension CSV. No ML re-inference needed.

Usage:
  python manage.py score_lineup --year 2025 --round 5
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from core.models import Event
from predictions.models import LineupRecommendation, MyLineup
from predictions.scoring import (
    compute_oracle,
    load_actual_constructor_pts,
    load_actual_driver_pts,
    score_roster,
)


class Command(BaseCommand):
    help = "Score ML recommendation and your lineup against post-race actuals."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--year", type=int, required=True)
        parser.add_argument("--round", type=int, required=True)

    def handle(self, *args, **options) -> None:
        year = options["year"]
        round_number = options["round"]

        try:
            event = Event.objects.select_related("season").get(
                season__year=year, round_number=round_number
            )
        except Event.DoesNotExist:
            raise CommandError(f"No event found for year={year}, round={round_number}")

        actual_driver_pts = load_actual_driver_pts(event)
        if not actual_driver_pts:
            raise CommandError(
                f"No FantasyDriverScore records for {event}. "
                "Import post-race CSV first: python manage.py import_fantasy_csv"
            )

        actual_constructor_pts = load_actual_constructor_pts(event)

        self.stdout.write(f"Scoring post-race results for {event}")
        _score_my_lineup(event, actual_driver_pts, actual_constructor_pts, self.stdout)
        _score_recommendations(event, actual_driver_pts, actual_constructor_pts, self.stdout)


def _score_my_lineup(
    event: Event,
    actual_driver_pts: dict[int, float],
    actual_constructor_pts: dict[int, float],
    stdout,
) -> None:
    try:
        my_lineup = MyLineup.objects.get(event=event)
    except MyLineup.DoesNotExist:
        stdout.write("  MyLineup: no lineup recorded for this event — skipping")
        return

    pts = score_roster(
        [my_lineup.driver_1_id, my_lineup.driver_2_id, my_lineup.driver_3_id,
         my_lineup.driver_4_id, my_lineup.driver_5_id],
        [my_lineup.constructor_1_id, my_lineup.constructor_2_id],
        my_lineup.drs_boost_driver_id,
        actual_driver_pts,
        actual_constructor_pts,
    )
    my_lineup.actual_points = pts
    my_lineup.save(update_fields=["actual_points"])
    stdout.write(f"  MyLineup: {pts:.0f} pts")


def _score_recommendations(
    event: Event,
    actual_driver_pts: dict[int, float],
    actual_constructor_pts: dict[int, float],
    stdout,
) -> None:
    recs = list(LineupRecommendation.objects.filter(event=event))
    if not recs:
        stdout.write("  LineupRecommendation: none found — skipping")
        return

    # Oracle is unconstrained — theoretical ceiling regardless of which rec we're scoring.
    oracle = compute_oracle(event, actual_driver_pts, actual_constructor_pts, budget=100.0)
    oracle_str = f"{oracle:.0f}" if oracle is not None else "—"
    if oracle is None:
        stdout.write("  Oracle: price data missing — oracle_actual_points not set")

    for rec in recs:
        pts = score_roster(
            [rec.driver_1_id, rec.driver_2_id, rec.driver_3_id, rec.driver_4_id, rec.driver_5_id],
            [rec.constructor_1_id, rec.constructor_2_id],
            rec.drs_boost_driver_id,
            actual_driver_pts,
            actual_constructor_pts,
        )
        rec.actual_points = pts
        rec.oracle_actual_points = oracle
        rec.save(update_fields=["actual_points", "oracle_actual_points"])
        stdout.write(
            f"  LineupRecommendation ({rec.strategy_type} / {rec.model_version}): "
            f"{pts:.0f} pts  oracle={oracle_str}"
        )
