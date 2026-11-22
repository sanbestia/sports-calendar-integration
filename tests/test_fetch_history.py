import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest

from sports_calendar.storage.fetch_history import FetchHistory
from tests.fakes import FakeClock

_created: list[FetchHistory] = []


def make_history(db_path: str = ":memory:", clock: FakeClock | None = None) -> FetchHistory:
    history = FetchHistory(db_path=db_path, clock=clock or FakeClock())
    _created.append(history)
    return history


@pytest.fixture(autouse=True)
def _close_histories():
    yield
    while _created:
        _created.pop().close()


def test_records_one_row_per_fetch_with_time_and_day():
    """Each row has the UTC time, the day, and what the fetch found and changed."""
    clock = FakeClock()
    history = make_history(clock=clock)

    history.record("Lakers", "team", "matches", api_calls=3, matches=64, created=1, updated=2)

    with history._connect() as conn:
        row = conn.execute("SELECT fetched_at, day, entity, player_type, outcome, api_calls, matches, created, updated, deleted, error FROM fetch_history").fetchone()
    assert row == (clock.now().isoformat(), clock.today().isoformat(), "Lakers", "team", "matches", 3, 64, 1, 2, 0, None)


def test_rejects_an_unknown_outcome():
    """Only 'matches', 'empty' and 'failed' are valid, so summaries stay correct."""
    with pytest.raises(ValueError, match="outcome"):
        make_history().record("Lakers", "team", "ok", api_calls=1)


def test_long_error_text_is_cut():
    """Error text is a short reason; the full traceback stays in the log."""
    history = make_history()
    history.record("Lakers", "team", "failed", api_calls=0, error="x" * 1000)

    with history._connect() as conn:
        (error,) = conn.execute("SELECT error FROM fetch_history").fetchone()
    assert len(error) == 200


def test_day_summary_adds_up_one_day_only():
    """The summary counts fetches by outcome, API calls and event changes for the given day."""
    clock = FakeClock()
    history = make_history(clock=clock)
    history.record("Lakers", "team", "matches", api_calls=3, matches=64, created=2, updated=1)
    history.record("Fever", "team", "empty", api_calls=2)
    history.record("Sinner", "player", "failed", api_calls=1, error="timeout")
    day = clock.today()
    clock.advance(days=1)
    history.record("Lakers", "team", "matches", api_calls=3, matches=63, deleted=1)  # next day: not counted

    assert history.day_summary(day) == {
        "fetches": 3, "with_matches": 1, "empty": 1, "failed": 1, "api_calls": 6, "created": 2, "updated": 1, "deleted": 0,
    }


def test_day_summary_of_a_day_without_fetches_is_all_zero():
    """A day with no rows gives zeros, not None."""
    summary = make_history().day_summary(FakeClock().today() - timedelta(days=5))
    assert set(summary.values()) == {0}


def test_shares_the_fetch_log_file_without_touching_fetch_log(tmp_path):
    """The history lives in fetch_log.db next to the fetch_log table, which it leaves alone."""
    db_path = str(tmp_path / "fetch_log.db")
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("CREATE TABLE fetch_log (team_name TEXT PRIMARY KEY, last_fetched TEXT NOT NULL)")
        conn.execute("INSERT INTO fetch_log VALUES ('Lakers', '2026-01-01')")

    make_history(db_path).record("Lakers", "team", "empty", api_calls=2)

    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT * FROM fetch_log").fetchall() == [("Lakers", "2026-01-01")]
        assert conn.execute("SELECT COUNT(*) FROM fetch_history").fetchone() == (1,)
