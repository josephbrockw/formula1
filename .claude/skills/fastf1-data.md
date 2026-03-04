# FastF1 Data Handling Skill

## Cache Setup

```python
import fastf1
fastf1.Cache.enable_cache('/path/to/fastf1_cache')
```

Call once at module import or in Django settings/apps.py. The cache directory stores raw API responses. Once a session is cached, loading it makes zero network requests. This is the primary mechanism for avoiding rate limits.

## Loading Sessions

```python
session = fastf1.get_session(2024, 1, 'R')  # year, round, identifier
session.load()  # this is the network call (or cache hit)
```

Session identifiers: `'FP1'`, `'FP2'`, `'FP3'`, `'Q'`, `'S'` (sprint), `'SQ'` (sprint qualifying), `'R'` (race). These changed across seasons — sprint format has evolved. Use the event schedule to know which sessions exist.

**Important:** `session.load()` can accept kwargs to control what's loaded:

```python
session.load(laps=True, telemetry=False, weather=True, messages=False)
```

We want `telemetry=False` — this is the 4Hz car data we're deferring. Loading telemetry is the slowest and most rate-limit-prone operation. Always set it explicitly.

## Event Schedule

```python
schedule = fastf1.get_event_schedule(2024)
```

Returns a DataFrame with one row per event. Key columns:
- `RoundNumber` (int)
- `EventName` (str, e.g. "Australian Grand Prix")
- `Country` (str)
- `Location` (str, city)
- `EventDate` (Timestamp)
- `EventFormat` (str: "conventional", "sprint_shootout", "sprint_qualifying", "testing")
- `Session1` through `Session5` (str, session names)
- `Session1Date` through `Session5Date` (Timestamp)

**Gotcha:** Testing sessions (pre-season) appear in the schedule. Filter them out: `schedule = schedule[schedule['EventFormat'] != 'testing']`

**Gotcha:** The number of sessions per event varies. Conventional weekends have 5 (FP1, FP2, FP3, Q, R). Sprint weekends may have different combinations depending on the year. Use the `Session1`–`Session5` columns to determine which sessions exist for each event — don't hardcode session lists.

## Laps DataFrame Structure

`session.laps` returns a DataFrame. Key columns:

| Column | Type | Notes |
|--------|------|-------|
| `Driver` | str | Three-letter code: "VER", "HAM" |
| `DriverNumber` | str | Car number as string |
| `LapNumber` | int | |
| `LapTime` | timedelta / NaT | NaT for in-laps, out-laps, or missing |
| `Sector1Time` | timedelta / NaT | |
| `Sector2Time` | timedelta / NaT | |
| `Sector3Time` | timedelta / NaT | |
| `PitInTime` | timedelta / NaT | NaT if not a pit-in lap |
| `PitOutTime` | timedelta / NaT | NaT if not a pit-out lap |
| `Stint` | int | Stint number (resets after each pit stop) |
| `Compound` | str / NaN | "SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET", or "UNKNOWN" |
| `TyreLife` | float / NaN | Laps on current set. Float because NaN. |
| `Position` | float / NaN | Race position at end of lap. Float because NaN. |
| `TrackStatus` | str | Bitfield string: "1"=green, "2"=yellow, "4"=SC, "5"=red, "6"=VSC, etc. |
| `IsPersonalBest` | bool | |
| `IsAccurate` | bool | FastF1's internal accuracy flag. False if timing data is suspect. |

**Critical data quality issues:**
- `LapTime` is `NaT` (not-a-time, pandas null for timedelta) frequently — in-laps, out-laps, first laps after red flags
- `Position` is `float` because pandas stores int columns with NaN as float. Cast to `int` after filtering NaN.
- `TyreLife` same issue — float with NaN. Cast after filtering.
- `Compound` can be `"UNKNOWN"` for early laps or when data is missing
- `TrackStatus` is a string encoding, not a human-readable label. May need mapping.

## Results DataFrame Structure

`session.results` returns a DataFrame. Key columns:

| Column | Type | Notes |
|--------|------|-------|
| `DriverNumber` | str | |
| `BroadcastName` | str | e.g. "M VERSTAPPEN" |
| `Abbreviation` | str | "VER" |
| `FullName` | str | "Max Verstappen" |
| `TeamName` | str | |
| `Position` | float / NaN | Final classification position. NaN for some DNFs. |
| `ClassifiedPosition` | str | "1", "2", ... or "R" (retired), "D" (disqualified), "E" (excluded), "W" (withdrawn) |
| `GridPosition` | int | |
| `Status` | str | "Finished", "+1 Lap", "Engine", "Collision damage", etc. Free-form text. |
| `Points` | float | |
| `Time` | timedelta / NaT | Time to leader. NaT if not classified. |
| `FastestLap` | NaT / timedelta | |
| `FastestLapTime` | timedelta / NaT | |

**Gotcha:** `ClassifiedPosition` is a string, not an int. It must be stored as CharField because of "R", "D", "E", "W" values.

**Gotcha:** `Position` can be NaN for drivers who didn't finish and weren't classified. Don't assume it's always populated.

**Gotcha:** Practice session results have a different structure — they're timing sheets, not race classifications. The columns largely overlap but `GridPosition`, `Status`, and `Points` are not meaningful for practice.

## Weather DataFrame Structure

`session.weather_data` returns a DataFrame. Key columns:

| Column | Type | Notes |
|--------|------|-------|
| `Time` | timedelta | Time offset from session start |
| `AirTemp` | float | Celsius |
| `TrackTemp` | float | Celsius |
| `Humidity` | float | Percentage |
| `Pressure` | float | mbar |
| `WindSpeed` | float | m/s |
| `WindDirection` | int | Degrees (0-359) |
| `Rainfall` | bool | |

**Gotcha:** `Time` is a timedelta offset, not an absolute timestamp. Convert to absolute datetime by adding it to `session.date`.

**Gotcha:** Weather data can be completely empty for some sessions (especially older ones). Check `session.weather_data` is not None and not empty before processing.

## Rate Limits

FastF1 pulls from multiple sources (Ergast API, F1 live timing). Rate limit behavior:
- Ergast (historical data): fairly generous but can throttle after many rapid requests
- F1 live timing: stricter, especially for telemetry
- Errors surface as HTTP 429 or `ConnectionError` / `requests.exceptions.HTTPError`

The FastF1 cache is the best defense. After the initial backfill, rate limits are rarely hit because everything is cached.

When catching rate limit errors, look for:
```python
from requests.exceptions import HTTPError, ConnectionError

# These are the most common rate-limit-adjacent exceptions from FastF1
# The exact exception depends on which data source hit the limit
```

FastF1 does not raise a dedicated rate limit exception class. You need to catch HTTP errors and check for 429 status codes, or catch connection errors that indicate throttling.

## Season-Specific Quirks

- **2018–2020:** Some sessions have incomplete data. Weather data may be missing entirely.
- **2020:** COVID calendar — fewer races, double-headers at same circuits.
- **2021:** Sprint introduced at 3 events. Format: FP1, Q, FP2 (renamed "Sprint Qualifying"), Sprint, Race.
- **2022:** New sprint format. Ground-effect regulations (major rule change).
- **2023:** Sprint format changed again. 6 sprint events.
- **2024:** Further sprint tweaks. Session naming may differ.
- **Pre-2018:** Data exists but quality is inconsistent. Out of scope.

Always derive session lists from the event schedule, never hardcode them.
