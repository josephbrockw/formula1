# F1 Fantasy Data Collection System — Implementation Plan

## Overview

A fresh Django project that collects all structured data from FastF1 (2018–present) and stores it in SQLite. Telemetry (4Hz car data) is explicitly deferred. The system uses Django management commands for collection, Slack webhooks for notifications, and FastF1's persistent file cache to minimize API requests.

---

## Core Tenets

1. **Less code is preferred.** Keep it simple. Remove dead code. No abstractions until they're needed twice.
2. **Small focused functions with strong unit tests.** Every function that contains logic gets tested. Tests never make external API calls — mock everything.
3. **Quiet terminal output.** Management commands print only: where we are in the process, what event we're currently working on, and catastrophic errors (with stack trace). No per-lap logging, no verbose output. Progress bars are okay if they are restricted to a single line of output.
4. **No wasted API requests.** Use FastF1's file cache. Track what's been collected. Detect gaps. Resume cleanly.

---

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Database | SQLite | Dataset is <1GB without telemetry. No server overhead. Django native support. |
| Telemetry | Deferred | 150M+ rows, 30-50GB. Not needed for the world model's lap-level inputs. Add later with Parquet. |
| FastF1 cache | Persistent local disk | Once cached, subsequent loads are free. Biggest rate-limit saver. |
| Slack | Webhook (one-way) | Simple POST to a URL. Sufficient for status reports and alerts. |
| Orchestration | Django management commands | No Prefect. Code organized into tasks/flows as a structural pattern, not a framework dependency. |
| History | 2018–present | FastF1 reliability boundary. |

---

## Data We're Collecting from FastF1

For each session (Practice 1, Practice 2, Practice 3, Sprint Qualifying, Sprint, Qualifying, Race) within each race weekend, collect:

### Session-Level Data
- Session metadata (event name, session type, date, circuit, status)
- Weather data (air temp, track temp, humidity, pressure, wind speed/direction, rainfall per timestamp)
- Session results (finishing order, grid position, status, points, fastest lap info)

### Lap-Level Data
- Lap number, driver, lap time, sector 1/2/3 times
- Pit in/out laps, pit duration
- Stint number, tire compound, tire life (laps on current set)
- Track status per lap (green, yellow, red, SC, VSC)
- Position, gap to leader, gap to car ahead
- Is personal best, is overall best

### Driver & Team Reference Data
- Driver info (code, name, number, team)
- Team/constructor info
- These change season to season (driver transfers, team name changes)

### Circuit Data
- Circuit key, name, country, location, length
- Number of corners, DRS zones (if available from FastF1)

### NOT Collecting (Deferred)
- Car telemetry (speed, throttle, brake, DRS, gear at ~4Hz)
- Position data (x, y, z coordinates)

---

## Django Models

### Reference Models

```
Season
  - year (int, unique, primary key-like)

Circuit
  - circuit_key (str, from FastF1)
  - name (str)
  - country (str)
  - city (str)  
  - circuit_length (float, meters, nullable)
  - total_corners (int, nullable)

Team
  - season (FK → Season)
  - name (str)
  - full_name (str)
  - unique_together: (season, name)

Driver
  - season (FK → Season)
  - code (str, e.g. "VER")
  - full_name (str)
  - driver_number (int)
  - team (FK → Team)
  - unique_together: (season, code)
```

### Event Models

```
Event
  - season (FK → Season)
  - round_number (int)
  - event_name (str)
  - country (str)
  - circuit (FK → Circuit)
  - event_date (date)
  - event_format (str: "conventional", "sprint_shootout", "sprint_qualifying", etc.)
  - unique_together: (season, round_number)

Session
  - event (FK → Event)
  - session_type (str: "FP1", "FP2", "FP3", "Q", "SQ", "S", "R")
  - date (datetime)
  - unique_together: (event, session_type)
```

### Result Models

```
SessionResult
  - session (FK → Session)
  - driver (FK → Driver)
  - team (FK → Team)
  - position (int, nullable — DNFs etc.)
  - classified_position (str — accounts for "R" retired, "D" disqualified, etc.)
  - grid_position (int)
  - status (str — "Finished", "+1 Lap", "Engine", "Collision", etc.)
  - points (float)
  - time (duration, nullable)
  - fastest_lap_rank (int, nullable)
  - unique_together: (session, driver)
```

