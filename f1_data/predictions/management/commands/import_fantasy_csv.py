"""
Import Chrome extension CSV exports into the DB.

Handles four file patterns (detected by filename suffix):
  YYYY-MM-DD-drivers.csv                  → FantasyDriverPrice
  YYYY-MM-DD-constructors.csv             → FantasyConstructorPrice
  YYYY-MM-DD-all-drivers-performance.csv  → FantasyDriverScore
  YYYY-MM-DD-all-constructors-performance.csv → FantasyConstructorScore

Usage:
  python manage.py import_fantasy_csv --dir data/2025/snapshots/
  python manage.py import_fantasy_csv --dir data/2025/outcomes/
  python manage.py import_fantasy_csv --dir data/2025/  (scans recursively)
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Driver, Event, Season, Team
from predictions.models import (
    FantasyConstructorPrice,
    FantasyConstructorScore,
    FantasyDriverPrice,
    FantasyDriverScore,
)

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


class Command(BaseCommand):
    help = "Import Chrome extension CSV exports (prices + performance) into the DB"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--dir", required=True, help="Directory containing CSV files to import")

    def handle(self, *args, **options) -> None:
        root = Path(options["dir"])
        if not root.exists():
            raise CommandError(f"Directory not found: {root}")

        csvs = sorted(root.rglob("*.csv"))
        if not csvs:
            raise CommandError(f"No CSV files found under {root}")

        self.stdout.write(f"Found {len(csvs)} CSV files under {root}")

        for path in csvs:
            name = path.name
            if name.endswith("-drivers.csv") and "performance" not in name:
                self._import_driver_snapshot(path)
            elif name.endswith("-constructors.csv") and "performance" not in name:
                self._import_constructor_snapshot(path)
            elif name.endswith("-all-drivers-performance.csv"):
                self._import_driver_performance(path)
            elif name.endswith("-all-constructors-performance.csv"):
                self._import_constructor_performance(path)
            else:
                self.stdout.write(f"  Skipping unrecognised file: {name}")

    # ------------------------------------------------------------------
    # Snapshot imports (prices)
    # ------------------------------------------------------------------

    def _import_driver_snapshot(self, path: Path) -> None:
        snapshot_date = _parse_date(path.name)
        event = _nearest_event(snapshot_date)
        if event is None:
            self.stdout.write(f"  [SKIP] {path.name} — no event found near {snapshot_date}")
            return

        season = event.season
        df = pd.read_csv(path)
        created = updated = skipped = 0

        with transaction.atomic():
            for _, row in df.iterrows():
                driver = _driver_by_name(str(row["Driver Name"]), season)
                if driver is None:
                    skipped += 1
                    continue
                _, was_created = FantasyDriverPrice.objects.update_or_create(
                    driver=driver,
                    event=event,
                    defaults={
                        "snapshot_date": snapshot_date,
                        "price": _parse_price(str(row["Current Value"])),
                        "price_change": _parse_price(str(row["Price Change"])),
                        "pick_percentage": float(row["% Picked"]),
                        "season_fantasy_points": int(row["Season Points"]),
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(
            f"  {path.name} → {event}: {created} created, {updated} updated, {skipped} skipped drivers"
        )

    def _import_constructor_snapshot(self, path: Path) -> None:
        snapshot_date = _parse_date(path.name)
        event = _nearest_event(snapshot_date)
        if event is None:
            self.stdout.write(f"  [SKIP] {path.name} — no event found near {snapshot_date}")
            return

        season = event.season
        df = pd.read_csv(path)
        created = updated = skipped = 0

        with transaction.atomic():
            for _, row in df.iterrows():
                team = _team_by_name(str(row["Constructor Name"]), season)
                if team is None:
                    skipped += 1
                    continue
                _, was_created = FantasyConstructorPrice.objects.update_or_create(
                    team=team,
                    event=event,
                    defaults={
                        "snapshot_date": snapshot_date,
                        "price": _parse_price(str(row["Current Value"])),
                        "price_change": _parse_price(str(row["Price Change"])),
                        "pick_percentage": float(row["% Picked"]),
                        "season_fantasy_points": int(row["Season Points"]),
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(
            f"  {path.name} → {event}: {created} created, {updated} updated, {skipped} skipped constructors"
        )

    # ------------------------------------------------------------------
    # Performance imports (scoring breakdowns)
    # ------------------------------------------------------------------

    def _import_driver_performance(self, path: Path) -> None:
        year = _parse_year(path.name)
        try:
            season = Season.objects.get(year=year)
        except Season.DoesNotExist:
            self.stdout.write(f"  [SKIP] {path.name} — no season for year {year}")
            return

        event_cache: dict[str, Event | None] = {}
        driver_cache: dict[str, Driver | None] = {}
        df = pd.read_csv(path)
        created = updated = skipped = 0

        with transaction.atomic():
            for _, row in df.iterrows():
                race_name = str(row["Race"])
                driver_name = str(row["Driver Name"])

                event = event_cache.setdefault(race_name, _event_by_race_name(race_name, year))
                if event is None:
                    skipped += 1
                    continue

                driver = driver_cache.setdefault(
                    driver_name, _driver_by_name(driver_name, season)
                )
                if driver is None:
                    skipped += 1
                    continue

                _, was_created = FantasyDriverScore.objects.update_or_create(
                    driver=driver,
                    event=event,
                    event_type=str(row["Event Type"]),
                    scoring_item=str(row["Scoring Item"]),
                    defaults={
                        "frequency": _int_or_none(row.get("Frequency")),
                        "position": _int_or_none(row.get("Position")),
                        "points": int(row["Points"]),
                        "race_total": int(row["Race Total"]),
                        "season_total": int(row["Season Total"]),
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(
            f"  {path.name}: {created} created, {updated} updated, {skipped} skipped rows"
        )

    def _import_constructor_performance(self, path: Path) -> None:
        year = _parse_year(path.name)
        try:
            season = Season.objects.get(year=year)
        except Season.DoesNotExist:
            self.stdout.write(f"  [SKIP] {path.name} — no season for year {year}")
            return

        event_cache: dict[str, Event | None] = {}
        team_cache: dict[str, Team | None] = {}
        df = pd.read_csv(path)
        created = updated = skipped = 0

        with transaction.atomic():
            for _, row in df.iterrows():
                race_name = str(row["Race"])
                team_name = str(row["Constructor Name"])

                event = event_cache.setdefault(race_name, _event_by_race_name(race_name, year))
                if event is None:
                    skipped += 1
                    continue

                team = team_cache.setdefault(team_name, _team_by_name(team_name, season))
                if team is None:
                    skipped += 1
                    continue

                _, was_created = FantasyConstructorScore.objects.update_or_create(
                    team=team,
                    event=event,
                    event_type=str(row["Event Type"]),
                    scoring_item=str(row["Scoring Item"]),
                    defaults={
                        "frequency": _int_or_none(row.get("Frequency")),
                        "position": _int_or_none(row.get("Position")),
                        "points": int(row["Points"]),
                        "race_total": int(row["Race Total"]),
                        "season_total": int(row["Season Total"]),
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(
            f"  {path.name}: {created} created, {updated} updated, {skipped} skipped rows"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_date(filename: str) -> date:
    """Extract YYYY-MM-DD from a filename like '2025-11-07-drivers.csv'."""
    m = _DATE_RE.match(filename)
    if not m:
        raise CommandError(f"Cannot extract date from filename: {filename}")
    return date.fromisoformat(m.group(1))


def _parse_year(filename: str) -> int:
    return int(_parse_date(filename).year)


def _parse_price(raw: str) -> Decimal:
    """Parse '$30.4M' or '-$0.1M' → Decimal('30.4') or Decimal('-0.1')."""
    cleaned = raw.strip().replace("$", "").replace("M", "").replace(",", "")
    return Decimal(cleaned)


def _int_or_none(value) -> int | None:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        return int(value)
    except (ValueError, TypeError):
        return None


def _nearest_event(snapshot_date: date) -> Event | None:
    """
    Return the most appropriate event for a snapshot taken on snapshot_date.

    Tries the next upcoming event first (prices are captured before a race).
    Falls back to the most recent past event if no upcoming event exists
    (e.g. snapshot taken after the season finale).
    """
    upcoming = (
        Event.objects.filter(event_date__gte=snapshot_date)
        .select_related("season")
        .order_by("event_date")
        .first()
    )
    if upcoming:
        return upcoming
    return (
        Event.objects.filter(event_date__lt=snapshot_date)
        .select_related("season")
        .order_by("-event_date")
        .first()
    )


def _driver_by_name(full_name: str, season: Season) -> Driver | None:
    return Driver.objects.filter(season=season, full_name__iexact=full_name).first()


def _team_by_name(name: str, season: Season) -> Team | None:
    return Team.objects.filter(season=season, name__iexact=name).first()


def _event_by_race_name(race_name: str, year: int) -> Event | None:
    """
    Match a short race name like 'Australia' to an event.

    Uses event_name__icontains so 'Australia' matches 'Australian Grand Prix',
    'Saudi Arabia' matches 'Saudi Arabian Grand Prix', etc.
    Returns the first match ordered by round number (handles potential duplicates).
    """
    return (
        Event.objects.filter(season__year=year, event_name__icontains=race_name)
        .order_by("round_number")
        .first()
    )
