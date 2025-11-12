# Phase 3: Quick Reference Guide

## One-Page Overview

### What Phase 3 Does

**Imports all F1 data efficiently while respecting API rate limits.**

### The Core Innovation

**Session-Once-Extract-Many Pattern:**
- Load each FastF1 session **once**
- Extract **all data types** from that single load
- Result: **Minimize API calls** (200/hour limit)

---

## Quick Start

### 1. Import Full Season

```python
from analytics.flows.import_fastf1 import import_fastf1_flow

summary = await import_fastf1_flow(year=2024, notify=True)
```

### 2. Check What's Missing

```python
from analytics.processing.gap_detection import generate_gap_report

report = generate_gap_report(2024)
print(f"Missing data: {len(report.session_gaps)} sessions")
```

### 3. Check Rate Limit

```python
from analytics.processing.rate_limiter import get_rate_limit_stats

stats = get_rate_limit_stats()
print(f"Remaining: {stats['remaining']}/200 calls")
```

---

## File Map

```
analytics/
├── processing/
│   ├── gap_detection.py      ← Finds missing data
│   ├── loaders.py             ← Loads FastF1 sessions
│   └── rate_limiter.py        ← Manages API limits
├── flows/
│   ├── import_fastf1.py       ← **Master pipeline**
│   └── import_weather.py      ← Weather sub-flow
└── models/
    ├── events.py              ← Data models (Race, Session, Weather, etc.)
    └── base.py                ← Base models (Season, Team, Driver)
```

---

## Key Classes

### SessionGap
```python
@dataclass
class SessionGap:
    session_id: int              # Database ID
    year: int                    # 2024
    round_number: int            # 1-24
    session_type: str            # 'Practice 1', 'Race', etc.
    session_number: int          # 1-5
    missing_weather: bool        # True if weather needed
    missing_circuit_data: bool   # True if circuit data needed
```

### GapReport
```python
@dataclass
class GapReport:
    season_year: int                     # 2024
    missing_races: List[int]             # [3, 7]
    missing_sessions: List[tuple]        # [(1,3), (2,5)]
    session_gaps: List[SessionGap]       # Full list
    total_api_calls_needed: int          # 45
```

---

## Flow Diagram

```
START
  │
  ├─> Phase 1: Gap Detection
  │     └─> generate_gap_report(year)
  │           └─> Returns: GapReport
  │
  ├─> Phase 2: Rate Limit Check
  │     └─> get_rate_limit_stats()
  │           └─> If needed: wait_for_rate_limit()
  │
  ├─> Phase 3: Build Plan
  │     └─> build_processing_plan(report, max_calls)
  │           └─> Returns: Processing plan
  │
  ├─> Phase 4: Process Sessions
  │     └─> For each SessionGap:
  │           ├─> Load FastF1 session (1 API call)
  │           ├─> Extract weather (if missing)
  │           ├─> Extract circuit (if missing)
  │           └─> Extract other data (future)
  │
  └─> Phase 5: Complete
        └─> send_completion_notification()
              └─> Returns: Summary dict
```

---

## Data Extraction Flow

```python
# Inside process_session_gap()

# STEP 1: Load session ONCE
session = load_fastf1_session(year, round, session_type)  # 1 API call

# STEP 2: Extract ALL data types from this session
if gap.missing_weather:
    weather_data = session.weather_data  # No API call
    save_weather_to_db(session_id, weather_data)

if gap.missing_circuit_data:
    circuit_info = session.get_circuit_info()  # No API call
    save_circuit_to_db(circuit_info)

# STEP 3: Future data types
# laps = session.laps  # No API call
# telemetry = session.car_data  # No API call
# results = session.results  # No API call
```

---

## Rate Limit Strategy

### Check Before Each Session
```python
for gap in gaps:
    # Check quota
    if not check_rate_limit():
        # Pause until reset
        await wait_for_rate_limit()
    
    # Process session
    result = await process_session_gap(gap)
```

### Smart Planning
```python
# Have: 55 API calls remaining
# Need: 80 API calls

# Option 1: Process 55 now
plan = build_processing_plan(report, max_calls=55)

# Option 2: Wait for reset, process all 80
if remaining < needed:
    await wait_for_rate_limit()
    plan = build_processing_plan(report, max_calls=None)
```

---

## Common Commands

### Django Shell
```python
# Get gap report
from analytics.processing.gap_detection import generate_gap_report
report = generate_gap_report(2024)
print(report)

# Check rate limit
from analytics.processing.rate_limiter import get_rate_limit_stats
stats = get_rate_limit_stats()
print(f"{stats['remaining']} calls remaining")

# Run import (async context needed)
import asyncio
from analytics.flows.import_fastf1 import import_fastf1_flow

async def run():
    summary = await import_fastf1_flow(2024)
    return summary

summary = asyncio.run(run())
```

### Python Script
```python
import asyncio
from analytics.flows.import_fastf1 import import_fastf1_flow

async def main():
    # Import 2024 season
    summary = await import_fastf1_flow(
        year=2024,
        force=False,      # Don't re-import existing data
        notify=True       # Send Slack notifications
    )
    
    print(f"Processed: {summary['sessions_processed']}")
    print(f"Succeeded: {summary['sessions_succeeded']}")
    print(f"Failed: {summary['sessions_failed']}")
    
    return summary

if __name__ == '__main__':
    summary = asyncio.run(main())
```