### Lap Models

```
Lap
  - session (FK → Session)
  - driver (FK → Driver)
  - lap_number (int)
  - lap_time (duration, nullable — in/out laps may be null)
  - sector1_time (duration, nullable)
  - sector2_time (duration, nullable)
  - sector3_time (duration, nullable)
  - pit_in_time (duration, nullable)
  - pit_out_time (duration, nullable)
  - is_pit_in_lap (bool)
  - is_pit_out_lap (bool)
  - stint (int)
  - compound (str: "SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET", nullable)
  - tyre_life (int, nullable — laps on current set)
  - track_status (str — green/yellow/SC/VSC/red flags)
  - position (int)
  - is_personal_best (bool)
  - is_accurate (bool — FastF1's accuracy flag)
  - unique_together: (session, driver, lap_number)
```

### Weather Models

```
WeatherSample  
  - session (FK → Session)
  - timestamp (datetime)
  - air_temp (float)
  - track_temp (float)
  - humidity (float)
  - pressure (float)
  - wind_speed (float)
  - wind_direction (int)
  - rainfall (bool)
  - unique_together: (session, timestamp)
```

### Collection Tracking Models

```
CollectionRun
  - started_at (datetime, auto)
  - finished_at (datetime, nullable)
  - status (str: "running", "completed", "paused_rate_limit", "failed")
  - sessions_processed (int, default 0)
  - sessions_skipped (int, default 0)
  - error_message (text, nullable)

SessionCollectionStatus
  - session (OneToOne → Session)
  - status (str: "pending", "collecting", "completed", "failed")
  - collected_at (datetime, nullable)
  - lap_count (int, default 0)
  - weather_sample_count (int, default 0)
  - result_count (int, default 0)
  - error_message (text, nullable)
  - retry_count (int, default 0)
```

**Note on `SessionCollectionStatus`:** This is the gap detection mechanism. When resuming collection, query for sessions where status != "completed" to know exactly where to pick up. When generating status reports, aggregate these to show coverage.

---

## Project Structure

```
f1_data/
├── manage.py
├── f1_data/
│   ├── settings.py
│   ├── urls.py           # minimal, maybe just admin
│   └── wsgi.py
├── core/
│   ├── models.py         # all models above
│   ├── admin.py          # register models for browsing data
│   ├── migrations/
│   ├── tasks/            # focused logic functions
│   │   ├── __init__.py
│   │   ├── fastf1_loader.py    # functions that call FastF1 API (thin wrappers)
│   │   ├── data_mappers.py     # transform FastF1 objects → Django model instances
│   │   ├── gap_detector.py     # find uncollected/incomplete sessions
│   │   └── notifier.py         # Slack webhook helper
│   ├── flows/            # orchestration logic (sequences of tasks)
│   │   ├── __init__.py
│   │   └── collect_season.py   # main collection flow
│   ├── management/
│   │   └── commands/
│   │       ├── collect_data.py      # main entry point
│   │       └── collection_status.py # print gap/coverage report
│   └── tests/
│       ├── test_data_mappers.py
│       ├── test_gap_detector.py
│       ├── test_fastf1_loader.py
│       ├── test_notifier.py
│       ├── test_collect_season.py
│       └── factories.py    # test data factories
├── fastf1_cache/         # persistent cache directory (gitignored)
└── requirements.txt
```

### Why tasks/ and flows/ (not Prefect)

`tasks/` contains pure functions with single responsibilities. They take explicit inputs and return outputs. They are easy to test in isolation.

`flows/` contains orchestration: the order to call tasks, error handling, retry logic, progress tracking. A flow calls tasks, handles the sequencing, and manages state (writing to `SessionCollectionStatus`).

This gives us the organizational clarity of Prefect's task/flow model without the dependency. If we ever want to add Prefect back, the refactor is trivial — decorate existing functions.

---

## Key Implementation Details

### FastF1 Loader (`tasks/fastf1_loader.py`)

