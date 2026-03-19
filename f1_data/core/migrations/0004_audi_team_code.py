from django.db import migrations


def set_audi_code(apps, schema_editor):
    Team = apps.get_model("core", "Team")
    Team.objects.filter(name="Audi").update(code="kick_sauber")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_team_code_data"),
    ]

    operations = [
        migrations.RunPython(set_audi_code, migrations.RunPython.noop),
    ]
