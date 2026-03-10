# F1 Fantasy Prediction System — Analysis & Improvement Roadmap

## Current System Summary

Three-layer pipeline: **Feature Store** (builds input vectors) → **Predictor** (XGBoost regression) → **Optimizer** (greedy lineup selection with transfers). Evaluated via walk-forward backtesting over 2024–2025 (43 races, min-train 5).

---

## Backtest Results Analysis

| # | FS | Pred | Opt | Lineup Pts | Oracle | Left on Table |
|---|-----|------|-----|------------|--------|---------------|
| 7 | v2 | v1 | v2 | **8374** | 9243 | **869** |
| 5 | v1 | v2 | v2 | 7735 | 9243 | 1508 |
| 3 | v1 | v1 | v2 | 7735 | 9243 | 1508 |
| 8 | v2 | v2 | v1 | 4093 | 9236 | 5143 |
| 6 | v2 | v1 | v1 | 4093 | 9236 | 5143 |
| 4 | v1 | v2 | v1 | 4270 | 9236 | 4966 |
| 2 | v1 | v1 | v1 | 4270 | 9236 | 4966 |
| 9 | v2 | v2 | v2 | — | — | — (26 races, incomplete) |

### What the numbers reveal

**The optimizer is carrying this system.** The v1→v2 optimizer upgrade is responsible for the biggest swing: ~4000 points. The upgrade pass + transfer constraints roughly doubled lineup output. Meanwhile, feature store and predictor changes move the needle by comparatively small amounts.

**Predictor version barely matters right now.** With FS v2, both pred v1 and pred v2 produce identical MAE (3.64 pos, 8.50 pts). The hybrid v2 didn't improve lineup points — it matched v1 because the MSE model driving lineup selection is the same in both. The quantile bounds aren't being used by the optimizer yet.

**Feature store v2 helps, but only when paired with opt v2.** FS v2 adds qualifying position history, team points, and practice delta. With opt v2, this produced 8374 vs 7735 (639-point improvement). With opt v1, the improvement was marginal (4093 vs 4270 — actually slightly *worse*, within noise). The v2 features help rank drivers more accurately, but only a good optimizer can exploit that.

**The best config captures 90.6% of oracle ceiling.** 8374 out of 9243 possible. That 869-point gap across 43 races is ~20 points/race left on the table. Not bad — but there's clearly room.

**Run 9 (all v2) only completed 26 of 43 races.** Something is crashing or timing out. Likely a data issue in the v2 predictor + v2 feature store combination for certain races (missing features causing NaN propagation, or a quantile model edge case). Worth debugging before any other changes.

---

## Component-by-Component Strengths & Weaknesses

### Feature Store

**Strengths:**
- Good foundational feature set: recent form, circuit history, practice pace, reliability, team strength
- Correct handling of cross-season driver identity (matching on code, not PK)
- Sensible defaults for missing data (midfield values)
- Practice pace rank normalization (ranks not raw times) makes features circuit-independent
- Sprint weekend flag gives the model a chance to learn different scoring patterns

**Weaknesses:**
- **No qualifying position for the current event.** This is by far the most predictive feature for race outcome, and you're deliberately excluding it because lineup lock is pre-qualifying. This is correct for the live use case, but it means the model is handicapped relative to what's theoretically possible. The practice pace features are the proxy, but they're noisy.
- **No weather features.** Rain completely reshuffles the field. A wet race is essentially a different sport — the model has no way to know this is coming. Session weather data exists in the DB but isn't in the feature vector.
- **No driver/team interaction features.** "How does this driver perform relative to their teammate" is a strong signal. Intra-team delta captures driver skill independent of car performance.
- **No tire degradation or long-run pace signals.** Practice sessions contain long-run data (consecutive laps on the same compound) which is the best pre-qualifying indicator of race pace. The current features only look at best laps (qualifying pace proxy).
- **No safety car / incident history per circuit.** Some circuits have 80%+ SC rates (Monaco, Singapore, Baku). This materially affects fantasy point distributions.
- **No points-per-position features.** The model learns fantasy points as a target but doesn't know the scoring system is non-linear. Encoding the points table as a feature (or using it in post-processing) could help.
- **Stale defaults for new drivers.** A rookie entering at round 1 gets "position 10, rank 10" defaults — but a Red Bull rookie is very different from a Williams rookie. No way to encode pre-season expectations.
- **No feature for "recently changed teams."** A driver who switched teams has unreliable historical data. Flagging this would let the model weight recent form differently.

### Predictor (XGBoost)

