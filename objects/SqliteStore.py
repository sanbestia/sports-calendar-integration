import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


class SqliteStore:
    """Base for the small SQLite stores (API call counts, fetch log, fetch history).

    Each operation opens a short-lived connection and closes it afterwards. For ":memory:" (tests) one
    connection is kept for the object's lifetime, because each new connection would see its own empty database.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False) if db_path == ":memory:" else None

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
