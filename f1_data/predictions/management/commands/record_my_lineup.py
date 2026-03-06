from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from core.models import Driver, Event, Team
from predictions.models import FantasyConstructorPrice, FantasyDriverPrice, MyLineup


class Command(BaseCommand):
    help = "Record the lineup you actually submitted for a race weekend."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--year", type=int, required=True)
        parser.add_argument("--round", type=int, required=True)
        parser.add_argument("--drivers", nargs=5, metavar="CODE", required=True,
                            help="Five driver codes, e.g. NOR VER PIA LEC HAM")
        parser.add_argument("--drs", metavar="CODE", required=True,
                            help="Driver code for your DRS Boost pick (must be one of --drivers)")
        parser.add_argument("--constructors", nargs=2, metavar="NAME", required=True,
                            help="Two constructor names, e.g. McLaren Ferrari")
        parser.add_argument("--actual-points", type=float, default=None,
                            help="Total points this lineup actually scored (fill in post-race)")

    def handle(self, *args, **options) -> None:
        year = options["year"]
        round_number = options["round"]

        try:
            event = Event.objects.select_related("season").get(
                season__year=year, round_number=round_number
            )
        except Event.DoesNotExist:
            raise CommandError(f"No event found for year={year}, round={round_number}")

        season = event.season
        drivers = _resolve_drivers(options["drivers"], season)
        drs_driver = _resolve_drs(options["drs"], drivers)
        constructors = _resolve_constructors(options["constructors"], season)

        team_cost = _compute_team_cost(event, drivers, constructors)
        budget_cap = _compute_budget_cap(event, drivers, constructors, team_cost)

        if team_cost is not None and budget_cap is not None and team_cost > budget_cap:
            raise CommandError(
                f"Team cost ${team_cost}M exceeds available budget ${budget_cap}M "
                f"(over by ${team_cost - budget_cap}M). Fix your lineup before recording."
            )

        MyLineup.objects.update_or_create(
            event=event,
            defaults={
                "driver_1": drivers[0],
                "driver_2": drivers[1],
                "driver_3": drivers[2],
                "driver_4": drivers[3],
                "driver_5": drivers[4],
                "drs_boost_driver": drs_driver,
                "constructor_1": constructors[0],
                "constructor_2": constructors[1],
                "team_cost": team_cost,
                "budget_cap": budget_cap,
                "actual_points": options["actual_points"],
            },
        )

        driver_codes = " ".join(d.code for d in drivers)
        constructor_names = " / ".join(c.name for c in constructors)
        self.stdout.write(f"Saved lineup for {event}:")
        self.stdout.write(f"  Drivers: {driver_codes}  (DRS: {drs_driver.code})")
        self.stdout.write(f"  Constructors: {constructor_names}")
        if team_cost is not None and budget_cap is not None:
            bank = float(budget_cap) - float(team_cost)
            self.stdout.write(f"  Team cost: ${team_cost}M  Budget cap: ${budget_cap}M  Bank: ${bank:.1f}M")
        else:
            self.stdout.write("  Note: prices not found — team_cost/budget_cap not saved")
        if options["actual_points"] is not None:
            self.stdout.write(f"  Actual points: {options['actual_points']}")


def _resolve_drivers(codes: list[str], season) -> list[Driver]:
    drivers = []
    for code in codes:
        try:
            drivers.append(Driver.objects.get(season=season, code=code.upper()))
        except Driver.DoesNotExist:
            raise CommandError(
                f"Driver '{code}' not found in {season.year} season. "
                f"Check available codes: python manage.py shell -c "
                f"\"from core.models import Driver; print(list(Driver.objects.filter(season__year={season.year}).values_list('code', flat=True)))\""
            )
    return drivers


def _resolve_drs(code: str, drivers: list[Driver]) -> Driver:
    code = code.upper()
    for driver in drivers:
        if driver.code == code:
            return driver
    raise CommandError(
        f"DRS driver '{code}' must be one of your selected drivers: "
        f"{', '.join(d.code for d in drivers)}"
    )


def _resolve_constructors(names: list[str], season) -> list[Team]:
    constructors = []
    for name in names:
        team = Team.objects.filter(season=season, name__iexact=name).first()
        if team is None:
            raise CommandError(
                f"Constructor '{name}' not found in {season.year} season. "
                f"Check available names: python manage.py shell -c "
                f"\"from core.models import Team; print(list(Team.objects.filter(season__year={season.year}).values_list('name', flat=True)))\""
            )
        constructors.append(team)
    return constructors


def _compute_team_cost(event: Event, drivers: list[Driver], constructors: list[Team]) -> Decimal | None:
    driver_ids = [d.id for d in drivers]
    team_ids = [c.id for c in constructors]
    driver_prices = dict(
        FantasyDriverPrice.objects.filter(event=event, driver_id__in=driver_ids)
        .values_list("driver_id", "price")
    )
    constructor_prices = dict(
        FantasyConstructorPrice.objects.filter(event=event, team_id__in=team_ids)
        .values_list("team_id", "price")
    )
    if len(driver_prices) != 5 or len(constructor_prices) != 2:
        return None
    return sum(driver_prices.values(), Decimal(0)) + sum(constructor_prices.values(), Decimal(0))


def _compute_budget_cap(
    event: Event,
    drivers: list[Driver],
    constructors: list[Team],
    team_cost: Decimal | None,
) -> Decimal | None:
    if team_cost is None:
        return None
    prev = (
        MyLineup.objects
        .filter(event__season=event.season, event__event_date__lt=event.event_date)
        .select_related("event")
        .order_by("-event__event_date")
        .first()
    )
    if prev is None:
        return Decimal("100.0")
    if prev.budget_cap is None or prev.team_cost is None:
        return None

    prev_bank = prev.budget_cap - prev.team_cost

    prev_driver_ids = [prev.driver_1_id, prev.driver_2_id, prev.driver_3_id,
                       prev.driver_4_id, prev.driver_5_id]
    prev_team_ids = [prev.constructor_1_id, prev.constructor_2_id]

    driver_prices = dict(
        FantasyDriverPrice.objects.filter(event=event, driver_id__in=prev_driver_ids)
        .values_list("driver_id", "price")
    )
    constructor_prices = dict(
        FantasyConstructorPrice.objects.filter(event=event, team_id__in=prev_team_ids)
        .values_list("team_id", "price")
    )
    if len(driver_prices) != 5 or len(constructor_prices) != 2:
        return None

    current_team_value = sum(driver_prices.values(), Decimal(0)) + sum(constructor_prices.values(), Decimal(0))
    return prev_bank + current_team_value
