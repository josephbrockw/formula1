# CLAUDE.md

## Project
I want to learn about ML and AI through this project. So it's **VERY** important that code changes are done in very small pieces with strong explanations of what the changes do and what decisions were made and why.
Please pause to teach me what we are doing throughout the process. 

F1 Fantasy data collection system. Django + SQLite + FastF1. Collects historical and current F1 session data (laps, results, weather) for a downstream ML/RL fantasy optimization system.
The project is in f1_data/ and chrome_extension/. f1_analytics/ is legacy abandoned code.

## Key Files

- `ML_PIPELINE_PLAN.md` - The plan to execute the ML portion of this project.
- `ML_PROCESS.md` - Living log of what has been implemented in the ML pipeline, decisions made, and current state. **Update this file whenever a step is completed or a significant decision is made.**
- `USAGE.md` - Living doc of how to use the ml to produce real insights. **Update this file whenever a step has changed, been added, or there is a new feature/utility.**
- `.claude/skills/django-models.md` — Conventions for Django model code
- `.claude/skills/testing.md` — Testing standards and patterns
- `.claude/skills/fastf1-data.md` — FastF1 API quirks and data handling

## Commands

```bash
python manage.py test                              # run all tests
python manage.py test core.tests.test_data_mappers  # run one test module
python manage.py migrate                           # apply migrations
python manage.py collect_data                      # run data collection
python manage.py collection_status                 # print coverage report
```

## Code Standards

### Python Style
- Python 3.12+
- Use type hints on all function signatures
- Use `from __future__ import annotations` in every module
- Format with Black (line length 99)
- Sort imports with isort (profile=black)
- No unused imports, no dead code, no commented-out code

### Less Code Is More
- Do not write abstractions until the same pattern appears twice
- Do not add helper functions that are only called once (inline them)
- Do not add logging unless the plan specifically calls for it
- Do not add docstrings to test methods — the test name should be self-documenting
- Do not create `__init__.py` files that re-export things. Keep them empty.
- Prefer built-in Python and Django over third-party libraries

### Function Design
- Functions should do one thing
- Functions that contain logic must be under 30 lines (excluding docstring)
- If a function is getting long, split it into smaller functions
- Pure functions (no side effects) are preferred when possible
- Side effects (DB writes, API calls, notifications) should be isolated in their own functions, not mixed into logic functions

### Django Conventions
- Models go in `core/models.py` (single file unless it exceeds 300 lines)
- Use `unique_together` / `UniqueConstraint` to enforce data integrity at the DB level
- Use `bulk_create` for batch inserts, never loop-and-save
- Use `transaction.atomic()` for any operation that writes multiple related records
- Management commands should be thin — call into flows, don't contain logic
- No Django signals. No custom middleware. No custom template tags. We're not building a web app.

### Error Handling
- Let exceptions propagate unless you have a specific recovery action
- Never write bare `except:` or `except Exception:` without re-raising
- The ONLY place that catches broad exceptions is the collection flow's main loop
- Rate limit errors must propagate up to the flow layer (not caught in loaders or mappers)

## Terminal Output Rules

Management commands print ONLY:
1. A summary line at start (what we're about to do, how many items)
2. One line per session being collected: `[N/total] Event Name — Session Type`
3. Status changes: rate limit pauses, retries
4. Catastrophic errors: full stack trace via `traceback.print_exc()`

**Do NOT print:** DataFrame shapes, cache hit/miss info, per-lap progress, model instance counts, "saving to database" messages, blank lines for formatting.

Use `self.stdout.write()` in management commands, not `print()`.

## Testing Rules

- Every function in `tasks/` must have tests
- Tests NEVER make external API calls (FastF1, Slack, any HTTP)
- Mock FastF1 at the function boundary in `tasks/fastf1_loader.py`, not deep inside FastF1 internals
- Use Django's `TestCase` for anything touching the DB, `SimpleTestCase` for pure functions
- Test file naming: `test_<module_name>.py`
- Use `factories.py` for building test data — never hardcode DataFrames in test methods
- Test names: `test_<what>_<condition>_<expected>` e.g. `test_map_laps_with_nan_sectors_skips_invalid`
- Run the full test suite after every step. Do not move to the next step with failing tests.

## Build Process

This project is built step-by-step per the build order in PLAN.md. **Implement ONE step at a time, then STOP, print out key changes/decisions and simple steps for manual testing, and wait for confirmation before starting the next step.**

After completing each step:
1. Run the full test suite (`python manage.py test`) if tests exist yet
2. State what you completed and what the verification step is
3. **STOP. Do not start the next step until I confirm.**

If I say "continue" or "next step", proceed to the next step in the build order.

## Architecture Rules

### tasks/ vs flows/
- `tasks/` = pure functions, single responsibility, no orchestration, easily testable
- `flows/` = sequencing, error handling, progress tracking, DB state management
- Tasks do not import from flows. Flows import from tasks.
- Tasks do not write to `SessionCollectionStatus`. That's the flow's job.

### Data Flow
```
FastF1 API → fastf1_loader (thin wrapper) → data_mappers (transform) → Django models (bulk_create)
                                                                              ↑
                                                              flows/collect_season.py orchestrates
```

### What NOT to Build
- No REST API / DRF — this is a data pipeline, not a web service
- No Celery / task queue — management commands are sufficient
- No custom Django settings per environment — one settings.py, env vars for secrets
- No Dockerfile — not needed yet
- No telemetry collection — explicitly deferred (see PLAN.md)
- No Prefect — we use the tasks/flows pattern as code organization only
