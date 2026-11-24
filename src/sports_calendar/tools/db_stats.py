"""Read-only queries on the service's databases, for a person (`uv run db-stats <function> [params...]`) or for code."""
import argparse
import inspect
import logging
import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from sports_calendar.config import MAX_API_CALLS
from sports_calendar.logs import configure_logging
from sports_calendar.storage.api_calls import DB_FILE as API_CALLS_DB
from sports_calendar.storage.fetch_tracker import DB_FILE as FETCH_LOG_DB, FetchTracker

logger = logging.getLogger(__name__)


@contextmanager
def _connect(db_path: str) -> Iterator[sqlite3.Connection]:
    """Yield a read-only connection to the given SQLite database file and close it afterwards."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)  # read-only URI prevents analytics code from accidentally writing
    try:
        yield conn
    finally:
        conn.close()


def _fmt_datetime(dt) -> str:
    """Format a pandas Timestamp or datetime as 'DD-MM-YYYY HH:MM:SS +00:00'; returns '' for NaT/None."""
    if pd.isna(dt):
        return ""
    return pd.Timestamp(dt).tz_convert("UTC").strftime("%d-%m-%Y %H:%M:%S +00:00")


def _fmt_float(f: float) -> float:
    """Round a float to 2 decimal places."""
    return round(f, 2)


def _formatted(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of a fetch_log DataFrame for reading: the times as text, the hours rounded to 2 decimals."""
    result = df.copy()
    for column in ("last_fetched", "next_match"):
        result[column] = result[column].map(_fmt_datetime)
    for column in ("hours_since_fetch", "hours_until_match"):
        if column in result:
            result[column] = result[column].map(_fmt_float)
    return result


# --- API calls ---

def calls_today(db_path: str = API_CALLS_DB) -> int:
    """Return the number of API calls made today."""
    return calls_on(date.today(), db_path)


def calls_on(day: date, db_path: str = API_CALLS_DB) -> int:
    """Return the number of API calls made on a specific date."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT count FROM api_calls WHERE date = ?",
            (str(day),)
        ).fetchone()
    return row[0] if row else 0


def days_at_max(db_path: str = API_CALLS_DB, limit: int = MAX_API_CALLS, raw: bool = False) -> list:
    """Return all dates where the API call count reached or exceeded the daily limit.

    raw=False (default): returns dates as 'DD-MM-YYYY' strings.
    raw=True: returns datetime.date objects for programmatic use.
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT date FROM api_calls WHERE count >= ? ORDER BY date",
            (limit,)
        ).fetchall()
    dates = [date.fromisoformat(row[0]) for row in rows]
    if raw:
        return dates
    return [d.strftime("%d-%m-%Y") for d in dates]


def pct_days_at_max(db_path: str = API_CALLS_DB, limit: int = MAX_API_CALLS, raw: bool = False) -> float | str:
    """Return the percentage of recorded days where the API limit was reached.

    raw=False (default): returns a formatted string like '42.50%'.
    raw=True: returns a raw float for programmatic use.
    """
    with _connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM api_calls").fetchone()[0]
        at_max = conn.execute(
            "SELECT COUNT(*) FROM api_calls WHERE count >= ?", (limit,)
        ).fetchone()[0]
    value = (at_max / total * 100) if total else 0.0
    if raw:
        return value
    return f"{value:.2f}%"


def avg_daily_calls(db_path: str = API_CALLS_DB, raw: bool = False) -> float:
    """Return the average number of API calls per recorded day.

    raw=False (default): returns value rounded to 2 decimal places.
    raw=True: returns full-precision float for programmatic use.
    """
    with _connect(db_path) as conn:
        row = conn.execute("SELECT AVG(count) FROM api_calls").fetchone()
    value = row[0] if row[0] is not None else 0.0
    if raw:
        return value
    return _fmt_float(value)


def all_calls_df(db_path: str = API_CALLS_DB, limit: int = MAX_API_CALLS) -> pd.DataFrame:
    """Return a DataFrame of all recorded days with columns: date, count, at_limit."""
    with _connect(db_path) as conn:
        df = pd.read_sql_query("SELECT date, count FROM api_calls ORDER BY date", conn)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["at_limit"] = df["count"] >= limit
    return df


