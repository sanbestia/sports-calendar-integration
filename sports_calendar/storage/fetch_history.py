from datetime import date

from sports_calendar.clock import Clock
from sports_calendar.storage.sqlite_store import SqliteStore

DB_FILE = "fetch_log.db"  # same file as FetchTracker: fetch_log holds the latest state, fetch_history every fetch

OUTCOMES = ("matches", "empty", "failed")
_MAX_ERROR_LENGTH = 200


class FetchHistory(SqliteStore):
    """One row per fetch: when, which entity, what came back, how many API calls it used and what it changed.

    It answers questions such as "calls per entity per day" or "failures this month" with a query
    (see sports_calendar/tools/db_stats.py) instead of parsing log text, and it survives restarts and log rotation.
    """

    def __init__(self, db_path: str = DB_FILE, clock: Clock | None = None):
        super().__init__(db_path)
        self._clock = clock or Clock()
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fetch_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fetched_at TEXT NOT NULL,   -- UTC time, ISO 8601
                    day TEXT NOT NULL,          -- local date, the same day basis as api_calls.date
                    entity TEXT NOT NULL,
                    player_type TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK (outcome IN ('matches', 'empty', 'failed')),
                    api_calls INTEGER NOT NULL,
                    matches INTEGER NOT NULL DEFAULT 0,
                    created INTEGER NOT NULL DEFAULT 0,
                    updated INTEGER NOT NULL DEFAULT 0,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS fetch_history_day ON fetch_history(day)")

    def record(self, entity: str, player_type: str, outcome: str, api_calls: int, matches: int = 0,
               created: int = 0, updated: int = 0, deleted: int = 0, error: str | None = None) -> None:
        """Add one fetch to the history. outcome is 'matches', 'empty' or 'failed'."""
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}, not {outcome!r}")
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO fetch_history (fetched_at, day, entity, player_type, outcome, api_calls,
                                           matches, created, updated, deleted, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self._clock.now().isoformat(), self._clock.today().isoformat(), entity, player_type, outcome,
                api_calls, matches, created, updated, deleted, error[:_MAX_ERROR_LENGTH] if error else None
            ))

    def day_summary(self, day: date) -> dict[str, int]:
        """Totals for one day: fetches by outcome, the API calls they used, and the event changes."""
        with self._connect() as conn:
            row = conn.execute("""
                SELECT COUNT(*),
                       COALESCE(SUM(outcome = 'matches'), 0), COALESCE(SUM(outcome = 'empty'), 0),
                       COALESCE(SUM(outcome = 'failed'), 0), COALESCE(SUM(api_calls), 0),
                       COALESCE(SUM(created), 0), COALESCE(SUM(updated), 0), COALESCE(SUM(deleted), 0)
                FROM fetch_history WHERE day = ?
            """, (day.isoformat(),)).fetchone()
        keys = ("fetches", "with_matches", "empty", "failed", "api_calls", "created", "updated", "deleted")
        return dict(zip(keys, row))
