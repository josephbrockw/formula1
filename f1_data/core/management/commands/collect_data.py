from __future__ import annotations

from django.core.management.base import BaseCommand

from core.flows.collect_season import collect_all


class Command(BaseCommand):
    help = "Collect F1 session data from FastF1"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--year", type=int, help="Collect only this season")
        parser.add_argument("--round", type=int, dest="round_number", help="Collect only this round (requires --year)")
        parser.add_argument("--force", action="store_true", help="Re-collect already completed sessions")
        parser.add_argument("--retry-failed", action="store_true", help="Re-attempt previously failed sessions")

    def handle(self, *args, **options) -> None:
        year = options["year"]
        round_number = options["round_number"]
        force = options["force"]

        collect_all(
            years=[year] if year else None,
            force_recollect=force,
            round_number=round_number,
            retry_failed=options["retry_failed"],
            stdout=self.stdout,
        )
