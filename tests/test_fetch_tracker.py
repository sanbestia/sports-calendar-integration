import pytest
from datetime import datetime, timezone, timedelta
from objects.FetchTracker import FetchTracker
from config import FETCH_THRESHOLDS


# --- Helpers ---

_created_trackers: list[FetchTracker] = []


def make_tracker(db_path: str = ":memory:") -> FetchTracker:
    """Create a tracker (in-memory SQLite by default); it is closed after the test."""
    tracker = FetchTracker(db_path=db_path)
    _created_trackers.append(tracker)
    return tracker


@pytest.fixture(autouse=True)
def _close_trackers():
    """Close every tracker created during a test so no SQLite connection is left open."""
    yield
    while _created_trackers:
        _created_trackers.pop().close()


def seed_entry(tracker: FetchTracker, team_name: str, hours_ago: float, days_until_match: float | None, match_count: int = 0) -> None:
    """Directly insert a fetch log entry into the tracker's database."""
    last_fetched = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    next_match = datetime.now(timezone.utc) + timedelta(days=days_until_match) if days_until_match is not None else None
    with tracker._connect() as conn:
        conn.execute("""
            INSERT INTO fetch_log (team_name, last_fetched, next_match, match_count) VALUES (?, ?, ?, ?)
        """, (
            team_name,
            last_fetched.isoformat(),
            next_match.isoformat() if next_match else None,
            match_count
        ))


# --- should_fetch tests ---

def test_should_fetch_returns_true_when_no_entry():
    """Returns True when team has never been fetched."""
    tracker = make_tracker()
    assert tracker.should_fetch("Carlos Alcaraz") is True


def test_should_fetch_returns_false_when_recently_fetched_far_match():
    """Returns False when fetched less than the far recheck interval ago."""
    tracker = make_tracker()
    seed_entry(tracker, "Carlos Alcaraz",
               hours_ago=FETCH_THRESHOLDS["far"]["recheck_hours"] - 0.1,
               days_until_match=FETCH_THRESHOLDS["far"]["days"] + 0.1)
    assert tracker.should_fetch("Carlos Alcaraz") is False


def test_should_fetch_returns_true_when_threshold_exceeded_far_match():
    """Returns True when fetched more than the far recheck interval ago."""
    tracker = make_tracker()
    seed_entry(tracker, "Carlos Alcaraz",
               hours_ago=FETCH_THRESHOLDS["far"]["recheck_hours"] + 0.1,
               days_until_match=FETCH_THRESHOLDS["far"]["days"] + 0.1)
    assert tracker.should_fetch("Carlos Alcaraz") is True


def test_should_fetch_returns_false_when_recently_fetched_medium_match():
    """Returns False when fetched less than the medium recheck interval ago."""
    tracker = make_tracker()
    seed_entry(tracker, "San Antonio Spurs",
               hours_ago=FETCH_THRESHOLDS["medium"]["recheck_hours"] - 0.1,
               days_until_match=FETCH_THRESHOLDS["medium"]["days"] + 0.1)
    assert tracker.should_fetch("San Antonio Spurs") is False


def test_should_fetch_returns_true_when_threshold_exceeded_medium_match():
    """Returns True when fetched more than the medium recheck interval ago."""
    tracker = make_tracker()
    seed_entry(tracker, "San Antonio Spurs",
               hours_ago=FETCH_THRESHOLDS["medium"]["recheck_hours"] + 0.1,
               days_until_match=FETCH_THRESHOLDS["medium"]["days"] + 0.1)
    assert tracker.should_fetch("San Antonio Spurs") is True


def test_should_fetch_returns_false_when_recently_fetched_near_match():
    """Returns False when fetched less than the near recheck interval ago."""
    tracker = make_tracker()
    seed_entry(tracker, "T1",
               hours_ago=FETCH_THRESHOLDS["near"]["recheck_hours"] - 0.1,
               days_until_match=FETCH_THRESHOLDS["near"]["days"] + 0.1)
    assert tracker.should_fetch("T1") is False


def test_should_fetch_returns_true_when_threshold_exceeded_near_match():
    """Returns True when fetched more than the near recheck interval ago."""
    tracker = make_tracker()
    seed_entry(tracker, "T1",
               hours_ago=FETCH_THRESHOLDS["near"]["recheck_hours"] + 0.1,
               days_until_match=FETCH_THRESHOLDS["near"]["days"] + 0.1)
    assert tracker.should_fetch("T1") is True


def test_should_fetch_returns_false_when_recently_fetched_no_match():
    """Returns False when fetched less than the unknown recheck interval ago with no match."""
    tracker = make_tracker()
    seed_entry(tracker, "Indiana Fever",
               hours_ago=FETCH_THRESHOLDS["unknown"]["recheck_hours"] - 0.1,
               days_until_match=None)
    assert tracker.should_fetch("Indiana Fever") is False


def test_should_fetch_returns_true_when_threshold_exceeded_no_match():
    """Returns True when fetched more than the unknown recheck interval ago with no match."""
    tracker = make_tracker()
    seed_entry(tracker, "Indiana Fever",
               hours_ago=FETCH_THRESHOLDS["unknown"]["recheck_hours"] + 0.1,
               days_until_match=None)
    assert tracker.should_fetch("Indiana Fever") is True


