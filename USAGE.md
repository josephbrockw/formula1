# F1 Fantasy System — Usage Guide

How to operate this system week-to-week during a live season. All commands run from `f1_data/`.

---

## New Season Initialisation

Run once before the season starts (or as soon as the new driver grid is confirmed).

### 1. Sync the season schedule

```bash
python manage.py collect_data --year YYYY
```

This pulls the race calendar from FastF1 and creates `Season`, `Event`, and `Session` records. No driver or team data yet — those come from race results.

Check how many events were created:
```bash
python manage.py shell -c "from core.models import Event; print(Event.objects.filter(season__year=YYYY).count())"
```

### 2. Seed driver and team reference data

FastF1 only creates driver/team records when race results are available. Pre-season, you need to seed them manually from a roster file.

Create `data/YYYY_roster.json` (copy `data/2026_roster.json` as a template):
```json
{
  "season": YYYY,
  "teams": [
    {"name": "Mercedes", "fastf1_name": "Mercedes"},
    ...
  ],
  "drivers": [
    {"code": "RUS", "full_name": "George Russell", "fastf1_full_name": "George Russell", "driver_number": 63, "team": "Mercedes"},
    ...
  ]
}
```

**`name`** — the label used in the drivers list and fantasy CSVs.
**`fastf1_name`** — what FastF1 returns as `TeamName` in results. This is what gets stored as `Team.name` in the DB. If you don't know it yet (new teams), set it equal to `name` and update after the first race.
**`fastf1_full_name`** — FastF1's `FullName` for a driver. Only needed for reference; `full_name` is what's stored.

Then seed:
```bash
python manage.py seed_season_reference --year YYYY --roster ../data/YYYY_roster.json
```

Verify:
```bash
python manage.py shell -c "
from core.models import Driver, Team
print('Teams:', list(Team.objects.filter(season__year=YYYY).values_list('name', flat=True)))
print('Drivers:', list(Driver.objects.filter(season__year=YYYY).values_list('code', flat=True)))
"
```

### 3. Check FastF1 team names after the first race

After the first race, run `collect_data` again (step 5 in the weekly workflow below). Check whether FastF1 used the same team names you seeded:

```bash
python manage.py shell -c "from core.models import Team; print(list(Team.objects.filter(season__year=YYYY).values_list('name', flat=True)))"
```

If you see unexpected duplicates (e.g. both `"Cadillac"` and `"Cadillac F1 Team"`), update the `fastf1_name` field in `data/YYYY_roster.json` for any mismatches, then re-run the seed:

```bash
python manage.py seed_season_reference --year YYYY --roster ../data/YYYY_roster.json
```

This renames the existing team row in-place — all driver and price foreign keys stay valid, no manual cleanup needed.

### 4. Set starting prices

Create `data/starting_prices/YYYY_drivers.csv` and `data/starting_prices/YYYY_constructors.csv` with the opening prices from the fantasy game:

```
# YYYY_drivers.csv (no header)
NOR,27.2
VER,27.7
...

# YYYY_constructors.csv (no header, team name must match Team.name in DB)
McLaren,28.9
Red Bull Racing,28.2
...
```

Then initialise the price records for all events:
```bash
python manage.py compute_fantasy_prices \
  --year YYYY \
  --driver-prices ../data/starting_prices/YYYY_drivers.csv \
  --constructor-prices ../data/starting_prices/YYYY_constructors.csv
```

This simulates the F1 Fantasy price formula forward from the starting prices. Prices will be overwritten with real values as you import Chrome extension CSVs each week.

Verify:
```bash
python manage.py shell -c "
from predictions.models import FantasyDriverPrice
print(FantasyDriverPrice.objects.filter(event__season__year=YYYY).count())
# Should be roughly: num_drivers × num_events
"
```

---

## Weekly Race Workflow

### Before the race (after qualifying locks)

```bash
# 1. Get ML recommendation for this round
python manage.py next_race --year YYYY --round N
```

This trains on all available data, generates predictions, and recommends a lineup considering your current team and banked transfers.

---

### After the race

Run these steps in order.

```bash
# 1. Pull FastF1 results (lap data, session results, weather)
python manage.py collect_data --year YYYY

# 2. Import Chrome extension CSVs (actual fantasy scores + prices)
#    Export from the Chrome extension and drop files in data/YYYY/
python manage.py import_fantasy_csv --dir ../data/YYYY/

# 3. Record the lineup you actually submitted
python manage.py record_my_lineup \
  --year YYYY --round N \
  --drivers NOR PIA LEC HAM RUS \
  --drs NOR \
  --constructors McLaren Ferrari

# 4. Plan for the next race
python manage.py next_race --year YYYY --round N+1
```

#### Chrome extension CSV file naming

The import command detects file type by filename:

| Filename pattern | Imports |
|---|---|
| `YYYY-MM-DD-drivers.csv` | Driver prices |
| `YYYY-MM-DD-constructors.csv` | Constructor prices |
| `YYYY-MM-DD-all-drivers-performance.csv` | Driver fantasy scores |
| `YYYY-MM-DD-all-constructors-performance.csv` | Constructor fantasy scores |

---

## Backtest (evaluate model accuracy)

Run a walk-forward backtest over historical seasons to check how the model would have performed:

```bash
python manage.py backtest --seasons 2023 2024 2025 --min-train 5
```

Options:
- `--feature-store v1|v2` (default: v2)
- `--predictor v1|v2` (default: v2)
- `--budget 100` (default: 100)

Output includes per-race MAE, actual lineup points, oracle optimal points, and number of transfers made.
