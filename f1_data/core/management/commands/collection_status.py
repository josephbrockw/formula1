from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import OuterRef, Subquery

from core.models import Session, SessionCollectionStatus
from core.tasks.gap_detector import get_collection_summary


class Command(BaseCommand):
    help = "Show F1 data collection coverage"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--year", type=int, help="Show session detail for one season")
        parser.add_argument("--gaps", action="store_true", help="Show only incomplete sessions")

    def handle(self, *args, **options) -> None:
        if options["year"] or options["gaps"]:
            self._print_sessions(options["year"], options["gaps"])
        else:
            self._print_summary()

    def _print_summary(self) -> None:
        summary = get_collection_summary()
        header = (
            f"{'Year':<6}  {'Sessions':>8}  {'Past':>6}  {'Complete':>12}  "
            f"{'Failed':>6}  {'Weather':>8}  {'Results':>8}  {'Laps':>6}"
        )
        self.stdout.write(header)
        self.stdout.write("-" * len(header))
        for year, c in summary.items():
            total = c["total"]
            pct = f"{round(100 * c['completed'] / total)}%" if total else "0%"
            complete_str = f"{c['completed']} ({pct})"
            self.stdout.write(
                f"{year:<6}  {total:>8}  {c['past']:>6}  {complete_str:>12}  "
                f"{c['failed']:>6}  {c['with_weather']:>8}  {c['with_results']:>8}  {c['with_laps']:>6}"
            )

    def _print_sessions(self, year: int | None, gaps_only: bool) -> None:
        status_subquery = Subquery(
            SessionCollectionStatus.objects.filter(session=OuterRef("pk")).values("status")[:1]
        )
        qs = (
            Session.objects.annotate(collection_status=status_subquery)
            .select_related("event__season")
            .order_by("event__season__year", "event__round_number", "session_type")
        )
        if year:
            qs = qs.filter(event__season__year=year)
        if gaps_only:
            qs = qs.exclude(collection_status="completed")

        for session in qs:
            status = session.collection_status or "pending"
            self.stdout.write(
                f"{session.event.season.year} R{session.event.round_number:02d}"
                f" {session.event.event_name} — {session.session_type}: {status}"
            )
