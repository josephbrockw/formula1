"""
Unit tests for import_all_seasons_flow and collect_all_data management command.

All FastF1 / Prefect / Slack calls are mocked — no network or DB queries beyond
what Django's test runner sets up.

Test classes:
- CountByYearTests          — _count_by_year helper
- ImportAllSeasonsFlowTests — flow summary, counters, context calls, ordering
- CollectAllDataDryRunTests — --dry-run output formatting
- CollectAllDataCommandTests — normal run, flag passthrough, error handling
"""

from datetime import datetime
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from analytics.processing.gap_detection import SessionGap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gap(year=2025, round_number=1, session_type='Race', session_id=None):
    """Build a minimal SessionGap for testing."""
    if session_id is None:
        session_id = round_number
    return SessionGap(
        session_id=session_id,
        year=year,
        round_number=round_number,
        session_type=session_type,
        session_number=5,
        missing_weather=True,
        missing_drivers=True,
        missing_telemetry=True,
        missing_pit_stops=True,
        missing_circuit=True,
    )


def _success_result(gap):
    return {
        'session_id': gap.session_id,
        'year': gap.year,
        'round': gap.round_number,
        'session_type': gap.session_type,
        'extracted': ['drivers', 'weather', 'circuit', 'telemetry'],
        'failed': [],
        'status': 'success',
    }


def _partial_result(gap):
    return {
        'session_id': gap.session_id,
        'year': gap.year,
        'round': gap.round_number,
        'session_type': gap.session_type,
        'extracted': ['weather'],
        'failed': ['telemetry'],
        'status': 'partial',
    }


def _failed_result(gap):
    return {
        'session_id': gap.session_id,
        'year': gap.year,
        'round': gap.round_number,
        'session_type': gap.session_type,
        'extracted': [],
        'failed': ['weather', 'telemetry'],
        'status': 'failed',
        'error': 'FastF1 connection error',
    }


