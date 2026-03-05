"""
Compute historical FantasyDriverPrice / FantasyConstructorPrice records using
the 2025 F1 Fantasy price change algorithm.

Requires:
  1. FantasyDriverScore records for the season (import with import_fantasy_csv)
  2. A starting-prices CSV with columns: code, price
     (driver codes like NOR, VER, or constructor names like McLaren, Ferrari)

The algorithm chains race-by-race:
  - Price at race N = price at race N-1 + price_change computed from races 1..N-1
  - price_change = f(AvgPPM over last 3 races, current tier)

Usage:
  python manage.py compute_fantasy_prices \\
    --year 2024 \\
    --driver-prices data/2024/starting_prices_drivers.csv \\
    --constructor-prices data/2024/starting_prices_constructors.csv

Starting prices CSV format (no header required, just two columns):
  NOR,28.0
  VER,30.0
  ...

For constructors:
  McLaren,30.5
  Ferrari,27.0
  ...
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Max

from core.models import Driver, Event, Season, Team
from predictions.models import FantasyConstructorPrice, FantasyDriverPrice, FantasyDriverScore, FantasyConstructorScore
from predictions.price_calculator import compute_avg_ppm, next_price


class Command(BaseCommand):
    help = "Compute historical race prices from starting prices + fantasy scores using the 2025 formula"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--year", type=int, required=True, help="Season year to compute prices for")
        parser.add_argument(
            "--driver-prices",
            help="CSV file with driver starting prices (columns: code, price)",
        )
        parser.add_argument(
            "--constructor-prices",
            help="CSV file with constructor starting prices (columns: name, price)",
        )
        parser.add_argument("--default-driver-price", type=float, default=10.0)
        parser.add_argument("--default-constructor-price", type=float, default=15.0)
        parser.add_argument(
            "--carry-over",
            action="store_true",
            help="Use end-of-previous-season prices as starting prices (clamped to $5–$28M)",
        )

    def handle(self, *args, **options) -> None:
        year = options["year"]
        default_driver = Decimal(str(options["default_driver_price"]))
        default_constructor = Decimal(str(options["default_constructor_price"]))
        carry_over = options["carry_over"]

        try:
            season = Season.objects.get(year=year)
        except Season.DoesNotExist:
            raise CommandError(f"No season found for year={year}")

        events = list(
            Event.objects.filter(season=season).order_by("round_number").select_related("season")
        )
        if not events:
            raise CommandError(f"No events found for {year} season")

        self.stdout.write(f"Computing prices for {year} season — {len(events)} events")

        drivers = {d.code: d for d in Driver.objects.filter(season=season)}
        if options["driver_prices"]:
            starting = _load_starting_prices(options["driver_prices"])
            missing = [code for code in starting if code not in drivers]
            if missing:
                self.stdout.write(f"  Warning: driver codes not in DB: {missing}")
        elif carry_over:
            starting = _carry_over_driver_prices(year - 1, drivers, default_driver)
        else:
            starting = {code: default_driver for code in drivers}
        _compute_driver_prices(events, season, starting, drivers)
        self.stdout.write(f"  Driver prices computed for {len(drivers)} drivers")

        teams = {t.name: t for t in Team.objects.filter(season=season)}
        if options["constructor_prices"]:
            starting = _load_starting_prices(options["constructor_prices"])
            missing = [name for name in starting if name not in teams]
            if missing:
                self.stdout.write(f"  Warning: constructor names not in DB: {missing}")
        elif carry_over:
            starting = _carry_over_constructor_prices(year - 1, teams, default_constructor)
        else:
            starting = {name: default_constructor for name in teams}
        _compute_constructor_prices(events, season, starting, teams)
        self.stdout.write(f"  Constructor prices computed for {len(teams)} constructors")


CARRY_OVER_FLOOR = Decimal("5.0")
CARRY_OVER_CEILING = Decimal("28.0")


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------


def _compute_driver_prices(
    events: list[Event],
    season,
    starting_prices: dict[str, Decimal],
    drivers: dict[str, Driver],
) -> None:
    """
    Chain the price formula across all events for all drivers.

    For each driver, price at round N is determined by the AvgPPM from rounds 1..N-1.
    Stores one FantasyDriverPrice record per (driver, event).
    """
    # fantasy_points[driver_code][event_id] → race_total
    fantasy_pts = _load_driver_fantasy_points(events)

    # recent_results[driver_code] = [(pts, price), ...] — last 3 races
    recent: dict[str, list[tuple[float, Decimal]]] = {code: [] for code in starting_prices}
    current_prices = dict(starting_prices)

    records = []
    for event in events:
        for code, price in current_prices.items():
            driver = drivers.get(code)
            if driver is None:
                continue
            avg_ppm = compute_avg_ppm(recent.get(code, []))
            price_change, new_price = next_price(price, avg_ppm)
            records.append(
                FantasyDriverPrice(
                    driver=driver,
                    event=event,
                    snapshot_date=event.event_date,
                    price=price,
                    price_change=price_change,
                    pick_percentage=0.0,
                    season_fantasy_points=0,
                )
            )
            # Accumulate this race's points into history for next race
            pts = fantasy_pts.get(code, {}).get(event.id)
            if pts is not None:
                recent.setdefault(code, [])
                recent[code] = (recent[code] + [(pts, price)])[-3:]

        # Advance prices for next race
        for code in list(current_prices):
            avg_ppm = compute_avg_ppm(recent.get(code, []))
            _, current_prices[code] = next_price(current_prices[code], avg_ppm)

    with transaction.atomic():
        FantasyDriverPrice.objects.filter(event__in=events).delete()
        FantasyDriverPrice.objects.bulk_create(records)


def _compute_constructor_prices(
    events: list[Event],
    season,
    starting_prices: dict[str, Decimal],
    teams: dict[str, Team],
) -> None:
    """Same logic as _compute_driver_prices but for constructors."""
    fantasy_pts = _load_constructor_fantasy_points(events)

    recent: dict[str, list[tuple[float, Decimal]]] = {name: [] for name in starting_prices}
    current_prices = dict(starting_prices)

    records = []
    for event in events:
        for name, price in current_prices.items():
            team = teams.get(name)
            if team is None:
                continue
            avg_ppm = compute_avg_ppm(recent.get(name, []))
            price_change, new_price = next_price(price, avg_ppm)
            records.append(
                FantasyConstructorPrice(
                    team=team,
                    event=event,
                    snapshot_date=event.event_date,
                    price=price,
                    price_change=price_change,
                    pick_percentage=0.0,
                    season_fantasy_points=0,
                )
            )
            pts = fantasy_pts.get(name, {}).get(event.id)
            if pts is not None:
                recent.setdefault(name, [])
                recent[name] = (recent[name] + [(pts, price)])[-3:]

        for name in list(current_prices):
            avg_ppm = compute_avg_ppm(recent.get(name, []))
            _, current_prices[name] = next_price(current_prices[name], avg_ppm)

    with transaction.atomic():
        FantasyConstructorPrice.objects.filter(event__in=events).delete()
        FantasyConstructorPrice.objects.bulk_create(records)


# ---------------------------------------------------------------------------
# Carry-over helpers
# ---------------------------------------------------------------------------


def _carry_over_driver_prices(
    prev_year: int,
    current_drivers: dict[str, Driver],
    default: Decimal,
) -> dict[str, Decimal]:
    prev_prices = _last_event_driver_prices(prev_year)
    return {
        code: max(CARRY_OVER_FLOOR, min(CARRY_OVER_CEILING, prev_prices.get(code, default)))
        for code in current_drivers
    }


def _carry_over_constructor_prices(
    prev_year: int,
    current_teams: dict[str, Team],
    default: Decimal,
) -> dict[str, Decimal]:
    prev_prices = _last_event_constructor_prices(prev_year)
    return {
        name: max(CARRY_OVER_FLOOR, min(CARRY_OVER_CEILING, prev_prices.get(name, default)))
        for name in current_teams
    }


def _last_event_driver_prices(year: int) -> dict[str, Decimal]:
    last_event = Event.objects.filter(season__year=year).order_by("-round_number").first()
    if last_event is None:
        return {}
    return {
        fdp.driver.code: fdp.price
        for fdp in FantasyDriverPrice.objects.filter(event=last_event).select_related("driver")
    }


def _last_event_constructor_prices(year: int) -> dict[str, Decimal]:
    last_event = Event.objects.filter(season__year=year).order_by("-round_number").first()
    if last_event is None:
        return {}
    return {
        fcp.team.name: fcp.price
        for fcp in FantasyConstructorPrice.objects.filter(event=last_event).select_related("team")
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_starting_prices(path_str: str) -> dict[str, Decimal]:
    """
    Load a two-column CSV (no header) mapping code/name → starting price.

    Accepts lines like:
      NOR,28.0
      McLaren,30.5
    """
    path = Path(path_str)
    if not path.exists():
        raise CommandError(f"Starting prices file not found: {path}")
    result = {}
    with open(path) as f:
        for row in csv.reader(f):
            if not row or row[0].startswith("#") or len(row) < 2:
                continue
            key = row[0].strip()
            result[key] = Decimal(row[1].strip())
    return result


def _load_driver_fantasy_points(events: list[Event]) -> dict[str, dict[int, float]]:
    """
    Return {driver_code: {event_id: race_total}} from FantasyDriverScore.
    Uses MAX(race_total) per (driver, event) since race_total is the same on all rows
    for a given driver+event — we just need one value.
    """
    rows = (
        FantasyDriverScore.objects.filter(event__in=events)
        .values("driver__code", "event_id")
        .annotate(total=Max("race_total"))
    )
    result: dict[str, dict[int, float]] = {}
    for row in rows:
        result.setdefault(row["driver__code"], {})[row["event_id"]] = float(row["total"])
    return result


def _load_constructor_fantasy_points(events: list[Event]) -> dict[str, dict[int, float]]:
    """Return {team_name: {event_id: race_total}} from FantasyConstructorScore."""
    rows = (
        FantasyConstructorScore.objects.filter(event__in=events)
        .values("team__name", "event_id")
        .annotate(total=Max("race_total"))
    )
    result: dict[str, dict[int, float]] = {}
    for row in rows:
        result.setdefault(row["team__name"], {})[row["event_id"]] = float(row["total"])
    return result
