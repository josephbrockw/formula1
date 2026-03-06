# Roadmap — F1 Fantasy ML

## Current state

The ML pipeline is fully operational via Django management commands:

- `next_race` — generates predictions + lineup recommendation for upcoming race
- `backtest` — runs the backtester and stores results in DB
- `record_my_lineup` — records actual submitted lineup + points
- `compute_fantasy_points` / `compute_fantasy_prices` — populates scoring/price history

The web UI (read-only) is being built in `predictions/views.py` and templates.

---

## Planned: Trigger ML actions from UI

These features are intentionally deferred until the read-only UI is stable.

### "Run predictions" button on Next Race page
- POST endpoint that shells out to `next_race` management command
- Show streaming output or a simple "running / done" state via htmx polling

### "Record actual points" form on Season Dashboard
- Small form to submit `actual_points` for a past race
- Calls `record_my_lineup --event N --actual-points N` equivalent in-process

### "Run backtest" button on Backtest Explorer
- POST endpoint triggering `backtest` management command
- Results refresh via htmx after completion

### Model version comparison on Backtest Explorer
- Currently: filter to a single model_version
- Future: overlay two versions side-by-side

---

## Possible future: notifications

- Post next-race recommendations to Slack automatically after `next_race` runs
- Currently a manual step (copy from stdout)
