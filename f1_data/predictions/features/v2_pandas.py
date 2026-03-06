from __future__ import annotations

from collections import defaultdict

import pandas as pd
from django.db.models import Max, Sum

from core.models import Driver, Event, SessionResult, WeatherSample
from predictions.features.v1_pandas import V1FeatureStore
from predictions.models import FantasyDriverPrice, FantasyDriverScore


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class V2FeatureStore:
    """
    Extends V1FeatureStore with 10 new features:

      Weather (from practice sessions, known before lineup lock):
        weather_practice_rainfall     — 1.0 if any practice had rain, else 0.0
        weather_track_temp_mean       — mean track surface temp (°C) across practice samples

      Car-circuit fit:
        team_constructor_standing_rank — team's rank in constructor championship before this event
        circuit_corner_density         — total_corners / circuit_length (Monaco≈5.7, Monza≈1.9)
        team_low_df_avg_pos            — team mean finish pos at low corner-density circuits
        team_high_df_avg_pos           — team mean finish pos at high corner-density circuits

      Driver intelligence:
        driver_vs_teammate_gap_last5   — teammate_mean_pos − driver_mean_pos, last 5 shared races
        pick_percentage                — % of fantasy teams selecting this driver (most recent)
        price_change_last_race         — price change at most recent event ≤ this event
        fantasy_points_trend_last5     — OLS slope of fantasy points over last 5 races
    """

    def __init__(self) -> None:
        self._v1 = V1FeatureStore()

    def get_driver_features(self, driver_id: int, event_id: int) -> dict[str, float]:
        df = self.get_all_driver_features(event_id)
        row = df[df["driver_id"] == driver_id]
        if row.empty:
            return {}
        return row.iloc[0].to_dict()

    def get_all_driver_features(self, event_id: int) -> pd.DataFrame:
        df = self._v1.get_all_driver_features(event_id)
        if df.empty:
            return df

        event = Event.objects.select_related("circuit", "season").get(pk=event_id)

        # Event-level scalars (same value for every driver row)
        weather = _weather_features(event_id)
        corner_density = _circuit_corner_density(event)

        # Team-level lookups computed once
        constructor_ranks = _constructor_standing_ranks(event)
        downforce_ratings = _downforce_ratings(event)

        # Fetch all season drivers for driver-level computations
        drivers = {
            d.id: d
            for d in Driver.objects.filter(season=event.season).select_related("team")
        }

        driver_codes = [drivers[did].code for did in df["driver_id"] if did in drivers]
        price_signals = _fantasy_price_signals(driver_codes, event)

        extra_rows = []
        for driver_id in df["driver_id"]:
            driver = drivers.get(int(driver_id))
            if driver is None:
                extra_rows.append(_default_extra())
                continue

            team_id = driver.team_id
            low_df, high_df = downforce_ratings.get(team_id, (10.0, 10.0))
            signals = price_signals.get(driver.code, {})

            extra_rows.append(
                {
                    "team_constructor_standing_rank": constructor_ranks.get(team_id, 5.0),
                    "circuit_corner_density": corner_density,
                    "team_low_df_avg_pos": low_df,
                    "team_high_df_avg_pos": high_df,
                    "driver_vs_teammate_gap_last5": _driver_vs_teammate_gap(driver, event),
                    "pick_percentage": signals.get("pick_percentage", 0.0),
                    "price_change_last_race": signals.get("price_change_last_race", 0.0),
                    "fantasy_points_trend_last5": _fantasy_points_trend(driver, event),
                    "weather_practice_rainfall": weather["weather_practice_rainfall"],
                    "weather_track_temp_mean": weather["weather_track_temp_mean"],
                }
            )

        extra_df = pd.DataFrame(extra_rows)
        return pd.concat([df.reset_index(drop=True), extra_df.reset_index(drop=True)], axis=1)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _default_extra() -> dict[str, float]:
    return {
        "team_constructor_standing_rank": 5.0,
        "circuit_corner_density": 0.0,
        "team_low_df_avg_pos": 10.0,
        "team_high_df_avg_pos": 10.0,
        "driver_vs_teammate_gap_last5": 0.0,
        "pick_percentage": 0.0,
        "price_change_last_race": 0.0,
        "fantasy_points_trend_last5": 0.0,
        "weather_practice_rainfall": 0.0,
        "weather_track_temp_mean": 0.0,
    }


def _weather_features(event_id: int) -> dict[str, float]:
    """Rainfall flag and mean track temp from practice WeatherSamples."""
    samples = list(
        WeatherSample.objects.filter(
            session__event_id=event_id,
            session__session_type__in=["FP1", "FP2", "FP3"],
        ).values("rainfall", "track_temp")
    )
    if not samples:
        return {"weather_practice_rainfall": 0.0, "weather_track_temp_mean": 0.0}
    rainfall = 1.0 if any(s["rainfall"] for s in samples) else 0.0
    track_temp_mean = sum(s["track_temp"] for s in samples) / len(samples)
    return {"weather_practice_rainfall": rainfall, "weather_track_temp_mean": track_temp_mean}


def _circuit_corner_density(event: Event) -> float:
    """Corners per km — continuous axis from power track (low) to technical (high)."""
    circuit = event.circuit
    if circuit is None:
        return 0.0
    length = circuit.circuit_length
    if not length:
        return 0.0
    corners = circuit.total_corners or 0
    return corners / length