def remaining_calls_today(db_path: str = API_CALLS_DB, limit: int = MAX_API_CALLS) -> int:
    """Return how many API calls remain before today's limit is reached (floor 0)."""
    return max(0, limit - calls_today(db_path))


def calls_in_range(start: date, end: date, db_path: str = API_CALLS_DB) -> int:
    """Return the total number of API calls made between start and end dates (inclusive)."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(count), 0) FROM api_calls WHERE date BETWEEN ? AND ?",
            (str(start), str(end))
        ).fetchone()
    return row[0]


def busiest_days(n: int = 5, db_path: str = API_CALLS_DB, limit: int = MAX_API_CALLS) -> pd.DataFrame:
    """Return the top N days by API call volume."""
    df = all_calls_df(db_path, limit=limit)
    return df.nlargest(n, "count").reset_index(drop=True)


def calls_per_period(freq: str = "W", db_path: str = API_CALLS_DB, limit: int = MAX_API_CALLS) -> pd.DataFrame:
    """Return API call totals grouped by time period.

    freq: any pandas offset alias, for example 'W' (week), 'ME' (month end) or 'QE' (quarter end).
    Columns: date (period start), total_calls, days_recorded, days_at_limit.
    """
    df = all_calls_df(db_path, limit=limit)
    if df.empty:
        return pd.DataFrame(columns=["date", "total_calls", "days_recorded", "days_at_limit"])
    indexed = df.set_index(pd.to_datetime(df["date"]))  # DatetimeIndex is required for resample()
    result = pd.DataFrame({
        "total_calls": indexed["count"].resample(freq).sum(),
        "days_recorded": indexed["count"].resample(freq).count(),
        "days_at_limit": indexed["at_limit"].resample(freq).sum().astype(int),
    }).reset_index()
    result.rename(columns={"date": "period_start"}, inplace=True)
    result["period_start"] = result["period_start"].dt.date
    return result


def api_budget_forecast(db_path: str = API_CALLS_DB, limit: int = MAX_API_CALLS) -> dict:
    """Project today's total API calls based on the current rate since midnight.

    Returns a dict with: calls_so_far, hours_elapsed, rate_per_hour,
    projected_daily_total, limit, will_exceed_limit.
    """
    count = calls_today(db_path)
    now = datetime.now()
    hours_elapsed = now.hour + now.minute / 60 + now.second / 3600
    rate_per_hour = count / hours_elapsed if hours_elapsed > 0 else 0.0
    projected = round(rate_per_hour * 24)  # linear extrapolation to end of day assuming constant rate
    return {
        "calls_so_far": count,
        "hours_elapsed": round(hours_elapsed, 2),
        "rate_per_hour": round(rate_per_hour, 2),
        "projected_daily_total": projected,
        "limit": limit,
        "will_exceed_limit": projected >= limit,
    }


# --- Fetch log ---

def all_fetches_df(db_path: str = FETCH_LOG_DB, raw: bool = False) -> pd.DataFrame:
    """Return a DataFrame of all teams with their last fetch info.

    raw=False (default): datetime columns formatted as 'DD-MM-YYYY HH:MM:SS +00:00' strings.
    raw=True: datetime columns as pandas Timestamps for programmatic use.
    """
    with _connect(db_path) as conn:
        df = pd.read_sql_query(
            "SELECT team_name, last_fetched, next_match, match_count FROM fetch_log ORDER BY team_name",
            conn
        )
    df["last_fetched"] = pd.to_datetime(df["last_fetched"], utc=True)
    df["next_match"] = pd.to_datetime(df["next_match"], utc=True, errors="coerce")
    return df if raw else _formatted(df)


def fetch_gap_df(db_path: str = FETCH_LOG_DB, raw: bool = False) -> pd.DataFrame:
    """Return a DataFrame with derived timing columns per team.

    Columns: team_name, last_fetched, next_match, match_count,
             hours_since_fetch, hours_until_match

    raw=False (default): datetime columns formatted as strings, floats rounded to 2dp.
    raw=True: native types for programmatic use.
    """
    df = all_fetches_df(db_path, raw=True)  # raw=True so Timestamps are available for arithmetic
    now = pd.Timestamp.now(tz="UTC")
    df["hours_since_fetch"] = (now - df["last_fetched"]).dt.total_seconds() / 3600
    df["hours_until_match"] = (df["next_match"] - now).dt.total_seconds() / 3600
    return df if raw else _formatted(df)


def teams_due_now(db_path: str = FETCH_LOG_DB) -> list[str]:
    """Return team names that the scheduler would fetch right now.

    It asks a read-only FetchTracker, so the result follows the scheduler's own rules and the database is not changed.
    """
    tracker = FetchTracker(db_path=db_path, read_only=True)
    df = all_fetches_df(db_path, raw=True)  # raw=True; team_name column is unaffected by formatting
    return [name for name in df["team_name"] if tracker.should_fetch(name)]


def teams_with_no_upcoming_match(db_path: str = FETCH_LOG_DB) -> list[str]:
    """Return team names for which no upcoming match is recorded."""
    df = all_fetches_df(db_path, raw=True)  # raw=True so isna() works on Timestamps (not empty strings)
    return df[df["next_match"].isna()]["team_name"].tolist()


def stale_teams(hours: float = 48, db_path: str = FETCH_LOG_DB, raw: bool = False) -> pd.DataFrame:
    """Return teams not fetched in the last `hours` hours, regardless of their schedule.

    Sorted by hours_since_fetch descending (stalest first).

    raw=False (default): datetime columns formatted as strings, floats rounded to 2dp.
    raw=True: native types for programmatic use.
    """
    df = fetch_gap_df(db_path, raw=True)  # raw=True so numeric comparison on hours_since_fetch works
    result = df[df["hours_since_fetch"] > hours].sort_values("hours_since_fetch", ascending=False).reset_index(drop=True)
    return result if raw else _formatted(result)


# --- fetch_history (one row per fetch, in fetch_log.db) ---

def _history_df(days: int, db_path: str, columns: str) -> pd.DataFrame:
    """Rows of fetch_history from the last `days` days (today included); empty if the table does not exist yet."""
    first_day = (date.today() - timedelta(days=days - 1)).isoformat()
    with _connect(db_path) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'fetch_history'").fetchone()
        if not exists:
            return pd.DataFrame(columns=[c.strip() for c in columns.split(",")])
        return pd.read_sql_query(f"SELECT {columns} FROM fetch_history WHERE day >= ? ORDER BY fetched_at", conn, params=(first_day,))


def calls_per_entity(days: int = 7, db_path: str = FETCH_LOG_DB) -> pd.DataFrame:
    """API calls and fetches per entity over the last N days, most calls first (columns: entity, fetches, api_calls, calls_per_day)."""
    df = _history_df(days, db_path, "entity, api_calls")
    if df.empty:
        return pd.DataFrame(columns=["entity", "fetches", "api_calls", "calls_per_day"])
    result = df.groupby("entity").agg(fetches=("api_calls", "size"), api_calls=("api_calls", "sum")).reset_index()
    result["calls_per_day"] = (result["api_calls"] / days).round(2)
    return result.sort_values(["api_calls", "entity"], ascending=[False, True]).reset_index(drop=True)


def outcomes_per_entity(days: int = 7, db_path: str = FETCH_LOG_DB) -> pd.DataFrame:
    """How each entity's fetches ended over the last N days (columns: entity, matches, empty, failed)."""
    df = _history_df(days, db_path, "entity, outcome")
    result = pd.crosstab(df["entity"], df["outcome"]) if not df.empty else pd.DataFrame()
    result = result.reindex(columns=["matches", "empty", "failed"], fill_value=0)
    return result.rename_axis(columns=None).reset_index().rename(columns={"index": "entity"})


