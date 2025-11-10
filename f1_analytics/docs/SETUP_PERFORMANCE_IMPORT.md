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
- `analytics_driverraceperformance` - Aggregated driver race performances
- `analytics_driveventscore` - Detailed driver scoring items
- `analytics_constructorraceperformance` - Aggregated constructor race performances
- `analytics_constructoreventscore` - Detailed constructor scoring items

### 2. Export Performance Data

Use the Chrome extension to export performance data:

1. Navigate to the F1 Fantasy drivers list page
2. Click the extension icon
3. Click "Export Performance Data"
4. Wait for the automation to complete (2-3 minutes)
5. Two CSV files will be automatically downloaded

**Expected filenames:**
- `YYYY-MM-DD-all-drivers-performance.csv`
- `YYYY-MM-DD-all-constructors-performance.csv`

Save both files to: `f1_analytics/data/2025/outcomes/`

### 3. Import Performance Data

Run both import commands:

```bash
# Import driver performance (most recent file automatically)
python manage.py import_driver_performance

# Import constructor performance (most recent file automatically)
python manage.py import_constructor_performance

# Or specify specific files
python manage.py import_driver_performance --file /path/to/2025-11-10-all-drivers-performance.csv
python manage.py import_constructor_performance --file /path/to/2025-11-10-all-constructors-performance.csv

# Or specify a different year
python manage.py import_driver_performance --year 2024
python manage.py import_constructor_performance --year 2024
```

**Expected output (drivers):**
```
Found season: 2025 Season
Found performance file: 2025-11-10-all-drivers-performance.csv
Processing 460 driver-race combinations...
  Processed 20 performances...
  ...

Import complete!
  Races created/updated: 20
  Driver performances: 460
  Event scores: 4,600
```

**Expected output (constructors):**
```
Found season: 2025 Season
Found performance file: 2025-11-10-all-constructors-performance.csv
Processing 200 constructor-race combinations...
  ...

Import complete!
  Races created/updated: 0 (already exist from driver import)
  Constructor performances: 200
  Event scores: 1,600
```

---

## Data Flow

```
┌──────────────────────────────────────────┐
│  F1 Fantasy Website                      │
│  https://fantasy.formula1.com           │
└─────────────────┬────────────────────────┘
                  │
                  │ Chrome Extension
                  │ (automated scraping)
                  ▼
┌──────────────────────────────────────────┐
│  CSV Files                               │
│  data/2025/outcomes/                     │
│  - 2025-11-10-all-drivers-perf.csv      │
│  - 2025-11-10-all-constructors-perf.csv│
└──────────┬──────────────┬────────────────┘
           │              │
           │              │ import commands
           │              │
           ▼              ▼
┌──────────────────────────────────────────┐
│  Database Tables                         │
│  - analytics_race (shared)               │
│  - analytics_driverraceperformance      │
│  - analytics_driveventscore             │
│  - analytics_constructorraceperformance │
│  - analytics_constructoreventscore      │
└──────────────────────────────────────────┘
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
- **Purpose**: Detailed scoring breakdown for drivers
- **Key Fields**: performance (FK), event_type, scoring_item, points
- **Example**: "Lando Norris - Bahrain - qualifying: Qualifying Position (10 pts)"
- **ML Use**: Event-specific analysis, pattern detection

### ConstructorRacePerformance Table
- **Purpose**: Aggregate performance per constructor per race
- **Key Fields**: team, race, total_points, fantasy_price
- **Example**: "McLaren - Bahrain (95 pts)"
- **ML Use**: Time-series features, rolling averages, team consistency

### ConstructorEventScore Table
- **Purpose**: Detailed scoring breakdown for constructors
- **Key Fields**: performance (FK), event_type, scoring_item, points
- **Example**: "McLaren - Bahrain - race: Pitstop Bonus (2 pts)"
- **ML Use**: Constructor-specific analysis, pitstop efficiency patterns

---

## Verify Import

### Django Shell
```bash
python manage.py shell
```

```python
from analytics.models import (
    Race, DriverRacePerformance, DriverEventScore,
    ConstructorRacePerformance, ConstructorEventScore
)

# Check races imported
print(f"Races: {Race.objects.count()}")
print(Race.objects.first())

# Check driver performances
print(f"\nDriver Performances: {DriverRacePerformance.objects.count()}")
d_perf = DriverRacePerformance.objects.first()
print(f"{d_perf.driver.full_name} - {d_perf.race.name}: {d_perf.total_points} pts")

# Check driver event scores
print(f"Driver Event Scores: {DriverEventScore.objects.count()}")

# Check constructor performances
print(f"\nConstructor Performances: {ConstructorRacePerformance.objects.count()}")
c_perf = ConstructorRacePerformance.objects.first()
print(f"{c_perf.team.name} - {c_perf.race.name}: {c_perf.total_points} pts")

# Check constructor event scores
print(f"Constructor Event Scores: {ConstructorEventScore.objects.count()}")
```

### Django Admin
Navigate to: http://localhost:8000/admin/

You should see new sections:
- **Races**
- **Driver Race Performances** (with inline event scores)
- **Driver Event Scores**
- **Constructor Race Performances** (with inline event scores)
- **Constructor Event Scores**

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
1. Export latest performance data (extension exports both CSVs)
2. Run `python manage.py import_driver_performance`
3. Run `python manage.py import_constructor_performance`
4. Both imports are idempotent - safe to run multiple times

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
