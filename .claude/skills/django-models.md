# Django Models Skill

## Model Field Conventions

- Use `models.DurationField()` for lap times and sector times (maps to Python `timedelta`)
- Use `models.DateTimeField()` for timestamps, `models.DateField()` for dates
- Use `models.FloatField()` for temps, speeds, pressures — not DecimalField (we don't need exact decimal math)
- Use `models.IntegerField()` for positions, lap numbers, counts
- Use `models.CharField(max_length=...)` for status codes, compounds, session types — keep max_length tight
- Use `models.BooleanField(default=False)` — always provide a default
- Mark fields `null=True, blank=True` when FastF1 legitimately returns NaN/None for that field
- Use `models.ForeignKey(..., on_delete=models.CASCADE)` — cascade is correct for this data (deleting a session should delete its laps)

## Constraints and Indexes

```python
class Meta:
    unique_together = [('session', 'driver', 'lap_number')]  # prevent duplicate laps
    indexes = [
        models.Index(fields=['session', 'driver']),  # common query pattern
    ]
```

Add indexes for fields used in filters and lookups. The main query patterns are:
- All laps for a session + driver
- All results for a session
- All weather for a session
- All uncollected sessions (SessionCollectionStatus.status != 'completed')

## bulk_create Pattern

```python
# Always use bulk_create for batch inserts
Lap.objects.bulk_create(lap_instances, batch_size=500)

# For updates, delete + recreate in a transaction (simpler than update_or_create loops)
with transaction.atomic():
    Lap.objects.filter(session=session).delete()
    Lap.objects.bulk_create(new_laps, batch_size=500)
```

Never use `get_or_create` or `update_or_create` in a loop. It generates N queries instead of 1.

## String Choices

Use plain strings, not Django's `TextChoices` enum class. The values come from FastF1 and may include unexpected strings. Validating against a strict enum creates fragile code that breaks when FastF1 adds a new status. Store what FastF1 gives us.

```python
# Do this
status = models.CharField(max_length=50)

# Not this
class Status(models.TextChoices):
    FINISHED = "Finished"
    ...
```
