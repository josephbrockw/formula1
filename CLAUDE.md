# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

F1 Analytics is a Django app for tracking F1 performance and calculating the best strategy for fantasy formula 1. The goal is train or derive algorithms that will make decisions on which constructors and drivers to pick to win my fantasy f1 league. The rules for fantasy formula 1 are (here)[https://fantasy.formula1.com/en/game-rules].

It pulls data from two sources:
1. **F1 Fantasy Game** — CSV exports via a Chrome extension (driver/constructor prices and fantasy scores)
2. **FastF1 API** — race telemetry, lap data, weather, and circuit geometry

The Django app lives in `f1_analytics/`. The Chrome extension that scrapes the F1 Fantasy website lives in `chrome_extension/f1-fantasy-exporter/`.

## Commands

All commands run from `f1_analytics/` with the virtualenv activated:

```bash
source venv/bin/activate
cd f1_analytics
```

**Run the dev server:**
```bash
python manage.py runserver
```

**Run all tests:**
```bash
python manage.py test
```

**Run a specific test module, class, or method:**
```bash
python manage.py test analytics.tests.test_performance_import_utils
python manage.py test analytics.tests.test_performance_import_utils.ParseFantasyPriceTests
python manage.py test analytics.tests.test_performance_import_utils.ParseFantasyPriceTests.test_parses_standard_price_format
```

**Useful test flags:**
```bash
python manage.py test --verbosity=2 --keepdb   # show names, reuse DB for speed
python manage.py test --parallel --failfast
```

**Coverage:**
```bash
coverage run --source='analytics' manage.py test analytics
coverage report
coverage html   # generates htmlcov/index.html
```

**Django shell (for running imports interactively):**
```bash
python manage.py shell
```

**Migrations:**
```bash
python manage.py migrate
python manage.py makemigrations
```

There is no configured linter or formatter in this project.

## Architecture

### Data Models (`analytics/models/`)

Models are split across multiple files — not a single `models.py`:

- `base.py` — Core entities: `User` (custom auth model), `Season`, `Team`, `Driver`, `CurrentLineup`
- `events.py` — Race structure: `Circuit`, `Corner`, `Race`, `Session`, `SessionWeather`, `SessionResult`
- `fantasy.py` — F1 Fantasy game data: `DriverSnapshot`, `ConstructorSnapshot`, `DriverRacePerformance`, `DriverEventScore`, `ConstructorRacePerformance`, `ConstructorEventScore`
- `telemetry.py` — FastF1 data: `Lap`, `Telemetry`, `PitStop`
- `pipeline.py` — Import state tracking: `SessionLoadStatus`

### FastF1 Import Pipeline (`analytics/flows/`)

The core workflow uses **Prefect** for orchestration. The master entry point is `import_fastf1_flow` in `flows/import_fastf1.py`.

**Session-Once-Extract-Many pattern:** Each FastF1 session is loaded exactly once per run, then all data types (weather, circuit, telemetry, drivers, pit stops) are extracted from that single session object. This is critical because FastF1 enforces a 200 API calls/hour limit.

Flow execution order:
1. `gap_detection.py` — Scans DB to find sessions with missing data, returns `GapReport` with `SessionGap` dataclasses
2. `rate_limiter.py` — Checks remaining API quota before proceeding
3. Master flow builds a processing plan, then iterates sessions
4. For each session: load once via `loaders.py`, then call sub-flows (`import_weather`, `import_circuit`, `import_telemetry`, `import_drivers`, `extract_pit_stops`)
5. `config/notifications.py` — Sends Slack summary on completion

To run an import in the Django shell:
```python
import asyncio
from analytics.flows.import_fastf1 import import_fastf1_flow
asyncio.run(import_fastf1_flow(year=2024, notify=True))
```

### Views and URLs

URL routes (`config/urls.py`):
- `/` → `dashboard` — current lineup with latest prices, price changes, points-per-million
- `/lineup/edit/` → `edit_lineup` — form to select 5 drivers + 2 teams
- `/data/` and `/data/<year>/` → `data_status` — data coverage gaps by session
- `/admin/` → Django admin

### Configuration

- `config/settings.py` — Standard Django settings. Key custom values:
  - `AUTH_USER_MODEL = 'analytics.User'`
  - `SLACK_WEBHOOK_URL` — loaded from `.env`
  - `FASTF1_TASK_RETRIES = 3` and `FASTF1_TASK_RETRY_DELAY = 60` — Prefect task retry config; tests override these via `task.with_options()`
- `.env` — Only `SLACK_WEBHOOK_URL` is required (see `.env.example`)

### Tests (`analytics/tests/`)

Tests use Django's built-in test framework (unittest). Test fixtures (CSV files) are in `analytics/tests/fixtures/`. Integration tests for driver/constructor performance import use `TestCase` with a real SQLite test database. Prefect task retries are overridden in tests for speed.
