# Driver Data Integrity Strategy

## Overview

This project imports F1 data from two sources:
1. **Formula1.com** (CSV exports) - Fantasy prices, performance scores
2. **FastF1 API** - Telemetry, lap times, weather, circuit data

Since driver names may differ slightly between sources, we use a robust matching strategy to maintain data integrity.

## Data Model

The `Driver` model includes fields from both sources:

```python
class Driver(models.Model):
    # Core fields (from Formula1.com - canonical)
    full_name = models.CharField(max_length=200, unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    
    # FastF1 identifiers (populated during telemetry import)
    driver_number = models.CharField(max_length=10, blank=True)  # e.g., "1", "44"
    abbreviation = models.CharField(max_length=3, blank=True)     # e.g., "VER", "HAM"
```

## Matching Strategy

### Source of Truth
**Formula1.com is the canonical source** for driver names. Drivers are initially created from CSV imports using the exact name from Formula1.com.

### FastF1 Matching (Priority Order)

When importing telemetry data from FastF1, drivers are matched using:

1. **Exact name match** (case-insensitive)
   - FastF1: "Max Verstappen" → DB: "Max Verstappen" ✓

2. **Driver number match** (if populated)
   - FastF1: "Max Verstappen" (#1) → DB: Driver with driver_number="1" ✓

3. **Abbreviation match** (if populated)
   - FastF1: "Max Verstappen" (VER) → DB: Driver with abbreviation="VER" ✓

4. **Normalized name match** (handles case/whitespace differences)
   - FastF1: "MAX VERSTAPPEN" → DB: "Max Verstappen" ✓

5. **Unique last name match**
   - FastF1: "Verstappen" → DB: Driver with last_name="Verstappen" (if only one) ✓

### Identifier Population

When a driver is successfully matched, their `driver_number` and `abbreviation` are automatically updated from FastF1 data (if not already set). This improves matching performance for future imports.

## Common Scenarios

### Scenario 1: New Season, CSV Import First
```
1. Import Fantasy CSV → Creates "Max Verstappen" (no identifiers)
2. Import telemetry → Matches by name, populates driver_number="1", abbreviation="VER"
3. Future telemetry imports → Fast match by driver_number
```

### Scenario 2: Name Variations
```
Formula1.com CSV: "Pierre Gasly"
FastF1 API: "PIERRE GASLY"

→ Matched via normalized name comparison
→ Identifiers populated: #10, GAS
→ Future imports use number/abbreviation for fast matching
```

### Scenario 3: Unmatched Driver
```
FastF1 provides: "Oscar Piastri" (#81, PIA)
But driver not in database (CSV not imported yet)

→ Warning logged: "Driver not found: Oscar Piastri (#81, PIA)"
→ Laps skipped for this driver
→ Manual action: Import CSV first, or create driver manually
```

## Monitoring & Maintenance

### Check Data Integrity
Run the integrity checker to identify issues:

```bash
python manage.py check_driver_integrity
python manage.py check_driver_integrity --verbose
```

This reports:
- Drivers missing FastF1 identifiers
- Potential duplicate drivers
- Recommendations for fixes

### Fix Missing Identifiers
Import telemetry data to auto-populate identifiers:

```bash
python manage.py import_fastf1 --year 2024
```

### Handle Duplicates
If duplicate drivers are detected:

1. **Review in Django admin** - Verify if truly duplicate
2. **Merge data manually** - Reassign foreign keys to correct driver
3. **Delete duplicate** - Remove the extra driver record
4. **Re-run integrity check** - Verify fix

## Best Practices

### 1. Import Order
For best results, import data in this order:
```bash
# 1. Create season and races
python manage.py import_schedule --year 2024

# 2. Import Fantasy CSV data (creates drivers)
python manage.py import_fantasy_prices --year 2024
python manage.py import_driver_performance --year 2024

# 3. Import FastF1 telemetry (matches drivers, populates identifiers)
python manage.py import_fastf1 --year 2024
```

### 2. Regular Integrity Checks
Run integrity checks after major imports:
```bash
python manage.py check_driver_integrity
```

### 3. Logging
The matching system logs all matches with their method:
```
INFO: Matched driver via exact_name: Max Verstappen -> Max Verstappen
INFO: Matched driver via driver_number: Verstappen -> Max Verstappen (#1)
WARNING: Driver not found: Oscar Piastri (#81, PIA). Skipping lap.
```

Review logs to identify matching issues.

### 4. Manual Overrides
For problematic matches, you can manually set identifiers in Django admin:
1. Find the driver in admin
2. Set `driver_number` and `abbreviation` from FastF1
3. Save - future imports will match correctly

## Code Reference

- **Matching logic**: `analytics/processing/driver_matching.py`
- **Telemetry import**: `analytics/flows/import_telemetry.py`
- **Integrity checker**: `analytics/management/commands/check_driver_integrity.py`
- **CSV import**: `analytics/management/commands/import_driver_performance.py`

## Edge Cases

### Reserve/Test Drivers
FastF1 may include drivers not in Formula1.com fantasy data (reserve/test drivers). These will be logged as unmatched. Create them manually if needed.

### Mid-Season Driver Changes
If a driver changes teams mid-season, the `current_team` field is updated automatically. Historical data remains correct via the `team` field on performance records.

### Retired Drivers
Historical drivers from past seasons remain in the database with their identifiers preserved.