**Strengths:**
- XGBoost handles non-linear relationships well (the P1→P2 vs P11→P12 fantasy point gap)
- Walk-forward validation is correctly implemented — no data leakage
- Two separate models for position and points is clean and avoids the position/bonus-points conflation
- The hybrid v2 approach (MSE mean for optimization, quantile bounds for uncertainty) is architecturally sound
- Residual analysis showed the right-skew problem with median estimates — good diagnostic instinct

**Weaknesses:**
- **Small effective training set.** With min-train 5, the first prediction uses only 5 races × ~20 drivers = ~100 training rows. Even at 43 races, that's 860 rows max. XGBoost can overfit hard at this scale.
- **No hyperparameter tuning.** Using default XGBoost params (100 trees, max_depth 6, learning_rate 0.3). These defaults are designed for much larger datasets. The model is almost certainly overfitting — 100 trees on 100–800 rows with 15+ features is too much capacity.
- **No regularization tuning.** `min_child_weight`, `subsample`, `colsample_bytree`, `reg_alpha`, `reg_lambda` — none of these are tuned. With small data, aggressive regularization matters enormously.
- **The model treats each race independently.** Features encode rolling history, but the model doesn't know that race N+1 is related to race N in any sequential sense. It's a flat tabular prediction.
- **Position predictions aren't used for lineup selection.** The optimizer only uses `predicted_fantasy_points`. The position model exists but only feeds MAE reporting. Its predictions could be used to construct the fantasy points estimate differently (applying the known scoring function to predicted positions + bonus probability estimates).
- **No calibration check on uncertainty bounds.** The v2 quantile models claim 80% coverage (q10–q90), but there's no reported calibration metric. Are 80% of actual outcomes actually within those bounds? If not, the bounds are misleading and the future risk-aware optimizer would make bad decisions.
- **All drivers weighted equally in loss function.** A prediction error on a $30M driver matters more than the same error on a $5M driver (because the expensive driver is more likely to be in your lineup). Sample weighting by price or by fantasy relevance could improve lineup outcomes even if raw MAE stays the same.

### Optimizer

**Strengths:**
- The upgrade pass is a big win — greedy alone often leaves budget unspent
- Transfer constraints act as implicit regularization (only act on high-confidence changes)
- Price-aware adjustment (predicted appreciation → bonus points) captures multi-race value
- Constructor estimation via driver sum is a reasonable proxy
- Budget lookahead prevents the "greedy trap" of picking expensive early and being stuck

**Weaknesses:**
- **Still fundamentally greedy.** PPM (points per million) ranking is a heuristic. It does not find the globally optimal lineup. Two $15M drivers scoring 25pts each (PPM=1.67) beat one $30M driver scoring 55pts (PPM=1.83) because you get 50pts for $30M vs 55pts for $30M. Greedy picks the $30M driver. ILP finds the correct answer.
- **No DRS Boost optimization.** DRS is assigned post-hoc to the highest-predicted driver. But the *optimal* strategy considers DRS during selection: a lineup with a slightly lower total but a much higher DRS ceiling can outscore a lineup with higher total but flat DRS. The DRS pick should influence who gets into the lineup.
- **PRICE_SENSITIVITY = 5.0 is a magic number.** There's no analysis showing this is optimal. It could be 3.0 or 8.0. A 1D sweep over this parameter during backtesting would find the right value.
- **Constructor model is weak.** Sum-of-drivers misses pit stop bonuses (can be 10+ points), qualifying progression bonuses, and the constructor-specific scoring structure. Constructors are 2 of 7 lineup slots and currently the least accurately predicted.
- **No consideration of opponent strategy.** In head-to-head or league formats, picking a differentiated lineup matters. If everyone owns Verstappen, his marginal value to you is lower. Pick percentage data exists in the DB (`pick_percentage` field) but isn't used.

---

## Improvement Ideas — Ordered by Expected Impact and Effort

### Tier 1: High Impact, Low Effort (incremental, no overhaul)

**1. Fix Run 9 (all-v2 crash)**
Debug why FS v2 + Pred v2 + Opt v2 only completes 26/43 races. Likely a NaN propagation issue or feature mismatch. This is blocking your best possible configuration from being evaluated.

#### 2. Replace Greedy with Integer Linear Programming (ILP)
Use PuLP or `scipy.optimize.milp`. Formulate as: maximize Σ(predicted_pts × selected) subject to Σ(price × selected) ≤ budget, Σ(driver_selected) = 5, Σ(constructor_selected) = 2. This finds the provably optimal lineup. Implementation: ~50 lines of code. Expected impact: the greedy→ILP gap is typically 5-15% in knapsack problems.