Thin wrappers around FastF1 calls. These are the **only** functions that touch the FastF1 API. Everything else works with the returned data objects.

```python
def get_event_schedule(year: int) -> fastf1.events.EventSchedule:
    """Fetch the event schedule for a season."""

def load_session(year: int, round_number: int, session_type: str) -> fastf1.core.Session:
    """Load a single session with laps, weather, and results.
    
    FastF1's cache handles deduplication. If already cached,
    this makes zero API requests.
    """
```

**Cache configuration:** Set `fastf1.Cache.enable_cache('fastf1_cache/')` once at module level or in Django settings. This directory persists between runs and is gitignored.

**Rate limit detection:** FastF1 raises specific exceptions on rate limiting. The loader functions should NOT handle rate limits — they should let the exception propagate up to the flow layer, which handles the pause/notify logic. Keep the loader functions simple.

### Data Mappers (`tasks/data_mappers.py`)

Pure functions that take FastF1 data objects and return Django model instances (unsaved). No database calls, no API calls — just data transformation. This is where most of the logic lives and where test coverage matters most.

```python
def map_session_results(session: fastf1.core.Session, session_model: Session) -> list[SessionResult]:
    """Map FastF1 session results to SessionResult instances."""

def map_laps(session: fastf1.core.Session, session_model: Session, driver_lookup: dict) -> list[Lap]:
    """Map FastF1 laps DataFrame to Lap instances."""

def map_weather(session: fastf1.core.Session, session_model: Session) -> list[WeatherSample]:
    """Map FastF1 weather DataFrame to WeatherSample instances."""
```

**Important:** FastF1 returns pandas DataFrames with NaN values, timedelta objects, and occasionally inconsistent dtypes. The mappers must handle this defensively — null checks, type coercion, and skipping rows with invalid data rather than crashing.

### Gap Detector (`tasks/gap_detector.py`)

```python
def find_uncollected_sessions(year: int = None) -> QuerySet:
    """Return sessions that haven't been successfully collected.
    
    A session is 'uncollected' if:
    - No SessionCollectionStatus exists for it, OR
    - Status is not 'completed'
    
    If year is None, checks all seasons.
    """

def get_collection_summary() -> dict:
    """Return a summary of collection status across all seasons.
    
    Returns dict like:
    {
        2024: {"total": 115, "completed": 110, "failed": 2, "pending": 3},
        2023: {"total": 112, "completed": 112, "failed": 0, "pending": 0},
        ...
    }
    """
```

### Notifier (`tasks/notifier.py`)

```python
SLACK_WEBHOOK_URL setting in Django settings (loaded from env var).

def send_slack_notification(message: str, level: str = "info") -> bool:
    """Send a message to Slack via webhook.
    
    level: "info", "warning", "error"
    Returns True if sent successfully, False otherwise.
    Failures are logged but never raise — notifications should
    never break the collection process.
    """
```

Message formatting: use Slack's block kit for structured messages. Include emoji prefix based on level (ℹ️, ⚠️, 🚨).

### Collection Flow (`flows/collect_season.py`)

This is the main orchestration logic. Pseudocode:

