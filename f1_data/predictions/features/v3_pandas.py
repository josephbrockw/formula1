from __future__ import annotations

from collections import defaultdict

import pandas as pd
from django.db.models import Count, Sum

from core.models import Driver, Event, SessionResult, WeatherSample
from predictions.features.v2_pandas import V2FeatureStore


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class V3FeatureStore:
    """
    Extends V2FeatureStore with 11 richer features (36 total):

      weather_practice_rain_fraction        — 0.0–1.0 fraction of rainy practice samples
                                              (vs V2's binary flag)
      circuit_historical_rain_rate          — fraction of past weekends at this circuit
                                              that had rain; proxy for forecast probability
      track_temp_deviation_from_circuit_mean — current practice track temp minus historical
                                              mean at this circuit (+/− °C)
      weather_air_temp_mean                 — mean air temperature across practice samples (°C)
      driver_wet_vs_dry_position_delta      — per-driver mean finish position in wet races
                                              minus mean in dry races (negative = wet specialist)
      driver_races                          — total race starts across all seasons prior to
                                              this event (experience proxy; 0 for rookies)
      driver_wet_session_count              — total wet Q + R session starts across all history
                                              before this event; grows from first wet session
      driver_vs_teammate_gap_cross_season   — driver's mean race finish minus teammate's mean
                                              across all shared races in history; negative =
                                              outperforms teammate; 0.0 if < 3 shared races
      team_qualifying_position_mean_last3   — mean qualifying position of both team drivers
                                              over the last 3 qualifying events this season;
                                              default 10.0 (mid-field)
      driver_championship_position          — driver's current championship rank this season
                                              based on points before this event; default 20
      team_recent_finish_mean_last3         — team's mean race finish position across both
                                              drivers over the last 3 race events this season;
                                              default 10.0
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

        event = Event.objects.select_related("circuit", "season").get(pk=event_id)

        # Four event-level scalars — same value for every driver row.
        new_features = _enhanced_weather_features(event)
        for key, val in new_features.items():
            df[key] = val  # pandas broadcasts the scalar to every row

        # Per-driver features that require cross-season lookup by driver code.
        # Fetch code, id, and team_id together to avoid a second query later.
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

        # Feature 1: wet session count (cross-season, Q + R)
        wet_counts = _driver_wet_session_counts(list(code_to_driver_id.keys()), event)
        df["driver_wet_session_count"] = (
            df["driver_id"].astype(int)
            .map({driver_id: wet_counts[code] for code, driver_id in code_to_driver_id.items() if code in wet_counts})
            .fillna(0).astype(int)
        )

        # Feature 2: teammate gap (cross-season)
        teammate_gaps = _driver_vs_teammate_gap(code_to_driver_id, driver_id_to_team_id, event)
        df["driver_vs_teammate_gap_cross_season"] = (
            df["driver_id"].astype(int)
            .map({driver_id: teammate_gaps[code] for code, driver_id in code_to_driver_id.items() if code in teammate_gaps})
            .fillna(0.0)
        )

        # Features 3 & 5: team-level current season
        qualifying_means = _team_qualifying_means(team_ids, event)
        finish_means = _team_recent_finish_means(team_ids, event)
        df["team_qualifying_position_mean_last3"] = (
            df["driver_id"].astype(int).map(driver_id_to_team_id).map(qualifying_means).fillna(10.0)
        )
        df["team_recent_finish_mean_last3"] = (
            df["driver_id"].astype(int).map(driver_id_to_team_id).map(finish_means).fillna(10.0)
        )

        # Feature 4: championship position (current season)
        champ_positions = _driver_championship_positions(list(code_to_driver_id.keys()), event)
        df["driver_championship_position"] = (
            df["driver_id"].astype(int)
            .map({driver_id: champ_positions[code] for code, driver_id in code_to_driver_id.items() if code in champ_positions})
            .fillna(20).astype(int)
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

    Default: 0.0 (no samples → treat as dry).
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

    Default: 0.2 — a soft prior acknowledging uncertainty (not 0.0).
    """
    if event.circuit is None:
        return 0.2

    # Fetch all past events at this circuit excluding the current one
    past_event_ids = list(
        Event.objects.filter(
            circuit=event.circuit,
            event_date__lt=event.event_date,
        ).values_list("id", flat=True)
    )
    if not past_event_ids:
        return 0.2

    # For each past event, check: (a) has any weather data, (b) has any rain
    weather_by_event: dict[int, list[bool]] = {}
    for row in WeatherSample.objects.filter(
        session__event_id__in=past_event_ids,
    ).values("session__event_id", "rainfall"):
        eid = row["session__event_id"]
        weather_by_event.setdefault(eid, []).append(row["rainfall"])

    events_with_data = len(weather_by_event)
    if events_with_data == 0:
        return 0.2  # No historical weather at all — fall back to soft prior

    events_with_rain = sum(1 for samples in weather_by_event.values() if any(samples))
    return events_with_rain / events_with_data


