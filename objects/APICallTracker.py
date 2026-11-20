import logging
from datetime import date

from config import MAX_API_CALLS
from objects.Clock import Clock
from objects.SqliteStore import SqliteStore

logger = logging.getLogger(__name__)

DB_FILE = "api_calls.db"


class APICallTracker(SqliteStore):
    def __init__(self, db_path: str = DB_FILE, clock: Clock | None = None):
        """Initialise the tracker, creating the database table and loading today's call count."""
        super().__init__(db_path)
        self.daily_limit = MAX_API_CALLS
        self._clock = clock or Clock()
        self.current_date = self._clock.today()  # cached locally; compared in _check_reset to detect day rollover
        self._init_db()
        self.count = self._load()

    def _init_db(self) -> None:
        """Create the table if it doesn't exist yet."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_calls (
                    date TEXT PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0
                )
            """)

    def _load(self) -> int:
        """Load today's count from the database, or return 0 if no entry exists."""
        return self.calls_on(self.current_date)

    def calls_on(self, day: date) -> int:
        """Return the stored call count for a day (0 if there is none), e.g. yesterday's for the daily summary."""
        with self._connect() as conn:
            row = conn.execute("SELECT count FROM api_calls WHERE date = ?", (str(day),)).fetchone()
        return row[0] if row else 0

    def _check_reset(self) -> None:
        """Reset counter if the date has changed since last call."""
        today = self._clock.today()
        if today != self.current_date:
            logger.info(f"New day detected, resetting API call counter (was {self.count})")
            self.count = 0
            self.current_date = today

    def increment(self) -> None:
        """Register one API call."""
        self._check_reset()
        self.count += 1
        with self._connect() as conn:
            # UPSERT: 'excluded' refers to the row that was going to be inserted
            conn.execute("""
                INSERT INTO api_calls (date, count) VALUES (?, ?)
                ON CONFLICT(date) DO UPDATE SET count = excluded.count
            """, (str(self.current_date), self.count))

    def is_limit_reached(self) -> bool:
        """Return True if the daily API call limit has been reached."""
        self._check_reset()
        return self.count >= self.daily_limit

    def status(self) -> str:
        """Return a human-readable status string."""
        self._check_reset()
        return f"API calls today: {self.count}/{self.daily_limit}"