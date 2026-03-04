# F1 Analytics

A Django app for tracking F1 performance and optimising a Fantasy Formula 1 lineup. The goal is to derive algorithms that make decisions on which constructors and drivers to pick each race week.

## Overview

Data flows in from two sources:

- **F1 Fantasy Game** — CSV exports via a Chrome extension (driver/constructor prices, fantasy scores, and performance breakdowns)
- **FastF1 API** — race telemetry, lap data, weather, pit stops, and circuit geometry

The Django app lives in `f1_analytics/`. The Chrome extension that scrapes the F1 Fantasy website lives in `chrome_extension/f1-fantasy-exporter/`.

---

## Setup

```bash
cd f1_analytics
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Add SLACK_WEBHOOK_URL to .env

python manage.py migrate
python manage.py runserver
```

---

## Architecture

### Data Models (`analytics/models/`)

Models are split across multiple files:

| File | Contents |
|------|----------|
| `base.py` | `User`, `Season`, `Team`, `Driver`, `CurrentLineup` |
| `events.py` | `Circuit`, `Corner`, `Race`, `Session`, `SessionWeather`, `SessionResult` |
| `fantasy.py` | `DriverSnapshot`, `ConstructorSnapshot`, `DriverRacePerformance`, `DriverEventScore`, `ConstructorRacePerformance`, `ConstructorEventScore` |
| `telemetry.py` | `Lap`, `Telemetry`, `PitStop` |
| `pipeline.py` | `SessionLoadStatus` |

### FastF1 Import Pipeline (`analytics/flows/`)

Orchestrated with **Prefect**. Entry point is `import_fastf1_flow` in `flows/import_fastf1.py`.

**Session-once-extract-many pattern:** each FastF1 session is loaded exactly once per run, then all data types (weather, circuit geometry, driver results, lap telemetry, pit stops) are extracted from that single session object. This is critical because FastF1 enforces a 200 API calls/hour limit.

### Views and URLs

| URL | View | Purpose |
|-----|------|---------|
| `/` | `dashboard` | Current lineup with latest prices, price changes, points-per-million |
| `/lineup/edit/` | `edit_lineup` | Form to select 5 drivers + 2 teams |
| `/data/` | `data_status` | Data coverage gaps by session |
| `/admin/` | Django admin | |

---

## Management Commands

All commands run from `f1_analytics/` with the virtualenv activated:

```bash
source venv/bin/activate
cd f1_analytics
```

---

### Data Collection

#### `collect_all_data` — Primary import orchestrator

The main command for backfilling or refreshing FastF1 data across multiple seasons.

```bash
python manage.py collect_all_data                              # 2018 to current year
python manage.py collect_all_data --start-year 2025           # 2025 to current year
python manage.py collect_all_data --start-year 2024 --end-year 2024  # single year
python manage.py collect_all_data --dry-run                   # show gaps, no import
python manage.py collect_all_data --notify                    # send Slack summary on finish
python manage.py collect_all_data --force                     # re-import existing data
```

**When to use:** Regular data refresh (weekly, or after a race weekend). Safe to re-run — gap detection skips sessions that already have complete data. If it exits with `RATE LIMITED`, just re-run; it will resume from where it left off. Rate limit retries are automatic: up to 8 attempts, 1 hour apart.

---

#### `import_fastf1` — Single-season FastF1 import

Imports weather, circuit geometry, driver results, and telemetry for one season (or one specific round).

```bash
python manage.py import_fastf1 --year 2025
python manage.py import_fastf1 --year 2025 --round 5
python manage.py import_fastf1 --year 2025 --dry-run
python manage.py import_fastf1 --year 2025 --round 5 --force
python manage.py import_fastf1 --year 2025 --notify
```

**When to use:** When you want to target a single season rather than the full backfill. `collect_all_data` calls this internally, but running it directly gives you finer control (e.g., re-importing a specific round).

---

#### `import_schedule` — Import race calendar

Populates the Season, Race, Session, and optionally Circuit records from FastF1's event schedule.

```bash
python manage.py import_schedule --year 2025
python manage.py import_schedule --year 2025 --with-circuits   # also download circuit geometry
python manage.py import_schedule --year 2025 --event 5         # single round only
python manage.py import_schedule --year 2025 --force
```

