from __future__ import annotations

import numpy as np
import pandas as pd
from django.conf import settings

from core.models import Driver, Event, Lap, Session, SessionResult
from predictions.features.v3_pandas import V3FeatureStore

# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class V4FeatureStore:
    """
    Extends V3FeatureStore with practice-telemetry, form-direction, and weather features.

    Telemetry features (derived from FP lap data):
      fp_long_run_pace_rank, fp_tyre_deg_rank, fp_sector1_rank,
      fp_sector2_rank, fp_sector3_rank, fp_total_laps,
      fp_session_availability, fp_short_vs_long_delta

    Form-direction features (derived from recent race/quali results):
      position_last1       — finishing position in the most recent race
      position_slope       — OLS slope over last 5 races (negative = improving)
      best_position_last5  — best (minimum) finishing position in last 5 races
      teammate_delta       — driver_position_last1 - teammate_position_last1
      team_best_position   — best finishing position across both cars, last 5 races
      quali_last1          — qualifying position at the most recent event
      quali_slope          — OLS slope of qualifying positions over last 5 events

    Weather features (derived from V3's continuous weather columns):
      practice_rainfall_any        — 1.0 if any practice session had rain, 0.0 otherwise
      driver_wet_performance_rank  — rank of driver_wet_vs_dry_position_delta (1 = best wet performer)
    """

    def get_driver_features(self, driver_id: int, event_id: int) -> dict[str, float]:
        df = self.get_all_driver_features(event_id)
        row = df[df["driver_id"] == driver_id]
        if row.empty:
            return {}
        return row.iloc[0].to_dict()

    def get_all_driver_features(self, event_id: int) -> pd.DataFrame:
        df = V3FeatureStore().get_all_driver_features(event_id)
        if df.empty:
            return df

        event = Event.objects.get(pk=event_id)
        driver_ids = df["driver_id"].astype(int).tolist()

        driver_rows = list(
            Driver.objects.filter(id__in=driver_ids).values("id", "code", "team_id")
        )
        form_features = _compute_form_features(driver_rows, event)
        for col, mapping in form_features.items():
            df[col] = df["driver_id"].astype(int).map(mapping)

        fp_laps = _load_fp_laps(event)

        long_run_ranks = _fp_long_run_pace_ranks(fp_laps, driver_ids)
        deg_ranks = _fp_tyre_deg_ranks(fp_laps, driver_ids)
        sector_ranks = _fp_sector_ranks(fp_laps, driver_ids)
        total_laps = _fp_total_laps(fp_laps, driver_ids)
        session_avail = _fp_session_availability(event)

        df["fp_long_run_pace_rank"] = df["driver_id"].astype(int).map(long_run_ranks)
        df["fp_tyre_deg_rank"] = df["driver_id"].astype(int).map(deg_ranks)
        df["fp_sector1_rank"] = df["driver_id"].astype(int).map(
            lambda d: sector_ranks.get(d, (10.5, 10.5, 10.5))[0]
        )
        df["fp_sector2_rank"] = df["driver_id"].astype(int).map(
            lambda d: sector_ranks.get(d, (10.5, 10.5, 10.5))[1]
        )
        df["fp_sector3_rank"] = df["driver_id"].astype(int).map(
            lambda d: sector_ranks.get(d, (10.5, 10.5, 10.5))[2]
        )
        df["fp_total_laps"] = df["driver_id"].astype(int).map(total_laps)
        df["fp_session_availability"] = session_avail

        # Derived: measures whether a driver is a quali specialist vs race pace specialist.
        # Positive = stronger in short runs (quali sim) than long runs (race pace).
        # Negative = stronger in race pace than short runs.
        df["fp_short_vs_long_delta"] = (
            df["practice_best_lap_rank"] - df["fp_long_run_pace_rank"]
        )

        # Binary wet-weekend flag. The ranker uses this as a conditioning signal to
        # weight wet-specialist features more heavily. Derived from V3's continuous
        # fraction — no extra DB query needed.
        df["practice_rainfall_any"] = (df["weather_practice_rain_fraction"] > 0.0).astype(float)

        # Rank drivers by wet vs dry position delta, ascending (rank 1 = best wet performer).
        # Drivers with insufficient wet history receive V3's default +2.0 penalty, which
        # naturally places them near the bottom when ranked — encoding "unknown wet ability".
        # When all drivers are on the same default (no wet races in history), method="average"
        # assigns them all equal rank (~10.5 for a 20-driver field).
        df["driver_wet_performance_rank"] = df["driver_wet_vs_dry_position_delta"].rank(
            method="average", ascending=True
        )

        return df


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _load_fp_laps(event: Event) -> pd.DataFrame:
    """
    Single DB query for all FP laps at this event that pass the base filter.

    We convert DurationField values (Python timedelta) to seconds immediately
    so all downstream helpers work with plain floats.
    """
    qs = Lap.objects.filter(
        session__event=event,
        session__session_type__in=["FP1", "FP2", "FP3"],
        is_accurate=True,
        is_pit_in_lap=False,
        is_pit_out_lap=False,
        lap_time__isnull=False,
    ).values(
        "driver_id",
        "session__session_type",
        "lap_time",
        "tyre_life",
        "stint",
        "compound",
        "sector1_time",
        "sector2_time",
        "sector3_time",
    )

    df = pd.DataFrame(list(qs))
    if df.empty:
        return df

    df = df.rename(columns={"session__session_type": "session_type"})

    def _to_seconds(src_col: str, dest_col: str) -> None:
        df[dest_col] = df[src_col].apply(
            lambda t: t.total_seconds() if t is not None else None
        )

    _to_seconds("lap_time", "lap_time_seconds")
    _to_seconds("sector1_time", "sector1_seconds")
    _to_seconds("sector2_time", "sector2_seconds")
    _to_seconds("sector3_time", "sector3_seconds")

    return df


