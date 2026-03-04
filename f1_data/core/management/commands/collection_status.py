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
        self.stdout.write(f"{'Season':<8} | {'Total':>5} | {'Done':>5} | {'Failed':>6} | {'Pending':>7}")
        self.stdout.write(f"{'-'*8}-+-{'-'*5}-+-{'-'*5}-+-{'-'*6}-+-{'-'*7}")
        for year, counts in summary.items():
            self.stdout.write(
                f"{year:<8} | {counts['total']:>5} | {counts['completed']:>5} |"
                f" {counts['failed']:>6} | {counts['pending']:>7}"
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
