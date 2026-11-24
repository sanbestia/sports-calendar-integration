"""Log one line with yesterday's totals: API calls, fetches by outcome, and calendar changes.

The summary timer starts it each day at 00:05 (deploy/sports-calendar-summary.timer). To run it by hand:
`uv run sports-calendar-summary`.
"""
import datetime
import logging

from sports_calendar.clock import Clock
from sports_calendar.config import API_CALL_CEILING
from sports_calendar.log_setup import configure_logging
from sports_calendar.storage.api_calls import APICallTracker
from sports_calendar.storage.fetch_history import FetchHistory

logger = logging.getLogger(__name__)


def log_daily_summary(api_tracker: APICallTracker, history: FetchHistory, day: datetime.date) -> None:
    """Log one line with a finished day's totals: API calls (api_calls.db) and fetches and changes (fetch_history).

    A day above API_CALL_CEILING is a WARNING, so it shows in `journalctl -p warning`. A failed read is a warning too.
    """
    try:
        calls = api_tracker.calls_on(day)
        totals = history.day_summary(day)
    except Exception as e:
        logger.warning(f"Could not build the daily summary for {day}: {e}")
        return
    over = calls > API_CALL_CEILING
    logger.log(
        logging.WARNING if over else logging.INFO,
        f"Daily summary for {day}: {calls} API calls"
        f"{f' (above the {API_CALL_CEILING}-call ceiling)' if over else ''}; "
        f"{totals['fetches']} fetches ({totals['with_matches']} with matches, {totals['empty']} empty, {totals['failed']} failed); "
        f"events: {totals['created']} created, {totals['updated']} updated, {totals['deleted']} deleted"
    )


def log_yesterday_summary(clock: Clock | None = None, api_tracker: APICallTracker | None = None,
                          history: FetchHistory | None = None) -> None:
    """Log the summary of yesterday (the local date). Without arguments it uses the real clock and databases."""
    clock = clock if clock is not None else Clock()
    yesterday = clock.today() - datetime.timedelta(days=1)
    log_daily_summary(api_tracker if api_tracker is not None else APICallTracker(clock=clock),
                      history if history is not None else FetchHistory(clock=clock),
                      yesterday)



def main() -> None:
    """Entry point of `uv run sports-calendar-summary`, which the summary timer starts."""
    configure_logging()
    log_yesterday_summary()
