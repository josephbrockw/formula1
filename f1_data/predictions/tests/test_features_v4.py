from __future__ import annotations

from datetime import date

from django.test import TestCase

from predictions.features.v4 import (
    V4FeatureStore,
    _fp_long_run_pace_ranks,
    _fp_sector_ranks,
    _fp_session_availability,
    _fp_total_laps,
    _fp_tyre_deg_ranks,
    _load_fp_laps,
)
from predictions.tests.factories import (
    make_circuit,
    make_driver,
    make_event,
    make_result,
    make_season,
    make_session,
    make_team,
    make_weather_sample,
)
from predictions.tests.factories import make_lap

# V3 has 26 features; V4 adds 8 new ones.
V3_FEATURE_COUNT = 26
V4_NEW_FEATURE_COUNT = 8


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _setup_base():
    """
    Minimal world: one season, team, two drivers, target event at round 10.
    Two drivers so ranking functions have something to rank against.
    """
    season = make_season(2024)
    circuit = make_circuit(key="monaco")
    team = make_team(season, name="Ferrari", code="FER")
    d1 = make_driver(season, team, code="LEC", driver_number=16)
    d2 = make_driver(season, team, code="SAI", driver_number=55)
    target_event = make_event(
        season, round_number=10, circuit=circuit, event_date=date(2024, 10, 1)
    )
    return season, team, d1, d2, target_event


def _make_long_run(session, driver, compound="MEDIUM", stint=1, n=6, base_time=90.0, slope=0.1):
    """
    Create n laps forming a long-run stint. slope adds seconds per lap of tyre age,
    so tyre_life goes 1..n and lap time increases linearly (simulating degradation).
    """
    laps = []
    for i in range(1, n + 1):
        laps.append(
            make_lap(
                session,
                driver,
                lap_number=i,
                lap_time_seconds=base_time + slope * i,
                compound=compound,
                tyre_life=i,
                stint=stint,
                is_accurate=True,
            )
        )
    return laps


# ---------------------------------------------------------------------------
# _fp_long_run_pace_ranks
# ---------------------------------------------------------------------------


class FpLongRunPaceRanksTest(TestCase):
    def setUp(self):
        self.season, self.team, self.d1, self.d2, self.event = _setup_base()
        self.fp2 = make_session(self.event, session_type="FP2")

    def test_fastest_driver_ranked_1(self):
        # d1 has median ~90.35s, d2 has median ~100.35s — d1 is faster
        _make_long_run(self.fp2, self.d1, base_time=90.0, slope=0.1)
        _make_long_run(self.fp2, self.d2, base_time=100.0, slope=0.1)
        fp_laps = _load_fp_laps(self.event)
        ranks = _fp_long_run_pace_ranks(fp_laps, [self.d1.id, self.d2.id])
        self.assertEqual(ranks[self.d1.id], 1.0)
        self.assertEqual(ranks[self.d2.id], 2.0)

    def test_stints_fewer_than_5_laps_excluded(self):
        # Only 4 laps — too short, doesn't qualify as a long run
        for i in range(1, 5):
            make_lap(
                self.fp2, self.d1,
                lap_number=i, lap_time_seconds=90.0,
                compound="SOFT", tyre_life=i, stint=1,
            )
        # d2 has a qualifying 6-lap stint
        _make_long_run(self.fp2, self.d2, base_time=95.0)
        fp_laps = _load_fp_laps(self.event)
        ranks = _fp_long_run_pace_ranks(fp_laps, [self.d1.id, self.d2.id])
        # d1 has no qualifying stint → default 10.5
        self.assertEqual(ranks[self.d1.id], 10.5)
        self.assertEqual(ranks[self.d2.id], 1.0)

    def test_pit_laps_excluded_by_load(self):
        # is_pit_in_lap=True laps should be filtered out in _load_fp_laps
        for i in range(1, 7):
            make_lap(
                self.fp2, self.d1,
                lap_number=i, lap_time_seconds=90.0,
                compound="SOFT", tyre_life=i, stint=1,
                is_pit_in_lap=(i == 6),  # last lap is a pit lap
            )
        fp_laps = _load_fp_laps(self.event)
        # Only 5 laps pass (pit lap excluded), exactly at threshold
        driver_laps = fp_laps[fp_laps["driver_id"] == self.d1.id]
        self.assertEqual(len(driver_laps), 5)

    def test_inaccurate_laps_excluded(self):
        for i in range(1, 7):
            make_lap(
                self.fp2, self.d1,
                lap_number=i, lap_time_seconds=90.0,
                compound="SOFT", tyre_life=i, stint=1,
                is_accurate=(i != 3),  # one inaccurate lap
            )
        fp_laps = _load_fp_laps(self.event)
        driver_laps = fp_laps[fp_laps["driver_id"] == self.d1.id]
        self.assertEqual(len(driver_laps), 5)

    def test_missing_driver_gets_default(self):
        _make_long_run(self.fp2, self.d1)
        fp_laps = _load_fp_laps(self.event)
        # d2 has no laps at all
        ranks = _fp_long_run_pace_ranks(fp_laps, [self.d1.id, self.d2.id])
        self.assertEqual(ranks[self.d2.id], 10.5)

    def test_empty_fp_laps_all_default(self):
        import pandas as pd
        ranks = _fp_long_run_pace_ranks(pd.DataFrame(), [self.d1.id, self.d2.id])
        self.assertEqual(ranks[self.d1.id], 10.5)
        self.assertEqual(ranks[self.d2.id], 10.5)


