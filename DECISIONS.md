# Decisions

Append-only log. Do not edit or reorder existing entries.

<!-- Entries are added via /decide and auto-timestamped. -->

## 2026-03-19 — Qualifying position excluded from ML features

Qualifying position for the current race **cannot** be used as a feature in any predictor. Fantasy lineup deadlines fall before qualifying sessions take place, so qualifying position is unknown at decision time. Only lagged qualifying features (e.g. `qualifying_position_mean_last3` from past events) are valid.
