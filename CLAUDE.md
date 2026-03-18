# Fantasy Formula 1 

I want to learn about ML and AI through this project. So it's **VERY** important that code changes are done in very small pieces with strong explanations of what the changes do and what decisions were made and why.
Please pause to teach me what we are doing throughout the process.

F1 Fantasy data collection system. Django + SQLite + FastF1. Collects historical and current F1 session data (laps, results, weather) for a downstream ML/RL fantasy optimization system.
The core project is in f1_data/ which is the Django app and chrome_extension/ is a scraper tool for getting data off the Formula1 website. 


**Stack:** Django / Django Templates / SQLite

## Current Focus

- **Sweep PRICE_SENSITIVITY:** Run full backtest with values ∈ [0, 1, 2, 3, 5, 8, 10, 15, 20]. Current value of 5.0 is untuned — 30 min run time, potentially significant improvement.
- **Weather features:** `forecast_rain_probability` (binary or 0–1), `track_temp_deviation_from_mean`, `air_temp`. Rain completely reshuffles the field; even a simple wet-race flag lets the model learn wet-weather specialists. Session weather data already exists in the DB. Consider non binary solution. What are the odds of rain? That's something that we can probably get a backlog of history for. Don't know if that makes sense.
- **Intra-team delta:** `driver_position_mean_last5 - teammate_position_mean_last5`. Isolates driver skill from car performance — a driver who consistently beats their teammate by 3 positions is extracting more from the car than form features alone show.

## Context Loading

- Current tasks → TASKS.md
- Architecture questions → docs/ARCHITECTURE.md
- ML Process questions → docs/ML_PROCESS.md 
- Application Usage → docs/USAGE.md (living doc of how to use the app, only change when there is a change in execution of commands) 
- Code patterns/style → docs/CONVENTIONS.md
- Past decisions → DECISIONS.md
- Future work / ideas → docs/BACKLOG.md (do not read unless asked)

## Commands

- `/task` — Add a task to TASKS.md
- `/done` — Remove a completed task from TASKS.md
- `/decide` — Append a timestamped decision to DECISIONS.md
- `/focus` — Update the Current Focus section above
- `/status` — Read TASKS.md and summarize current state
- `/park` — Add an item to docs/BACKLOG.md under a category
- `/pull` — Read docs/BACKLOG.md and select items to move to TASKS.md
- `/recap` — Summarize recent work from git history

## Rules

- Do not modify files outside the current task scope.
- Ask before creating new files in docs/.
- Keep TASKS.md under 7 items. Push back if it would exceed this.
- When completing a task, remove it from TASKS.md immediately.
- Do not read docs/BACKLOG.md unless explicitly asked or running /pull.
- Do not edit or reorder existing entries in docs/BACKLOG.md — append only.
- Do not edit existing entries in DECISIONS.md — append only.
- If Current Focus appears outdated based on conversation context, flag it and suggest an update.
