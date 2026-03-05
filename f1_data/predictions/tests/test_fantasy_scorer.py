from __future__ import annotations

from django.test import SimpleTestCase

from predictions.fantasy_scorer import (
    _count_overtakes,
    score_constructor_q_progression,
    score_driver_qualifying,
    score_driver_race,
)


class TestScoreDriverRace(SimpleTestCase):
    def _race(self, position=1, grid=1, status="Finished", classified=None, fastest_lap_rank=None, laps=None):
        return score_driver_race(
            position=position,
            grid_position=grid,
            status=status,
            classified_position=classified or str(position) if position else "R",
            fastest_lap_rank=fastest_lap_rank,
            laps=laps or [],
            session_type="R",
        )

    def test_p1_scores_25_points(self) -> None:
        rows = self._race(position=1, grid=1)
        pts = {item: p for _, item, _, _, p in rows}
        self.assertEqual(pts["Race Position"], 25)

    def test_p10_scores_1_point(self) -> None:
        rows = self._race(position=10, grid=10)
        pts = {item: p for _, item, _, _, p in rows}
        self.assertEqual(pts["Race Position"], 1)

    def test_p11_scores_no_position_points(self) -> None:
        rows = self._race(position=11, grid=11)
        items = [item for _, item, _, _, _ in rows]
        self.assertNotIn("Race Position", items)

    def test_dnf_scores_minus_20(self) -> None:
        rows = score_driver_race(
            position=None,
            grid_position=5,
            status="Engine",
            classified_position="R",
            fastest_lap_rank=None,
            laps=[],
            session_type="R",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][4], -20)
        self.assertEqual(rows[0][1], "Race DNF")

    def test_dnf_gives_no_position_points(self) -> None:
        rows = score_driver_race(
            position=None,
            grid_position=5,
            status="Engine",
            classified_position="R",
            fastest_lap_rank=None,
            laps=[],
            session_type="R",
        )
        items = [item for _, item, _, _, _ in rows]
        self.assertNotIn("Race Position", items)

    def test_dsq_scores_minus_20(self) -> None:
        rows = score_driver_race(
            position=1,
            grid_position=1,
            status="Finished",
            classified_position="D",
            fastest_lap_rank=None,
            laps=[],
            session_type="R",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][4], -20)
        self.assertEqual(rows[0][1], "Race DSQ")

    def test_positions_gained_grid5_finish2(self) -> None:
        rows = self._race(position=2, grid=5)
        pts = {item: p for _, item, _, _, p in rows}
        self.assertEqual(pts["Positions Gained"], 3)

    def test_positions_lost_grid2_finish5(self) -> None:
        rows = self._race(position=5, grid=2)
        pts = {item: p for _, item, _, _, p in rows}
        self.assertEqual(pts["Positions Lost"], -3)

    def test_no_positions_row_when_same_position(self) -> None:
        rows = self._race(position=3, grid=3)
        items = [item for _, item, _, _, _ in rows]
        self.assertNotIn("Positions Gained", items)
        self.assertNotIn("Positions Lost", items)

    def test_fastest_lap_rank_1_scores_10(self) -> None:
        rows = self._race(position=3, grid=3, fastest_lap_rank=1)
        pts = {item: p for _, item, _, _, p in rows}
        self.assertEqual(pts["Fastest Lap"], 10)

    def test_fastest_lap_rank_2_scores_nothing(self) -> None:
        rows = self._race(position=3, grid=3, fastest_lap_rank=2)
        items = [item for _, item, _, _, _ in rows]
        self.assertNotIn("Fastest Lap", items)

    def test_overtakes_score_1_point_each(self) -> None:
        # Three clean position improvements, no pit laps
        laps = [(5, False, False), (4, False, False), (3, False, False), (2, False, False)]
        rows = self._race(position=2, grid=5, laps=laps)
        pts = {item: (freq, p) for _, item, freq, _, p in rows}
        self.assertIn("Overtake Bonus", pts)
        freq, p = pts["Overtake Bonus"]
        self.assertEqual(freq, 3)
        self.assertEqual(p, 3)

    def test_no_overtake_row_when_no_improvements(self) -> None:
        laps = [(2, False, False), (3, False, False), (4, False, False)]
        rows = self._race(position=4, grid=2, laps=laps)
        items = [item for _, item, _, _, _ in rows]
        self.assertNotIn("Overtake Bonus", items)