# ---------------------------------------------------------------------------
# _fp_tyre_deg_ranks
# ---------------------------------------------------------------------------


class FpTyreDegRanksTest(TestCase):
    def setUp(self):
        self.season, self.team, self.d1, self.d2, self.event = _setup_base()
        self.fp2 = make_session(self.event, session_type="FP2")

    def test_highest_slope_gets_worst_rank(self):
        # d1: slope 0.5 s/lap (high deg), d2: slope 0.05 s/lap (low deg)
        _make_long_run(self.fp2, self.d1, slope=0.5)
        _make_long_run(self.fp2, self.d2, slope=0.05)
        fp_laps = _load_fp_laps(self.event)
        ranks = _fp_tyre_deg_ranks(fp_laps, [self.d1.id, self.d2.id])
        # d2 has lower slope → ranked 1
        self.assertEqual(ranks[self.d2.id], 1.0)
        self.assertEqual(ranks[self.d1.id], 2.0)

    def test_flat_pace_gets_rank_1(self):
        # slope=0 means no deg at all — should get best rank
        _make_long_run(self.fp2, self.d1, slope=0.0)
        _make_long_run(self.fp2, self.d2, slope=1.0)
        fp_laps = _load_fp_laps(self.event)
        ranks = _fp_tyre_deg_ranks(fp_laps, [self.d1.id, self.d2.id])
        self.assertEqual(ranks[self.d1.id], 1.0)

    def test_missing_driver_gets_default(self):
        _make_long_run(self.fp2, self.d1)
        fp_laps = _load_fp_laps(self.event)
        ranks = _fp_tyre_deg_ranks(fp_laps, [self.d1.id, self.d2.id])
        self.assertEqual(ranks[self.d2.id], 10.5)

    def test_empty_fp_laps_all_default(self):
        import pandas as pd
        ranks = _fp_tyre_deg_ranks(pd.DataFrame(), [self.d1.id, self.d2.id])
        self.assertEqual(ranks[self.d1.id], 10.5)


# ---------------------------------------------------------------------------
# _fp_sector_ranks
# ---------------------------------------------------------------------------


class FpSectorRanksTest(TestCase):
    def setUp(self):
        self.season, self.team, self.d1, self.d2, self.event = _setup_base()
        self.fp1 = make_session(self.event, session_type="FP1")

    def test_best_sector_time_ranks_1(self):
        make_lap(self.fp1, self.d1, lap_number=1, sector1_seconds=25.0, sector2_seconds=30.0, sector3_seconds=35.0)
        make_lap(self.fp1, self.d2, lap_number=1, sector1_seconds=26.0, sector2_seconds=29.0, sector3_seconds=36.0)
        fp_laps = _load_fp_laps(self.event)
        ranks = _fp_sector_ranks(fp_laps, [self.d1.id, self.d2.id])
        # d1 faster in S1 and S3, d2 faster in S2
        self.assertEqual(ranks[self.d1.id][0], 1.0)  # S1
        self.assertEqual(ranks[self.d2.id][1], 1.0)  # S2
        self.assertEqual(ranks[self.d1.id][2], 1.0)  # S3

    def test_null_sector_times_ignored(self):
        # d1 has sector data, d2 has no sector data
        make_lap(self.fp1, self.d1, lap_number=1, sector1_seconds=25.0, sector2_seconds=30.0, sector3_seconds=35.0)
        make_lap(self.fp1, self.d2, lap_number=1)  # no sector times
        fp_laps = _load_fp_laps(self.event)
        ranks = _fp_sector_ranks(fp_laps, [self.d1.id, self.d2.id])
        self.assertEqual(ranks[self.d1.id][0], 1.0)
        self.assertEqual(ranks[self.d2.id][0], 10.5)  # no data → default

    def test_missing_driver_gets_default(self):
        import pandas as pd
        ranks = _fp_sector_ranks(pd.DataFrame(), [self.d1.id, self.d2.id])
        self.assertEqual(ranks[self.d1.id], (10.5, 10.5, 10.5))


