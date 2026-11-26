import logging
from datetime import date

from sports_calendar.config import MAX_API_CALLS
from sports_calendar.clock import Clock
from sports_calendar.storage.sqlite_store import SqliteStore

logger = logging.getLogger(__name__)

DB_FILE = "api_calls.db"


class APICallTracker(SqliteStore):
    """Counts the AllSports calls of each local day in api_calls.db, so a run stops before the daily limit."""

    def __init__(self, db_path: str = DB_FILE, clock: Clock | None = None):
        """Initialise the tracker, creating the database table and loading today's call count."""
        super().__init__(db_path)
        self.daily_limit = MAX_API_CALLS
        self._clock = clock or Clock()
        self.current_date = self._clock.today()  # the day that count belongs to; _reset_on_a_new_day compares it with today
        self._init_db()
        self.count = self.calls_on(self.current_date)

    def _init_db(self) -> None:
        """Create the table if it doesn't exist yet."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_calls (
                    date TEXT PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0
                )
            """)

    def calls_on(self, day: date) -> int:
        """Return the stored call count for a day (0 if there is none), e.g. yesterday's for the daily summary."""
        with self._connect() as conn:
            row = conn.execute("SELECT count FROM api_calls WHERE date = ?", (str(day),)).fetchone()
        return row[0] if row else 0

    def _reset_on_a_new_day(self) -> None:
        """Start the count again at 0 when the local date has changed since the last call."""
        today = self._clock.today()
        if today != self.current_date:
            logger.info(f"New day detected, resetting API call counter (was {self.count})")
            self.count = 0
            self.current_date = today

    def increment(self, reported_used: int = 0) -> None:
        """Register one API call. reported_used is the day's count that RapidAPI sent, and a higher one wins: it also
        counts calls this tracker never saw. RapidAPI's day may end at another hour; that can only stop fetches earlier."""
        self._reset_on_a_new_day()
        day = str(self.current_date)
        with self._connect() as conn:  # the count is added in SQL, so two processes never lose a call
            conn.execute("""
                INSERT INTO api_calls (date, count) VALUES (?, MAX(1, ?))
                ON CONFLICT(date) DO UPDATE SET count = MAX(count + 1, ?)
            """, (day, reported_used, reported_used))
            self.count = conn.execute("SELECT count FROM api_calls WHERE date = ?", (day,)).fetchone()[0]

    def is_limit_reached(self) -> bool:
        """Return True if the daily API call limit has been reached."""
        self._reset_on_a_new_day()
        return self.count >= self.daily_limit

    def status(self) -> str:
        """Return a human-readable status string."""
        self._reset_on_a_new_day()
        return f"API calls today: {self.count}/{self.daily_limit}"