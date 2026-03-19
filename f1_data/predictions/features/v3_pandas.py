from __future__ import annotations

from collections import defaultdict

import pandas as pd
from django.db.models import Count, Sum

from core.models import Driver, Event, SessionResult, Team, WeatherSample
from predictions.features.v2_pandas import V2FeatureStore


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class V3FeatureStore:
    """
    Extends V2FeatureStore with 9 richer features, then drops 6 zero-importance
    features identified via walk-forward feature importance analysis (29 total).

    Changes vs V2:
      - weather_practice_rainfall (binary) is REPLACED by weather_practice_rain_fraction
        (continuous 0.0–1.0), giving the model a graded wet signal.
      - driver_vs_teammate_gap_last5 (from V2) is kept; the cross-season version is
        omitted because V2's rolling-5 captures current form better.

    New features added:
      weather_practice_rain_fraction        — 0.0–1.0 fraction of rainy practice samples
      circuit_historical_rain_rate          — fraction of past weekends at this circuit
                                              that had rain; proxy for forecast probability
      track_temp_deviation_from_circuit_mean — current practice track temp minus historical
                                              mean at this circuit (+/− °C)
      weather_air_temp_mean                 — mean air temperature across practice samples (°C)
      driver_wet_vs_dry_position_delta      — per-driver mean finish position in wet races
                                              minus mean in dry races (negative = wet specialist)
      driver_races                          — total race starts across all seasons prior to
                                              this event (experience proxy; 0 for true rookies)
      driver_wet_session_count              — total wet Q + R session starts across all history
                                              before this event
      team_qualifying_position_mean_last3   — mean qualifying position of both team drivers
                                              over the last 3 qualifying events this season;
                                              falls back to previous season when no current-
                                              season data exists yet (e.g. round 1)
      driver_vs_teammate_championship_gap   — driver's championship rank minus their teammate's
                                              championship rank (season-to-date); negative means
                                              the driver is ahead in the standings, positive means
                                              behind; falls back to previous season's final ranks
                                              at the start of a new season
      team_recent_finish_mean_last3         — team's mean race finish position across both
                                              drivers over the last 3 race events this season;
                                              falls back to previous season when no data yet
    """

    def __init__(self) -> None:
        self._v2 = V2FeatureStore()

    def get_driver_features(self, driver_id: int, event_id: int) -> dict[str, float]:
        df = self.get_all_driver_features(event_id)
        row = df[df["driver_id"] == driver_id]
        if row.empty:
            return {}
        return row.iloc[0].to_dict()

    def get_all_driver_features(self, event_id: int) -> pd.DataFrame:
        df = self._v2.get_all_driver_features(event_id)
        if df.empty:
            return df

        # Replace V2's binary rainfall flag with the continuous fraction.
        # Both are derived from the same WeatherSample rows, but the fraction
        # gives the model a graded signal (0.05 = light drizzle, 0.9 = full wet).
        if "weather_practice_rainfall" in df.columns:
            df = df.drop(columns=["weather_practice_rainfall"])

        # Drop zero-importance features identified via walk-forward analysis.
        # Removing them reduces model noise and overfitting on the small dataset
        # (~100–800 rows). V1/V2 feature stores are left intact for independent use.
        #   circuit_length, total_corners — raw circuit geometry; subsumed by
        #     circuit_corner_density and circuit-specific history features.
        #   circuit_corner_density, team_low_df_avg_pos, team_high_df_avg_pos —
        #     downforce-split ratings showed no predictive lift in walk-forward folds.
        #   pick_percentage — ownership data has leakage risk and near-zero importance.
        _zero_importance = [
            "circuit_length",
            "total_corners",
            "circuit_corner_density",
            "team_low_df_avg_pos",
            "team_high_df_avg_pos",
            "pick_percentage",
            "dnf_rate_last10",
            "price_change_last_race",
            "driver_vs_teammate_championship_gap",
        ]
        df = df.drop(columns=[c for c in _zero_importance if c in df.columns])

        event = Event.objects.select_related("circuit", "season").get(pk=event_id)
        prev_year = event.season.year - 1

        # Four event-level scalars — same value for every driver row.
        new_features = _enhanced_weather_features(event)
        for key, val in new_features.items():
            df[key] = val  # pandas broadcasts the scalar to every row

        # Per-driver features that require cross-season lookup by driver code.
        driver_ids = df["driver_id"].astype(int).tolist()
        driver_rows = list(
            Driver.objects.filter(id__in=driver_ids).values("id", "code", "team_id")
        )
        code_to_driver_id = {r["code"]: r["id"] for r in driver_rows}
        driver_id_to_team_id = {r["id"]: r["team_id"] for r in driver_rows}
        team_ids = list({r["team_id"] for r in driver_rows})

        # wet/dry position delta
        wet_dry_deltas = _wet_vs_dry_position_deltas(code_to_driver_id, event)
        driver_id_to_delta = {
            driver_id: wet_dry_deltas[code]
            for code, driver_id in code_to_driver_id.items()
            if code in wet_dry_deltas
        }
        df["driver_wet_vs_dry_position_delta"] = (
            df["driver_id"].astype(int).map(driver_id_to_delta).fillna(2.0)
        )

        # race start count (experience proxy)
        race_counts = _driver_race_counts(list(code_to_driver_id.keys()), event)
        driver_id_to_race_count = {
            driver_id: race_counts[code]
            for code, driver_id in code_to_driver_id.items()
            if code in race_counts
        }
        df["driver_races"] = (
            df["driver_id"].astype(int).map(driver_id_to_race_count).fillna(0).astype(int)
        )

        # wet session count (cross-season, Q + R)
        wet_counts = _driver_wet_session_counts(list(code_to_driver_id.keys()), event)
        df["driver_wet_session_count"] = (
            df["driver_id"].astype(int)
            .map({driver_id: wet_counts[code] for code, driver_id in code_to_driver_id.items() if code in wet_counts})
            .fillna(0).astype(int)
        )

        # Team-level current season features with previous-season fallback.
        # At the start of a new season (rounds 1–2), there are no current-season
        # results yet — without a fallback, every team gets the same default (10.0),
        # which is pure noise. Using last season's final data gives real signal.
        qualifying_means = _team_qualifying_means(team_ids, event, prev_year)
        finish_means = _team_recent_finish_means(team_ids, event, prev_year)
        df["team_qualifying_position_mean_last3"] = (
            df["driver_id"].astype(int).map(driver_id_to_team_id).map(qualifying_means).fillna(10.0)
        )
        df["team_recent_finish_mean_last3"] = (
            df["driver_id"].astype(int).map(driver_id_to_team_id).map(finish_means).fillna(10.0)
        )

        # Championship rank relative to teammate, with previous-season fallback.
        # Unlike raw championship position (correlated with team_constructor_standing_rank),
        # this isolates driver quality from car quality: both teammates share the same
        # car, so the difference is pure driver performance signal.
        champ_vs_teammate = _driver_championship_vs_teammate_gap(driver_rows, event, prev_year)
        df["driver_vs_teammate_championship_gap"] = (
            df["driver_id"].astype(int).map(champ_vs_teammate).fillna(0.0)
        )

        return df


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _enhanced_weather_features(event: Event) -> dict[str, float]:
    """Aggregate all four new weather features for an event."""
    return {
        "weather_practice_rain_fraction": _practice_rain_fraction(event.id),
        "circuit_historical_rain_rate": _circuit_historical_rain_rate(event),
        "track_temp_deviation_from_circuit_mean": _track_temp_deviation(event),
        "weather_air_temp_mean": _air_temp_mean(event.id),
    }