# --- record_fetch tests ---

def test_record_fetch_saves_entry_with_next_match():
    """Saves last_fetched and next_match correctly for a team."""
    tracker = make_tracker()
    next_match = datetime.now(timezone.utc) + timedelta(days=3)
    tracker.record_fetch("Carlos Alcaraz", next_match, match_count=0)

    with tracker._connect() as conn:
        row = conn.execute(
            "SELECT next_match FROM fetch_log WHERE team_name = ?", ("Carlos Alcaraz",)
        ).fetchone()

    assert row is not None
    assert row[0] == next_match.isoformat()


def test_record_fetch_saves_entry_without_next_match():
    """Saves None for next_match when no upcoming match is known."""
    tracker = make_tracker()
    tracker.record_fetch("Indiana Fever", None, match_count=0)

    with tracker._connect() as conn:
        row = conn.execute(
            "SELECT next_match FROM fetch_log WHERE team_name = ?", ("Indiana Fever",)
        ).fetchone()

    assert row is not None
    assert row[0] is None


def test_record_fetch_overwrites_previous_entry():
    """Overwrites an existing entry when a team is fetched again."""
    tracker = make_tracker()
    old_match = datetime.now(timezone.utc) + timedelta(days=5)
    new_match = datetime.now(timezone.utc) + timedelta(days=2)

    tracker.record_fetch("Carlos Alcaraz", old_match, match_count=0)
    tracker.record_fetch("Carlos Alcaraz", new_match, match_count=2)

    with tracker._connect() as conn:
        row = conn.execute(
            "SELECT next_match, match_count FROM fetch_log WHERE team_name = ?", ("Carlos Alcaraz",)
        ).fetchone()

    assert row[0] == new_match.isoformat()
    assert row[1] == 2


def test_record_fetch_saves_match_count():
    """Saves match_count correctly when recording a fetch."""
    tracker = make_tracker()
    tracker.record_fetch("Carlos Alcaraz", None, match_count=3)

    with tracker._connect() as conn:
        row = conn.execute(
            "SELECT match_count FROM fetch_log WHERE team_name = ?", ("Carlos Alcaraz",)
        ).fetchone()

    assert row[0] == 3


def test_record_fetch_saves_zero_match_count():
    """Saves match_count of 0 when no matches were found."""
    tracker = make_tracker()
    tracker.record_fetch("Indiana Fever", None, match_count=0)

    with tracker._connect() as conn:
        row = conn.execute(
            "SELECT match_count FROM fetch_log WHERE team_name = ?", ("Indiana Fever",)
        ).fetchone()

    assert row[0] == 0


# --- imminent logic tests ---

IMMINENT_RECHECK = FETCH_THRESHOLDS["imminent"]["recheck_hours"]
IMMINENT_HOURS = FETCH_THRESHOLDS["imminent"]["hours"]
IMMINENT_CLOSE_RECHECK = FETCH_THRESHOLDS["imminent_close"]["recheck_hours"]
IMMINENT_CLOSE_HOURS = FETCH_THRESHOLDS["imminent_close"]["hours"]


def test_imminent_clause_triggers_when_single_match_within_threshold():
    """Returns False when match_count==1 and match is within imminent but outside imminent_close."""
    tracker = make_tracker()
    seed_entry(tracker, "Carlos Alcaraz",
               hours_ago=IMMINENT_RECHECK - 0.1,
               days_until_match=IMMINENT_CLOSE_HOURS / 24 + 0.01,
               match_count=1)
    assert tracker.should_fetch("Carlos Alcaraz") is False


def test_imminent_clause_triggers_fetch_when_threshold_exceeded():
    """Returns True when match_count==1, within imminent but outside imminent_close, and recheck time passed."""
    tracker = make_tracker()
    seed_entry(tracker, "Carlos Alcaraz",
               hours_ago=IMMINENT_RECHECK + 0.1,
               days_until_match=IMMINENT_CLOSE_HOURS / 24 + 0.01,
               match_count=1)
    assert tracker.should_fetch("Carlos Alcaraz") is True


def test_imminent_clause_does_not_trigger_when_multiple_matches():
    """Does not apply imminent logic when match_count > 1."""
    tracker = make_tracker()
    seed_entry(tracker, "Carlos Alcaraz",
               hours_ago=IMMINENT_RECHECK - 0.1,
               days_until_match=IMMINENT_CLOSE_HOURS / 24 + 0.01,
               match_count=2)
    assert tracker.should_fetch("Carlos Alcaraz") is False


def test_imminent_clause_does_not_trigger_when_match_beyond_window():
    """Does not apply imminent logic when match is beyond the imminent window."""
    tracker = make_tracker()
    seed_entry(tracker, "Carlos Alcaraz",
               hours_ago=IMMINENT_RECHECK + 0.1,
               days_until_match=IMMINENT_HOURS / 24 + 0.1,
               match_count=1)
    assert tracker.should_fetch("Carlos Alcaraz") is False


