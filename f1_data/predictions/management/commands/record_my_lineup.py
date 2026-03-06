from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from core.models import Driver, Event, Team
from predictions.models import MyLineup


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
                "actual_points": options["actual_points"],
            },
        )

        driver_codes = " ".join(d.code for d in drivers)
        constructor_names = " / ".join(c.name for c in constructors)
        self.stdout.write(f"Saved lineup for {event}:")
        self.stdout.write(f"  Drivers: {driver_codes}  (DRS: {drs_driver.code})")
        self.stdout.write(f"  Constructors: {constructor_names}")
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