**When to use:** At the start of each season, or when rounds are added/changed. Must be run before `import_fastf1` or `collect_all_data` — those commands require races and sessions to exist in the DB first. `--with-circuits` downloads full telemetry per session to get geometry, so only use it when needed (it's expensive).

---

#### `import_fantasy_prices` — Import F1 Fantasy price snapshots

Imports driver and constructor price/ownership snapshots exported from the Fantasy website via the Chrome extension. Place CSV files in `data/{year}/snapshots/` before running.

```bash
python manage.py import_fantasy_prices                    # use today's date
python manage.py import_fantasy_prices --date 2025-03-14  # specific snapshot date
```

**When to use:** After exporting a snapshot from the Fantasy website. Run weekly or before each race to track price changes and ownership shifts.

---

#### `import_driver_performance` — Import driver fantasy performance

Imports per-race driver performance breakdowns from the Fantasy game CSV exports. Place files in `data/{year}/outcomes/`.

```bash
python manage.py import_driver_performance
python manage.py import_driver_performance --year 2024
python manage.py import_driver_performance --file data/2025/outcomes/2025-03-14-all-drivers-performance.csv
```

**When to use:** After each race weekend, once the Fantasy site has published scoring breakdowns.

---

#### `import_constructor_performance` — Import constructor fantasy performance

Same as above but for constructors.

```bash
python manage.py import_constructor_performance
python manage.py import_constructor_performance --year 2024
python manage.py import_constructor_performance --file data/2025/outcomes/2025-03-14-all-constructors-performance.csv
```

---

### Monitoring

#### `data_status` — Check data coverage (read-only)

Shows how much data has been collected, by year and session. No imports triggered.

```bash
python manage.py data_status                # summary table across all years
python manage.py data_status --year 2025    # per-session breakdown for one year
```

**When to use:** Before running an import to understand what's missing, or to verify a completed import. Faster than `--dry-run` because it queries the DB directly without loading any Prefect flows.

---

#### `check_driver_integrity` — Audit driver data

Identifies drivers with missing FastF1 identifiers (driver number, abbreviation) that would prevent telemetry imports, and flags potential duplicate drivers.

```bash
python manage.py check_driver_integrity
python manage.py check_driver_integrity --verbose
```

**When to use:** When telemetry imports silently skip drivers, or after importing a new season's schedule when new drivers join the grid.

---

### Lineup Optimisation

#### `optimize_lineup` — Find the best Fantasy lineup

Runs a dynamic programming (knapsack) algorithm over current driver/constructor prices and points-per-million scores to recommend the optimal 5 drivers + 2 constructors within budget.

```bash
python manage.py optimize_lineup                  # use budget from current lineup
python manage.py optimize_lineup --budget 100     # specify budget in millions
python manage.py optimize_lineup --save           # save result to current lineup
```

**When to use:** Before each race's transfer deadline.

---

### Backup and Restore

#### `backup_db` — Backup the database

Creates a timestamped, compressed backup of the SQLite database and `data/` directory. Safe to run while the server is running (uses SQLite's online backup API).

```bash
python manage.py backup_db
python manage.py backup_db --keep 20    # retain 20 most-recent backups
python manage.py backup_db --tag pre-import
```

**When to use:** Before any large import (`collect_all_data`, `--force` runs), or on a regular schedule.

---

#### `restore_db` — Restore from a backup

```bash
python manage.py restore_db --list                          # see available backups
python manage.py restore_db                                  # interactive restore (most recent)
python manage.py restore_db --backup db_2025-03-14_09-00-00
python manage.py restore_db --restore-data                   # also restore data/ directory
python manage.py restore_db --yes                            # skip confirmation prompt
```

**When to use:** After a bad import, or to roll back to a known-good state.

---

### Development / Debugging

#### `test_slack_notifications` — Verify Slack webhook

Sends test notifications for all notification types (rate limit pause/resume, import completion) without running any real import.

```bash
python manage.py test_slack_notifications
python manage.py test_slack_notifications --completion
python manage.py test_slack_notifications --pause
python manage.py test_slack_notifications --resume
```

#### `test_rate_limit_notifications` — Verify rate limit Slack messages

```bash
python manage.py test_rate_limit_notifications
python manage.py test_rate_limit_notifications --pause
python manage.py test_rate_limit_notifications --resume
```

#### `test_telemetry_import` — Debug telemetry extraction for one session

Loads a specific session from FastF1, extracts driver and lap data, and prints a summary. Useful when diagnosing extraction failures without running the full pipeline.

```bash
python manage.py test_telemetry_import --year 2025 --round 1
python manage.py test_telemetry_import --year 2025 --round 1 --session Qualifying
```

---

## Typical Workflows

### Season start (new year)

```bash
python manage.py backup_db --tag season-start
python manage.py import_schedule --year 2026
python manage.py collect_all_data --start-year 2026 --notify
```

### After a race weekend

```bash
# Import Fantasy game data (after exporting CSVs with the Chrome extension)
python manage.py import_fantasy_prices
python manage.py import_driver_performance
python manage.py import_constructor_performance

# Fetch telemetry for the new race
python manage.py collect_all_data --start-year 2025 --notify

# Check what came in
python manage.py data_status --year 2025

# Pick your team
python manage.py optimize_lineup --save
```

### Check coverage without importing

```bash
python manage.py data_status
python manage.py data_status --year 2024
```

---

## Testing

```bash
python manage.py test
python manage.py test --verbosity=2 --keepdb
python manage.py test analytics.tests.test_performance_import_utils
```

**Coverage:**

```bash
coverage run --source='analytics' manage.py test analytics
coverage report
coverage html   # generates htmlcov/index.html
```
