# Setup Guide: Performance Data Import

## Quick Start

### 1. Create Database Migrations

The new models need to be added to your database:

```bash
cd /Users/joewilkinson/Projects/formula1/f1_analytics
python manage.py makemigrations
python manage.py migrate
```

This will create tables for:
- `analytics_race` - Grand Prix events
- `analytics_driverraceperformance` - Aggregated race performances
- `analytics_driveventscore` - Detailed scoring items

### 2. Export Performance Data

Use the Chrome extension to export driver performance data:

1. Navigate to the F1 Fantasy drivers list page
2. Click the extension icon
3. Click "Export Driver Performance"
4. Wait for the automation to complete
5. Save the CSV to: `f1_analytics/data/2025/outcomes/`

**Expected filename format:** `YYYY-MM-DD-all-drivers-performance.csv`

### 3. Import Performance Data

Run the import command:

```bash
# Import most recent file automatically
python manage.py import_driver_performance

# Or specify a specific file
python manage.py import_driver_performance --file /path/to/2025-11-10-all-drivers-performance.csv

# Or specify a different year
python manage.py import_driver_performance --year 2024
```

**Expected output:**
```
Found season: 2025 Season
Found performance file: 2025-11-10-all-drivers-performance.csv
Processing 460 driver-race combinations...
  Processed 20 performances...
  Processed 40 performances...
  ...

Import complete!
  Races created/updated: 20
  Driver performances: 460
  Event scores: 4,600
```

---

## Data Flow

```
┌─────────────────────────────────────────┐
│  F1 Fantasy Website                     │
│  https://fantasy.formula1.com          │
└────────────────┬────────────────────────┘
                 │
                 │ Chrome Extension
                 │ (automated scraping)
                 ▼
┌─────────────────────────────────────────┐
│  CSV File                               │
│  data/2025/outcomes/                    │
│  2025-11-10-all-drivers-performance.csv│
└────────────────┬────────────────────────┘
                 │
                 │ import_driver_performance
                 │ (management command)
                 ▼
┌─────────────────────────────────────────┐
│  Database Tables                        │
│  - analytics_race                       │
│  - analytics_driverraceperformance     │
│  - analytics_driveventscore            │
└─────────────────────────────────────────┘
```

---

## Database Schema

### Race Table
- **Purpose**: Normalize race/GP events
- **Key Fields**: season, name, round_number
- **Example**: "2025 Bahrain GP (Round 1)"

### DriverRacePerformance Table
- **Purpose**: Aggregate performance per driver per race
- **Key Fields**: driver, race, total_points, fantasy_price
- **Example**: "Lando Norris - Bahrain (59 pts)"
- **ML Use**: Time-series features, rolling averages, consistency

### DriverEventScore Table
- **Purpose**: Detailed scoring breakdown
- **Key Fields**: performance (FK), event_type, scoring_item, points
- **Example**: "Lando Norris - Bahrain - qualifying: Qualifying Position (10 pts)"
- **ML Use**: Event-specific analysis, pattern detection

---

## Verify Import

### Django Shell
```bash
python manage.py shell
```

```python
from analytics.models import Race, DriverRacePerformance, DriverEventScore

# Check races imported
print(f"Races: {Race.objects.count()}")
print(Race.objects.first())

# Check performances
print(f"Performances: {DriverRacePerformance.objects.count()}")
perf = DriverRacePerformance.objects.first()
print(f"{perf.driver.full_name} - {perf.race.name}: {perf.total_points} pts")

# Check event scores
print(f"Event scores: {DriverEventScore.objects.count()}")
score = DriverEventScore.objects.first()
print(f"{score.performance.driver.full_name} - {score.event_type}: {score.scoring_item}")
```

### Django Admin
Navigate to: http://localhost:8000/admin/

You should see new sections:
- Races
- Driver Race Performances  
- Driver Event Scores

---

## Common Issues

### Issue: "Season not found"
```
CommandError: Season 2025 not found.
```

**Solution**: Run the snapshot import first to create the season:
```bash
python manage.py import_fantasy_prices
```

### Issue: "No performance files found"
```
CommandError: No performance files found in data/2025/outcomes
```

**Solution**: 
1. Make sure the directory exists: `mkdir -p data/2025/outcomes`
2. Export data using the Chrome extension
3. Move the CSV file to the outcomes directory

### Issue: Duplicate race numbers
If races are created out of order, round numbers might be wrong.

**Solution**: Delete and reimport:
```python
from analytics.models import Race, DriverRacePerformance
Race.objects.filter(season__year=2025).delete()  # Cascades to performances
# Then run import again
```

---

## Next Steps

After importing performance data:

1. **Verify Data**: Check Django admin or run shell queries
2. **Feature Engineering**: See `docs/ML_MODEL_DESIGN.md` for ML features
3. **Build Predictions**: Create prediction models using the data
4. **Optimize Lineup**: Use ML to suggest optimal driver selections

---

## Maintenance

### Regular Updates
After each race weekend:
1. Export latest performance data (extension)
2. Run `python manage.py import_driver_performance`
3. The import is idempotent - safe to run multiple times

### Seasonal Cleanup
At season end:
```python
from analytics.models import Season
season = Season.objects.get(year=2025)
season.is_active = False
season.save()
```

---

## File Structure

```
f1_analytics/
├── data/
│   └── 2025/
│       ├── snapshots/          # Price snapshots
│       │   └── 2025-11-10-drivers.csv
│       └── outcomes/           # Performance data
│           └── 2025-11-10-all-drivers-performance.csv
├── analytics/
│   ├── models.py              # Database models
│   └── management/
│       └── commands/
│           ├── import_fantasy_prices.py
│           └── import_driver_performance.py  # New!
└── docs/
    ├── ML_MODEL_DESIGN.md     # ML/RL design rationale
    └── SETUP_PERFORMANCE_IMPORT.md  # This file
```
