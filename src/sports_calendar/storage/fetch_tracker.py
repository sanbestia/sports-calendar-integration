import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from sports_calendar.config import FETCH_THRESHOLDS
from sports_calendar.clock import Clock
from sports_calendar.storage.sqlite_store import SqliteStore

logger = logging.getLogger(__name__)

DB_FILE = "fetch_log.db"

# Columns added after the first release; _init_db adds them to older databases.
_ADDED_COLUMNS = {
    "last_failed": "TEXT",
    "fail_streak": "INTEGER NOT NULL DEFAULT 0",
    "empty_since": "TEXT",
    "empty_wait_hours": "REAL",
}


def _to_iso(time: datetime | None) -> str | None:
    return time.isoformat() if time is not None else None


def _from_iso(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


@dataclass(frozen=True)
class FetchState:
    """One fetch_log row with its times parsed: what the latest fetch of an entity found."""
    last_fetched: datetime
    next_match: datetime | None
    match_count: int
    last_failed: datetime | None  # set only while the latest attempt failed; a success clears it
    fail_streak: int
    empty_since: datetime | None  # the first empty result of the current run of empty results
    empty_wait_hours: float | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "FetchState":
        return cls(
            last_fetched=datetime.fromisoformat(row["last_fetched"]),
            next_match=_from_iso(row["next_match"]),
            match_count=row["match_count"],
            last_failed=_from_iso(row["last_failed"]),
            fail_streak=row["fail_streak"],
            empty_since=_from_iso(row["empty_since"]),
            empty_wait_hours=row["empty_wait_hours"],
        )

    @property
    def failed(self) -> bool:
        return self.last_failed is not None

    @property
    def last_attempt(self) -> datetime:
        return self.last_failed if self.failed else self.last_fetched


class FetchTracker(SqliteStore):
    def __init__(self, db_path: str = DB_FILE, clock: Clock | None = None, read_only: bool = False):
        """Initialise the tracker. A writable one creates the fetch_log table or adds missing columns; a read-only one never writes."""
        super().__init__(db_path, read_only=read_only)
        self._clock = clock or Clock()
        if not read_only:
            self._init_db()

    def _init_db(self) -> None:
        """Create the table if it doesn't exist yet, and add columns that older databases lack."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fetch_log (
                    team_name TEXT PRIMARY KEY,  -- the entity's name: a team or a player (the stored name stays)
                    last_fetched TEXT NOT NULL,
                    next_match TEXT,
                    match_count INTEGER NOT NULL DEFAULT 0,
                    last_failed TEXT,
                    fail_streak INTEGER NOT NULL DEFAULT 0,
                    empty_since TEXT,
                    empty_wait_hours REAL
                )
            """)
            columns = {name for (name,) in conn.execute("SELECT name FROM pragma_table_info('fetch_log')")}
            for column, definition in _ADDED_COLUMNS.items():  # databases created before failures were tracked
                if column not in columns:
                    conn.execute(f"ALTER TABLE fetch_log ADD COLUMN {column} {definition}")

    def should_fetch(self, entity_name: str) -> bool:
        """Return True if this entity is due for a fetch."""
        return self.hours_until_next_fetch(entity_name) == 0.0

    def hours_until_next_fetch(self, entity_name: str) -> float:
        """Return how many hours until this entity is due for a fetch (0.0 if due now or no record)."""
        with self._connect() as conn:
            state = self._read_state(conn, entity_name)
        if state is None:  # no entry for this entity yet; first fetch is always allowed
            return 0.0
        return self._hours_left(state, self._clock.now())

    def fail_streak(self, entity_name: str) -> int:
        """Return how many fetches of this entity failed in a row (0 after a fetch that worked, or with no row)."""
        with self._connect() as conn:
            state = self._read_state(conn, entity_name)
        return state.fail_streak if state else 0

    def last_result_was_empty(self, entity_name: str) -> bool:
        """Return True when the latest fetch of this entity that worked found no match. A failure keeps that result."""
        with self._connect() as conn:
            state = self._read_state(conn, entity_name)
        return state is not None and state.empty_since is not None

    @staticmethod
    def _read_state(conn: sqlite3.Connection, entity_name: str) -> FetchState | None:
        """Return the stored state of this entity, or None when it has no row yet."""
        cursor = conn.cursor()
        cursor.row_factory = sqlite3.Row  # on this cursor only: other reads on the shared :memory: connection expect tuples
        row = cursor.execute("""
            SELECT last_fetched, next_match, match_count, last_failed, fail_streak, empty_since, empty_wait_hours
            FROM fetch_log WHERE team_name = ?
        """, (entity_name,)).fetchone()
        return FetchState.from_row(row) if row else None

    def _hours_left(self, state: FetchState, now: datetime) -> float:
        """Return the wait for this state minus the hours since its latest attempt, never below 0."""
        hours_since_attempt = (now - state.last_attempt).total_seconds() / 3600
        return max(0.0, self._wait_hours(state, now) - hours_since_attempt)

    def _wait_hours(self, state: FetchState, now: datetime) -> float:
        """Return the failure backoff while the latest attempt failed, else the recheck interval of what the fetch found."""
        if state.failed:
            return self._failure_recheck_hours(state.fail_streak)
        return self._recheck_hours(state.next_match, now, state.match_count, empty_wait_hours=state.empty_wait_hours)

    @staticmethod
    def _failure_recheck_hours(fail_streak: int) -> float:
        """Wait 1h after a failure, doubling for each failure in a row, up to max_recheck_hours.

        The cap keeps a persistent failure (e.g. an ID the API no longer accepts) at about 3 tries a day,
        so one broken entity cannot use the daily budget.
        """
        settings = FETCH_THRESHOLDS["failed"]
        return min(settings["recheck_hours"] * 2 ** max(fail_streak - 1, 0), settings["max_recheck_hours"])

    def _recheck_hours(self, next_match: datetime | None, now: datetime, match_count: int = 0,
                       empty_wait_hours: float | None = None) -> float:
        """Return the recheck interval in hours: the empty-schedule wait, or the band of the next match with the season minimum."""
        if next_match is None:  # empty schedule: the wait stored by record_fetch (8h for rows from before it existed)
            return empty_wait_hours if empty_wait_hours is not None else FETCH_THRESHOLDS["unknown"]["recheck_hours"]
        return self._with_season_minimum(self._match_band_hours(next_match, now, match_count), match_count)

    @staticmethod
    def _with_season_minimum(hours: float, match_count: int) -> float:
        """Make the wait of a full season (min_matches or more upcoming matches) at least min_recheck_hours.

        It only lengthens a wait: a season whose first game is far away keeps the far interval.
        """
        season = FETCH_THRESHOLDS["season"]
        return max(hours, season["min_recheck_hours"]) if match_count >= season["min_matches"] else hours

    @staticmethod
    def _match_band_hours(next_match: datetime, now: datetime, match_count: int) -> float:
        """Return the recheck interval of the band that the next match falls in (imminent, near, medium or far)."""
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

    def record_fetch(self, entity_name: str, next_match: datetime | None, match_count: int, player_type: str = "team") -> None:
        """Record a successful fetch for this entity, along with its next match time.

        An empty result (no next match) also stores how long to wait before the next check: the 'unknown'
        interval during the first grace_days of empty results, then double the previous wait for each
        empty result, up to the cap for the player_type. A result with a match clears that state.
        """
        now = self._clock.now()
        empty_since, empty_wait_hours = None, None
        with self._connect() as conn:  # one transaction for the read of the previous empty wait and the write
            if next_match is None:
                empty_since, empty_wait_hours = self._next_empty_wait(self._read_state(conn, entity_name), now, player_type)
            conn.execute("""
                INSERT INTO fetch_log (team_name, last_fetched, next_match, match_count, last_failed, fail_streak,
                                       empty_since, empty_wait_hours)
                VALUES (?, ?, ?, ?, NULL, 0, ?, ?)
                ON CONFLICT(team_name) DO UPDATE SET
                    last_fetched = excluded.last_fetched,
                    next_match = excluded.next_match,
                    match_count = excluded.match_count,
                    last_failed = NULL,
                    fail_streak = 0,
                    empty_since = excluded.empty_since,
                    empty_wait_hours = excluded.empty_wait_hours
            """, (
                entity_name,
                now.isoformat(),
                _to_iso(next_match),
                match_count,
                _to_iso(empty_since),
                empty_wait_hours
            ))

    @staticmethod
    def _next_empty_wait(previous: FetchState | None, now: datetime, player_type: str) -> tuple[datetime, float]:
        """Return (empty_since, wait in hours) for an empty result, given the state stored before it."""
        settings = FETCH_THRESHOLDS["unknown"]
        stored_since = previous.empty_since if previous else None
        stored_wait = previous.empty_wait_hours if previous else None
        empty_since = stored_since or now  # this empty run starts now if it is new
        previous_wait = stored_wait if stored_wait is not None else settings["recheck_hours"]
        if now - empty_since < timedelta(days=settings["grace_days"]):
            wait = settings["recheck_hours"]
        else:
            wait = min(previous_wait * 2, settings["max_recheck_hours"][player_type])
        return empty_since, wait

    def record_failure(self, entity_name: str) -> None:
        """Record that the latest fetch or sync for this entity failed, so it is retried after the failure wait.

        The last successful fetch's data (last_fetched, next_match, match_count) is kept. An entity with no
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
            """, (entity_name, now, now))