def _mock_summary(**overrides):
    base = {
        'start_year': 2025,
        'end_year': 2025,
        'gaps_detected': 3,
        'sessions_processed': 3,
        'sessions_succeeded': 3,
        'sessions_failed': 0,
        'data_extracted': {'weather': 3, 'circuit': 3, 'telemetry': 3},
        'by_year': {2025: {'detected': 3, 'succeeded': 3, 'failed': 0}},
        'status': 'complete',
        'duration_seconds': 42.5,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _count_by_year
# ---------------------------------------------------------------------------

class CountByYearTests(TestCase):
    """Tests for the _count_by_year helper."""

    def _fn(self):
        from analytics.flows.import_all_seasons import _count_by_year
        return _count_by_year

    def test_counts_gaps_grouped_by_year(self):
        gaps = [
            _make_gap(year=2025, round_number=1),
            _make_gap(year=2025, round_number=2),
            _make_gap(year=2024, round_number=1),
        ]
        self.assertEqual(self._fn()(gaps), {2025: 2, 2024: 1})

    def test_empty_list_returns_empty_dict(self):
        self.assertEqual(self._fn()([]), {})

    def test_single_year_all_in_one_bucket(self):
        gaps = [_make_gap(year=2023)] * 4
        self.assertEqual(self._fn()(gaps), {2023: 4})

    def test_each_year_counted_independently(self):
        gaps = [
            _make_gap(year=2022),
            _make_gap(year=2023),
            _make_gap(year=2024),
        ]
        result = self._fn()(gaps)
        self.assertEqual(result, {2022: 1, 2023: 1, 2024: 1})


# ---------------------------------------------------------------------------
# import_all_seasons_flow
# ---------------------------------------------------------------------------

@mock.patch('analytics.flows.import_all_seasons.get_run_logger')
@mock.patch('analytics.flows.import_all_seasons.clear_run_context')
@mock.patch('analytics.flows.import_all_seasons.update_run_context')
@mock.patch('analytics.flows.import_all_seasons.process_session_gap')
@mock.patch('analytics.flows.import_all_seasons.get_sessions_to_process')
class ImportAllSeasonsFlowTests(TestCase):
    """Tests for import_all_seasons_flow logic.

    All Prefect tasks and run-context helpers are mocked.
    The flow is called directly; Prefect runs it synchronously in local mode.
    """

    def _flow(self):
        from analytics.flows.import_all_seasons import import_all_seasons_flow
        return import_all_seasons_flow

    # --- basic return shape --------------------------------------------------

    def test_returns_complete_with_no_gaps(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        mock_sessions.return_value = []
        result = self._flow()(start_year=2025, end_year=2025)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['sessions_processed'], 0)
        self.assertEqual(result['gaps_detected'], 0)
        mock_process.assert_not_called()

    def test_summary_contains_expected_keys(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        mock_sessions.return_value = []
        result = self._flow()(start_year=2025, end_year=2025)
        for key in (
            'start_year', 'end_year', 'gaps_detected', 'sessions_processed',
            'sessions_succeeded', 'sessions_failed', 'data_extracted', 'by_year',
            'status', 'duration_seconds',
        ):
            self.assertIn(key, result, f"Missing key: {key}")

    def test_duration_seconds_is_non_negative(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        mock_sessions.return_value = []
        result = self._flow()(start_year=2025, end_year=2025)
        self.assertGreaterEqual(result['duration_seconds'], 0)

    # --- session counting ----------------------------------------------------

    def test_processes_all_gaps_across_years(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        gaps_2025 = [_make_gap(year=2025, round_number=1, session_id=1)]
        gaps_2024 = [
            _make_gap(year=2024, round_number=1, session_id=2),
            _make_gap(year=2024, round_number=2, session_id=3),
        ]
        mock_sessions.side_effect = lambda year, force: {2025: gaps_2025, 2024: gaps_2024}[year]
        mock_process.return_value = _success_result(gaps_2025[0])

        result = self._flow()(start_year=2024, end_year=2025)

        self.assertEqual(result['gaps_detected'], 3)
        self.assertEqual(result['sessions_processed'], 3)
        self.assertEqual(mock_process.call_count, 3)

    def test_succeeded_count_correct_for_all_successes(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        gaps = [_make_gap(year=2025, round_number=i, session_id=i) for i in range(1, 4)]
        mock_sessions.return_value = gaps
        mock_process.return_value = _success_result(gaps[0])

        result = self._flow()(start_year=2025, end_year=2025)

        self.assertEqual(result['sessions_succeeded'], 3)
        self.assertEqual(result['sessions_failed'], 0)

    def test_partial_sessions_count_as_succeeded(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        gap = _make_gap()
        mock_sessions.return_value = [gap]
        mock_process.return_value = _partial_result(gap)

        result = self._flow()(start_year=2025, end_year=2025)

        self.assertEqual(result['sessions_succeeded'], 1)
        self.assertEqual(result['sessions_failed'], 0)

    def test_failed_sessions_counted_separately(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        gap1 = _make_gap(round_number=1, session_id=1)
        gap2 = _make_gap(round_number=2, session_id=2)
        mock_sessions.return_value = [gap1, gap2]
        mock_process.side_effect = [_success_result(gap1), _failed_result(gap2)]

        result = self._flow()(start_year=2025, end_year=2025)

        self.assertEqual(result['sessions_succeeded'], 1)
        self.assertEqual(result['sessions_failed'], 1)
        # Overall status is still 'complete' — individual failures don't abort the run
        self.assertEqual(result['status'], 'complete')

    # --- data_extracted accumulation -----------------------------------------

    def test_accumulates_data_extracted_across_sessions(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        gaps = [_make_gap(round_number=i, session_id=i) for i in range(1, 4)]
        mock_sessions.return_value = gaps
        mock_process.return_value = {
            **_success_result(gaps[0]),
            'extracted': ['weather', 'circuit', 'telemetry'],
        }

        result = self._flow()(start_year=2025, end_year=2025)

        self.assertEqual(result['data_extracted']['weather'], 3)
        self.assertEqual(result['data_extracted']['circuit'], 3)
        self.assertEqual(result['data_extracted']['telemetry'], 3)

    def test_failed_sessions_do_not_increment_data_extracted(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        gap = _make_gap()
        mock_sessions.return_value = [gap]
        mock_process.return_value = _failed_result(gap)

        result = self._flow()(start_year=2025, end_year=2025)

        self.assertEqual(result['data_extracted']['weather'], 0)
        self.assertEqual(result['data_extracted']['telemetry'], 0)

    # --- by_year breakdown ---------------------------------------------------

    def test_by_year_tracks_detected_succeeded_failed(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        gap1 = _make_gap(year=2025, round_number=1, session_id=1)
        gap2 = _make_gap(year=2024, round_number=1, session_id=2)
        mock_sessions.side_effect = lambda year, force: {2025: [gap1], 2024: [gap2]}[year]
        mock_process.side_effect = [_success_result(gap1), _failed_result(gap2)]

        result = self._flow()(start_year=2024, end_year=2025)

        self.assertEqual(result['by_year'][2025]['detected'], 1)
        self.assertEqual(result['by_year'][2025]['succeeded'], 1)
        self.assertEqual(result['by_year'][2025]['failed'], 0)
        self.assertEqual(result['by_year'][2024]['detected'], 1)
        self.assertEqual(result['by_year'][2024]['succeeded'], 0)
        self.assertEqual(result['by_year'][2024]['failed'], 1)

    def test_by_year_zero_gaps_entry_present(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        """Years with no gaps should still appear in by_year with detected=0."""
        mock_sessions.side_effect = lambda year, force: {2025: [], 2024: []}[year]

        result = self._flow()(start_year=2024, end_year=2025)

        self.assertIn(2025, result['by_year'])
        self.assertIn(2024, result['by_year'])
        self.assertEqual(result['by_year'][2025]['detected'], 0)

    # --- year ordering -------------------------------------------------------

    def test_gap_detection_called_in_descending_year_order(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        mock_sessions.return_value = []

        self._flow()(start_year=2022, end_year=2024)

        years_called = [c[1]['year'] for c in mock_sessions.call_args_list]
        self.assertEqual(years_called, [2024, 2023, 2022])

    def test_single_year_range_calls_gap_detection_once(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        mock_sessions.return_value = []

        self._flow()(start_year=2025, end_year=2025)

        self.assertEqual(mock_sessions.call_count, 1)
        self.assertEqual(mock_sessions.call_args[1]['year'], 2025)

    # --- force flag ----------------------------------------------------------

    def test_force_flag_passed_to_get_sessions(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        mock_sessions.return_value = []

        self._flow()(start_year=2025, end_year=2025, force=True)

        for call in mock_sessions.call_args_list:
            self.assertTrue(call[1]['force'])

    def test_force_false_by_default(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        mock_sessions.return_value = []

        self._flow()(start_year=2025, end_year=2025)

        for call in mock_sessions.call_args_list:
            self.assertFalse(call[1]['force'])

    def test_force_passed_to_process_session_gap(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        gap = _make_gap()
        mock_sessions.return_value = [gap]
        mock_process.return_value = _success_result(gap)

        self._flow()(start_year=2025, end_year=2025, force=True)

        _gap_arg, force_arg = mock_process.call_args[0]
        self.assertTrue(force_arg)

    # --- run context calls ---------------------------------------------------

    def test_clear_run_context_called_at_start_and_in_finally(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        mock_sessions.return_value = []

        self._flow()(start_year=2025, end_year=2025)

        # Once explicitly at start, once in finally block
        self.assertEqual(mock_clear.call_count, 2)

    def test_clear_run_context_called_even_after_exception(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        mock_sessions.side_effect = RuntimeError('gap detection failure')

        result = self._flow()(start_year=2025, end_year=2025)

        self.assertEqual(result['status'], 'failed')
        # clear_run_context must still be called (in finally)
        self.assertEqual(mock_clear.call_count, 2)

    def test_update_run_context_called_once_per_session(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        gaps = [_make_gap(round_number=i, session_id=i) for i in range(1, 5)]
        mock_sessions.return_value = gaps
        mock_process.return_value = _success_result(gaps[0])

        self._flow()(start_year=2025, end_year=2025)

        self.assertEqual(mock_update.call_count, 4)

    def test_update_run_context_first_call_has_all_gaps_remaining(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        """Before the first session is processed, all gaps should be 'remaining'."""
        gaps = [_make_gap(round_number=i, session_id=i) for i in range(1, 3)]
        mock_sessions.return_value = gaps
        mock_process.return_value = _success_result(gaps[0])

        self._flow()(start_year=2025, end_year=2025)

        first_kwargs = mock_update.call_args_list[0][1]
        self.assertEqual(first_kwargs['sessions_done'], 0)
        self.assertEqual(first_kwargs['sessions_remaining_by_year'], {2025: 2})

    def test_update_run_context_second_call_decrements_remaining(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        gaps = [_make_gap(round_number=i, session_id=i) for i in range(1, 3)]
        mock_sessions.return_value = gaps
        mock_process.return_value = _success_result(gaps[0])

        self._flow()(start_year=2025, end_year=2025)

        second_kwargs = mock_update.call_args_list[1][1]
        self.assertEqual(second_kwargs['sessions_done'], 1)
        self.assertEqual(second_kwargs['sessions_remaining_by_year'], {2025: 1})

    def test_update_run_context_tracks_succeeded_failed_counts(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        gap1 = _make_gap(round_number=1, session_id=1)
        gap2 = _make_gap(round_number=2, session_id=2)
        mock_sessions.return_value = [gap1, gap2]
        mock_process.side_effect = [_success_result(gap1), _failed_result(gap2)]

        self._flow()(start_year=2025, end_year=2025)

        # Second update_run_context call should reflect 1 succeeded, 0 failed
        # (it's called BEFORE processing gap2, so at that point only gap1 has finished)
        second_kwargs = mock_update.call_args_list[1][1]
        self.assertEqual(second_kwargs['sessions_succeeded'], 1)
        self.assertEqual(second_kwargs['sessions_failed'], 0)

    # --- error handling ------------------------------------------------------

    def test_exception_in_gap_detection_sets_failed_status(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        mock_sessions.side_effect = Exception('DB unavailable')

        result = self._flow()(start_year=2025, end_year=2025)

        self.assertEqual(result['status'], 'failed')
        self.assertIn('error', result)

    def test_exception_in_process_session_gap_sets_failed_status(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        gap = _make_gap()
        mock_sessions.return_value = [gap]
        mock_process.side_effect = RuntimeError('FastF1 crash')

        result = self._flow()(start_year=2025, end_year=2025)

        self.assertEqual(result['status'], 'failed')

    # --- notifications -------------------------------------------------------

    def test_sends_completion_notification_when_notify_true(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        # Use a non-empty gap list so the flow doesn't return early before the notify branch
        gap = _make_gap()
        mock_sessions.return_value = [gap]
        mock_process.return_value = _success_result(gap)

        with mock.patch(
            'analytics.flows.import_all_seasons._send_completion_notification'
        ) as mock_notify:
            self._flow()(start_year=2025, end_year=2025, notify=True)
            mock_notify.assert_called_once()

    def test_no_notification_when_notify_false(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        gap = _make_gap()
        mock_sessions.return_value = [gap]
        mock_process.return_value = _success_result(gap)

        with mock.patch(
            'analytics.flows.import_all_seasons._send_completion_notification'
        ) as mock_notify:
            self._flow()(start_year=2025, end_year=2025, notify=False)
            mock_notify.assert_not_called()

    def test_default_end_year_is_current_year(
        self, mock_sessions, mock_process, mock_update, mock_clear, mock_logger
    ):
        mock_sessions.return_value = []

        with mock.patch('analytics.flows.import_all_seasons.datetime') as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 1)
            result = self._flow()(start_year=2026)

        self.assertEqual(result['end_year'], 2026)


# ---------------------------------------------------------------------------
# collect_all_data --dry-run
# ---------------------------------------------------------------------------

class CollectAllDataDryRunTests(TestCase):
    """Tests for collect_all_data --dry-run output."""

    @mock.patch('analytics.processing.session_processor.get_sessions_to_process')
    def test_dry_run_shows_year_table(self, mock_sessions):
        mock_sessions.side_effect = lambda year, force: [_make_gap(year=year)] * (2026 - year)

        out = StringIO()
        call_command(
            'collect_all_data', '--dry-run', '--start-year', '2024', '--end-year', '2025',
            stdout=out,
        )
        output = out.getvalue()

        self.assertIn('2025', output)
        self.assertIn('2024', output)

    @mock.patch('analytics.processing.session_processor.get_sessions_to_process')
    def test_dry_run_shows_total_row(self, mock_sessions):
        mock_sessions.return_value = [_make_gap()] * 3

        out = StringIO()
        call_command(
            'collect_all_data', '--dry-run', '--start-year', '2025', '--end-year', '2025',
            stdout=out,
        )
        output = out.getvalue()

        self.assertIn('Total', output)
        self.assertIn('3', output)

    @mock.patch('analytics.processing.session_processor.get_sessions_to_process')
    def test_dry_run_shows_dry_run_message(self, mock_sessions):
        mock_sessions.return_value = []

        out = StringIO()
        call_command(
            'collect_all_data', '--dry-run', '--start-year', '2025', '--end-year', '2025',
            stdout=out,
        )
        output = out.getvalue()

        self.assertIn('DRY RUN', output)

    @mock.patch('analytics.processing.session_processor.get_sessions_to_process')
    def test_dry_run_shows_processing_order(self, mock_sessions):
        mock_sessions.return_value = []

        out = StringIO()
        call_command(
            'collect_all_data', '--dry-run', '--start-year', '2022', '--end-year', '2024',
            stdout=out,
        )
        output = out.getvalue()

        self.assertIn('2024 → 2022', output)

    @mock.patch('analytics.processing.session_processor.get_sessions_to_process')
    def test_dry_run_does_not_call_flow(self, mock_sessions):
        mock_sessions.return_value = []

        with mock.patch(
            'analytics.flows.import_all_seasons.import_all_seasons_flow'
        ) as mock_flow:
            call_command(
                'collect_all_data', '--dry-run', '--start-year', '2025', '--end-year', '2025',
            )
            mock_flow.assert_not_called()

    @mock.patch('analytics.processing.session_processor.get_sessions_to_process')
    def test_dry_run_calls_get_sessions_for_each_year_in_range(self, mock_sessions):
        mock_sessions.return_value = []

        call_command(
            'collect_all_data', '--dry-run', '--start-year', '2022', '--end-year', '2024',
        )

        years_called = [c[1]['year'] for c in mock_sessions.call_args_list]
        # Descending order: 2024, 2023, 2022
        self.assertIn(2024, years_called)
        self.assertIn(2023, years_called)
        self.assertIn(2022, years_called)
        self.assertEqual(len(years_called), 3)

    @mock.patch('analytics.processing.session_processor.get_sessions_to_process')
    def test_dry_run_force_flag_passed_to_get_sessions(self, mock_sessions):
        mock_sessions.return_value = []

        call_command(
            'collect_all_data', '--dry-run', '--start-year', '2025', '--end-year', '2025',
            '--force',
        )

        for call in mock_sessions.call_args_list:
            self.assertTrue(call[1]['force'])


# ---------------------------------------------------------------------------
# collect_all_data normal run
# ---------------------------------------------------------------------------

class CollectAllDataCommandTests(TestCase):
    """Tests for collect_all_data normal (non-dry-run) execution."""

    def _run(self, *args, **kwargs):
        out = StringIO()
        call_command('collect_all_data', *args, stdout=out, **kwargs)
        return out.getvalue()

    @mock.patch('analytics.flows.import_all_seasons.import_all_seasons_flow')
    def test_calls_flow_with_correct_years(self, mock_flow):
        mock_flow.return_value = _mock_summary(start_year=2023, end_year=2025)

        self._run('--start-year', '2023', '--end-year', '2025')

        mock_flow.assert_called_once()
        call_kwargs = mock_flow.call_args[1]
        self.assertEqual(call_kwargs['start_year'], 2023)
        self.assertEqual(call_kwargs['end_year'], 2025)

    @mock.patch('analytics.flows.import_all_seasons.import_all_seasons_flow')
    def test_notify_flag_passed_to_flow(self, mock_flow):
        mock_flow.return_value = _mock_summary()

        self._run('--start-year', '2025', '--end-year', '2025', '--notify')

        self.assertTrue(mock_flow.call_args[1]['notify'])

    @mock.patch('analytics.flows.import_all_seasons.import_all_seasons_flow')
    def test_notify_false_by_default(self, mock_flow):
        mock_flow.return_value = _mock_summary()

        self._run('--start-year', '2025', '--end-year', '2025')

        self.assertFalse(mock_flow.call_args[1]['notify'])

    @mock.patch('analytics.flows.import_all_seasons.import_all_seasons_flow')
    def test_force_flag_passed_to_flow(self, mock_flow):
        mock_flow.return_value = _mock_summary()

        self._run('--start-year', '2025', '--end-year', '2025', '--force')

        self.assertTrue(mock_flow.call_args[1]['force'])

    @mock.patch('analytics.flows.import_all_seasons.import_all_seasons_flow')
    def test_output_shows_seasons_covered(self, mock_flow):
        mock_flow.return_value = _mock_summary(start_year=2020, end_year=2025)

        output = self._run('--start-year', '2020', '--end-year', '2025')

        self.assertIn('2020', output)
        self.assertIn('2025', output)

    @mock.patch('analytics.flows.import_all_seasons.import_all_seasons_flow')
    def test_output_shows_session_counts(self, mock_flow):
        mock_flow.return_value = _mock_summary(
            sessions_processed=10, sessions_succeeded=9, sessions_failed=1
        )

        output = self._run('--start-year', '2025', '--end-year', '2025')

        self.assertIn('10', output)
        self.assertIn('9', output)
        self.assertIn('1', output)

    @mock.patch('analytics.flows.import_all_seasons.import_all_seasons_flow')
    def test_output_shows_per_year_breakdown(self, mock_flow):
        mock_flow.return_value = _mock_summary(
            by_year={
                2025: {'detected': 5, 'succeeded': 5, 'failed': 0},
                2024: {'detected': 8, 'succeeded': 7, 'failed': 1},
            }
        )

        output = self._run('--start-year', '2024', '--end-year', '2025')

        self.assertIn('2025', output)
        self.assertIn('2024', output)

    @mock.patch('analytics.flows.import_all_seasons.import_all_seasons_flow')
    def test_output_shows_complete_status(self, mock_flow):
        mock_flow.return_value = _mock_summary(status='complete')

        output = self._run('--start-year', '2025', '--end-year', '2025')

        self.assertIn('COMPLETE', output)

    @mock.patch('analytics.flows.import_all_seasons.import_all_seasons_flow')
    def test_output_shows_failed_status(self, mock_flow):
        mock_flow.return_value = _mock_summary(status='failed')

        output = self._run('--start-year', '2025', '--end-year', '2025')

        self.assertIn('FAILED', output)

    @mock.patch('analytics.flows.import_all_seasons.import_all_seasons_flow')
    def test_output_shows_extracted_data_types(self, mock_flow):
        mock_flow.return_value = _mock_summary(
            data_extracted={'weather': 5, 'circuit': 4, 'telemetry': 3}
        )

        output = self._run('--start-year', '2025', '--end-year', '2025')

        self.assertIn('Weather', output)
        self.assertIn('Circuit', output)
        self.assertIn('Telemetry', output)

    @mock.patch('analytics.flows.import_all_seasons.import_all_seasons_flow')
    def test_keyboard_interrupt_is_reraised(self, mock_flow):
        mock_flow.side_effect = KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self._run('--start-year', '2025', '--end-year', '2025')

    @mock.patch('analytics.flows.import_all_seasons.import_all_seasons_flow')
    def test_generic_exception_is_reraised(self, mock_flow):
        mock_flow.side_effect = RuntimeError('pipeline exploded')

        with self.assertRaises(RuntimeError):
            self._run('--start-year', '2025', '--end-year', '2025')

    @mock.patch('analytics.flows.import_all_seasons.import_all_seasons_flow')
    def test_output_shows_duration(self, mock_flow):
        mock_flow.return_value = _mock_summary(duration_seconds=123.4)

        output = self._run('--start-year', '2025', '--end-year', '2025')

        self.assertIn('123', output)

    @mock.patch('analytics.flows.import_all_seasons.import_all_seasons_flow')
    def test_default_start_year_is_2018(self, mock_flow):
        """--start-year should default to 2018 if omitted."""
        mock_flow.return_value = _mock_summary(start_year=2018)

        self._run('--end-year', '2025')

        self.assertEqual(mock_flow.call_args[1]['start_year'], 2018)