def _long_run_stints(fp_laps: pd.DataFrame, session_types: list[str]) -> pd.DataFrame:
    """
    Filter fp_laps to long-run qualifying stints: ≥5 laps sharing the same
    (driver_id, session_type, stint, compound), with compound and tyre_life
    present. This is the shared definition used by both pace and deg helpers.
    """
    if fp_laps.empty:
        return fp_laps

    mask = (
        fp_laps["session_type"].isin(session_types)
        & fp_laps["compound"].notna()
        & fp_laps["stint"].notna()
        & fp_laps["tyre_life"].notna()
        & fp_laps["lap_time_seconds"].notna()
    )
    filtered = fp_laps[mask].copy()
    if filtered.empty:
        return filtered

    group_keys = ["driver_id", "session_type", "stint", "compound"]
    counts = filtered.groupby(group_keys).transform("count")["lap_time_seconds"]
    return filtered[counts >= 5]


def _fp_long_run_pace_ranks(fp_laps: pd.DataFrame, driver_ids: list[int]) -> dict[int, float]:
    """
    Median lap time in qualifying long-run stints, ranked ascending (1=fastest).

    Prefers FP2; falls back to FP1 if fewer than half the field have FP2 data.
    Default 10.5 for drivers with no qualifying stints.
    """
    if fp_laps.empty:
        return {d: 10.5 for d in driver_ids}

    stints_fp2 = _long_run_stints(fp_laps, ["FP2"])
    fp2_drivers = stints_fp2["driver_id"].nunique() if not stints_fp2.empty else 0

    if fp2_drivers >= len(driver_ids) / 2:
        stints = stints_fp2
    else:
        stints = _long_run_stints(fp_laps, ["FP1", "FP2"])

    if stints.empty:
        return {d: 10.5 for d in driver_ids}

    medians = stints.groupby("driver_id")["lap_time_seconds"].median()
    # Rank ascending: fastest driver gets rank 1
    ranks = medians.rank(method="average", ascending=True)
    return {d: float(ranks.get(d, 10.5)) for d in driver_ids}


def _fp_tyre_deg_ranks(fp_laps: pd.DataFrame, driver_ids: list[int]) -> dict[int, float]:
    """
    Average degradation slope (seconds/lap of tyre age) in long-run FP2 stints,
    ranked ascending (1=lowest degradation, best tyre management).

    A slope near 0 means pace is flat across tyre life; a large positive slope
    means the driver is losing time as tyres age.

    Default 10.5 for drivers with no qualifying stints.
    """
    if fp_laps.empty:
        return {d: 10.5 for d in driver_ids}

    stints = _long_run_stints(fp_laps, ["FP2"])
    if stints.empty:
        return {d: 10.5 for d in driver_ids}

    slopes: dict[int, list[float]] = {}
    for (driver_id, _stype, stint, compound), group in stints.groupby(
        ["driver_id", "session_type", "stint", "compound"]
    ):
        tyre_life = group["tyre_life"].values
        lap_times = group["lap_time_seconds"].values
        if len(tyre_life) < 2:
            continue
        slope, _ = np.polyfit(tyre_life, lap_times, 1)
        slopes.setdefault(driver_id, []).append(float(slope))

    if not slopes:
        return {d: 10.5 for d in driver_ids}

    avg_slopes = pd.Series({d: sum(s) / len(s) for d, s in slopes.items()})
    ranks = avg_slopes.rank(method="average", ascending=True)
    return {d: float(ranks.get(d, 10.5)) for d in driver_ids}


