from __future__ import annotations

import numpy as np
import pandas as pd
from django.conf import settings

from core.models import Event, Lap, Session
from predictions.features.v3_pandas import V3FeatureStore

# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class V4FeatureStore:
    """
    Extends V3FeatureStore with 8 new practice-telemetry features.

    These features are derived from FP lap data — long-run pace, tyre
    degradation, sector times, lap counts, and session availability.
    None of these signals were available in V1–V3.
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
