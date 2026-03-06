from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("predictions", "0002_mylineup"),
    ]

    operations = [
        migrations.AddField(
            model_name="mylineup",
            name="team_cost",
            field=models.DecimalField(
                blank=True,
                decimal_places=1,
                help_text="Total cost of this lineup at submission time ($M)",
                max_digits=6,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="mylineup",
            name="budget_cap",
            field=models.DecimalField(
                blank=True,
                decimal_places=1,
                help_text="Total available budget at time of submission ($M)",
                max_digits=6,
                null=True,
            ),
        ),
    ]