# ---------------------------------------------------------------------------
# _fp_total_laps
# ---------------------------------------------------------------------------


class FpTotalLapsTest(TestCase):
    def setUp(self):
        self.season, self.team, self.d1, self.d2, self.event = _setup_base()

    def test_counts_all_fp_laps(self):
        fp1 = make_session(self.event, session_type="FP1")
        fp2 = make_session(self.event, session_type="FP2")
        make_lap(fp1, self.d1, lap_number=1)
        make_lap(fp1, self.d1, lap_number=2)
        make_lap(fp2, self.d1, lap_number=1)
        fp_laps = _load_fp_laps(self.event)
        counts = _fp_total_laps(fp_laps, [self.d1.id, self.d2.id])
        self.assertEqual(counts[self.d1.id], 3.0)

    def test_missing_driver_gets_zero(self):
        fp1 = make_session(self.event, session_type="FP1")
        make_lap(fp1, self.d1, lap_number=1)
        fp_laps = _load_fp_laps(self.event)
        counts = _fp_total_laps(fp_laps, [self.d1.id, self.d2.id])
        self.assertEqual(counts[self.d2.id], 0.0)

    def test_pit_laps_excluded(self):
        fp1 = make_session(self.event, session_type="FP1")
        make_lap(fp1, self.d1, lap_number=1)
        make_lap(fp1, self.d1, lap_number=2, is_pit_in_lap=True)  # excluded
        fp_laps = _load_fp_laps(self.event)
        counts = _fp_total_laps(fp_laps, [self.d1.id])
        self.assertEqual(counts[self.d1.id], 1.0)


# ---------------------------------------------------------------------------
# _fp_session_availability
# ---------------------------------------------------------------------------


class FpSessionAvailabilityTest(TestCase):
    def setUp(self):
        self.season, self.team, self.d1, self.d2, self.event = _setup_base()

    def test_only_fp1_returns_1(self):
        make_session(self.event, session_type="FP1")
        self.assertEqual(_fp_session_availability(self.event), 1.0)

    def test_fp1_fp2_fp3_returns_3(self):
        make_session(self.event, session_type="FP1")
        make_session(self.event, session_type="FP2")
        make_session(self.event, session_type="FP3")
        self.assertEqual(_fp_session_availability(self.event), 3.0)

    def test_no_fp_sessions_returns_0(self):
        self.assertEqual(_fp_session_availability(self.event), 0.0)


# ---------------------------------------------------------------------------
# fp_short_vs_long_delta (integration)
# ---------------------------------------------------------------------------


class FpShortVsLongDeltaTest(TestCase):
    def setUp(self):
        self.season, self.team, self.d1, self.d2, self.event = _setup_base()
        # Add a qualifying session so V1 can compute practice_best_lap_rank
        self.fp1 = make_session(self.event, session_type="FP1")
        self.fp2 = make_session(self.event, session_type="FP2")
        self.fp3 = make_session(self.event, session_type="FP3")
        self.race = make_session(self.event, session_type="R")
        make_result(self.race, self.d1, self.team, position=1)
        make_result(self.race, self.d2, self.team, position=2)

    def test_delta_is_difference_of_ranks(self):
        # d1: fast short run (rank 1 in FP best lap), slower long run (rank 2)
        # d2: slower short run (rank 2 in FP best lap), faster long run (rank 1)
        make_lap(self.fp1, self.d1, lap_number=1, lap_time_seconds=85.0)   # d1 fastest short
        make_lap(self.fp1, self.d2, lap_number=1, lap_time_seconds=87.0)   # d2 slower short
        # Long runs: d2 faster pace
        _make_long_run(self.fp2, self.d1, base_time=91.0, slope=0.1)
        _make_long_run(self.fp2, self.d2, base_time=89.0, slope=0.1)

        df = V4FeatureStore().get_all_driver_features(self.event.id)
        d1_row = df[df["driver_id"] == self.d1.id].iloc[0]
        d2_row = df[df["driver_id"] == self.d2.id].iloc[0]

        # d1: short rank 1, long rank 2 → delta = 1 - 2 = -1 (better in short)
        self.assertAlmostEqual(
            d1_row["fp_short_vs_long_delta"],
            d1_row["practice_best_lap_rank"] - d1_row["fp_long_run_pace_rank"],
        )
        self.assertAlmostEqual(
            d2_row["fp_short_vs_long_delta"],
            d2_row["practice_best_lap_rank"] - d2_row["fp_long_run_pace_rank"],
        )