```
def collect_all(years=None, force_recollect=False):
    """Main collection flow."""
    
    create CollectionRun record (status="running")
    
    if years is None:
        years = range(2018, current_year + 1)
    
    for year in years:
        schedule = get_event_schedule(year)
        ensure Season, Circuit, Event records exist
        ensure Session records exist for all sessions in schedule
        
    # Now collect data for uncollected sessions
    uncollected = find_uncollected_sessions()
    
    if force_recollect:
        uncollected = Session.objects.all()  # or filtered by year
    
    total = uncollected.count()
    print(f"Sessions to collect: {total}")
    
    for i, session in enumerate(uncollected.order_by('event__season', 'event__round_number')):
        print(f"[{i+1}/{total}] {session.event.event_name} — {session.session_type}")
        
        try:
            collect_single_session(session)
        except RateLimitError:
            mark CollectionRun as "paused_rate_limit"
            send_slack_notification(
                f"🚨 Rate limited at {session}. "
                f"Progress: {i}/{total}. "
                f"Pausing 61 minutes.",
                level="warning"
            )
            sleep(61 * 60)
            # retry this session
            collect_single_session(session)
        except Exception as e:
            mark SessionCollectionStatus as "failed"
            print full stack trace (this is the catastrophic error case)
            send_slack_notification(f"🚨 Error: {session}: {e}", level="error")
            continue  # move to next session
    
    mark CollectionRun as "completed"
    send_slack_notification(summary report, level="info")


def collect_single_session(session_model):
    """Collect all data for one session."""
    
    mark SessionCollectionStatus as "collecting"
    
    ff1_session = load_session(year, round, type)
    
    results = map_session_results(ff1_session, session_model)
    laps = map_laps(ff1_session, session_model, driver_lookup)
    weather = map_weather(ff1_session, session_model)
    
    # Bulk create in a transaction
    with transaction.atomic():
        # Delete any existing data for this session (handles partial previous runs)
        SessionResult.objects.filter(session=session_model).delete()
        Lap.objects.filter(session=session_model).delete()
        WeatherSample.objects.filter(session=session_model).delete()
        
        SessionResult.objects.bulk_create(results)
        Lap.objects.bulk_create(laps)
        WeatherSample.objects.bulk_create(weather)
    
    update SessionCollectionStatus: completed, counts
```

**Key design choice:** `collect_single_session` deletes existing data before writing. This means re-running a failed session is idempotent — no duplicate data, no need to diff. The atomic transaction ensures we never have partial data.

### Rate Limit Handling

FastF1 uses the Ergast API and its own data sources. Rate limit errors surface as exceptions. The detection strategy:

1. Catch the specific FastF1/requests exception types that indicate rate limiting (HTTP 429, connection throttling)
2. Log the error, mark the run as paused, send Slack notification with progress summary
3. Sleep for 61 minutes (their rate limit window is 60 minutes)
4. Resume from the same session that triggered the limit

**Important:** The FastF1 cache means we'll only hit rate limits during the initial historical backfill. Once cached, re-runs are essentially free. The rate limit handling is critical for the first run but rarely triggered after that.

### Management Commands

#### `collect_data`

```
python manage.py collect_data                    # collect everything uncollected, 2018-present
python manage.py collect_data --year 2024        # collect only 2024
python manage.py collect_data --year 2024 --round 5   # collect only round 5 of 2024
python manage.py collect_data --force            # recollect even completed sessions
```

Terminal output example:
```
Starting collection. 847 sessions to process.
[1/847] 2018 Australian Grand Prix — FP1
[2/847] 2018 Australian Grand Prix — FP2
[3/847] 2018 Australian Grand Prix — FP3
...
[42/847] 2018 Chinese Grand Prix — R
⚠️  Rate limited. Slack notified. Pausing until 14:32 UTC.
[42/847] 2018 Chinese Grand Prix — R (retry)
...
Collection complete. 847 processed, 0 failed.
```

That's it. No per-lap output, no DataFrame shapes, no cache hit/miss logs.

#### `collection_status`

```
python manage.py collection_status               # print summary table
python manage.py collection_status --year 2024   # detail for one year
python manage.py collection_status --gaps         # show only incomplete sessions
```

Output example:
```
Season  | Total | Done | Failed | Pending
--------|-------|------|--------|--------
2018    |   105 |  105 |      0 |       0
2019    |   105 |  105 |      0 |       0
2020    |    89 |   89 |      0 |       0
2021    |   112 |  112 |      0 |       0
2022    |   112 |  112 |      0 |       0
2023    |   115 |  115 |      0 |       0
2024    |   120 |  117 |      1 |       2
2025    |    48 |   48 |      0 |       0
```

---

## Testing Strategy

### What Gets Mocked
- **All FastF1 calls.** Create fixture DataFrames that mirror FastF1's output structure. Store as JSON or build in test factories.
- **Slack webhook.** Mock `requests.post`. Assert the payload structure.
- **Time/sleep.** Mock `time.sleep` in rate limit tests so they run instantly.

