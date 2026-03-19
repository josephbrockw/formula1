# Decisions

Append-only log. Do not edit or reorder existing entries.

<!-- Entries are added via /decide and auto-timestamped. -->

## 2026-03-19 — Promote ML_PREDICTOR to v3 (recency weighting)

Backtest over 2022–2025 (87 races, fs=v3, opt=v2) showed pred=v3 achieves identical MAE (pos 3.55, pts 8.43) vs pred=v2 but +453 total lineup points (14,490 vs 14,037, +3.2%). Improvement is concentrated in 2025 (+140) and 2022 (+268); a minor 2023 regression (−74 over 22 races) is within noise. The feature importance shift is qualitatively sound: `qualifying_position_mean_last3` rises from 20% → 28% (reflects modern F1's reduced overtaking), `team_constructor_standing_rank` halves (midfield has compressed in 2024–25), `is_sprint_weekend` drops to zero. `ML_PREDICTOR` updated from `"v2"` to `"v3"` in settings.py.

## 2026-03-19 — Qualifying position excluded from ML features

Qualifying position for the current race **cannot** be used as a feature in any predictor. Fantasy lineup deadlines fall before qualifying sessions take place, so qualifying position is unknown at decision time. Only lagged qualifying features (e.g. `qualifying_position_mean_last3` from past events) are valid.
