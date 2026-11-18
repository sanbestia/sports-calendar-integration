import pytest
from datetime import date
from unittest.mock import patch
from objects.APICallTracker import APICallTracker


_created_trackers: list[APICallTracker] = []


def make_tracker(db_path: str = ":memory:") -> APICallTracker:
    """Create a tracker (in-memory SQLite by default); it is closed after the test."""
    tracker = APICallTracker(db_path=db_path)
    _created_trackers.append(tracker)
    return tracker


@pytest.fixture(autouse=True)
def _close_trackers():
    """Close every tracker created during a test so no SQLite connection is left open."""
    yield
    while _created_trackers:
        _created_trackers.pop().close()


# --- Tests ---

def test_initial_count_is_zero_when_no_entry():
    """Counter starts at zero when no entry exists in the database."""
    tracker = make_tracker()
    assert tracker.count == 0


def test_loads_count_from_db_if_same_day(tmp_path):
    """A new tracker on the same file starts from today's stored count (the service restart case)."""
    db_path = str(tmp_path / "api_calls.db")
    tracker = make_tracker(db_path)
    tracker.increment()
    tracker.increment()

    restarted = make_tracker(db_path)

    assert restarted.count == 2


def test_does_not_load_count_from_previous_day(tmp_path):
    """A new tracker ignores the count stored for an earlier date."""
    db_path = str(tmp_path / "api_calls.db")
    tracker = make_tracker(db_path)
    tracker.increment()

    with patch("objects.APICallTracker.date") as mock_date:
        mock_date.today.return_value = date(2099, 1, 1)
        next_day = make_tracker(db_path)

    assert next_day.count == 0


def test_increment_increases_count():
    """Each increment increases count by one."""
    tracker = make_tracker()
    tracker.increment()
    tracker.increment()
    assert tracker.count == 2


def test_increment_persists_to_db():
    """Increment writes updated count to the database."""
    tracker = make_tracker()
    tracker.increment()
    assert tracker._load() == 1


def test_status_returns_correct_string():
    """Status string reflects current count and daily limit."""
    tracker = make_tracker()
    tracker.increment()
    tracker.increment()
    assert tracker.status() == f"API calls today: 2/{tracker.daily_limit}"


def test_daily_limit_comes_from_config():
    """Daily limit is loaded from config."""
    from config import MAX_API_CALLS
    tracker = make_tracker()
    assert tracker.daily_limit == MAX_API_CALLS


def test_resets_on_new_day():
    """Resets count automatically when the date changes."""
    tracker = make_tracker()
    tracker.increment()
    tracker.increment()
    assert tracker.count == 2

    with patch("objects.APICallTracker.date") as mock_date:
        mock_date.today.return_value = date(2099, 1, 1)
        tracker.increment()

    assert tracker.count == 1


def test_status_triggers_reset_on_new_day():
    """Status check also triggers a reset if the date has changed."""
    tracker = make_tracker()
    tracker.increment()
    tracker.increment()

    with patch("objects.APICallTracker.date") as mock_date:
        mock_date.today.return_value = date(2099, 1, 1)
        status = tracker.status()

    assert status == f"API calls today: 0/{tracker.daily_limit}"


def test_is_limit_reached_returns_false_when_under_limit():
    """Returns False when count is below the daily limit."""
    tracker = make_tracker()
    tracker.increment()
    assert tracker.is_limit_reached() is False


def test_is_limit_reached_returns_true_when_at_limit():
    """Returns True when count equals the daily limit."""
    tracker = make_tracker()
    tracker.count = tracker.daily_limit
    assert tracker.is_limit_reached() is True


def test_is_limit_reached_returns_true_when_over_limit():
    """Returns True when count exceeds the daily limit."""
    tracker = make_tracker()
    tracker.count = tracker.daily_limit + 1
    assert tracker.is_limit_reached() is True


def test_is_limit_reached_resets_on_new_day():
    """Returns False after a day reset even if count was at the limit."""
    tracker = make_tracker()
    tracker.count = tracker.daily_limit

    with patch("objects.APICallTracker.date") as mock_date:
        mock_date.today.return_value = date(2099, 1, 1)
        result = tracker.is_limit_reached()

    assert result is False