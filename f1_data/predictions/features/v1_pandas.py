from __future__ import annotations

import pandas as pd
from django.conf import settings
from django.db.models import Max

from core.models import Driver, Event, Lap, SessionResult
from predictions.models import FantasyDriverScore


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class V1FeatureStore:
    """
    MVP feature store: computes driver features via Django ORM queries.

    All features use only data available BEFORE lineup lock (which is before
    qualifying or the sprint race, whichever comes first). Current-event
    qualifying position is NOT a valid feature — we don't know it yet.

    Cross-season queries match on driver.code (e.g. "VER") rather than driver.id,
    because Driver is a per-season model — "VER 2023" and "VER 2024" are
    different DB records with different PKs.

    Features computed for each driver+event:

      Recent race form (rolling over past races, cross-season):
        position_mean_last3           — mean finishing position, last 3 races
        position_mean_last5           — mean finishing position, last 5 races
        position_std_last5            — std dev of finishing positions, last 5 (consistency)
        dnf_rate_last10               — fraction of races that were DNFs, last 10
        positions_gained_mean_last5   — mean (grid_pos - finish_pos), last 5

      Recent qualifying form (historical, from past events):
        qualifying_position_mean_last3 — mean Q position in last 3 qualifying sessions

      Track-specific history:
        circuit_position_mean_last3   — mean finishing position at THIS circuit, last 3 visits

      Team/constructor context:
        team_position_mean_last5      — mean finishing position of both team cars, last 5 races

      Fantasy points history (when available from Chrome extension imports):
        fantasy_points_mean_last3     — mean total fantasy points per race, last 3 races

      This event's practice sessions (available before lineup lock):
        practice_best_lap_rank        — rank of driver's single fastest practice lap (1=fastest)
        practice_avg_best_5_rank      — rank by avg of 5 best practice laps (race pace proxy)

      Static context:
        circuit_length                — track length in km (0.0 if unknown)
        total_corners                 — number of corners (0.0 if unknown)
        round_number                  — which race in the season (e.g. 14.0)
        is_sprint_weekend             — 1.0 if sprint format, else 0.0
    """

    def get_driver_features(self, driver_id: int, event_id: int) -> dict[str, float]:
        event = Event.objects.select_related("circuit", "season").get(pk=event_id)
        driver = Driver.objects.select_related("team").get(pk=driver_id)
        features: dict[str, float] = {}
        features.update(_recent_race_form(driver, event))
        features.update(_recent_qualifying_form(driver, event))
        features.update(_circuit_history(driver, event))
        features.update(_team_recent_form(driver, event))
        features.update(_fantasy_points_history(driver, event))
        features.update(_practice_pace(driver_id, event_id))
        features.update(_event_context(event))
        return features

    def get_all_driver_features(self, event_id: int) -> pd.DataFrame:
        """Compute features for every driver in the season and return as DataFrame."""
        event = Event.objects.select_related("season").get(pk=event_id)
        driver_ids = list(Driver.objects.filter(season=event.season).values_list("id", flat=True))
        rows = []
        for driver_id in driver_ids:
            row = self.get_driver_features(driver_id, event_id)
            row["driver_id"] = driver_id
            rows.append(row)
        return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Private helpers — one concern each
# ---------------------------------------------------------------------------


def _recent_race_form(driver: Driver, event: Event) -> dict[str, float]:
    """
    Rolling race form from races BEFORE this event, matched by driver.code
    so results from prior seasons are included.
    """
    past_results = list(
        SessionResult.objects.filter(
            driver__code=driver.code,
            session__session_type="R",
            session__event__event_date__lt=event.event_date,
        )
        .order_by("-session__event__event_date")
        .values("position", "grid_position", "status")[:10]
    )

    if not past_results:
        # No cross-season results at all → true rookie in their first race.
        # Use a pessimistic position default rather than mid-field.
        _d = settings.NEW_ENTRANT_POSITION_DEFAULT
        return {
            "position_mean_last3": _d,
            "position_mean_last5": _d,
            "position_std_last5": 0.0,
            "dnf_rate_last10": 0.0,
            "positions_gained_mean_last5": 0.0,
        }

    positions = [float(r["position"]) for r in past_results if r["position"] is not None]
    dnf_flags = [1.0 if _is_dnf(r["status"]) else 0.0 for r in past_results]
    gained = [
        float(r["grid_position"] - r["position"])
        for r in past_results[:5]
        if r["position"] is not None and r["grid_position"] is not None
    ]

    return {
        "position_mean_last3": _mean(positions[:3]),
        "position_mean_last5": _mean(positions[:5]),
        "position_std_last5": _stdev(positions[:5]),
        "dnf_rate_last10": _mean(dnf_flags),
        "positions_gained_mean_last5": _mean(gained),
    }


def _recent_qualifying_form(driver: Driver, event: Event) -> dict[str, float]:
    """
    Historical qualifying positions from past events only, cross-season.
    Never the current event — qualifying hasn't happened yet when lineups lock.
    """
    past_q_results = list(
        SessionResult.objects.filter(
            driver__code=driver.code,
            session__session_type="Q",
            session__event__event_date__lt=event.event_date,
        )
        .order_by("-session__event__event_date")
        .values("position")[:3]
    )
    positions = [float(r["position"]) for r in past_q_results if r["position"] is not None]
    # No cross-season qualifying history → true rookie in their first race.
    return {"qualifying_position_mean_last3": _mean(positions) if positions else settings.NEW_ENTRANT_POSITION_DEFAULT}


