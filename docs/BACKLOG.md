# Backlog

Future work, deferred tasks, and ideas. Do not read unless explicitly asked.

---

## Bugs / Known Issues

- **Run 9 crash (FS v2 + Pred v2 + Opt v2):** Only completes 26/43 races. Likely NaN propagation or feature mismatch between v2 feature store and v2 predictor. Blocks evaluation of the best configuration.

---

## Quick Wins (High Impact, Low Effort)

These are incremental changes, no overhaul required. Each is roughly a single session of work.

- **Sweep PRICE_SENSITIVITY:** Run full backtest with values ∈ [0, 1, 2, 3, 5, 8, 10, 15, 20]. Current value of 5.0 is untuned — 30 min run time, potentially significant improvement.
- **DRS Boost co-optimization:** Instead of assigning DRS post-hoc, enumerate all candidate lineups and pick the lineup+DRS combo that maximises total score. Small loop change, guaranteed non-negative impact.

---

## Feature Store

### Near-term additions

- **Weather features:** `forecast_rain_probability` (binary or 0–1), `track_temp_deviation_from_mean`, `air_temp`. Rain completely reshuffles the field; even a simple wet-race flag lets the model learn wet-weather specialists. Session weather data already exists in the DB.
- **Intra-team delta:** `driver_position_mean_last5 - teammate_position_mean_last5`. Isolates driver skill from car performance — a driver who consistently beats their teammate by 3 positions is extracting more from the car than form features alone show.
- **Rolling variance feature:** `fantasy_points_std_last5`. High variance = boom/bust pick. Interacts with the risk-aware optimizer: a consistent 20 pts/race driver may be worth more than one alternating between 5 and 40.
- **Long-run practice pace:** Compute average of 5th–15th best laps per driver per session (filtering install laps/outliers). Captures sustainable race pace vs. peak qualifying pace. The gap between best-lap rank and long-run rank is also informative (big gap = fast in quali, poor in race).

### Medium-term additions

- **Circuit clustering features:** Encode circuits by type — street (Monaco, Singapore, Baku), high-speed (Monza, Spa, Silverstone), technical (Hungary, Zandvoort). Driver performance clusters by circuit type more than by individual circuit. K-means on historical lap time distributions or manual labelling.
- **Safety car / incident rate per circuit:** Some circuits have 80%+ SC rates (Monaco, Singapore, Baku). Materially affects fantasy point distributions. Encode as a circuit-level feature.
- **Team-change flag:** Flag drivers who recently switched teams — their historical data is less reliable and the model should weight recent form differently.
- **Rookie prior encoding:** New drivers get midfield defaults, but a Red Bull rookie vs. a Williams rookie are very different. Encode pre-season expectations (e.g. team championship position as a prior).

### Feature Store v3

- Precomputed feature table (avoid recomputing on every run)
- Incorporate practice session long-run signals
- Add weather features (see above)

### OpenMeteo integration (historical rain probability)

The `circuit_historical_rain_rate` in V3 uses our own DB as a proxy — only covers race weekends we've already collected, not general climate patterns. OpenMeteo provides free historical hourly weather by lat/lon, going back decades. Would give a richer circuit rain prior than "fraction of F1 weekends with rain".

Steps: (1) map circuits to lat/lon coordinates, (2) one-off backfill management command hitting the OpenMeteo historical API, (3) new `CircuitWeatherHistory` model or flat CSV to store results, (4) replace `_circuit_historical_rain_rate` in `v3_pandas.py` with a lookup into that data. Would replace the 0.2 soft-prior default with an actual climatological estimate.

---

## Predictor

### Near-term

- **XGBoost hyperparameter tuning:** Grid or random search over `n_estimators` [20, 50, 100], `max_depth` [2, 3, 4], `learning_rate` [0.05, 0.1, 0.2], `min_child_weight` [3, 5, 10], `subsample` [0.7, 0.8], `colsample_bytree` [0.7, 0.8], `reg_lambda` [1, 5, 10]. With 100–800 rows, shallow trees + heavy regularisation will almost certainly outperform defaults.
- **Feature importance analysis + pruning:** Run `model.feature_importances_` across all walk-forward folds. Features with near-zero importance consistently should be removed — fewer features on small datasets means less overfitting.
- **Calibration check on quantile bounds:** Compute empirical coverage — what % of actual outcomes fall within [q10, q90]? If it's 60% instead of 80%, the bounds are too narrow. Use Platt scaling or isotonic regression to recalibrate.

### Medium-term

- **Predictor v3 (quantile regression):** Predict quantiles (10th, 50th, 90th percentile) for uncertainty estimates. Feeds confidence intervals to risk-aware optimizer.
- **Position → fantasy points analytical model:** Predict finishing position, then apply the known scoring function analytically. Advantage: non-linearity (P1=25, P2=18 etc.) is free; only the unknown bonus parts (fastest lap probability, overtake count, DOTD) need modelling. Position is also lower-noise than raw fantasy points.
- **Recency-weighted training:** Apply exponential decay weights to training rows so recent races matter more. Helps the model track mid-season form changes (team upgrades, driver confidence shifts) without needing an explicit feature.
- **Sample weighting by price/relevance:** A prediction error on a $30M driver matters more than the same error on a $5M driver (more likely to be in lineup). Weighting by price or pick percentage could improve lineup outcomes without changing raw MAE.