def recent_failures(days: int = 7, db_path: str = FETCH_LOG_DB) -> pd.DataFrame:
    """Failed fetches over the last N days, newest first (columns: fetched_at, entity, error)."""
    df = _history_df(days, db_path, "fetched_at, entity, outcome, error")
    df = df[df["outcome"] == "failed"][["fetched_at", "entity", "error"]]
    return df.iloc[::-1].reset_index(drop=True)


# --- command line ---

class _CommandError(Exception):
    """A command that cannot run or that failed. Each argument is one line of the error message."""


def _public_functions() -> dict[str, Callable]:
    """The functions that this module defines for the command line: not private, not main, and not imported."""
    module = sys.modules[__name__]
    return {name: function for name, function in inspect.getmembers(module, inspect.isfunction)
            if not name.startswith("_") and name != "main" and function.__module__ == __name__}


def _log_usage(functions: dict[str, Callable]) -> None:
    """Log the usage line, then each function with its signature and the first line of its docstring."""
    logger.info("No function specified.")
    logger.info("Usage: uv run db-stats <function> [params...]")
    logger.info("")
    logger.info("Available functions:")
    for name, function in sorted(functions.items()):
        logger.info(f" * {name} {inspect.signature(function)}")
        first_doc_line = (function.__doc__ or "").split("\n")[0].strip()
        if first_doc_line:
            logger.info(f"     {first_doc_line}")
            logger.info("  ------")