def _practice_rain_fraction(event_id: int) -> float:
    """
    Fraction of practice weather samples where rainfall=True (0.0–1.0).

    Unlike V2's binary flag, a session with light drizzle at the start and
    dry conditions the rest of the time might score 0.05 rather than 1.0.
    This gives the model a continuous signal about *how wet* practice was.

    Default: 0.0 (no samples → assume dry).
    """
    samples = list(
        WeatherSample.objects.filter(
            session__event_id=event_id,
            session__session_type__in=["FP1", "FP2", "FP3"],
        ).values_list("rainfall", flat=True)
    )
    if not samples:
        return 0.0
    return sum(1 for r in samples if r) / len(samples)


def _circuit_historical_rain_rate(event: Event) -> float:
    """
    Fraction of past weekends at this circuit that had at least one rainy
    weather sample — our proxy for "forecast rain probability".

    We use events-with-weather-data as denominator (not all events) to avoid
    falsely crediting a dry circuit that we just never collected weather for.

    Default: 0.0 — assume dry when no historical weather data exists.
    """
    if event.circuit is None:
        return 0.0

    past_event_ids = list(
        Event.objects.filter(
            circuit=event.circuit,
            event_date__lt=event.event_date,
        ).values_list("id", flat=True)
    )
    if not past_event_ids:
        return 0.0

    weather_by_event: dict[int, list[bool]] = {}
    for row in WeatherSample.objects.filter(
        session__event_id__in=past_event_ids,
    ).values("session__event_id", "rainfall"):
        eid = row["session__event_id"]
        weather_by_event.setdefault(eid, []).append(row["rainfall"])

    events_with_data = len(weather_by_event)
    if events_with_data == 0:
        return 0.0

    events_with_rain = sum(1 for samples in weather_by_event.values() if any(samples))
    return events_with_rain / events_with_data


