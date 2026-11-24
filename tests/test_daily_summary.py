import datetime
from unittest.mock import MagicMock

from sports_calendar.config import API_CALL_CEILING
from sports_calendar.daily_summary import log_daily_summary, log_yesterday_summary
from tests.fakes import FakeClock

DAY = datetime.date(2026, 1, 15)
DAY_SUMMARY = {"fetches": 25, "with_matches": 5, "empty": 18, "failed": 2, "api_calls": 31, "created": 1, "updated": 3, "deleted": 0}


def make_stores(calls_that_day: int) -> tuple[MagicMock, MagicMock]:
    api_tracker = MagicMock()
    api_tracker.calls_on.return_value = calls_that_day
    history = MagicMock()
    history.day_summary.return_value = DAY_SUMMARY
    return api_tracker, history


def test_logs_one_line_with_the_days_totals(caplog):
    """The totals come from api_calls.db and fetch_history, so they do not depend on the runs of that day."""
    api_tracker, history = make_stores(calls_that_day=32)

    log_daily_summary(api_tracker, history, DAY)

    history.day_summary.assert_called_once_with(DAY)
    assert ("Daily summary for 2026-01-15: 32 API calls; 25 fetches (5 with matches, 18 empty, 2 failed); "
            "events: 1 created, 3 updated, 0 deleted") in caplog.text


def test_warns_above_the_call_ceiling(caplog):
    """A day above the ceiling is a WARNING, so it shows in `journalctl -p warning`."""
    api_tracker, history = make_stores(calls_that_day=API_CALL_CEILING + 5)

    log_daily_summary(api_tracker, history, DAY)

    (record,) = [r for r in caplog.records if r.getMessage().startswith("Daily summary")]
    assert record.levelname == "WARNING"
    assert f"above the {API_CALL_CEILING}-call ceiling" in record.getMessage()


def test_a_failed_read_is_a_warning(caplog):
    api_tracker, history = make_stores(calls_that_day=10)
    history.day_summary.side_effect = OSError("database is locked")

    log_daily_summary(api_tracker, history, DAY)

    assert "Could not build the daily summary for 2026-01-15: database is locked" in caplog.text


def test_summarizes_yesterday():
    """The timer starts it at 00:05, so the finished day is yesterday."""
    api_tracker, history = make_stores(calls_that_day=10)

    log_yesterday_summary(clock=FakeClock(datetime.datetime(2026, 1, 16, 0, 5, tzinfo=datetime.timezone.utc)), api_tracker=api_tracker, history=history)

    api_tracker.calls_on.assert_called_once_with(DAY)
    history.day_summary.assert_called_once_with(DAY)
