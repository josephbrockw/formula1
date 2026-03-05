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

A Chrome extension (v1.3) that scrapes the F1 Fantasy website and exports data as CSV files.

### Installation

1. Go to `chrome://extensions/` and enable **Developer mode**
2. Click **Load unpacked** and select `chrome_extension/f1-fantasy-exporter/`
3. Pin the extension icon to your toolbar

### Exports

**Prices snapshot** — navigate to the F1 Fantasy driver/constructor list, click the extension icon, then:
- **Export Drivers** — downloads `YYYY-MM-DD-drivers.csv`
- **Export Constructors** — switch to the Constructors tab first, then **Export Constructors** — downloads `YYYY-MM-DD-constructors.csv`

Fields: name, team, % picked, season points, current value, price change.

**Performance data** — navigate to the Drivers tab, click **Export Performance Data**. The extension automatically clicks through every driver card, then every constructor card (~2–3 minutes), and downloads:
- `YYYY-MM-DD-all-drivers-performance.csv`
- `YYYY-MM-DD-all-constructors-performance.csv`

Fields: driver/constructor, race, event type (qualifying / sprint / race), scoring item, frequency, position, points, race total, season total.

### CSV samples

```
# Prices
Driver Name,Team,% Picked,Season Points,Current Value,Price Change
Lando Norris,McLaren,22.00,614,$30.4M,-$0.1M

# Performance
Driver Name,Team,Driver Value,Race,Event Type,Scoring Item,Frequency,Position,Points,Race Total,Season Total
Lando Norris,McLaren,$30.4M,Australia,qualifying,Qualifying Position,,1,10,59,614
Lando Norris,McLaren,$30.4M,Australia,race,Race Overtake Bonus,5,,5,59,614
```