### Longer-term

- **LightGBM or CatBoost as alternatives:** LightGBM tends to outperform XGBoost on small datasets with proper regularisation. CatBoost handles categorical features (circuit, team, driver) natively. Run a model horse-race across all three.
- **Stacking ensemble:** Train XGBoost, LightGBM, and ridge regression; use a simple meta-learner (linear regression on their predictions). Ensembles reduce variance, which matters more than bias when data is small.
- **Bayesian hyperparameter optimisation (Optuna):** Replace grid search. Automatically finds optimal hyperparameters across all tunable knobs jointly (model params, PRICE_SENSITIVITY, risk-aversion λ, transfer penalty valuation).
- **Ordinal position prediction:** Finishing position is ordinal and mutually exclusive. Ordinal regression or a ranking model (LambdaMART) respects the structure. Alternatively: predict position probabilities per driver via softmax, then compute expected fantasy points as Σ(P(position) × points_for_position).
- **Deep learning sequence model:** Replace XGBoost with an LSTM or transformer that takes the last N races as a sequence of feature vectors per driver. Captures temporal patterns (momentum, form streaks, post-crash confidence drops) that flat rolling features approximate but don't model directly. Needs more data — viable with sector-level features or data augmentation.

---

## Optimizer

### Near-term

- **Confidence-weighted / risk-aware optimizer:** Infrastructure already built (quantile bounds from v2 predictor). Implement `adjusted_score = predicted_pts - λ × (q90 - q10)`. Wide intervals = risky pick = penalised. Sweep λ in backtesting. Should reduce lineup variance and improve average performance by avoiding high-variance picks.

### Medium-term

- **Dedicated constructor model:** Build separate features — average pit stop time rank (FastF1 pit data), Q3 appearance rate, both-cars-in-points rate, historical constructor championship position. Predict constructor fantasy points directly rather than summing driver predictions. Constructors have a different scoring structure (pit stop bonuses, qualifying progression) that driver features don't capture.
- **Better DRS Boost selection:** Variance-aware and circuit-specific. Consider DRS during selection rather than assigning post-hoc.
- **Opponent modelling:** Pick percentage data already exists in the DB (`pick_percentage` field). In head-to-head or league formats, picking a differentiated lineup matters — if everyone owns Verstappen, his marginal value to you is lower. Use pick percentages to penalise over-owned drivers.

### Longer-term

- **Multi-race horizon optimizer:** Currently maximises this week's points with a price adjustment heuristic. A true multi-race optimizer simulates the next 3–5 races: "if I pick driver X now, their price rises, giving me budget to pick driver Y in 3 weeks when they hit a favourable circuit." CEM (cross-entropy method) over the multi-race action space is the natural algorithm.
- **Chip optimisation:** When to deploy Wildcard, Limitless, Extra DRS, etc. for maximum season-long value.

---

## New Capabilities (Medium-term)

- **Sprint race handling:** Separate predictions for sprint vs. main race weekends.
- **Price predictor v2:** Learn the exact price-change scaling function from historical data (buy-low/sell-high strategy enabler).
- **Target encoding for categorical features:** Replace circuit_length/total_corners with target-encoded circuit features (average fantasy points at this circuit historically). Same for team encoding. Requires leave-one-out encoding or noise to avoid leakage.

---

## Long-term / Research

### Bio-RL Roadmap

- **Phase 1 — Embedding encoder:** Encode race weekends as dense vectors; retrieve similar past races for prediction.
- **Phase 2 — Novelty-gated learning:** Selective model updates; pattern separation + complementary learning systems.
- **Phase 3 — Full world model + Monte Carlo simulation:** Lap-level race simulator. Models lap-by-lap dynamics rather than predicting a static outcome. Enables scenario analysis, uncertainty quantification from first principles, and dynamic position modelling. ~8–10 week project.

### Other Research

- **Multi-agent race simulation for constructor modelling:** Model the race as interactions between 20 agents. Naturally produces constructor outputs while capturing correlated risk between teammates (car reliability issues affect both drivers).
- **Opponent-aware optimizer (game theory):** If playing in a league, optimal strategy is to maximise advantage over opponents, not just your own points. Requires modelling opponent lineups from pick percentage data and maximising expected margin.

---

## Tech Debt

- **V3 hyperparameter tuning:** `tune_hyperparams` builds a plain `XGBRegressor` and doesn't apply exponential decay sample weights, so it tunes for the unweighted (V2) model. To tune for V3, extend the command with a `--predictor` flag; when `v3` is selected, compute decay weights from `event_index` and pass them as `sample_weight` to the CV scorer. This matters because recency weighting shifts the effective training distribution, which changes the optimal tree depth, learning rate, and regularisation.

---

## Ideas

- **Circuit-type models:** Separate models per circuit type (street, high-speed, mixed). Currently a single model must compromise across very different track dynamics. Street circuits (Monaco, Singapore, Baku) heavily favour positioning and reliability; high-speed tracks (Monza, Spa) reward raw pace. A model trained on all circuits averages these signals, diluting both.

---