def _track_temp_deviation(event: Event) -> float:
    """
    Current practice mean track temp minus historical mean at this circuit (°C).

    Positive = hotter than usual, negative = cooler.
    The model can learn that unusually cold conditions affect tyre warm-up,
    and unusually hot conditions cause degradation outliers.

    Default: 0.0 (no deviation — treat as normal).
    """
    if event.circuit is None:
        return 0.0

    # Current practice track temp
    current_samples = list(
        WeatherSample.objects.filter(
            session__event_id=event.id,
            session__session_type__in=["FP1", "FP2", "FP3"],
        ).values_list("track_temp", flat=True)
    )
    if not current_samples:
        return 0.0
    current_mean = sum(current_samples) / len(current_samples)

    # Historical track temp at this circuit (excluding this event)
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

    hist_mean = sum(hist_samples) / len(hist_samples)
    return current_mean - hist_mean


def _air_temp_mean(event_id: int) -> float:
    """
    Mean air temperature across practice weather samples (°C).

    Air temp correlates with tyre operating window — teams tune aero/tyre
    pressure differently at 40°C vs 15°C. Raw temp (not deviation) lets
    the model learn absolute temperature thresholds.

    Default: 0.0 (no samples).
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

    A "race start" is any SessionResult for a race session (session_type="R"),
    regardless of finishing status. This gives the model an experience signal:
    a rookie with 3 starts is very different from a veteran with 280.

    Single aggregation query across all drivers — not N queries.
    Default 0 for drivers with no prior records (true rookies).
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
      0.0      → neutral or insufficient dry history

    Defaults:
      < 3 wet appearances  → +2.0  (rookie wet penalty; established drivers get credit)
      < 3 dry appearances  → 0.0   (can't compute a meaningful dry baseline)
      no past results      → +2.0

    Single batch: two DB queries for all drivers (results + rainy event ids), not N queries.
    """
    codes = list(code_to_driver_id.keys())

    # 1. All past race results for these driver codes across all seasons
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

    # 2. Which of those race events had rain during the race session
    all_event_ids = {r["session__event_id"] for r in past_results}
    rainy_event_ids = set(
        WeatherSample.objects.filter(
            session__event_id__in=all_event_ids,
            session__session_type="R",
            rainfall=True,
        ).values_list("session__event_id", flat=True).distinct()
    )

    # 3. Split each driver's results into wet / dry and compute the delta
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
    Hamilton/Alonso will have high counts; a 2024 rookie will have 0.

    Two queries for all drivers at once:
      1. All Q+R participations before this event
      2. Which (event_id, session_type) pairs had rainfall → intersect in Python

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