# ---------------------------------------------------------------------------
# Feature count assertion
# ---------------------------------------------------------------------------


class V4FeatureCountTest(TestCase):
    def setUp(self):
        self.season, self.team, self.d1, self.d2, self.event = _setup_base()
        for stype in ["FP1", "FP2", "FP3", "R"]:
            s = make_session(self.event, session_type=stype)
            make_result(s, self.d1, self.team, position=1)
            make_result(s, self.d2, self.team, position=2)
            make_weather_sample(s)
            if stype in ("FP1", "FP2", "FP3"):
                make_lap(s, self.d1, lap_number=1)
                make_lap(s, self.d2, lap_number=1)

    def test_v4_adds_exactly_8_features_over_v3(self):
        from predictions.features.v3_pandas import V3FeatureStore
        v3_df = V3FeatureStore().get_all_driver_features(self.event.id)
        v4_df = V4FeatureStore().get_all_driver_features(self.event.id)
        v3_cols = set(v3_df.columns)
        v4_cols = set(v4_df.columns)
        new_cols = v4_cols - v3_cols
        self.assertEqual(len(new_cols), V4_NEW_FEATURE_COUNT, f"New columns: {new_cols}")


# ---------------------------------------------------------------------------
# Sprint weekend — sensible defaults when only FP1 available
# ---------------------------------------------------------------------------


class SprintWeekendTest(TestCase):
    def setUp(self):
        season = make_season(2024)
        circuit = make_circuit(key="sprint")
        team = make_team(season, name="Red Bull", code="RBR")
        self.d1 = make_driver(season, team, code="VER", driver_number=1)
        self.d2 = make_driver(season, team, code="PER", driver_number=11)
        self.event = make_event(
            season, round_number=5, circuit=circuit,
            event_format="sprint", event_date=date(2024, 5, 1)
        )
        # Sprint: only FP1 available
        self.fp1 = make_session(self.event, session_type="FP1")
        race = make_session(self.event, session_type="R")
        make_result(race, self.d1, team, position=1)
        make_result(race, self.d2, team, position=2)
        make_weather_sample(self.fp1)

    def test_session_availability_is_1(self):
        self.assertEqual(_fp_session_availability(self.event), 1.0)

    def test_long_run_defaults_when_no_fp2(self):
        # No FP2 data → long run ranks should be default 10.5
        make_lap(self.fp1, self.d1, lap_number=1)
        fp_laps = _load_fp_laps(self.event)
        ranks = _fp_long_run_pace_ranks(fp_laps, [self.d1.id, self.d2.id])
        # No qualifying stints (only 1 lap) → defaults
        self.assertEqual(ranks[self.d1.id], 10.5)
        self.assertEqual(ranks[self.d2.id], 10.5)

    def test_v4_features_have_sensible_values(self):
        make_lap(self.fp1, self.d1, lap_number=1)
        df = V4FeatureStore().get_all_driver_features(self.event.id)
        self.assertFalse(df.empty)
        # All rank features should be non-negative
        for col in ["fp_long_run_pace_rank", "fp_tyre_deg_rank",
                    "fp_sector1_rank", "fp_sector2_rank", "fp_sector3_rank"]:
            self.assertTrue((df[col] >= 0).all(), f"{col} has negative values")
        # fp_total_laps should be ≥ 0
        self.assertTrue((df["fp_total_laps"] >= 0).all())
        # fp_session_availability should be 1.0 (only FP1)
        self.assertTrue((df["fp_session_availability"] == 1.0).all())