##### Results
Implemented but didn't perform well because noisy predictions led to suboptimal lineups. 
Options:
- Improve prediction error
- Reformulate the ILP objective to explicitly penalize uncertainty — something like maximize Σ(predicted_pts - λ × confidence_width) × selected, which is the risk-aware approach from your v2 predictor bounds. That would make ILP conservative where predictions are uncertain and aggressive only where the model is confident.

**3. XGBoost Hyperparameter Tuning**
Run a grid or random search over: `n_estimators` [20, 50, 100], `max_depth` [2, 3, 4], `learning_rate` [0.05, 0.1, 0.2], `min_child_weight` [3, 5, 10], `subsample` [0.7, 0.8], `colsample_bytree` [0.7, 0.8], `reg_lambda` [1, 5, 10]. Use the walk-forward structure for evaluation (average MAE across all test folds). With 100-800 rows, shallow trees (depth 2-3) and fewer estimators (20-50) with heavy regularization will almost certainly outperform the defaults.

**4. Sweep PRICE_SENSITIVITY**
Run the full backtest with PRICE_SENSITIVITY ∈ [0, 1, 2, 3, 5, 8, 10, 15, 20]. Plot lineup points vs. sensitivity. This takes 30 minutes to run and might find a sweet spot that's significantly different from 5.0.

**5. Add Weather Features**
Add to the feature vector: `forecast_rain_probability` (binary or 0-1), `track_temp_deviation_from_mean`, `air_temp`. Rain races are the highest-variance events and the easiest for an informed model to exploit. Even a simple "is it likely to rain" flag lets the model learn that certain drivers (Verstappen, Hamilton historically) gain more in wet conditions.

**6. Add Intra-Team Delta Feature**
`driver_position_mean_last5 - teammate_position_mean_last5`. This isolates driver skill from car performance. A driver who consistently beats their teammate by 3 positions is extracting more from the car than features alone show.

**7. DRS Boost Co-optimization**
Instead of assigning DRS post-hoc, enumerate: for each candidate lineup, assign DRS to each of the 5 drivers, compute total score with DRS doubling. Keep the lineup+DRS combo that maximizes. With ILP this is a separate variable; with greedy it's a small loop.

### Tier 2: Medium Impact, Medium Effort

**8. Dedicated Constructor Model**
Build separate features for constructors: average pit stop time rank (from FastF1 pit data), Q3 appearance rate, both-cars-in-points rate, historical constructor championship position. Predict constructor fantasy points directly rather than summing driver predictions. Constructors have a different scoring structure (pit stop bonuses, qualifying progression) that driver features don't capture.

**9. Position → Fantasy Points Analytical Model**
Instead of predicting fantasy points directly, predict finishing position, then apply the known scoring function analytically. The advantage: you get the non-linearity for free (P1=25, P2=18, etc.) and only need to model the unknown parts separately (fastest lap probability, overtake count, Driver of the Day). The position model has lower noise (finishing position is more stable than fantasy points which include bonus stochasticity).

**10. Circuit Clustering Features**
Encode circuits not just by length/corners, but by type: street circuit (Monaco, Singapore, Baku), high-speed (Monza, Spa, Silverstone), technical (Hungary, Zandvoort). Driver performance patterns cluster by circuit type more than by individual circuit. K-means on historical lap time distributions or manual labeling would work.

**11. Feature Importance Analysis + Pruning**
Run `model.feature_importances_` across all walk-forward folds. If some features have near-zero importance consistently, remove them. Fewer features on small datasets = less overfitting.

**12. Calibration Check + Recalibration of Quantile Bounds**
Compute empirical coverage: what % of actual outcomes fall within [q10, q90]? If it's 60% instead of 80%, the bounds are too narrow. If it's 95%, they're too wide. Platt scaling or isotonic regression on the quantile outputs can fix this.

**13. Confidence-Weighted Optimizer (Risk-Aware)**
The infrastructure for this is already built (quantile bounds from v2 predictor). Implement: `adjusted_score = predicted_pts - λ * (q90 - q10)`. Wide intervals = risky pick = penalized. λ is a tunable risk aversion parameter. Sweep λ in backtesting. This should reduce the variance of lineup outcomes and improve average performance by avoiding high-variance picks that sound good but often disappoint.

**14. Long-Run Practice Pace Feature**
From practice session lap data, compute the average of the 5th-15th best laps per driver (filtering out install laps and outliers). This captures sustainable race pace rather than peak qualifying pace. The gap between a driver's best-lap rank and their long-run rank is also informative (big gap = fast in quali but poor in race).

**15. Rolling Variance Feature**
`fantasy_points_std_last5` — high variance means the driver is a boom/bust pick. This interacts with the risk-aware optimizer: a consistent 20pts/race driver might be worth more than a driver who alternates between 5 and 40.

