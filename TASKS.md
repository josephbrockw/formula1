# Tasks

Active work only. Max 7 items. Remove when complete.

- [ ] **Price fallback for missing FantasyDriverPrice records**: When scraping gaps mean no price exists for a driver at an event, compute an estimated price by rolling the AvgPPM formula (see `PRICE_RULES.md`) forward from the last known `FantasyDriverPrice` record using actual `FantasyDriverScore.race_total` values. Implement in a new `predictions/price_estimator.py` helper. Use it in `backtester.py` so `_optimize_and_score` never returns `None` due to missing prices. This eliminates the `budget+1.0` sentinel and makes the backtest more complete.
- [ ] **circuit-specific driver history** - some drivers genuinely perform better at specific tracks