---

## Return Values

### import_fastf1_flow() Returns:

```python
{
    'year': 2024,
    'round_number': None,                     # Or specific round
    'force': False,
    'gaps_detected': 45,
    'sessions_processed': 45,
    'sessions_succeeded': 43,
    'sessions_failed': 2,
    'data_extracted': {
        'weather': 43,
        'circuit': 38,
    },
    'api_calls_made': 45,
    'rate_limit_pauses': 1,                   # Times we paused for rate limit
    'status': 'complete',                     # or 'failed'
    'start_time': '2024-03-15T10:00:00Z',
    'end_time': '2024-03-15T10:08:15Z',
    'duration_seconds': 495.3
}
```

---

## Debug Logging

### Enable Detailed Logs
```python
import logging

# Set Prefect logging to DEBUG
logging.getLogger('prefect').setLevel(logging.DEBUG)

# Run flow
summary = await import_fastf1_flow(2024)
```

### Log Output Example
```
[10:00:00] INFO | ==========================================
[10:00:00] INFO | FastF1 Master Import Pipeline - Season 2024
[10:00:00] INFO | Target: Full season
[10:00:00] INFO | ==========================================
[10:00:01] INFO | PHASE 1: Gap Detection
[10:00:02] INFO | 📊 Gap Report:
[10:00:02] INFO |   • Missing races: 0
[10:00:02] INFO |   • Missing sessions: 5
[10:00:02] INFO |   • Sessions with gaps: 45
[10:00:02] INFO |   • API calls needed: 45
[10:00:03] INFO | PHASE 2: Rate Limit Check
[10:00:03] INFO | 📊 Rate Limit Status:
[10:00:03] INFO |   • Calls made: 145/200
[10:00:03] INFO |   • Remaining: 55
[10:00:03] INFO |   • Status: WARNING
```

---

## Error Messages

### "Season does not exist"
```python
# Fix: Create season
from analytics.models import Season
Season.objects.create(year=2024, name='2024 Season')
```

### "Rate limit exceeded"
```python
# Fix: Wait (automatic) or check status
from analytics.processing.rate_limiter import get_rate_limit_stats
stats = get_rate_limit_stats()
print(f"Resets at: {stats['next_reset']}")
```

### "Session not found"
```python
# Fix: Check if race exists and has sessions
from analytics.models import Race, Session
race = Race.objects.get(season__year=2024, round_number=5)
sessions = Session.objects.filter(race=race)
print(f"Found {sessions.count()} sessions")
```

---

## Performance

### API Efficiency

| Scenario | Old Approach | Phase 3 | Savings |
|----------|-------------|---------|---------|
| Weather only | 120 calls | 120 calls | 0% |
| Weather + Laps | 240 calls | 120 calls | **50%** |
| Weather + Laps + Telemetry | 360 calls | 120 calls | **67%** |

### Time Estimates

| Operation | Duration | Notes |
|-----------|----------|-------|
| Single session | ~4s | Includes extraction |
| Single race (5 sessions) | ~20s | Sequential processing |
| Full season (24 races) | ~8min | 120 sessions |
| Rate limit pause | ~60min | Auto-resume after |

---

## Future Extensions

### Adding New Data Types

1. **Create extraction function:**
```python
@task(name="Extract Lap Times")
def extract_lap_times(session_id: int, fastf1_session):
    laps = fastf1_session.laps
    # Save to database
    return {'status': 'success'}
```

2. **Add to SessionGap:**
```python
@dataclass
class SessionGap:
    # ... existing fields ...
    missing_lap_times: bool = False  # ADD THIS
```

3. **Add to process_session_gap:**
```python
if gap.missing_lap_times:
    result_laps = await extract_lap_times(gap.session_id, fastf1_session)
    if result_laps['status'] == 'success':
        result['extracted'].append('lap_times')
```

Done! No additional API calls needed. 🎉

---

## Monitoring

### Slack Notifications

**Success:**
```
✅ FastF1 Import Complete - Season 2024
Sessions Processed: 45
Succeeded: 43
Failed: 2
Duration: 495s
```

**Failure:**
```
❌ FastF1 Import Failed - Season 2024
Error: Connection timeout
Sessions Processed: 12
Succeeded: 11
```

### Check Status Programmatically

```python
# Get latest import summary
summary = await import_fastf1_flow(2024)

if summary['status'] == 'complete':
    success_rate = summary['sessions_succeeded'] / summary['sessions_processed']
    print(f"Success rate: {success_rate:.1%}")
    
    if summary['rate_limit_pauses'] > 0:
        print(f"Had to pause {summary['rate_limit_pauses']} times")
```

---

## Key Takeaways

1. **Load Once, Extract Many** - Core efficiency pattern
2. **Gap Detection First** - Don't import what you have
3. **Rate Limits Matter** - Auto-pause prevents exhaustion
4. **Chronological Processing** - Process sessions in order
5. **Graceful Degradation** - Partial success > total failure
6. **Comprehensive Logging** - Understand what's happening
7. **Easy Extension** - Add data types without refactoring

---

## Quick Links

- Full Documentation: `docs/PHASE3_MASTER_PIPELINE.md`
- Gap Detection: `analytics/processing/gap_detection.py`
- Master Flow: `analytics/flows/import_fastf1.py`
- Rate Limiter: `analytics/processing/rate_limiter.py`
- Models: `analytics/models/events.py`