def _as_bool(text: str) -> bool:
    """Return the boolean that the command-line text names (true/1/yes or false/0/no)."""
    if text.lower() in ("true", "1", "yes"):
        return True
    if text.lower() in ("false", "0", "no"):
        return False
    raise ValueError(f"expected true/false, got '{text}'")


_CONVERTERS: dict[Any, Callable[[str], Any]] = {date: date.fromisoformat, int: int, float: float, bool: _as_bool}


def _converted(text: str, parameter: inspect.Parameter) -> Any:
    """Return the command-line text as the type that the parameter declares; text for str or no annotation."""
    converter = _CONVERTERS.get(parameter.annotation)
    if converter is None:
        return text
    try:
        return converter(text)
    except (ValueError, TypeError) as e:
        raise _CommandError(f"Bad value for '{parameter.name}': {e}") from e


def _arguments(name: str, function: Callable, texts: list[str]) -> list:
    """Return the command-line values converted to the types of the function's parameters, in order."""
    parameters = list(inspect.signature(function).parameters.values())
    required = [p.name for p in parameters if p.default is inspect.Parameter.empty]
    if len(texts) < len(required):
        raise _CommandError(f"'{name}' requires: {', '.join(required)}",
                            f"Missing: {', '.join(required[len(texts):])}",
                            f"Usage: uv run db-stats {name} {' '.join(f'<{n}>' for n in required)}")
    return [_converted(text, parameter) for text, parameter in zip(texts, parameters)]


def _call(functions: dict[str, Callable], name: str, texts: list[str]) -> Any:
    """Call the named function with the command-line values; raise _CommandError when it cannot run or fails."""
    if name not in functions:
        raise _CommandError(f"Unknown function '{name}'. Run without arguments to see available functions.")
    arguments = _arguments(name, functions[name], texts)
    try:
        return functions[name](*arguments)
    except Exception as e:
        raise _CommandError(f"Error running '{name}': {e}") from e


def _log_result(result: Any) -> None:
    """Log a result: a DataFrame as a table, a dict with one key per line, a list with one item per line, else as it is."""
    if isinstance(result, pd.DataFrame):
        logger.info(result.to_string(index=False) if not result.empty else "(no data)")
    elif isinstance(result, dict):
        for key, value in result.items():
            logger.info(f"  {key}: {value}")
    elif isinstance(result, list):
        for item in result:
            logger.info(f"  {item}")
        if not result:
            logger.info("(none)")
    else:
        logger.info(result)


def _run_command(argv: list[str] | None = None) -> None:
    """Call a public function of this module by name, with its parameters from argv (sys.argv[1:] by default)."""
    parser = argparse.ArgumentParser(prog="db-stats", add_help=True)
    parser.add_argument("function", nargs="?", metavar="FUNCTION")
    parser.add_argument("params", nargs="*", metavar="PARAM")
    args = parser.parse_args(argv)
    functions = _public_functions()
    if not args.function:
        _log_usage(functions)
        sys.exit(0)
    try:
        result = _call(functions, args.function, args.params)
    except _CommandError as e:
        for line in e.args:
            logger.error(line)
        sys.exit(1)
    _log_result(result)


def main() -> None:
    """Entry point of `uv run db-stats`. Tests call _run_command, because configure_logging replaces the root handlers."""
    configure_logging()
    _run_command()
