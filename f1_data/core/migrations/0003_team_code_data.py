from django.db import migrations

# Maps every team name string found in the DB (2018–2026) to a stable code.
# Name-change chains (e.g. Renault → Alpine) share the same code so that
# cross-season queries can treat them as the same constructor.
NAME_TO_CODE = {
    # Stable names
    "Ferrari": "ferrari",
    "Mercedes": "mercedes",
    "McLaren": "mclaren",
    "Williams": "williams",
    "Haas F1 Team": "haas",
    # Red Bull (edge-case variant seen in some seasons)
    "Red Bull Racing": "red_bull",
    "Red Bull": "red_bull",
    # Renault → Alpine
    "Renault": "alpine",
    "Alpine F1 Team": "alpine",
    "Alpine": "alpine",
    # Toro Rosso → AlphaTauri → RB → Racing Bulls
    "Toro Rosso": "rb",
    "AlphaTauri": "rb",
    "RB": "rb",
    "Racing Bulls": "rb",
    # Force India → Racing Point → Aston Martin
    "Force India": "aston_martin",
    "Racing Point": "aston_martin",
    "Aston Martin": "aston_martin",
    # Alfa Romeo → Kick Sauber → Sauber
    "Alfa Romeo Racing": "kick_sauber",
    "Alfa Romeo": "kick_sauber",
    "Kick Sauber": "kick_sauber",
    "Sauber": "kick_sauber",
    "Audi": "kick_sauber",
}


def populate_team_codes(apps, schema_editor):
    Team = apps.get_model("core", "Team")
    to_update = []
    for team in Team.objects.all():
        code = NAME_TO_CODE.get(team.name, "")
        if code:
            team.code = code
            to_update.append(team)
    if to_update:
        Team.objects.bulk_update(to_update, ["code"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_team_code"),
    ]

    operations = [
        migrations.RunPython(populate_team_codes, migrations.RunPython.noop),
    ]