### What Gets Tested (No Mocks Needed)
- **Data mappers.** These are pure functions. Feed in test DataFrames, assert correct model instances come out. Test edge cases: NaN lap times, missing sectors, DNF status codes, sprint weekends with different session types.
- **Gap detector.** Use Django's test DB. Create some SessionCollectionStatus records with various states, assert the gap detector finds the right ones.
- **Model constraints.** Test unique_together constraints, nullable fields, etc.

### Test Fixtures
Build a `factories.py` with functions that produce realistic FastF1-shaped DataFrames:

```python
def make_laps_dataframe(num_drivers=20, num_laps=57, include_pits=True, include_nans=False):
    """Build a DataFrame matching FastF1's laps structure."""

def make_results_dataframe(num_drivers=20, include_dnf=True):
    """Build a DataFrame matching FastF1's results structure."""

def make_weather_dataframe(num_samples=100, include_rain=False):
    """Build a DataFrame matching FastF1's weather structure."""
```

### Priority Test Cases
1. **Happy path:** Full race session maps correctly to all model instances
2. **Sprint weekend:** Different session types (SQ, S) are handled
3. **DNFs and DSQs:** Status codes map correctly, nullable positions handled
4. **Missing data:** NaN lap times, missing sectors, partial weather data
5. **Gap detection:** Correctly identifies pending, failed, and uncollected sessions
6. **Rate limit flow:** Exception triggers notification and pause (with mocked sleep)
7. **Idempotency:** Running collection on an already-completed session replaces data cleanly

---

## Build Order for Claude Code

Implement in this exact order so each step can be manually verified before proceeding.

### Step 1: Project Scaffolding
- `django-admin startproject f1_data`
- Create `core` app
- Configure SQLite, install FastF1
- `requirements.txt`: django, fastf1, requests
- Settings: FastF1 cache directory, Slack webhook URL (from env), basic logging config
- **Verify:** `python manage.py runserver` works

### Step 2: Models + Migrations
- Implement all models from the spec above
- Register all models in admin.py
- **Verify:** `python manage.py migrate` succeeds. Admin shows all models.

### Step 3: Data Mappers + Tests
- Implement `tasks/data_mappers.py` — all mapper functions
- Implement `tests/factories.py` — test DataFrame builders
- Implement `tests/test_data_mappers.py` — comprehensive tests
- **Verify:** `python manage.py test core.tests.test_data_mappers` — all pass

### Step 4: FastF1 Loader
- Implement `tasks/fastf1_loader.py` — thin wrappers
- Implement `tests/test_fastf1_loader.py` — mocked tests
- **Verify:** Tests pass. Manually test `load_session(2024, 1, 'R')` in Django shell to confirm cache works.

### Step 5: Gap Detector + Tests
- Implement `tasks/gap_detector.py`
- Implement `tests/test_gap_detector.py`
- **Verify:** Tests pass.

### Step 6: Notifier + Tests
- Implement `tasks/notifier.py`
- Implement `tests/test_notifier.py`
- **Verify:** Tests pass. Manually test with a real webhook URL to confirm message formatting.

### Step 7: Collection Flow
- Implement `flows/collect_season.py`
- Implement `tests/test_collect_season.py` — fully mocked integration tests
- **Verify:** Tests pass.

### Step 8: Management Commands
- Implement `collect_data` command
- Implement `collection_status` command
- **Verify:** Run `python manage.py collect_data --year 2024 --round 1` and manually verify data in admin. Run `python manage.py collection_status`.

### Step 9: Full Backfill Test
- Run `python manage.py collect_data --year 2024` for a full season
- Monitor output, verify gap detection works on interruption
- Check Slack notifications fire correctly
- **Verify:** `python manage.py collection_status` shows 2024 complete.

---

## Django Settings Notes

```python
# FastF1 cache
FASTF1_CACHE_DIR = os.path.join(BASE_DIR, 'fastf1_cache')

# Slack
SLACK_WEBHOOK_URL = os.environ.get('SLACK_WEBHOOK_URL', '')

# SQLite
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}
```

## Dependencies (requirements.txt)

```
django>=5.0
fastf1>=3.3
requests
```

Keep it minimal. No extra test frameworks (use Django's built-in). No extra ORMs. No task runners.
