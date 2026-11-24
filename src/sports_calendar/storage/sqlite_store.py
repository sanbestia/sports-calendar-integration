import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


class SqliteStore:
    """Base for the small SQLite stores (API call counts, fetch log, fetch history).

    Each operation opens a short-lived connection and closes it afterwards. For ":memory:" (tests) one
    connection is kept for the object's lifetime, because each new connection would see its own empty database.
    A read-only store opens its connections with SQLite's mode=ro: a write raises an error instead of changing the file.
    """

    def __init__(self, db_path: str, read_only: bool = False):
        self._db_path = db_path
        self._read_only = read_only
        self._conn = sqlite3.connect(db_path, check_same_thread=False) if db_path == ":memory:" else None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection that commits on success and rolls back on error.

        File-based connections are closed afterwards; the persistent :memory: connection is kept open.
        """
        conn = self._conn if self._conn is not None else self._open()
        try:
            with conn:  # sqlite3's own context manager only commits/rolls back; it does not close
                yield conn
        finally:
            if conn is not self._conn:
                conn.close()

    def _open(self) -> sqlite3.Connection:
        """Open a new connection to the database file: read-only when the store is, so no write can reach the file."""
        if self._read_only:
            return sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        return sqlite3.connect(self._db_path)

    def close(self) -> None:
        """Close the persistent :memory: connection, if any."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
