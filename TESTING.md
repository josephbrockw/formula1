Milestone 1 — Agent Infrastructure

1a. Verify the package installed correctly
pip install anthropic scikit-learn
python -c "import anthropic; import sklearn; print('packages OK')"

1b. Check the agent files exist
ls agents/
# Expected: __init__.py  base.py  planner.py  run.py  tools.py

1c. Test the tools directly in a Python shell
python -c "
import os, django
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
django.setup()
from agents.tools import query_data_summary, read_file, run_management_command
print('=== Data summary ===')
print(query_data_summary())
print('=== Read file ===')
print(read_file('analytics/models/fantasy.py')[:200])
print('=== Sandbox test (should deny) ===')
print(read_file('../../../etc/passwd'))
"
Expected: JSON counts table, first 200 chars of fantasy.py, then an access-denied error.

1d. Run the planner agent (requires ANTHROPIC_API_KEY in env)
cd ..  # back to project root
python f1_analytics/agents/run.py plan
# Expected: prints [tool] calls as it reasons, then writes f1_analytics/agents/plan.json
cat f1_analytics/agents/plan.json

---
Milestone 2 — Complete SessionResult Data Collection

2a. Confirm the migration was applied
cd f1_analytics
python manage.py showmigrations analytics | tail -5
# Expected: [X] 0013_add_fastest_lap_safety_car_laps

2b. Confirm new fields exist in the database
python manage.py shell -c "
from analytics.models import Lap, Session, SessionResult
# Check fields
print('Lap.is_fastest_lap:', Lap._meta.get_field('is_fastest_lap'))
print('Session.safety_car_laps:', Session._meta.get_field('safety_car_laps'))
print('SessionResult.points:', SessionResult._meta.get_field('points'))
print('SessionResult.time:', SessionResult._meta.get_field('time'))
"

2c. If you have any imported sessions already, check what was populated before (this is pre-re-import state)
python manage.py shell -c "
from analytics.models import SessionResult
total = SessionResult.objects.count()
with_points = SessionResult.objects.filter(points__isnull=False).count()
print(f'SessionResults: {total} total, {with_points} with points')
from analytics.models import Lap
with_fl = Lap.objects.filter(is_fastest_lap=True).count()
print(f'Laps with is_fastest_lap=True: {with_fl}  (0 expected before re-import)')
from analytics.models import Session
with_sc = Session.objects.filter(safety_car_laps__isnull=False).count()
print(f'Sessions with safety_car_laps: {with_sc}  (0 expected before re-import)')
"

2d. Re-import one race session to verify the new fields populate (pick a round you have data for)
# First check what rounds exist
python manage.py shell -c "
from analytics.models import Race, Session
for r in Race.objects.filter(season__year=2025).order_by('round_number')[:5]:
  print(r.round_number, r.name, Session.objects.filter(race=r).count(), 'sessions')
"

# Then re-import round 1 with force (replace 2025 / round 1 with what you have)
python manage.py import_fastf1 --year 2025 --round 1 --force

2e. Verify the new fields were populated after the re-import
python manage.py shell -c "
from analytics.models import SessionResult, Lap, Session

# Points and time
r = SessionResult.objects.filter(points__isnull=False).first()
if r:
  print(f'points populated: {r.driver.full_name} scored {r.points} pts, time={r.time!r}')
else:
  print('No SessionResults with points yet')

# Fastest lap
fl = Lap.objects.filter(is_fastest_lap=True).first()
if fl:
  print(f'is_fastest_lap: {fl.driver.full_name} Lap {fl.lap_number} ({fl.lap_time:.3f}s)')
else:
  print('No fastest lap marked yet')

# Safety car laps
s = Session.objects.filter(safety_car_laps__isnull=False).first()
if s:
  print(f'safety_car_laps: {s} -> {s.safety_car_laps} laps')
else:
  print('No sessions with safety_car_laps populated yet')
"

---
Milestone 3 — Smart Data Collection

3a. Verify recent-first ordering
python manage.py shell -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
from analytics.processing.session_processor import get_sessions_to_process
sessions = get_sessions_to_process(year=2025, force=False)
print(f'Sessions with gaps: {len(sessions)}')
for s in sessions[:5]:
  print(f'  Round {s.round_number} {s.session_type}')
# Verify rounds are descending (most-recent first)
rounds = [s.round_number for s in sessions]
print('Descending order:', rounds == sorted(rounds, reverse=True))
"

3b. Test --dry-run
python manage.py import_fastf1 --year 2025 --dry-run
# Expected:
# - Lists sessions to process in order (most-recent round first)
# - Shows which data types are missing per session
# - Prints "Estimated API calls needed: ~N"
# - Exits WITHOUT making any FastF1 API calls (no actual import happens)

3c. Verify --dry-run with a specific round
python manage.py import_fastf1 --year 2025 --round 1 --dry-run
# Expected: lists only sessions from round 1

3d. Verify checkpoint file is written during a real import (optional — only if you want to trigger a real import)
python manage.py import_fastf1 --year 2025 --round 1
# After it finishes:
cat data/import_progress.json
# Expected: JSON array of session entries with year, round, session_type, status, extracted, processed_at
