import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
import logging
from datetime import datetime

from config import FETCH_THRESHOLDS
from objects.Clock import Clock

logger = logging.getLogger(__name__)

DB_FILE = "fetch_log.db"

# Columns added after the first release; _init_db adds them to older databases.
_ADDED_COLUMNS = {
    "last_failed": "TEXT",
    "fail_streak": "INTEGER NOT NULL DEFAULT 0",
}


class FetchTracker:
    def __init__(self, db_path: str = DB_FILE, clock: Clock | None = None):
        """Initialise the tracker and create the fetch_log table if it doesn't exist."""
        self._db_path = db_path
        self._clock = clock or Clock()
        # For :memory: databases, keep a single connection alive for the lifetime
        # of the object — each new connection() call would get its own empty database
        self._conn = sqlite3.connect(db_path, check_same_thread=False) if db_path == ":memory:" else None
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection that commits on success and rolls back on error.

        File-based connections are closed afterwards; the persistent :memory: connection is kept open.
        """
        conn = self._conn if self._conn is not None else sqlite3.connect(self._db_path)
        try:
            with conn:  # sqlite3's own context manager only commits/rolls back; it does not close
                yield conn
        finally:
            if conn is not self._conn:
                conn.close()

    def close(self) -> None:
        """Close the persistent :memory: connection, if any."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _init_db(self) -> None:
        """Create the table if it doesn't exist yet, and add columns that older databases lack."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fetch_log (
                    team_name TEXT PRIMARY KEY,
                    last_fetched TEXT NOT NULL,
                    next_match TEXT,
                    match_count INTEGER NOT NULL DEFAULT 0,
                    last_failed TEXT,
                    fail_streak INTEGER NOT NULL DEFAULT 0
                )
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(fetch_log)")}
            for column, definition in _ADDED_COLUMNS.items():  # databases created before failures were tracked
                if column not in columns:
                    conn.execute(f"ALTER TABLE fetch_log ADD COLUMN {column} {definition}")

    def should_fetch(self, team_name: str) -> bool:
        """Return True if this team is due for a fetch."""
        return self.hours_until_next_fetch(team_name) == 0.0

    def hours_until_next_fetch(self, team_name: str) -> float:
        """Return how many hours until this team is due for a fetch (0.0 if due now or no record)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_fetched, next_match, match_count, last_failed, fail_streak FROM fetch_log WHERE team_name = ?",
                (team_name,)
            ).fetchone()

        if not row:  # no entry for this team yet; first fetch is always allowed
            return 0.0

        last_fetched = datetime.fromisoformat(row[0])
        next_match = datetime.fromisoformat(row[1]) if row[1] else None
        match_count = row[2]
        last_failed = datetime.fromisoformat(row[3]) if row[3] else None
        now = self._clock.now()

        if last_failed is not None:  # set only while the latest attempt failed; a success clears it
            hours_since_failure = (now - last_failed).total_seconds() / 3600
            return max(0.0, self._get_failure_recheck_hours(row[4]) - hours_since_failure)

        hours_since_fetch = (now - last_fetched).total_seconds() / 3600
        recheck_hours = self._get_recheck_hours(next_match, now, match_count)
        return max(0.0, recheck_hours - hours_since_fetch)

    @staticmethod
    def _get_failure_recheck_hours(fail_streak: int) -> float:
        """Wait 1h after a failure, doubling for each failure in a row, up to max_recheck_hours.

        The cap keeps a persistent failure (e.g. an ID the API no longer accepts) at about 3 tries a day,
        so one broken entity cannot use the daily budget.
        """
        settings = FETCH_THRESHOLDS["failed"]
        return min(settings["recheck_hours"] * 2 ** max(fail_streak - 1, 0), settings["max_recheck_hours"])

    def _get_recheck_hours(self, next_match: datetime | None, now: datetime, match_count: int = 0) -> float:
        """Return the recheck interval in hours based on how soon the next match is."""
        if next_match is None:
            return FETCH_THRESHOLDS["unknown"]["recheck_hours"]

        hours_until_match = (next_match - now).total_seconds() / 3600

        if match_count == 1:  # imminent-interval checks only apply when a single upcoming match is known
            if hours_until_match <= FETCH_THRESHOLDS["imminent_close"]["hours"]:
                return FETCH_THRESHOLDS["imminent_close"]["recheck_hours"]
            if hours_until_match <= FETCH_THRESHOLDS["imminent"]["hours"]:
                return FETCH_THRESHOLDS["imminent"]["recheck_hours"]

        days_until_match = hours_until_match / 24

        if days_until_match > FETCH_THRESHOLDS["far"]["days"]:
            return FETCH_THRESHOLDS["far"]["recheck_hours"]
        elif days_until_match > FETCH_THRESHOLDS["medium"]["days"]:
            return FETCH_THRESHOLDS["medium"]["recheck_hours"]
        else:
            return FETCH_THRESHOLDS["near"]["recheck_hours"]

    def record_fetch(self, team_name: str, next_match: datetime | None, match_count: int) -> None:
        """Record that a fetch was just performed for this team, along with their next match time."""
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO fetch_log (team_name, last_fetched, next_match, match_count, last_failed, fail_streak)
                VALUES (?, ?, ?, ?, NULL, 0)
                ON CONFLICT(team_name) DO UPDATE SET
                    last_fetched = excluded.last_fetched,
                    next_match = excluded.next_match,
                    match_count = excluded.match_count,
                    last_failed = NULL,
                    fail_streak = 0
            """, (
                team_name,
                self._clock.now().isoformat(),
                next_match.isoformat() if next_match else None,
                match_count
            ))

    def record_failure(self, team_name: str) -> None:
        """Record that the latest fetch or sync for this team failed, so it is retried after the failure wait.

        The last successful fetch's data (last_fetched, next_match, match_count) is kept. A team with no
        record yet gets one, with last_fetched set to now because that column cannot be empty.
        """
        now = self._clock.now().isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO fetch_log (team_name, last_fetched, next_match, match_count, last_failed, fail_streak)
                VALUES (?, ?, NULL, 0, ?, 1)
                ON CONFLICT(team_name) DO UPDATE SET
                    last_failed = excluded.last_failed,
                    fail_streak = fail_streak + 1
            """, (team_name, now, now))