### Tier 3: High Impact, High Effort (significant refactors)

**16. LightGBM or CatBoost as Predictor Alternatives**
LightGBM tends to perform better than XGBoost on small datasets with proper regularization. CatBoost handles categorical features (circuit, team, driver) natively without encoding. Both are drop-in replacements with the same fit/predict API. Run a model horse-race across all three.

**17. Stacking Ensemble**
Train XGBoost, LightGBM, and a ridge regression model. Use a simple meta-learner (linear regression on their predictions) for the final output. Ensembles reduce variance, which matters more than bias when data is small.

**18. Bayesian Optimization for Hyperparameters**
Replace grid search with Optuna or similar. Automatically finds optimal hyperparameters across all tunable knobs (model params, PRICE_SENSITIVITY, risk aversion λ, transfer penalty valuation) jointly. This is the "tune everything at once" approach.

**19. Multi-Race Horizon Optimizer**
Currently the optimizer maximizes this week's points with a price adjustment heuristic. A true multi-race optimizer would simulate the next 3-5 races: "if I pick driver X now, their price rises, giving me budget to pick driver Y in 3 weeks when they hit a favorable circuit." This requires rolling the price heuristic forward + the predictor forward for multiple races and searching over transfer sequences. CEM (cross-entropy method) over the multi-race action space is the natural algorithm.

**20. Target Encoding for Categorical Features**
Replace circuit_length/total_corners with target-encoded circuit features: "average fantasy points scored at this circuit historically." Same for team encoding: "average team finish position this season." These are more directly predictive than raw circuit dimensions, but require careful regularization to avoid leakage (use leave-one-out encoding or add noise).

**21. Recency-Weighted Training**
Currently all training rows are weighted equally. Race 1 of 2023 matters as much as race 22 of 2024 for predicting race 1 of 2025. Apply exponential decay weights so recent races matter more. This helps the model track mid-season form changes (team upgrades, driver confidence shifts) without needing an explicit feature for it.

**22. Ordinal Position Prediction**
Finishing position is ordinal (1st > 2nd > 3rd) and mutually exclusive (only one driver can be P1). Standard regression ignores both properties. An ordinal regression model or a ranking model (LambdaMART) respects the structure. Even simpler: predict position probabilities per driver via softmax, then compute expected fantasy points as Σ(P(position) × points_for_position).

### Tier 4: Overhaul-Level Changes

**23. The Lap-Level World Model (from the MBRL doc)**
This is the big one. Replace the flat feature→points XGBoost pipeline with a race simulator that models lap-by-lap dynamics. Covered in detail in the MBRL analysis doc. This is a 8-10 week project but produces something qualitatively different: scenario analysis, uncertainty quantification from first principles, and the ability to model how positions change dynamically rather than predicting a static outcome.

**24. Deep Learning Sequence Model**
Replace XGBoost with an LSTM or transformer that takes the last N races as a sequence of feature vectors per driver. This captures temporal patterns (momentum, form streaks, post-crash confidence drops) that flat features approximate but don't model directly. Requires more data to train well — probably viable with sector-level features or if you augment with constructed data.

**25. Multi-Agent Race Simulation for Constructor Modeling**
Model the race as interactions between 20 agents. This naturally produces constructor outputs (sum of driver outcomes) while also capturing the correlated risk between teammates (if the car has a reliability issue, both drivers suffer). This subsumes the current separate constructor estimation.

**26. Opponent-Aware Optimizer (Game Theory)**
If playing in a league, the optimal strategy isn't to maximize your own points — it's to maximize your advantage over opponents. This requires modeling what lineups opponents are likely to pick (using pick_percentage data) and selecting a lineup that maximizes expected margin. This is a game-theoretic extension that requires fundamentally different optimization logic.

---

## What I'd Do Next (Priority Stack)

1. **Fix Run 9** — unblock the all-v2 evaluation
2. **ILP optimizer** — biggest bang for least effort, probably 500+ points
3. **XGBoost hyperparameter tuning** — almost certainly overfitting right now
4. **PRICE_SENSITIVITY sweep** — 30 minutes, potentially significant
5. **Weather + intra-team delta features** — low effort, directly useful
6. **DRS co-optimization** — small code change, guaranteed non-negative impact
7. **Confidence-weighted optimizer** — the quantile infrastructure is already built, use it
8. **Constructor model** — currently the weakest prediction in the pipeline

Items 1-6 are all incremental and could each be done in a single session. Item 7-8 are a day's work each. Everything in Tier 3-4 is a multi-day or multi-week effort.
