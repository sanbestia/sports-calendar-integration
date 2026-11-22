import sqlite3
from contextlib import closing

import pytest
from datetime import datetime, timezone, timedelta
from sports_calendar.storage.fetch_tracker import FetchTracker
from sports_calendar.config import FETCH_THRESHOLDS
from tests.fakes import FakeClock


# --- Helpers ---

_created_trackers: list[FetchTracker] = []


def make_tracker(db_path: str = ":memory:", clock=None) -> FetchTracker:
    """Create a tracker (in-memory SQLite by default); it is closed after the test."""
    tracker = FetchTracker(db_path=db_path, clock=clock)
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


# --- full seasons: a long schedule waits at least the season interval ---

SEASON = FETCH_THRESHOLDS["season"]
NEAR_DAYS = FETCH_THRESHOLDS["near"]["days"] + 0.1  # an NBA team's next game is always in the near band


def test_a_full_season_is_not_fetched_after_the_near_interval():
    """A full season changes rarely (titles, renames), and each fetch is 3 pages: it waits the season interval."""
    tracker = make_tracker()
    seed_entry(tracker, "San Antonio Spurs", hours_ago=FETCH_THRESHOLDS["near"]["recheck_hours"] + 0.1,
               days_until_match=NEAR_DAYS, match_count=SEASON["min_matches"])
    assert tracker.should_fetch("San Antonio Spurs") is False


def test_a_full_season_is_fetched_after_the_season_interval():
    """The longer wait still ends: a change shows up within the season interval."""
    tracker = make_tracker()
    seed_entry(tracker, "San Antonio Spurs", hours_ago=SEASON["min_recheck_hours"] + 0.1,
               days_until_match=NEAR_DAYS, match_count=SEASON["min_matches"])
    assert tracker.should_fetch("San Antonio Spurs") is True


def test_fewer_matches_than_a_season_keep_the_near_interval():
    """A national team or a tournament run (a few matches) keeps the band of its next match."""
    tracker = make_tracker()
    seed_entry(tracker, "France - Basketball National Team", hours_ago=FETCH_THRESHOLDS["near"]["recheck_hours"] + 0.1,
               days_until_match=NEAR_DAYS, match_count=SEASON["min_matches"] - 1)
    assert tracker.should_fetch("France - Basketball National Team") is True


def test_the_season_rule_never_shortens_a_longer_wait():
    """In the preseason the first game is far away: the far interval, which is longer, stays."""
    tracker = make_tracker()
    far_hours = FETCH_THRESHOLDS["far"]["recheck_hours"]
    seed_entry(tracker, "Los Angeles Lakers", hours_ago=0,
               days_until_match=FETCH_THRESHOLDS["far"]["days"] + 0.1, match_count=SEASON["min_matches"] + 50)
    assert tracker.hours_until_next_fetch("Los Angeles Lakers") == pytest.approx(max(far_hours, SEASON["min_recheck_hours"]), abs=0.01)


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


# --- failed fetches ---

FAILED_HOURS = FETCH_THRESHOLDS["failed"]["recheck_hours"]
FAILED_MAX_HOURS = FETCH_THRESHOLDS["failed"]["max_recheck_hours"]


def test_failed_fetch_is_retried_after_the_failed_interval():
    """A failure overrides the normal schedule: the entity is due again after the short 'failed' interval."""
    clock = FakeClock()
    tracker = make_tracker(clock=clock)
    tracker.record_fetch("Lakers", clock.now() + timedelta(days=10), 66)  # far match: normally 48h
    clock.advance(hours=FETCH_THRESHOLDS["far"]["recheck_hours"])  # due again
    tracker.record_failure("Lakers")

    assert tracker.should_fetch("Lakers") is False
    assert tracker.hours_until_next_fetch("Lakers") == pytest.approx(FAILED_HOURS)
    clock.advance(hours=FAILED_HOURS)
    assert tracker.should_fetch("Lakers") is True


def test_failure_keeps_the_last_known_schedule():
    """A failure does not erase the next match and count that the last successful fetch found."""
    clock = FakeClock()
    tracker = make_tracker(clock=clock)
    next_match = clock.now() + timedelta(days=10)
    tracker.record_fetch("Lakers", next_match, 66)
    tracker.record_failure("Lakers")

    with tracker._connect() as conn:
        row = conn.execute("SELECT next_match, match_count FROM fetch_log WHERE team_name = 'Lakers'").fetchone()
    assert row == (next_match.isoformat(), 66)


def test_success_after_failure_returns_to_normal_schedule():
    """The next successful fetch clears the failure, so the normal interval applies again."""
    clock = FakeClock()
    tracker = make_tracker(clock=clock)
    tracker.record_failure("Lakers")
    tracker.record_fetch("Lakers", clock.now() + timedelta(days=10), 66)

    assert tracker.hours_until_next_fetch("Lakers") == pytest.approx(FETCH_THRESHOLDS["far"]["recheck_hours"])


def test_first_fetch_that_fails_is_retried_after_the_failed_interval():
    """An entity whose very first fetch fails is not retried every cycle, only after the 'failed' interval."""
    clock = FakeClock()
    tracker = make_tracker(clock=clock)
    tracker.record_failure("New Team")

    assert tracker.should_fetch("New Team") is False
    clock.advance(hours=FAILED_HOURS)
    assert tracker.should_fetch("New Team") is True