def _circuit_history(driver: Driver, event: Event) -> dict[str, float]:
    """
    Driver's historical finishing positions at this specific circuit, cross-season.
    Captures track-specific affinity — some drivers consistently excel or
    struggle at particular circuits regardless of their general form.
    """
    if event.circuit is None:
        return {"circuit_position_mean_last3": 10.0}

    past_results = list(
        SessionResult.objects.filter(
            driver__code=driver.code,
            session__session_type="R",
            session__event__circuit=event.circuit,
            session__event__event_date__lt=event.event_date,
        )
        .order_by("-session__event__event_date")
        .values("position")[:3]
    )
    positions = [float(r["position"]) for r in past_results if r["position"] is not None]
    return {"circuit_position_mean_last3": _mean(positions) if positions else 10.0}


def _team_recent_form(driver: Driver, event: Event) -> dict[str, float]:
    """
    Constructor's recent finishing positions across BOTH drivers, last 5 races.

    Car performance is the single biggest determinant of F1 results. We stay
    within the driver's current-season team since Team is a per-season model.
    Up to 10 results (2 drivers × 5 races) for a stable estimate.
    """
    past_results = list(
        SessionResult.objects.filter(
            team=driver.team,
            session__session_type="R",
            session__event__event_date__lt=event.event_date,
        )
        .order_by("-session__event__event_date")
        .values("position")[:10]
    )
    positions = [float(r["position"]) for r in past_results if r["position"] is not None]
    return {"team_position_mean_last5": _mean(positions) if positions else 10.0}


def _fantasy_points_history(driver: Driver, event: Event) -> dict[str, float]:
    """
    Mean total fantasy points per race weekend, last 3 races, cross-season.

    Directly measures what we're trying to predict. Defaults to 0.0 when no
    fantasy data has been imported yet. Groups by event_id so we get one
    total per race weekend (FantasyDriverScore has one row per scoring item).
    """
    events_with_totals = list(
        FantasyDriverScore.objects.filter(
            driver__code=driver.code,
            event__event_date__lt=event.event_date,
        )
        .values("event_id")
        .annotate(
            race_total=Max("race_total"),
            event_date=Max("event__event_date"),
        )
        .order_by("-event_date")[:3]
    )
    points = [float(e["race_total"]) for e in events_with_totals]
    return {"fantasy_points_mean_last3": _mean(points) if points else 0.0}


def _practice_pace(driver_id: int, event_id: int) -> dict[str, float]:
    """
    Practice session pace ranks for this event.

    Uses driver_id (not driver.code) because Lap is a current-event query —
    the laps were created for the season-specific Driver record for this event.

    practice_best_lap_rank:
        Rank by single fastest lap — qualifying pace proxy (max effort, low fuel).

    practice_avg_best_5_rank:
        Rank by average of 5 best laps — race pace proxy (sustainable pace).

    Both are ranks (1=fastest) so they're comparable across circuits.
    """
    all_laps = list(
        Lap.objects.filter(
            session__event_id=event_id,
            session__session_type__in=["FP1", "FP2", "FP3"],
            is_accurate=True,
            lap_time__isnull=False,
        ).values("driver_id", "lap_time")
    )

    if not all_laps:
        return {"practice_best_lap_rank": 10.0, "practice_avg_best_5_rank": 10.0}

    # Group laps by driver, convert to seconds
    driver_laps: dict[int, list[float]] = {}
    for lap in all_laps:
        seconds = lap["lap_time"].total_seconds()
        driver_laps.setdefault(lap["driver_id"], []).append(seconds)

    best_laps = {d: min(times) for d, times in driver_laps.items()}

    avg_best5: dict[int, float] = {}
    for d, times in driver_laps.items():
        times.sort()
        top5 = times[:5]
        avg_best5[d] = sum(top5) / len(top5)

    return {
        "practice_best_lap_rank": float(_rank_among(best_laps, driver_id)),
        "practice_avg_best_5_rank": float(_rank_among(avg_best5, driver_id)),
    }


def _event_context(event: Event) -> dict[str, float]:
    """Static facts about this race weekend — always available before lineup lock."""
    circuit = event.circuit
    return {
        "circuit_length": float(circuit.circuit_length or 0.0) if circuit else 0.0,
        "total_corners": float(circuit.total_corners or 0) if circuit else 0.0,
        "round_number": float(event.round_number),
        "is_sprint_weekend": 1.0 if event.event_format == "sprint" else 0.0,
    }


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _rank_among(scores: dict[int, float], driver_id: int) -> int:
    """
    Return the 1-based rank of driver_id in scores, where lower score = better rank.
    If the driver has no score (no practice laps), returns last place + 1.
    """
    if driver_id not in scores:
        return len(scores) + 1
    sorted_drivers = sorted(scores, key=lambda d: scores[d])
    return sorted_drivers.index(driver_id) + 1


def _is_dnf(status: str) -> bool:
    """
    "Finished" and lapped-but-classified results (e.g. "+1 Lap") are not DNFs.
    Everything else (Engine, Retired, Collision, etc.) is a DNF.
    """
    return not (status == "Finished" or status.startswith("+"))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: list[float]) -> float:
    """Sample standard deviation. Returns 0.0 if fewer than 2 values."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((x - mean) ** 2 for x in values) / (len(values) - 1)) ** 0.5
