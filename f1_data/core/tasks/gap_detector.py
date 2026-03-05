from __future__ import annotations

from django.db.models import Count, QuerySet
from django.utils import timezone

from core.models import Season, Session, SessionCollectionStatus


def find_uncollected_sessions(year: int | None = None, include_failed: bool = False) -> QuerySet:
    exclude_statuses = ["completed"] if include_failed else ["completed", "failed"]
    excluded_ids = SessionCollectionStatus.objects.filter(status__in=exclude_statuses).values("session_id")
    qs = Session.objects.exclude(id__in=excluded_ids)
    if year is not None:
        qs = qs.filter(event__season__year=year)
    return qs


def get_collection_summary() -> dict[int, dict[str, int]]:
    summary = {}
    now = timezone.now()
    for season in Season.objects.order_by("year"):
        total = Session.objects.filter(event__season=season).count()
        past = Session.objects.filter(event__season=season, date__lt=now).count()
        rows = (
            SessionCollectionStatus.objects.filter(session__event__season=season)
            .values("status")
            .annotate(count=Count("id"))
        )
        counts = {row["status"]: row["count"] for row in rows}
        completed = counts.get("completed", 0)
        failed = counts.get("failed", 0)
        scs_qs = SessionCollectionStatus.objects.filter(session__event__season=season)
        summary[season.year] = {
            "total": total,
            "past": past,
            "completed": completed,
            "failed": failed,
            "pending": total - completed - failed,
            "with_weather": scs_qs.filter(weather_sample_count__gt=0).count(),
            "with_results": scs_qs.filter(result_count__gt=0).count(),
            "with_laps": scs_qs.filter(lap_count__gt=0).count(),
        }
    return summary
