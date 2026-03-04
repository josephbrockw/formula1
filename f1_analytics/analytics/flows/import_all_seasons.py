"""
Multi-Season FastF1 Import Pipeline.

Runs gap detection across a range of seasons (default 2018–current) and
processes every missing session in descending year order, most-recent event
first within each year.

Key design decisions:
- Resumability is free: re-running gap detection against the DB naturally
  skips sessions already imported.
- Rate-limit context: before each session the module-level _run_context in
  rate_limiter is updated, so pause notifications show cross-season progress.
- Single entry point: import_all_seasons_flow wraps the same process_session_gap
  task used by the single-year import_fastf1_flow.

Usage (Django shell):
    import asyncio
    from analytics.flows.import_all_seasons import import_all_seasons_flow
    import_all_seasons_flow(start_year=2018, notify=True)
"""

from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional

from prefect import flow, get_run_logger

from analytics.flows.import_fastf1 import process_session_gap
from analytics.processing.gap_detection import SessionGap
from analytics.processing.rate_limiter import clear_run_context, update_run_context
from analytics.processing.session_processor import get_sessions_to_process


def _count_by_year(gaps: List[SessionGap]) -> Dict[int, int]:
    """Return {year: session_count} for the given gap list."""
    return dict(Counter(g.year for g in gaps))


@flow(name="Import All Seasons", log_prints=True)
def import_all_seasons_flow(
    start_year: int = 2018,
    end_year: Optional[int] = None,
    force: bool = False,
    notify: bool = False,
) -> Dict:
    """
    Import FastF1 data across multiple seasons.

    Processes years from end_year down to start_year. Within each year,
    gap detection already returns sessions most-recent-first.

    Args:
        start_year: Earliest season to backfill (default: 2018).
        end_year:   Latest season to collect (default: current year).
        force:      Re-import data that already exists in the DB.
        notify:     Send a Slack summary on completion.

    Returns:
        Summary dict with per-season and aggregate statistics.
    """
    logger = get_run_logger()
    start_time = datetime.now()

    if end_year is None:
        end_year = datetime.now().year

    logger.info("=" * 80)
    logger.info(f"Multi-Season FastF1 Import — {start_year} to {end_year}")
    logger.info(f"Force mode: {force}")
    logger.info("=" * 80)

    summary: Dict = {
        'start_year': start_year,
        'end_year': end_year,
        'force': force,
        'gaps_detected': 0,
        'sessions_processed': 0,
        'sessions_succeeded': 0,
        'sessions_failed': 0,
        'data_extracted': {'weather': 0, 'circuit': 0, 'telemetry': 0},
        'by_year': {},
        'status': 'running',
        'start_time': start_time.isoformat(),
    }

    try:
        clear_run_context()

        # ------------------------------------------------------------------
        # PHASE 1: Gap detection across all seasons
        # ------------------------------------------------------------------
        logger.info("\nPHASE 1: Gap Detection Across All Seasons")
        logger.info("=" * 80)

        all_gaps: List[SessionGap] = []
        year_gap_counts: Dict[int, int] = {}

        for year in range(end_year, start_year - 1, -1):
            year_gaps = get_sessions_to_process(year=year, force=force)
            year_gap_counts[year] = len(year_gaps)
            all_gaps.extend(year_gaps)
            logger.info(f"  {year}: {len(year_gaps)} sessions to process")

        summary['gaps_detected'] = len(all_gaps)
        summary['by_year'] = {
            y: {'detected': c, 'succeeded': 0, 'failed': 0}
            for y, c in year_gap_counts.items()
        }

        breakdown = ' | '.join(
            f'{y}: {c}'
            for y, c in sorted(year_gap_counts.items(), reverse=True)
        )
        logger.info(f"\nPre-run breakdown: {breakdown}")
        logger.info(f"Total sessions to process: {len(all_gaps)}")

        if not all_gaps:
            logger.info("No sessions need processing. Database is up to date.")
            summary['status'] = 'complete'
            end_time = datetime.now()
            summary['end_time'] = end_time.isoformat()
            summary['duration_seconds'] = (end_time - start_time).total_seconds()
            return summary

        # ------------------------------------------------------------------
        # PHASE 2: Process sessions
        # ------------------------------------------------------------------
        logger.info("\nPHASE 2: Processing Sessions")
        logger.info("=" * 80)

        total_sessions = len(all_gaps)

        for i, gap in enumerate(all_gaps):
            # Update context so rate-limit pause notifications show rich info
            update_run_context(
                sessions_done=i,
                sessions_succeeded=summary['sessions_succeeded'],
                sessions_failed=summary['sessions_failed'],
                data_extracted=dict(summary['data_extracted']),
                sessions_remaining_by_year=_count_by_year(all_gaps[i:]),
            )

            pct = int(i / total_sessions * 100) if total_sessions > 0 else 0
            logger.info(
                f"\nSession {i + 1}/{total_sessions}: "
                f"{gap.year} Round {gap.round_number} {gap.session_type} "
                f"({pct}% complete)"
            )

            result = process_session_gap(gap, force)
            summary['sessions_processed'] += 1

            if result['status'] in ('success', 'partial'):
                summary['sessions_succeeded'] += 1
                summary['by_year'][gap.year]['succeeded'] += 1
                for data_type in result.get('extracted', []):
                    if data_type in summary['data_extracted']:
                        summary['data_extracted'][data_type] += 1
                if result['status'] == 'success':
                    logger.info(f"✅ Success — extracted: {', '.join(result['extracted'])}")
                else:
                    logger.warning(
                        f"⚠️  Partial — extracted: {', '.join(result['extracted'])}, "
                        f"failed: {', '.join(result['failed'])}"
                    )
            else:
                summary['sessions_failed'] += 1
                summary['by_year'][gap.year]['failed'] += 1
                logger.error(f"❌ Failed — {result.get('error', 'Unknown error')}")

        summary['status'] = 'complete'

    except Exception as e:
        logger.error(f"❌ Multi-season import failed: {e}")
        summary['status'] = 'failed'
        summary['error'] = str(e)

    finally:
        clear_run_context()

    end_time = datetime.now()
    summary['end_time'] = end_time.isoformat()
    summary['duration_seconds'] = (end_time - start_time).total_seconds()

    logger.info("\nFinal Summary:")
    logger.info(f"  • Sessions processed: {summary['sessions_processed']}")
    logger.info(f"  • Succeeded:          {summary['sessions_succeeded']}")
    logger.info(f"  • Failed:             {summary['sessions_failed']}")
    for data_type, count in summary['data_extracted'].items():
        logger.info(f"  • {data_type.capitalize()} extracted: {count}")
    logger.info(f"  • Duration:           {summary['duration_seconds']:.1f}s")

    if notify:
        _send_completion_notification(summary)

    return summary


