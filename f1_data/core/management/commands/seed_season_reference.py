from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.models import Driver, Season, Team


def _upsert_team(season: Season, roster_label: str, fastf1_name: str, code: str = "") -> tuple[Team, int, int]:
    """Find or create a Team, renaming it if fastf1_name changed since last seed.

    Returns (team, created, renamed) where each is 0 or 1.

    Lookup order:
    1. By fastf1_name — normal case, team already has the right name.
    2. By roster_label — team was seeded before we knew the FastF1 name.
       Rename it in-place so all FK references (drivers, prices) remain valid.
    3. Neither exists — create fresh.

    If `code` is provided it is written to team.code; an empty `code` is
    ignored so that re-seeding never blanks out an existing code value.
    """
    team = Team.objects.filter(season=season, name=fastf1_name).first()
    if team:
        if code and team.code != code:
            team.code = code
            team.save(update_fields=["code"])
        return team, 0, 0

    team = Team.objects.filter(season=season, name=roster_label).first()
    if team:
        fields = ["name"]
        team.name = fastf1_name
        if code and team.code != code:
            team.code = code
            fields.append("code")
        team.save(update_fields=fields)
        return team, 0, 1

    team = Team.objects.create(season=season, name=fastf1_name, code=code)
    return team, 1, 0


class Command(BaseCommand):
    help = "Seed Driver and Team records for a season from a roster JSON file."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--year", type=int, required=True)
        parser.add_argument("--roster", type=str, required=True, help="Path to roster JSON file")

    def handle(self, *args, **options) -> None:
        year = options["year"]
        roster_path = Path(options["roster"])

        if not roster_path.exists():
            raise CommandError(f"Roster file not found: {roster_path}")

        try:
            season = Season.objects.get(year=year)
        except Season.DoesNotExist:
            raise CommandError(
                f"Season {year} not found in DB. Run collect_data --year {year} first."
            )

        data = json.loads(roster_path.read_text())

        # fastf1_name is what FastF1 returns and is stored as Team.name so that
        # _sync_drivers_teams (which uses FastF1 strings directly) finds existing
        # records without any mapping. The roster `name` is a human-readable label
        # and the key used in the drivers list.
        #
        # If fastf1_name changes after the first race (e.g. we learn FastF1 calls
        # it "Cadillac F1 Team" not "Cadillac"), re-running this command renames
        # the existing team row rather than creating a new one, keeping all driver
        # and price FK references intact.
        teams_created = 0
        teams_renamed = 0
        team_map: dict[str, Team] = {}  # keyed by roster `name` (drivers reference this)

        for team_data in data["teams"]:
            roster_label = team_data["name"]
            fastf1_name = team_data.get("fastf1_name", roster_label)
            code = team_data.get("code", "")
            team, created, renamed = _upsert_team(season, roster_label, fastf1_name, code)
            team_map[roster_label] = team
            teams_created += created
            teams_renamed += renamed

        drivers_created = 0
        drivers_updated = 0
        for driver_data in data["drivers"]:
            team_label = driver_data["team"]
            if team_label not in team_map:
                raise CommandError(
                    f"Team '{team_label}' for driver {driver_data['code']} "
                    f"not listed in roster teams."
                )
            _, created = Driver.objects.update_or_create(
                season=season,
                code=driver_data["code"],
                defaults={
                    "full_name": driver_data["full_name"],
                    "driver_number": driver_data["driver_number"],
                    "team": team_map[team_label],
                },
            )
            if created:
                drivers_created += 1
            else:
                drivers_updated += 1

        parts = [f"{teams_created} teams created"]
        if teams_renamed:
            parts.append(f"{teams_renamed} teams renamed")
        parts += [f"{drivers_created} drivers created", f"{drivers_updated} drivers updated"]
        self.stdout.write(f"{year}: {', '.join(parts)}")
