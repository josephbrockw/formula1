"""
Pure functions for reconstructing F1 Fantasy scores from FastF1 session data.

No DB access. All functions take Python primitives and return lists of ScoreRow tuples.
Score row: (event_type, scoring_item, frequency, position, points)
"""

from __future__ import annotations

# (event_type, scoring_item, frequency, position, points)
ScoreRow = tuple[str, str, int | None, int | None, int]

# ---------------------------------------------------------------------------
# Scoring tables
# ---------------------------------------------------------------------------

RACE_POSITION_POINTS = {1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1}
SPRINT_POSITION_POINTS = {1: 8, 2: 7, 3: 6, 4: 5, 5: 4, 6: 3, 7: 2, 8: 1}
QUAL_POSITION_POINTS = {1: 10, 2: 9, 3: 8, 4: 7, 5: 6, 6: 5, 7: 4, 8: 3, 9: 2, 10: 1}

RACE_DNF_POINTS = -20
RACE_DSQ_POINTS = -20
QUAL_NO_TIME_POINTS = -5
SPRINT_NO_TIME_POINTS = -10
FASTEST_LAP_POINTS = 10
OVERTAKE_POINTS = 1
POSITIONS_GAINED_POINTS = 1
POSITIONS_LOST_POINTS = -1

# (drivers_in_q3, drivers_in_q2_only) → bonus points for the constructor
Q_PROGRESSION_POINTS: dict[tuple[int, int], int] = {
    (2, 0): 10,  # Both Q3
    (1, 1): 5,   # One Q3, one Q2
    (1, 0): 5,   # One Q3, other Q1
    (0, 2): 3,   # Both Q2
    (0, 1): 1,   # One Q2
    (0, 0): -1,  # Neither Q2
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_dnf(status: str, position: int | None) -> bool:
    return position is None and not status.startswith("+") and status != "Finished"


def _is_dsq(classified_position: str | None) -> bool:
    return str(classified_position).upper() == "D"


def _count_overtakes(laps: list[tuple[int | None, bool, bool]]) -> int:
    """
    Count position improvements on non-pit laps.

    Input: list of (lap_position, is_pit_in_lap, is_pit_out_lap) tuples in lap order.
    A lap is excluded if it or the preceding lap is a pit lap.
    """
    overtakes = 0
    for i in range(1, len(laps)):
        pos_prev, pit_in_prev, pit_out_prev = laps[i - 1]
        pos_curr, pit_in_curr, pit_out_curr = laps[i]
        if pit_in_prev or pit_out_prev or pit_in_curr or pit_out_curr:
            continue
        if pos_prev is not None and pos_curr is not None and pos_curr < pos_prev:
            overtakes += pos_prev - pos_curr
    return overtakes


# ---------------------------------------------------------------------------
# Driver scoring
# ---------------------------------------------------------------------------


def score_driver_race(
    position: int | None,
    grid_position: int | None,
    status: str,
    classified_position: str | None,
    fastest_lap_rank: int | None,
    laps: list[tuple[int | None, bool, bool]],
    session_type: str = "R",
) -> list[ScoreRow]:
    """
    Score a driver's race or sprint result.

    session_type: "R" for race, "S" for sprint.
    laps: list of (lap_position, is_pit_in_lap, is_pit_out_lap).
    """
    event_type = "race" if session_type == "R" else "sprint"
    position_table = RACE_POSITION_POINTS if session_type == "R" else SPRINT_POSITION_POINTS
    position_label = "Race Position" if session_type == "R" else "Sprint Position"
    dnf_label = "Race DNF" if session_type == "R" else "Sprint DNF"
    dsq_label = "Race DSQ" if session_type == "R" else "Sprint DSQ"
    rows: list[ScoreRow] = []

    if _is_dsq(classified_position):
        rows.append((event_type, dsq_label, None, None, RACE_DSQ_POINTS))
        return rows

    if _is_dnf(status, position):
        rows.append((event_type, dnf_label, None, None, RACE_DNF_POINTS))
        return rows

    if position is not None and position in position_table:
        rows.append((event_type, position_label, None, position, position_table[position]))

    if position is not None and grid_position is not None:
        delta = grid_position - position  # positive = gained, negative = lost
        if delta > 0:
            rows.append((event_type, "Positions Gained", delta, None, delta))
        elif delta < 0:
            rows.append((event_type, "Positions Lost", abs(delta), None, delta))

    if fastest_lap_rank == 1:
        rows.append((event_type, "Fastest Lap", None, None, FASTEST_LAP_POINTS))

    overtakes = _count_overtakes(laps)
    if overtakes > 0:
        rows.append((event_type, "Overtake Bonus", overtakes, None, overtakes * OVERTAKE_POINTS))

    return rows


def score_driver_qualifying(
    position: int | None,
    status: str,
    classified_position: str | None,
    session_type: str = "Q",
) -> list[ScoreRow]:
    """
    Score a driver's qualifying or sprint qualifying result.

    session_type: "Q" for qualifying, "SQ" for sprint qualifying.
    """
    event_type = "qualifying" if session_type == "Q" else "sprint_qualifying"
    position_label = "Qualifying Position" if session_type == "Q" else "Sprint Qualifying Position"
    no_time_label = "Qualifying No Time" if session_type == "Q" else "Sprint Qualifying No Time"
    no_time_points = QUAL_NO_TIME_POINTS if session_type == "Q" else SPRINT_NO_TIME_POINTS
    rows: list[ScoreRow] = []

    if _is_dsq(classified_position):
        return rows  # DSQ in qual — no points, no penalty

    if position is not None and position in QUAL_POSITION_POINTS:
        rows.append((event_type, position_label, None, position, QUAL_POSITION_POINTS[position]))
        return rows

    if position is None and not _is_dnf(status, position):
        rows.append((event_type, no_time_label, None, None, no_time_points))

    return rows


# ---------------------------------------------------------------------------
# Constructor scoring
# ---------------------------------------------------------------------------


def score_constructor_q_progression(driver_qual_positions: list[int | None]) -> ScoreRow:
    """
    Compute the Q progression bonus for a constructor given their two drivers' qual positions.

    Positions 1–10 = Q3, 11–15 = Q2, 16+ or None = Q1 only.
    """
    q3 = sum(1 for p in driver_qual_positions if p is not None and p <= 10)
    q2_only = sum(1 for p in driver_qual_positions if p is not None and 11 <= p <= 15)
    key = (q3, q2_only)
    pts = Q_PROGRESSION_POINTS.get(key, -1)
    return ("qualifying", "Q Progression", None, None, pts)