def test_adds_failure_column_to_an_existing_database(tmp_path):
    """A fetch_log.db created before failures were tracked gets the new column and keeps its rows."""
    db_path = str(tmp_path / "fetch_log.db")
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("CREATE TABLE fetch_log (team_name TEXT PRIMARY KEY, last_fetched TEXT NOT NULL, next_match TEXT, match_count INTEGER NOT NULL DEFAULT 0)")
        conn.execute("INSERT INTO fetch_log VALUES ('Lakers', ?, NULL, 0)", (datetime.now(timezone.utc).isoformat(),))

    tracker = make_tracker(db_path)
    tracker.record_failure("Lakers")
    tracker.record_failure("Lakers")

    assert tracker.should_fetch("Lakers") is False
    assert tracker.hours_until_next_fetch("Lakers") == pytest.approx(2 * FAILED_HOURS, abs=0.01)  # streak of 2


def test_repeated_failures_double_the_wait_up_to_the_cap():
    """Each failure in a row doubles the wait (1h, 2h, 4h, ...) until max_recheck_hours, to protect the daily budget."""
    clock = FakeClock()
    tracker = make_tracker(clock=clock)
    waits = []
    for _ in range(6):
        tracker.record_failure("Broken Team")
        waits.append(tracker.hours_until_next_fetch("Broken Team"))
        clock.advance(hours=waits[-1])

    expected = [min(FAILED_HOURS * 2 ** n, FAILED_MAX_HOURS) for n in range(6)]
    assert waits == pytest.approx(expected)
    assert waits[-1] == FAILED_MAX_HOURS


def test_success_resets_the_failure_streak():
    """After a successful fetch, the next failure starts again at the shortest wait."""
    clock = FakeClock()
    tracker = make_tracker(clock=clock)
    for _ in range(3):
        tracker.record_failure("Lakers")
    tracker.record_fetch("Lakers", clock.now() + timedelta(days=10), 66)
    tracker.record_failure("Lakers")

    assert tracker.hours_until_next_fetch("Lakers") == pytest.approx(FAILED_HOURS)


# --- empty schedules: 3-day grace at the 'unknown' interval, then a doubling wait up to a cap ---

UNKNOWN = FETCH_THRESHOLDS["unknown"]
EMPTY_HOURS = UNKNOWN["recheck_hours"]


def record_empties(tracker, clock, name, count, player_type):
    """Record `count` empty fetches, each one exactly when the previous wait ends; return the waits."""
    waits = []
    for _ in range(count):
        tracker.record_fetch(name, None, 0, player_type=player_type)
        waits.append(tracker.hours_until_next_fetch(name))
        clock.advance(hours=waits[-1])
    return waits


def test_empty_results_wait_the_unknown_interval_during_the_grace_period():
    """For the first grace_days of empty results the wait stays at 8h (playoff gaps, tennis between rounds)."""
    clock = FakeClock()
    tracker = make_tracker(clock=clock)
    fetches_in_grace = int(UNKNOWN["grace_days"] * 24 / EMPTY_HOURS)

    waits = record_empties(tracker, clock, "Indiana Fever", fetches_in_grace, "team")

    assert waits == pytest.approx([EMPTY_HOURS] * fetches_in_grace)


@pytest.mark.parametrize("player_type, expected_after_grace", [
    ("team", [16, 32, 48, 48]),
    ("player", [12, 12, 12]),
])
def test_after_the_grace_period_the_wait_doubles_up_to_the_cap(player_type, expected_after_grace):
    """After the grace period each empty result doubles the wait, up to 12h for players and 48h for teams."""
    clock = FakeClock()
    tracker = make_tracker(clock=clock)
    fetches_in_grace = int(UNKNOWN["grace_days"] * 24 / EMPTY_HOURS)

    waits = record_empties(tracker, clock, "Off Season", fetches_in_grace + len(expected_after_grace), player_type)

    assert waits[fetches_in_grace:] == pytest.approx(expected_after_grace)
    assert UNKNOWN["max_recheck_hours"][player_type] == expected_after_grace[-1]


def test_a_found_match_resets_the_empty_wait_and_grace():
    """When matches appear again, the next empty result starts over at 8h with a new grace period."""
    clock = FakeClock()
    tracker = make_tracker(clock=clock)
    record_empties(tracker, clock, "Indiana Fever", 14, "team")  # well past the grace period
    tracker.record_fetch("Indiana Fever", clock.now() + timedelta(days=2), 2, player_type="team")
    clock.advance(days=3)

    waits = record_empties(tracker, clock, "Indiana Fever", 2, "team")

    assert waits == pytest.approx([EMPTY_HOURS, EMPTY_HOURS])


def test_a_failure_does_not_change_the_empty_wait():
    """A failed fetch is retried on the failure schedule; the next empty result continues the doubling."""
    clock = FakeClock()
    tracker = make_tracker(clock=clock)
    waits = record_empties(tracker, clock, "Indiana Fever", 10, "team")  # last wait is 16h
    tracker.record_failure("Indiana Fever")
    clock.advance(hours=FAILED_HOURS)
    tracker.record_fetch("Indiana Fever", None, 0, player_type="team")

    assert waits[-1] == pytest.approx(16)
    assert tracker.hours_until_next_fetch("Indiana Fever") == pytest.approx(32)


def test_existing_empty_rows_start_with_the_unknown_interval(tmp_path):
    """Rows written before this change (no empty columns) still wait 8h, and get their grace from the next empty fetch."""
    db_path = str(tmp_path / "fetch_log.db")
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("CREATE TABLE fetch_log (team_name TEXT PRIMARY KEY, last_fetched TEXT NOT NULL, next_match TEXT, match_count INTEGER NOT NULL DEFAULT 0)")
        conn.execute("INSERT INTO fetch_log VALUES ('Indiana Fever', ?, NULL, 0)", (FakeClock().now().isoformat(),))

    tracker = make_tracker(db_path, clock=FakeClock())

    assert tracker.hours_until_next_fetch("Indiana Fever") == pytest.approx(EMPTY_HOURS)