def _fp_sector_ranks(
    fp_laps: pd.DataFrame, driver_ids: list[int]
) -> dict[int, tuple[float, float, float]]:
    """
    Best sector time per driver across all FP sessions.
    Each sector ranked independently, ascending (1=fastest).
    Default (10.5, 10.5, 10.5) for missing drivers.
    """
    default = (10.5, 10.5, 10.5)
    if fp_laps.empty:
        return {d: default for d in driver_ids}

    result: dict[int, tuple[float, float, float]] = {}
    for sector_idx, col in enumerate(["sector1_seconds", "sector2_seconds", "sector3_seconds"]):
        if col not in fp_laps.columns:
            continue
        best = fp_laps.dropna(subset=[col]).groupby("driver_id")[col].min()
        ranks = best.rank(method="average", ascending=True)
        for d in driver_ids:
            current = result.get(d, list(default))
            if isinstance(current, tuple):
                current = list(current)
            current[sector_idx] = float(ranks.get(d, 10.5))
            result[d] = tuple(current)  # type: ignore[assignment]

    # Fill in drivers with no sector data at all
    for d in driver_ids:
        result.setdefault(d, default)
    return result


def _fp_total_laps(fp_laps: pd.DataFrame, driver_ids: list[int]) -> dict[int, float]:
    """
    Total FP laps per driver (all sessions, all base filters applied in _load_fp_laps).
    A low count signals setup problems or mechanical issues during practice.
    Default 0.0.
    """
    if fp_laps.empty:
        return {d: 0.0 for d in driver_ids}

    counts = fp_laps.groupby("driver_id").size()
    return {d: float(counts.get(d, 0)) for d in driver_ids}


def _driver_recent_race_positions(
    codes: list[str], event: Event, n: int = 5
) -> dict[str, list[float]]:
    """
    Returns the last n race finishing positions per driver, in chronological order.

    Queries by driver.code so the same driver across different seasons (e.g.
    VER-2024 and VER-2025) is matched correctly — the same cross-season pattern
    used in V3.

    DNF detection: status != "Finished" and not starting with "+" means the
    driver started but failed to finish. We assign 20.0 (worse than last place,
    to distinguish from a true backmarker finish). This is consistent with V1.

    Returns an empty list for drivers with no prior race data.
    """
    rows = list(
        SessionResult.objects.filter(
            driver__code__in=codes,
            session__session_type="R",
            session__event__event_date__lt=event.event_date,
        )
        .select_related("session__event")
        .order_by("driver__code", "-session__event__event_date")
        .values("driver__code", "position", "status", "session__event__event_date")
    )

    by_code: dict[str, list[float]] = {c: [] for c in codes}
    # rows are ordered newest-first per driver; we collect up to n then reverse
    temp: dict[str, list[float]] = {c: [] for c in codes}
    for row in rows:
        code = row["driver__code"]
        if len(temp[code]) >= n:
            continue
        status = row["status"] or ""
        if status == "Finished" or status.startswith("+"):
            pos = float(row["position"]) if row["position"] is not None else 20.0
        else:
            pos = 20.0
        temp[code].append(pos)

    for code, positions in temp.items():
        by_code[code] = list(reversed(positions))  # chronological ascending

    return by_code


def _driver_recent_quali_positions(
    codes: list[str], event: Event, n: int = 5
) -> dict[str, list[float]]:
    """
    Same as _driver_recent_race_positions but for qualifying sessions.

    Qualifying has no DNF concept; we use the classified position directly.
    Null positions (e.g. driver did not set a time) default to 20.0.
    """
    rows = list(
        SessionResult.objects.filter(
            driver__code__in=codes,
            session__session_type="Q",
            session__event__event_date__lt=event.event_date,
        )
        .select_related("session__event")
        .order_by("driver__code", "-session__event__event_date")
        .values("driver__code", "position", "session__event__event_date")
    )

    by_code: dict[str, list[float]] = {c: [] for c in codes}
    temp: dict[str, list[float]] = {c: [] for c in codes}
    for row in rows:
        code = row["driver__code"]
        if len(temp[code]) >= n:
            continue
        pos = float(row["position"]) if row["position"] is not None else 20.0
        temp[code].append(pos)

    for code, positions in temp.items():
        by_code[code] = list(reversed(positions))

    return by_code


