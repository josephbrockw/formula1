from __future__ import annotations

from django.db.models import Count, QuerySet

from core.models import Season, Session, SessionCollectionStatus


def find_uncollected_sessions(year: int | None = None) -> QuerySet:
    completed_ids = SessionCollectionStatus.objects.filter(status="completed").values("session_id")
    qs = Session.objects.exclude(id__in=completed_ids)
    if year is not None:
        qs = qs.filter(event__season__year=year)
    return qs


def get_collection_summary() -> dict[int, dict[str, int]]:
    summary = {}
    for season in Season.objects.order_by("year"):
        total = Session.objects.filter(event__season=season).count()
        rows = (
            SessionCollectionStatus.objects.filter(session__event__season=season)
            .values("status")
            .annotate(count=Count("id"))
        )
        counts = {row["status"]: row["count"] for row in rows}
        completed = counts.get("completed", 0)
        failed = counts.get("failed", 0)
        summary[season.year] = {
            "total": total,
            "completed": completed,
            "failed": failed,
            "pending": total - completed - failed,
        }
    return summary