def test_imminent_clause_does_not_trigger_when_match_count_is_zero():
    """Does not apply imminent logic when match_count is 0."""
    tracker = make_tracker()
    seed_entry(tracker, "Indiana Fever",
               hours_ago=IMMINENT_RECHECK + 0.1,
               days_until_match=IMMINENT_CLOSE_HOURS / 24 + 0.01,
               match_count=0)
    assert tracker.should_fetch("Indiana Fever") is False


def test_imminent_clause_uses_config_values():
    """Imminent threshold values are read from config, not hardcoded."""
    assert "imminent" in FETCH_THRESHOLDS
    assert "hours" in FETCH_THRESHOLDS["imminent"]
    assert "recheck_hours" in FETCH_THRESHOLDS["imminent"]


def test_imminent_close_triggers_when_single_match_within_close_threshold():
    """Returns False when match_count==1 and match is within the imminent_close window."""
    tracker = make_tracker()
    seed_entry(tracker, "Jannik Sinner",
               hours_ago=IMMINENT_CLOSE_RECHECK - 0.1,
               days_until_match=IMMINENT_CLOSE_HOURS / 24 - 0.01,
               match_count=1)
    assert tracker.should_fetch("Jannik Sinner") is False


def test_imminent_close_triggers_fetch_when_threshold_exceeded():
    """Returns True when match_count==1, within imminent_close window, and recheck time passed."""
    tracker = make_tracker()
    seed_entry(tracker, "Jannik Sinner",
               hours_ago=IMMINENT_CLOSE_RECHECK + 0.1,
               days_until_match=IMMINENT_CLOSE_HOURS / 24 - 0.01,
               match_count=1)
    assert tracker.should_fetch("Jannik Sinner") is True


def test_imminent_close_takes_priority_over_imminent():
    """imminent_close recheck is applied (not imminent) when match is within the close window."""
    tracker = make_tracker()
    seed_entry(tracker, "Jannik Sinner",
               hours_ago=IMMINENT_RECHECK + 0.1,
               days_until_match=IMMINENT_CLOSE_HOURS / 24 - 0.01,
               match_count=1)
    assert tracker.should_fetch("Jannik Sinner") is True


def test_imminent_close_does_not_trigger_when_multiple_matches():
    """Does not apply imminent_close logic when match_count > 1."""
    tracker = make_tracker()
    seed_entry(tracker, "Jannik Sinner",
               hours_ago=IMMINENT_CLOSE_RECHECK - 0.1,
               days_until_match=IMMINENT_CLOSE_HOURS / 24 - 0.01,
               match_count=2)
    assert tracker.should_fetch("Jannik Sinner") is False


def test_imminent_close_uses_config_values():
    """imminent_close threshold values are read from config, not hardcoded."""
    assert "imminent_close" in FETCH_THRESHOLDS
    assert "hours" in FETCH_THRESHOLDS["imminent_close"]
    assert "recheck_hours" in FETCH_THRESHOLDS["imminent_close"]

# --- hours_until_next_fetch tests ---

def test_hours_until_next_fetch_is_zero_when_no_entry():
    """A team that was never fetched is due now."""
    tracker = make_tracker()
    assert tracker.hours_until_next_fetch("Carlos Alcaraz") == 0.0


def test_hours_until_next_fetch_returns_time_left_in_interval():
    """Returns the recheck interval minus the time since the last fetch."""
    tracker = make_tracker()
    seed_entry(tracker, "Carlos Alcaraz", hours_ago=10, days_until_match=10)  # far match
    expected = FETCH_THRESHOLDS["far"]["recheck_hours"] - 10

    assert tracker.hours_until_next_fetch("Carlos Alcaraz") == pytest.approx(expected, abs=0.01)


def test_hours_until_next_fetch_is_zero_when_overdue():
    """Never returns a negative value for an overdue team."""
    tracker = make_tracker()
    seed_entry(tracker, "Carlos Alcaraz", hours_ago=FETCH_THRESHOLDS["unknown"]["recheck_hours"] + 5, days_until_match=None)

    assert tracker.hours_until_next_fetch("Carlos Alcaraz") == 0.0


def test_hours_until_next_fetch_agrees_with_should_fetch():
    """A team with time left is not due; a team with no time left is due."""
    tracker = make_tracker()
    seed_entry(tracker, "Fresh", hours_ago=1, days_until_match=10)
    seed_entry(tracker, "Stale", hours_ago=100, days_until_match=10)

    assert tracker.hours_until_next_fetch("Fresh") > 0 and tracker.should_fetch("Fresh") is False
    assert tracker.hours_until_next_fetch("Stale") == 0.0 and tracker.should_fetch("Stale") is True


# --- file-based persistence ---

def test_fetch_log_survives_restart(tmp_path):
    """A new tracker on the same file sees the earlier fetch (the service restart case)."""
    db_path = str(tmp_path / "fetch_log.db")
    next_match = datetime.now(timezone.utc) + timedelta(days=10)
    make_tracker(db_path).record_fetch("Carlos Alcaraz", next_match, 1)

    restarted = make_tracker(db_path)

    assert restarted.should_fetch("Carlos Alcaraz") is False