def _driver_vs_teammate_gap(
    code_to_driver_id: dict[str, int],
    driver_id_to_team_id: dict[int, int],
    event: Event,
) -> dict[str, float]:
    """
    Driver's mean race finish position minus teammate's mean finish across all shared
    race appearances in history. Negative = outperforms teammate.

    Isolates driver skill from car quality: two teammates in the same car who average
    8th and 12th show −2 and +2 respectively, regardless of which team they're on.

    Default: 0.0 if fewer than 3 shared races (insufficient data). Neutral assumption —
    no penalty applied.

    Two queries for all drivers at once.
    """
    codes = list(code_to_driver_id.keys())
    our_results = list(
        SessionResult.objects.filter(
            driver__code__in=codes,
            session__session_type="R",
            session__event__event_date__lt=event.event_date,
            position__isnull=False,
        ).values("driver__code", "session__event_id", "position", "team_id")
    )
    if not our_results:
        return {code: 0.0 for code in codes}

    all_event_ids = {r["session__event_id"] for r in our_results}
    all_results = list(
        SessionResult.objects.filter(
            session__event_id__in=all_event_ids,
            session__session_type="R",
            position__isnull=False,
        ).values("driver__code", "session__event_id", "position", "team_id")
    )

    # Group all results by (event_id, team_id) → list of (code, position)
    team_race_results: dict[tuple, list[tuple]] = defaultdict(list)
    for r in all_results:
        team_race_results[(r["session__event_id"], r["team_id"])].append(
            (r["driver__code"], float(r["position"]))
        )

    gaps: dict[str, list[float]] = defaultdict(list)
    for r in our_results:
        key = (r["session__event_id"], r["team_id"])
        teammates = [(c, p) for c, p in team_race_results[key] if c != r["driver__code"]]
        if teammates:
            tm_pos = sum(p for _, p in teammates) / len(teammates)
            gaps[r["driver__code"]].append(float(r["position"]) - tm_pos)

    result = {}
    for code in codes:
        g = gaps.get(code, [])
        result[code] = sum(g) / len(g) if len(g) >= 3 else 0.0
    return result


def _team_qualifying_means(team_ids: list[int], event: Event) -> dict[int, float]:
    """
    Mean qualifying position of both team drivers over the last 3 qualifying
    events in the current season.

    Current season only — team competitiveness changes year-to-year. A team
    qualifying 1st–2nd vs 15th–16th has a fundamentally different race outcome
    distribution. Last 3 events capture recent form, not full-season average.

    Default: 10.0 (mid-field assumption when no current-season qualifying history).
    Single query.
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

    result = {}
    for team_id in team_ids:
        event_means = [sum(v) / len(v) for v in team_event_pos[team_id].values()]
        last3 = event_means[-3:]
        result[team_id] = sum(last3) / len(last3) if last3 else 10.0
    return result


def _driver_championship_positions(codes: list[str], event: Event) -> dict[str, int]:
    """
    Driver's current championship rank in the current season, based on race
    points accumulated before this event.

    Championship leaders are in faster cars and/or in better form — a compounding
    signal of car quality + reliability + pace. P1 vs P18 has vastly different
    expected outcomes.

    Mirrors V2's _constructor_standing_ranks pattern.
    Default: 20 (unranked = last in the field). Single aggregation query.
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
    return {code: rank_map.get(code, 20) for code in codes}


def _team_recent_finish_means(team_ids: list[int], event: Event) -> dict[int, float]:
    """
    Team's mean race finish position across both drivers over the last 3 race
    events in the current season.

    Captures whether a team is on an upswing or struggling (reliability issues,
    development regression, setup issues at specific track types). Complements
    team_qualifying_position_mean_last3 — a team can qualify well but retire often.

    Same pattern as _team_qualifying_means but for session_type="R".
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

    result = {}
    for team_id in team_ids:
        event_means = [sum(v) / len(v) for v in team_event_pos[team_id].values()]
        last3 = event_means[-3:]
        result[team_id] = sum(last3) / len(last3) if last3 else 10.0
    return result
