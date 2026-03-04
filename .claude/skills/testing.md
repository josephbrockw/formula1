# Testing Skill

## Framework

Use Django's built-in test framework only. No pytest, no pytest-django, no extra test runners.

```bash
python manage.py test                          # all tests
python manage.py test core.tests               # all core tests
python manage.py test core.tests.test_mappers  # one module
```

## Test Classes

- `SimpleTestCase` — for pure functions (data mappers, notifier formatting). No DB access.
- `TestCase` — for anything that reads/writes the DB (gap detector, collection flow). Wraps each test in a transaction and rolls back.

## Mocking

### FastF1 Mocks

Mock at the boundary — the functions in `tasks/fastf1_loader.py`. Never mock FastF1 internals.

```python
from unittest.mock import patch, MagicMock
from core.tests.factories import make_laps_dataframe, make_session_mock

class TestCollectSeason(TestCase):
    @patch('core.tasks.fastf1_loader.load_session')
    def test_collect_session_stores_laps(self, mock_load):
        mock_load.return_value = make_session_mock(num_drivers=20, num_laps=57)
        # ... test logic
```

### Slack Mocks

```python
@patch('core.tasks.notifier.requests.post')
def test_notification_sends_correct_payload(self, mock_post):
    mock_post.return_value = MagicMock(status_code=200)
    send_slack_notification("test message", level="warning")
    mock_post.assert_called_once()
    payload = mock_post.call_args[1]['json']
    # assert payload structure
```

### Sleep Mocks

Always mock `time.sleep` in rate limit tests:

```python
@patch('core.flows.collect_season.time.sleep')
@patch('core.tasks.fastf1_loader.load_session')
def test_rate_limit_pauses_and_retries(self, mock_load, mock_sleep):
    mock_load.side_effect = [RateLimitError(), make_session_mock()]
    # ... assert sleep was called with 3660, then session was retried
```

## Test Factories (`core/tests/factories.py`)

Build functions that return FastF1-shaped data. These are the single source of test fixtures.

```python
def make_laps_dataframe(num_drivers=20, num_laps=57, **overrides) -> pd.DataFrame:
    """Return a DataFrame matching fastf1.core.Laps structure."""

def make_results_dataframe(num_drivers=20, **overrides) -> pd.DataFrame:
    """Return a DataFrame matching fastf1.core.SessionResults structure."""

def make_weather_dataframe(num_samples=100, **overrides) -> pd.DataFrame:
    """Return a DataFrame matching fastf1.core.Weather structure."""

def make_session_mock(num_drivers=20, num_laps=57, **overrides) -> MagicMock:
    """Return a MagicMock mimicking a loaded fastf1.core.Session.
    
    Sets .laps, .results, .weather attributes to factory DataFrames.
    """
```

Factory functions must accept `**overrides` so tests can tweak specific fields without rebuilding entire DataFrames:

```python
def test_map_laps_handles_wet_compound(self):
    laps_df = make_laps_dataframe(num_laps=5, Compound="WET")
    result = map_laps(laps_df, self.session, self.driver_lookup)
    assert all(lap.compound == "WET" for lap in result)
```

## What to Test in Data Mappers

These are the highest-priority tests. Data mappers handle messy real-world data.

### Happy Path
- Full race session with 20 drivers, 57 laps maps to correct model instances
- All fields populated correctly (spot-check key fields, don't assert every field)

### Edge Cases (test each independently)
- `NaT` (pandas not-a-time) in lap_time → `None` in model field
- `NaN` in sector times → `None`
- DNF status codes: "Retired", "Engine", "Collision", "+1 Lap", "+2 Laps"
- Disqualification: classified_position = "DSQ" with null position
- Sprint sessions: fewer laps, different session_type
- Pit stop laps: is_pit_in_lap / is_pit_out_lap flags
- Missing weather data: session exists but weather DataFrame is empty
- Single-driver edge: session with only 1 driver in results (rare but possible in testing sessions)

### Things NOT to Test
- Django model field types (that's Django's job)
- FastF1's internal parsing (that's their job)
- SQLite behavior (that's SQLite's job)

## Test Naming

```python
# Pattern: test_<function>_<scenario>_<expectation>
def test_map_laps_with_nat_laptime_sets_none(self):
def test_map_laps_pit_in_lap_flagged_correctly(self):
def test_map_results_dnf_has_null_position(self):
def test_find_uncollected_excludes_completed(self):
def test_collect_session_on_rate_limit_sleeps_61_min(self):
```