def _track_temp_deviation(event: Event) -> float:
    """
    Current practice mean track temp minus historical mean at this circuit (°C).

    Positive = hotter than usual, negative = cooler.

    Default: 0.0 (no deviation — treat as normal).
    """
    if event.circuit is None:
        return 0.0

    current_samples = list(
        WeatherSample.objects.filter(
            session__event_id=event.id,
            session__session_type__in=["FP1", "FP2", "FP3"],
        ).values_list("track_temp", flat=True)
    )
    if not current_samples:
        return 0.0
    current_mean = sum(current_samples) / len(current_samples)

    past_event_ids = list(
        Event.objects.filter(
            circuit=event.circuit,
            event_date__lt=event.event_date,
        ).values_list("id", flat=True)
    )
    if not past_event_ids:
        return 0.0

    hist_samples = list(
        WeatherSample.objects.filter(
            session__event_id__in=past_event_ids,
        ).values_list("track_temp", flat=True)
    )
    if not hist_samples:
        return 0.0

    return current_mean - sum(hist_samples) / len(hist_samples)


def _air_temp_mean(event_id: int) -> float:
    """
    Mean air temperature across practice weather samples (°C).

    Default: 0.0 (no samples — assume dry/neutral).
    """
    samples = list(
        WeatherSample.objects.filter(
            session__event_id=event_id,
            session__session_type__in=["FP1", "FP2", "FP3"],
        ).values_list("air_temp", flat=True)
    )
    if not samples:
        return 0.0
    return sum(samples) / len(samples)


def _driver_race_counts(codes: list[str], event: Event) -> dict[str, int]:
    """
    Count race starts per driver across all seasons before this event.

    Default 0 for drivers with no prior records (true rookies).
    Single aggregation query across all drivers.
    """
    counts = dict(
        SessionResult.objects.filter(
            driver__code__in=codes,
            session__session_type="R",
            session__event__event_date__lt=event.event_date,
        )
        .values("driver__code")
        .annotate(n=Count("id"))
        .values_list("driver__code", "n")
    )
    return {code: counts.get(code, 0) for code in codes}


def _wet_vs_dry_position_deltas(
    code_to_driver_id: dict[str, int],
    event: Event,
) -> dict[str, float]:
    """
    For each driver: mean finish position in wet past races minus mean in dry past races.

    Interpretation:
      negative → wet specialist (finishes better in wet, e.g. −3.0 = 3 places forward)
      positive → struggles in wet (e.g. +2.5 = 2.5 places back)

    Defaults:
      < 3 wet appearances  → +2.0  (rookie wet penalty)
      < 3 dry appearances  → 0.0   (can't compute a meaningful dry baseline)
    """
    codes = list(code_to_driver_id.keys())

    past_results = list(
        SessionResult.objects.filter(
            driver__code__in=codes,
            session__session_type="R",
            session__event__event_date__lt=event.event_date,
            position__isnull=False,
        ).values("driver__code", "session__event_id", "position")
    )

    if not past_results:
        return {code: 2.0 for code in codes}

    all_event_ids = {r["session__event_id"] for r in past_results}
    rainy_event_ids = set(
        WeatherSample.objects.filter(
            session__event_id__in=all_event_ids,
            session__session_type="R",
            rainfall=True,
        ).values_list("session__event_id", flat=True).distinct()
    )

    wet_positions: dict[str, list[float]] = defaultdict(list)
    dry_positions: dict[str, list[float]] = defaultdict(list)
    for row in past_results:
        code = row["driver__code"]
        pos = float(row["position"])
        if row["session__event_id"] in rainy_event_ids:
            wet_positions[code].append(pos)
        else:
            dry_positions[code].append(pos)

    result: dict[str, float] = {}
    for code in codes:
        wet = wet_positions[code]
        dry = dry_positions[code]
        if not wet and not dry:
            result[code] = 2.0
        elif len(wet) < 3:
            result[code] = 2.0
        elif len(dry) < 3:
            result[code] = 0.0
        else:
            result[code] = sum(wet) / len(wet) - sum(dry) / len(dry)

    return result