def _send_completion_notification(summary: Dict) -> None:
    """Send a Slack summary on completion (sync, mirrors rate_limiter style)."""
    from config.notifications import send_slack_notification
    import pytz

    try:
        cst = pytz.timezone('America/Chicago')
        import pytz as _pytz
        now_cst = datetime.now(tz=_pytz.utc).astimezone(cst)

        status = summary.get('status', 'unknown')
        header_text = (
            "✅ Multi-Season Import Complete"
            if status == 'complete'
            else "❌ Multi-Season Import Failed"
        )

        extracted = summary.get('data_extracted', {})
        duration = summary.get('duration_seconds', 0)
        duration_str = f"{duration:.0f}s" if duration < 3600 else f"{duration / 3600:.1f}h"

        by_year = summary.get('by_year', {})
        year_lines = '\n'.join(
            f"{year}: {info['succeeded']} succeeded · {info['failed']} failed"
            for year in sorted(by_year.keys(), reverse=True)
        ) or 'No data'

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": header_text, "emoji": True},
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Seasons:*\n{summary['start_year']}–{summary['end_year']}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Sessions Processed:*\n{summary['sessions_processed']}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Succeeded:*\n{summary['sessions_succeeded']}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Failed:*\n{summary['sessions_failed']}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Weather:*\n{extracted.get('weather', 0)}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Circuit:*\n{extracted.get('circuit', 0)}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Telemetry:*\n{extracted.get('telemetry', 0)}",
                    },
                    {"type": "mrkdwn", "text": f"*Duration:*\n{duration_str}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Per-Year Breakdown:*\n{year_lines}",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Completed at {now_cst.strftime('%Y-%m-%d %I:%M %p CST')}",
                    }
                ],
            },
        ]

        message = f"{header_text} — {summary['sessions_processed']} sessions processed"
        send_slack_notification(message=message, blocks=blocks)
    except Exception as e:
        print(f"Failed to send completion notification: {e}")