def _constructor_standing_ranks(event: Event) -> dict[int, float]:
    """
    Returns {team_id: rank} based on constructor championship points
    accumulated before this event. Empty dict if no results yet (round 1).
    """
    totals = list(
        SessionResult.objects.filter(
            session__session_type="R",
            session__event__season=event.season,
            session__event__event_date__lt=event.event_date,
        )
        .values("team_id")
        .annotate(total=Sum("points"))
        .order_by("-total")
    )
    if not totals:
        return {}
    sorted_teams = sorted(totals, key=lambda x: -x["total"])
    return {t["team_id"]: float(i + 1) for i, t in enumerate(sorted_teams)}


def _downforce_ratings(event: Event) -> dict[int, tuple[float, float]]:
    """
    Returns {team_id: (low_df_avg_pos, high_df_avg_pos)}.

    Uses current + previous season race results, before this event.
    Splits circuits into low/high corner-density buckets using the
    median density of all results (no hardcoded threshold).
    """
    current_year = event.season.year
    results = list(
        SessionResult.objects.filter(
            session__session_type="R",
            session__event__season__year__in=[current_year, current_year - 1],
            session__event__event_date__lt=event.event_date,
            position__isnull=False,
            session__event__circuit__circuit_length__isnull=False,
            session__event__circuit__total_corners__isnull=False,
        ).values(
            "team_id",
            "position",
            "session__event__circuit__circuit_length",
            "session__event__circuit__total_corners",
        )
    )
    if not results:
        return {}

    enriched = []
    for r in results:
        length = r["session__event__circuit__circuit_length"]
        if not length:
            continue
        enriched.append(
            {
                "team_id": r["team_id"],
                "position": float(r["position"]),
                "density": r["session__event__circuit__total_corners"] / length,
            }
        )
    if not enriched:
        return {}

    densities = sorted(e["density"] for e in enriched)
    n = len(densities)
    median = (
        (densities[n // 2 - 1] + densities[n // 2]) / 2 if n % 2 == 0 else densities[n // 2]
    )

    low_pos: dict[int, list[float]] = defaultdict(list)
    high_pos: dict[int, list[float]] = defaultdict(list)
    for r in enriched:
        bucket = low_pos if r["density"] < median else high_pos
        bucket[r["team_id"]].append(r["position"])

    all_team_ids = {r["team_id"] for r in enriched}
    ratings: dict[int, tuple[float, float]] = {}
    for tid in all_team_ids:
        low = sum(low_pos[tid]) / len(low_pos[tid]) if low_pos[tid] else 10.0
        high = sum(high_pos[tid]) / len(high_pos[tid]) if high_pos[tid] else 10.0
        ratings[tid] = (low, high)
    return ratings


def _driver_vs_teammate_gap(driver: Driver, event: Event) -> float:
    """
    Positive = driver outperforms teammate (lower position number = better).
    Returns 0.0 if no teammate or fewer than 1 shared race.
    """
    teammates = list(
        Driver.objects.filter(team=driver.team, season=driver.season).exclude(pk=driver.pk)
    )
    if not teammates:
        return 0.0
    teammate = teammates[0]

    # Fetch up to 20 of driver's recent races, then find shared ones
    driver_results_ordered = list(
        SessionResult.objects.filter(
            driver=driver,
            session__session_type="R",
            session__event__event_date__lt=event.event_date,
            position__isnull=False,
        )
        .order_by("-session__event__event_date")
        .values("session__event_id", "position")[:20]
    )
    if not driver_results_ordered:
        return 0.0

    candidate_event_ids = [r["session__event_id"] for r in driver_results_ordered]
    teammate_positions = {
        r["session__event_id"]: float(r["position"])
        for r in SessionResult.objects.filter(
            driver=teammate,
            session__session_type="R",
            session__event_id__in=candidate_event_ids,
            position__isnull=False,
        ).values("session__event_id", "position")
    }

    shared = [
        (float(r["position"]), teammate_positions[r["session__event_id"]])
        for r in driver_results_ordered
        if r["session__event_id"] in teammate_positions
    ][:5]

    if not shared:
        return 0.0

    driver_mean = sum(p[0] for p in shared) / len(shared)
    teammate_mean = sum(p[1] for p in shared) / len(shared)
    return teammate_mean - driver_mean


def _fantasy_price_signals(
    driver_codes: list[str], event: Event
) -> dict[str, dict[str, float]]:
    """
    Batched fetch of most recent FantasyDriverPrice ≤ event for each driver code.
    Returns {driver_code: {pick_percentage, price_change_last_race}}.
    """
    records = list(
        FantasyDriverPrice.objects.filter(
            driver__code__in=driver_codes,
            event__event_date__lte=event.event_date,
        )
        .order_by("driver__code", "-event__event_date")
        .values("driver__code", "pick_percentage", "price_change")
    )
    result: dict[str, dict[str, float]] = {}
    for r in records:
        code = r["driver__code"]
        if code not in result:  # First = most recent (ordered by -event_date)
            result[code] = {
                "pick_percentage": float(r["pick_percentage"]),
                "price_change_last_race": float(r["price_change"]),
            }
    return result


def _fantasy_points_trend(driver: Driver, event: Event) -> float:
    """
    OLS slope of fantasy points over last 5 races (ascending date order).
    Positive = improving. Returns 0.0 if fewer than 2 data points.
    Pure Python — no numpy.
    """
    scores = list(
        FantasyDriverScore.objects.filter(
            driver__code=driver.code,
            event__event_date__lt=event.event_date,
        )
        .values("event_id")
        .annotate(race_total=Max("race_total"), event_date=Max("event__event_date"))
        .order_by("event_date")[:5]
    )
    if len(scores) < 2:
        return 0.0

    points = [float(s["race_total"]) for s in scores]
    n = len(points)
    x_mean = (n - 1) / 2
    y_mean = sum(points) / n
    num = sum((i - x_mean) * (s - y_mean) for i, s in enumerate(points))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den else 0.0
