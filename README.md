# F1 Analytics

A project for tracking F1 performance and optimising a Fantasy Formula 1 lineup. The goal is to derive algorithms that make decisions on which constructors and drivers to pick each race week.

---

## Repository layout

```
f1_data/          New data collection pipeline (active development)
f1_analytics/     Original analytics app (web UI, lineup optimiser, fantasy imports)
chrome_extension/ Chrome extension for exporting F1 Fantasy CSV data
```

---

## f1_data — Data collection pipeline

A focused Django + SQLite + FastF1 pipeline that collects historical and live session data (laps, results, weather) for downstream ML/RL use.

### Setup

```bash
cd f1_data
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
```

### Data models

| Model | Description |
|-------|-------------|
| `Season` | A championship year |
| `Circuit` | Track metadata |
| `Event` | A race weekend (round within a season) |
| `Session` | A single session within an event (FP1–FP3, Q, SQ, S, R) |
| `Driver` | Driver per season |
| `Team` | Constructor per season |
| `Lap` | Per-lap data: lap time, sectors, compound, stint, pit, position |
| `SessionResult` | Finishing position, points, grid, status per driver per session |
| `WeatherSample` | Weather readings at 5-minute intervals per session |
| `CollectionRun` | Audit record for each `collect_data` invocation |
| `SessionCollectionStatus` | Per-session collection state: pending / collecting / completed / failed |

### Data flow

```
FastF1 API
  └── fastf1_loader.py   (thin wrapper — only place FastF1 is imported)
        └── data_mappers.py    (transform DataFrames → model instances)
              └── collect_season.py  (orchestrate, write to DB, track status)
```

### Management commands

#### `collect_data` — Collect session data from FastF1

```bash
python manage.py collect_data                          # all seasons (2018–present)
python manage.py collect_data --year 2025              # one season
python manage.py collect_data --year 2025 --round 5   # one round
python manage.py collect_data --retry-failed           # also attempt previously failed sessions
python manage.py collect_data --force                  # re-collect completed sessions too
```

By default, completed and failed sessions are skipped. Use `--retry-failed` to retry failures without touching completed sessions. Use `--force` to recollect everything.

Rate limits are handled automatically with exponential backoff (1 min → 5 min → 60 min). A Slack notification is sent on completion and when rate-limited.

#### `collection_status` — Check data coverage

```bash
python manage.py collection_status                     # summary table across all seasons
python manage.py collection_status --year 2025         # per-session breakdown for one season
python manage.py collection_status --gaps              # show only incomplete sessions
python manage.py collection_status --year 2025 --gaps  # gaps for one season
```

### Running tests

```bash
cd f1_data
python manage.py test                                          # full suite
python manage.py test core.tests.test_gap_detector            # one module
```

---

## f1_analytics — Analytics app

The original Django app with a web UI, lineup optimiser, fantasy data imports, and backup tooling. See `f1_analytics/` for its own setup and commands (documented in the old README history).

---

## chrome_extension — F1 Fantasy exporter

A Chrome extension that scrapes the F1 Fantasy website and exports driver/constructor prices, ownership percentages, and performance breakdowns as CSV files. See `chrome_extension/f1-fantasy-exporter/README.md`.