class TestCountOvertakes(SimpleTestCase):
    def test_three_improvements_count_3(self) -> None:
        laps = [(5, False, False), (4, False, False), (3, False, False), (2, False, False)]
        self.assertEqual(_count_overtakes(laps), 3)

    def test_pit_in_lap_excluded(self) -> None:
        # Improvement on lap after pit-in should be excluded
        laps = [(5, True, False), (3, False, False), (2, False, False)]
        # lap 0→1: pit_in_prev=True → skip; lap 1→2: clean, improvement 3→2 = 1
        self.assertEqual(_count_overtakes(laps), 1)

    def test_pit_out_lap_excluded(self) -> None:
        laps = [(5, False, False), (3, False, True), (1, False, False)]
        # lap 0→1: pit_out_curr=True → skip; lap 1→2: pit_out_prev=True → skip
        self.assertEqual(_count_overtakes(laps), 0)

    def test_multi_position_jump_counts_correctly(self) -> None:
        laps = [(8, False, False), (5, False, False)]
        self.assertEqual(_count_overtakes(laps), 3)

    def test_empty_laps_returns_0(self) -> None:
        self.assertEqual(_count_overtakes([]), 0)

    def test_none_position_skipped(self) -> None:
        laps = [(None, False, False), (3, False, False)]
        self.assertEqual(_count_overtakes(laps), 0)


class TestScoreDriverQualifying(SimpleTestCase):
    def test_q_p1_scores_10(self) -> None:
        rows = score_driver_qualifying(position=1, status="Finished", classified_position="1")
        self.assertEqual(rows[0][4], 10)

    def test_q_p10_scores_1(self) -> None:
        rows = score_driver_qualifying(position=10, status="Finished", classified_position="10")
        self.assertEqual(rows[0][4], 1)

    def test_q_p11_scores_0(self) -> None:
        rows = score_driver_qualifying(position=11, status="Finished", classified_position="11")
        self.assertEqual(rows, [])

    def test_q_no_time_scores_minus_5(self) -> None:
        rows = score_driver_qualifying(position=None, status="Finished", classified_position=None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][4], -5)

    def test_sprint_qualifying_p1_scores_10(self) -> None:
        rows = score_driver_qualifying(position=1, status="Finished", classified_position="1", session_type="SQ")
        self.assertEqual(rows[0][4], 10)

    def test_sprint_qualifying_no_time_scores_minus_10(self) -> None:
        rows = score_driver_qualifying(position=None, status="Finished", classified_position=None, session_type="SQ")
        self.assertEqual(rows[0][4], -10)


class TestScoreSprint(SimpleTestCase):
    def test_sprint_p1_scores_8(self) -> None:
        rows = score_driver_race(
            position=1, grid_position=1, status="Finished",
            classified_position="1", fastest_lap_rank=None, laps=[], session_type="S",
        )
        pts = {item: p for _, item, _, _, p in rows}
        self.assertEqual(pts["Sprint Position"], 8)

    def test_sprint_p8_scores_1(self) -> None:
        rows = score_driver_race(
            position=8, grid_position=8, status="Finished",
            classified_position="8", fastest_lap_rank=None, laps=[], session_type="S",
        )
        pts = {item: p for _, item, _, _, p in rows}
        self.assertEqual(pts["Sprint Position"], 1)

    def test_sprint_p9_scores_nothing(self) -> None:
        rows = score_driver_race(
            position=9, grid_position=9, status="Finished",
            classified_position="9", fastest_lap_rank=None, laps=[], session_type="S",
        )
        items = [item for _, item, _, _, _ in rows]
        self.assertNotIn("Sprint Position", items)


class TestConstructorQProgression(SimpleTestCase):
    def test_both_q3_scores_10(self) -> None:
        row = score_constructor_q_progression([1, 5])
        self.assertEqual(row[4], 10)

    def test_one_q3_one_q2_scores_5(self) -> None:
        row = score_constructor_q_progression([3, 12])
        self.assertEqual(row[4], 5)

    def test_one_q3_one_q1_scores_5(self) -> None:
        row = score_constructor_q_progression([3, 18])
        self.assertEqual(row[4], 5)

    def test_both_q2_scores_3(self) -> None:
        row = score_constructor_q_progression([11, 15])
        self.assertEqual(row[4], 3)

    def test_one_q2_one_q1_scores_1(self) -> None:
        row = score_constructor_q_progression([13, 18])
        self.assertEqual(row[4], 1)

    def test_both_q1_scores_minus_1(self) -> None:
        row = score_constructor_q_progression([16, 18])
        self.assertEqual(row[4], -1)

    def test_none_positions_treated_as_q1(self) -> None:
        row = score_constructor_q_progression([None, None])
        self.assertEqual(row[4], -1)