def _driver_wet_session_counts(codes: list[str], event: Event) -> dict[str, int]:
    """
    Total wet Q + R session starts per driver across all history before this event.

    Unlike wet/dry delta (which needs ≥3 wet races to produce a signal),
    this counter starts from the very first wet session and keeps growing.

    Default: 0.
    """
    participations = list(
        SessionResult.objects.filter(
            driver__code__in=codes,
            session__session_type__in=["Q", "R"],
            session__event__event_date__lt=event.event_date,
        ).values("driver__code", "session__event_id", "session__session_type")
    )
    if not participations:
        return {code: 0 for code in codes}

    all_event_ids = {r["session__event_id"] for r in participations}
    wet_pairs = set(
        WeatherSample.objects.filter(
            session__event_id__in=all_event_ids,
            session__session_type__in=["Q", "R"],
            rainfall=True,
        ).values_list("session__event_id", "session__session_type").distinct()
    )

    counts: dict[str, int] = defaultdict(int)
    for r in participations:
        if (r["session__event_id"], r["session__session_type"]) in wet_pairs:
            counts[r["driver__code"]] += 1
    return {code: counts.get(code, 0) for code in codes}


def _team_qualifying_means(team_ids: list[int], event: Event, prev_year: int) -> dict[int, float]:
    """
    Mean qualifying position of both team drivers over the last 3 qualifying
    events in the current season.

    Falls back to the previous season's last-3 qualifying events for teams
    with no current-season qualifying history yet (e.g. first 1–2 races of
    the year). This prevents all teams from sharing the same 10.0 default
    at the season start, which is pure noise.

    Default: 10.0 (mid-field) if no data in either season.
    """
    rows = list(
        SessionResult.objects.filter(
            team_id__in=team_ids,
            session__session_type="Q",
            session__event__season=event.season,
            session__event__event_date__lt=event.event_date,
            position__isnull=False,
        ).values("team_id", "session__event_id", "position")
        .order_by("session__event__event_date")
    )
    team_event_pos: dict[int, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        team_event_pos[r["team_id"]][r["session__event_id"]].append(float(r["position"]))

    # Previous-season fallback for teams with no current-season qualifying data.
    # We can't filter by team_id across seasons — a 2024 Red Bull has a different
    # team_id than the 2023 Red Bull. Instead we look up each team's stable `code`
    # and query the previous season by code, then route results back to the
    # current-season team_id.
    no_data_team_ids = [t for t in team_ids if t not in team_event_pos]
    if no_data_team_ids:
        code_to_current_id = {
            code: tid for tid, code in
            Team.objects.filter(id__in=no_data_team_ids, code__gt="").values_list("id", "code")
        }
        if code_to_current_id:
            prev_rows = list(
                SessionResult.objects.filter(
                    team__code__in=list(code_to_current_id.keys()),
                    session__session_type="Q",
                    session__event__season__year=prev_year,
                    position__isnull=False,
                ).values("team__code", "session__event_id", "position")
                .order_by("session__event__event_date")
            )
            for r in prev_rows:
                current_id = code_to_current_id.get(r["team__code"])
                if current_id:
                    team_event_pos[current_id][r["session__event_id"]].append(float(r["position"]))

    result = {}
    for team_id in team_ids:
        event_means = [sum(v) / len(v) for v in team_event_pos[team_id].values()]
        last3 = event_means[-3:]
        result[team_id] = sum(last3) / len(last3) if last3 else 10.0
    return result


def _driver_championship_vs_teammate_gap(
    driver_rows: list[dict],
    event: Event,
    prev_year: int,
) -> dict[int, float]:
    """
    For each driver: their championship rank minus their current-season teammate's rank.

    Negative = driver leads teammate in standings (extracting more from the car).
    Positive = driver trails teammate.

    Reuses _driver_championship_positions (with its prev-season fallback) so that
    round 1 of a new season compares prior-season final ranks rather than defaulting
    everyone to the same value.

    Default: 0.0 — neutral when no teammate is found or no data in either season.
    """
    codes = [r["code"] for r in driver_rows]
    champ_positions = _driver_championship_positions(codes, event, prev_year)

    team_to_codes: dict[int, list[str]] = defaultdict(list)
    for r in driver_rows:
        team_to_codes[r["team_id"]].append(r["code"])

    result: dict[int, float] = {}
    for r in driver_rows:
        code = r["code"]
        teammates = [c for c in team_to_codes[r["team_id"]] if c != code]
        if not teammates:
            result[r["id"]] = 0.0
            continue
        my_rank = champ_positions.get(code, 20)
        teammate_rank = champ_positions.get(teammates[0], 20)
        result[r["id"]] = float(my_rank - teammate_rank)
    return result


def _driver_championship_positions(codes: list[str], event: Event, prev_year: int) -> dict[str, int]:
    """
    Driver's current championship rank in the current season, based on race
    points accumulated before this event.

    Falls back to the driver's final championship rank from the previous season
    for any driver not yet on the board in the current season. This is a much
    better signal than defaulting everyone to 20th at the start of the year —
    a defending champion or consistent top-5 finisher should get credit for it.

    Default: 20 (unranked in both current and previous season). Single aggregation query.
    """
    standings = list(
        SessionResult.objects.filter(
            driver__code__in=codes,
            session__session_type="R",
            session__event__season=event.season,
            session__event__event_date__lt=event.event_date,
            points__isnull=False,
        )
        .values("driver__code")
        .annotate(total=Sum("points"))
        .order_by("-total")
        .values_list("driver__code", flat=True)
    )
    rank_map = {code: rank + 1 for rank, code in enumerate(standings)}
    result = {code: rank_map.get(code, 20) for code in codes}

    # Previous-season fallback for drivers not yet ranked this season
    unranked = [c for c in codes if c not in rank_map]
    if unranked:
        prev_standings = list(
            SessionResult.objects.filter(
                driver__code__in=unranked,
                session__session_type="R",
                session__event__season__year=prev_year,
                points__isnull=False,
            )
            .values("driver__code")
            .annotate(total=Sum("points"))
            .order_by("-total")
            .values_list("driver__code", flat=True)
        )
        for rank, code in enumerate(prev_standings):
            result[code] = rank + 1  # overrides the placeholder 20

    return result


def _team_recent_finish_means(team_ids: list[int], event: Event, prev_year: int) -> dict[int, float]:
    """
    Team's mean race finish position across both drivers over the last 3 race
    events in the current season.

    Falls back to previous season's last-3 race events for teams with no
    current-season race history yet (same reasoning as _team_qualifying_means).

    Default: 10.0. Single query.
    """
    rows = list(
        SessionResult.objects.filter(
            team_id__in=team_ids,
            session__session_type="R",
            session__event__season=event.season,
            session__event__event_date__lt=event.event_date,
            position__isnull=False,
        ).values("team_id", "session__event_id", "position")
        .order_by("session__event__event_date")
    )
    team_event_pos: dict[int, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        team_event_pos[r["team_id"]][r["session__event_id"]].append(float(r["position"]))

    no_data_team_ids = [t for t in team_ids if t not in team_event_pos]
    if no_data_team_ids:
        code_to_current_id = {
            code: tid for tid, code in
            Team.objects.filter(id__in=no_data_team_ids, code__gt="").values_list("id", "code")
        }
        if code_to_current_id:
            prev_rows = list(
                SessionResult.objects.filter(
                    team__code__in=list(code_to_current_id.keys()),
                    session__session_type="R",
                    session__event__season__year=prev_year,
                    position__isnull=False,
                ).values("team__code", "session__event_id", "position")
                .order_by("session__event__event_date")
            )
            for r in prev_rows:
                current_id = code_to_current_id.get(r["team__code"])
                if current_id:
                    team_event_pos[current_id][r["session__event_id"]].append(float(r["position"]))

    result = {}
    for team_id in team_ids:
        event_means = [sum(v) / len(v) for v in team_event_pos[team_id].values()]
        last3 = event_means[-3:]
        result[team_id] = sum(last3) / len(last3) if last3 else 10.0
    return result