def _ols_slope(values: list[float]) -> float:
    """
    OLS slope of a sequence of values against their index positions.

    Returns 0.0 when fewer than 2 data points are available.
    Negative slope means values are decreasing (improving positions).

    Reuses the np.polyfit approach from fantasy_points_trend_last5 in V2.
    """
    if len(values) < 2:
        return 0.0
    return float(np.polyfit(range(len(values)), values, 1)[0])


def _compute_form_features(
    driver_rows: list[dict], event: Event
) -> dict[str, dict[int, float]]:
    """
    Computes all 7 form-direction features and returns them as a dict of
    {feature_name: {driver_id: value}} mappings, ready to be assigned to df.

    driver_rows is a list of dicts with keys: id, code, team_id.

    Design notes:
    - teammate_delta uses last1 (not last5) to capture current form vs teammate,
      providing a different signal from v3's rolling driver_vs_teammate_gap_last5.
    - team_best_position uses last5 to capture the car's ceiling — what's the
      best this car can do recently, not just last race.
    - position_slope is negative when improving (smaller position number = better).
    """
    from django.conf import settings

    DEFAULT_POS = settings.NEW_ENTRANT_POSITION_DEFAULT
    codes = [r["code"] for r in driver_rows]

    race_positions = _driver_recent_race_positions(codes, event)
    quali_positions = _driver_recent_quali_positions(codes, event)

    # Build per-driver scalar features first, keyed by driver id
    position_last1: dict[int, float] = {}
    position_slope: dict[int, float] = {}
    best_position_last5: dict[int, float] = {}
    quali_last1: dict[int, float] = {}
    quali_slope: dict[int, float] = {}

    code_to_id = {r["code"]: r["id"] for r in driver_rows}

    for r in driver_rows:
        driver_id = r["id"]
        code = r["code"]

        rpos = race_positions[code]
        qpos = quali_positions[code]

        position_last1[driver_id] = rpos[-1] if rpos else DEFAULT_POS
        position_slope[driver_id] = _ols_slope(rpos)
        best_position_last5[driver_id] = min(rpos) if rpos else DEFAULT_POS
        quali_last1[driver_id] = qpos[-1] if qpos else DEFAULT_POS
        quali_slope[driver_id] = _ols_slope(qpos)

    # Teammate delta and team best position require grouping by team
    teammate_delta: dict[int, float] = {}
    team_best_position: dict[int, float] = {}

    # Group driver rows by team_id
    from collections import defaultdict as _defaultdict
    teams: dict[int, list[dict]] = _defaultdict(list)
    for r in driver_rows:
        teams[r["team_id"]].append(r)

    for team_id, members in teams.items():
        # team_best_position: min finishing position across all team members, last 5 races
        all_race_pos: list[float] = []
        for m in members:
            all_race_pos.extend(race_positions[m["code"]])
        tbp = min(all_race_pos) if all_race_pos else DEFAULT_POS
        for m in members:
            team_best_position[m["id"]] = tbp

        # teammate_delta: only meaningful when exactly 2 drivers on the team
        if len(members) == 2:
            d1, d2 = members[0], members[1]
            delta_d1 = position_last1[d1["id"]] - position_last1[d2["id"]]
            delta_d2 = position_last1[d2["id"]] - position_last1[d1["id"]]
            teammate_delta[d1["id"]] = delta_d1
            teammate_delta[d2["id"]] = delta_d2
        else:
            for m in members:
                teammate_delta[m["id"]] = 0.0

    return {
        "position_last1": position_last1,
        "position_slope": position_slope,
        "best_position_last5": best_position_last5,
        "teammate_delta": teammate_delta,
        "team_best_position": team_best_position,
        "quali_last1": quali_last1,
        "quali_slope": quali_slope,
    }


def _fp_session_availability(event: Event) -> float:
    """
    Number of FP sessions with any lap data at this event (0–3).
    Sprint weekends typically have only FP1, giving 1.0.
    Conventional weekends give 3.0 when data collection was complete.
    """
    sessions = list(
        Session.objects.filter(
            event=event,
            session_type__in=["FP1", "FP2", "FP3"],
        ).values_list("session_type", flat=True).distinct()
    )
    return float(len(sessions))
