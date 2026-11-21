import os
import json
import signal
import subprocess
import logging
import argparse
import datetime
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, NoReturn

from dotenv import load_dotenv
from tzlocal import get_localzone
from googleapiclient.discovery import build

from functions.check_calendar_tokens import check_calendar_tokens, TOKEN_ERRORS
from functions.calendar_methods import update_events, SyncResult
from functions.get_next_matches import get_next_matches
from functions.get_ids import get_ids
from functions.queries_file import REQUIRED_QUERY_FIELDS
from functions.log_setup import configure_logging
from functions.utils import wait
from objects.APICallTracker import APICallTracker
from objects.AllSportsClient import AllSportsClient
from objects.FetchTracker import FetchTracker
from objects.FetchHistory import FetchHistory
from objects.Clock import Clock
from objects.Heartbeat import Heartbeat
from config import MAIN_LOOP_SLEEP_MINUTES, API_CALL_CEILING

logger = logging.getLogger(__name__)

RELOGIN_STEPS = ("To renew it: on a computer with a browser, run `uv run python -m functions.check_calendar_tokens` "
                 "in the repo, then copy the new token.json into the service's folder on the server. The service starts "
                 "again every minute, so it picks up the file by itself (README: 'Renewing the Google login').")


def load_queries(filepath: str, env: Mapping[str, str] = os.environ) -> dict:
    """Load queries from a JSON file, resolving calendar IDs from the given environment."""
    with open(filepath, "r") as f:
        raw = json.load(f)

    for name, data in raw.items():
        missing = REQUIRED_QUERY_FIELDS - data.keys()
        if missing:
            raise ValueError(f"Query '{name}' is missing required fields: {', '.join(sorted(missing))}")
        env_key = data["calendar"]
        resolved = env.get(env_key)
        if not resolved:
            raise ValueError(f"Environment variable '{env_key}' not found for '{name}'")
        data["calendar"] = resolved  # replace the env var key with the actual calendar ID so callers get the resolved value

    return raw


def build_queries_from_get_ids(client: AllSportsClient, ask: Callable[[str], str] = input) -> dict:
    """Interactively build queries using get_ids(), prompting for calendar IDs."""
    id_dict = get_ids(client, ask=ask)
    queries = {}
    for team_id, data in id_dict.items():
        calendar_id = ask(f"Enter calendar id for {data['name']}: ")
        queries[data["name"]] = {
            "id": team_id,
            "sport": data["sport"],
            "player_type": data["player_type"],
            "calendar": calendar_id
        }
    return queries


class CalendarConnection:
    """Holds the current Google Calendar service and can build a fresh one after a dropped connection."""

    def __init__(self, connect: Callable[[], Any]):
        self._connect = connect
        self.service = connect()

    def reconnect(self) -> None:
        """Replace the service with a newly built one (fresh credentials and HTTP connection)."""
        self.service = self._connect()


@dataclass
class LoopDeps:
    """Everything the fetch-and-sync loop talks to. main() builds the real ones; tests pass fakes."""
    client: AllSportsClient
    api_tracker: APICallTracker
    fetch_tracker: FetchTracker
    history: FetchHistory
    calendar: CalendarConnection
    clock: Clock
    tz: datetime.tzinfo
    fetch_matches: Callable[..., list | None] = get_next_matches
    sync_events: Callable[..., SyncResult] = update_events
    heartbeat: Heartbeat = field(default_factory=lambda: Heartbeat(None))  # off unless main() passes the real one


@dataclass(frozen=True)
class FetchOutcome:
    """What one entity's fetch-and-sync step ended with; it becomes one fetch_history row."""
    outcome: str  # 'matches', 'empty' or 'failed'
    matches: int = 0
    sync: SyncResult | None = None
    error: str | None = None


def _sync(deps: LoopDeps, data_dict: dict, matches: list) -> SyncResult:
    """Write one entity's matches to its calendar and return what changed."""
    return deps.sync_events(
        service=deps.calendar.service,
        calendar_id=data_dict["calendar"],
        game_list=matches,
        time_zone=str(deps.tz),
        team_id=data_dict["id"],
        clock=deps.clock
    )


def _fetch_and_sync(name: str, data_dict: dict, deps: LoopDeps) -> FetchOutcome:
    """Fetch one entity's matches, write them to its calendar, and record the result in the fetch log.

    A failed fetch (None) is recorded as a failure, not as an empty schedule. Any other error is raised
    to run_cycle, which records it as a failure too.
    """
    matches = deps.fetch_matches(
        deps.client,
        team_id=data_dict["id"],
        team_name=name,
        sport=data_dict["sport"],
        player_type=data_dict["player_type"],
        time_zone=str(deps.tz)
    )
    if matches is None:  # already logged by the fetch; retry soon
        deps.fetch_tracker.record_failure(name)
        return FetchOutcome("failed", error="fetch failed (see the log)")

    earliest = min((m.start_time for m in matches), default=None)  # default=None avoids ValueError when matches is empty

    sync = None
    if matches:
        try:
            sync = _sync(deps, data_dict, matches)
        except BrokenPipeError:
            logger.warning("Calendar connection lost (post-sleep?). Reconnecting and retrying...")
            deps.calendar.reconnect()
            sync = _sync(deps, data_dict, matches)  # a second failure goes to run_cycle's handler

    deps.fetch_tracker.record_fetch(name, earliest, sync.event_count if sync else 0, player_type=data_dict["player_type"])
    return FetchOutcome("matches", matches=len(matches), sync=sync) if matches else FetchOutcome("empty")


def _record_history(deps: LoopDeps, name: str, data_dict: dict, result: FetchOutcome, api_calls: int) -> None:
    """Add the fetch to fetch_history. A failed write is only a warning: it must not turn a good fetch into a failure."""
    sync = result.sync or SyncResult(event_count=0)
    try:
        deps.history.record(name, data_dict["player_type"], result.outcome, api_calls=api_calls, matches=result.matches,
                            created=sync.created, updated=sync.updated, deleted=sync.deleted, error=result.error)
    except Exception as e:
        logger.warning(f"Could not write fetch history for '{name}': {e}")


def run_cycle(queries: dict, deps: LoopDeps) -> int:
    """Fetch and sync every entity that is due, once; stop early when the daily API limit is reached.

    Returns how many entities were fetched (0 when every entity was skipped).
    """
    fetched = 0
    for name, data_dict in queries.items():

        if deps.api_tracker.is_limit_reached():
            logger.warning(
                f"Daily API limit of {deps.api_tracker.daily_limit} calls reached — "
                f"skipping remaining fetches until tomorrow"
                )
            break

        if not deps.fetch_tracker.should_fetch(name):
            hours_left = deps.fetch_tracker.hours_until_next_fetch(name)
            logger.debug(f"Skipping {name} — next fetch in {hours_left:.1f}h")
            continue

        fetched += 1
        calls_before = deps.client.calls_made
        try:
            result = _fetch_and_sync(name, data_dict, deps)
        except TOKEN_ERRORS:
            raise  # no entity can sync without a token: main() reports it, instead of one entity's backoff
        except Exception as e:  # one entity's failure must not stop the service: a crash would restart, refetch and crash again
            logger.exception(f"Fetch or calendar sync failed for '{name}'")
            deps.fetch_tracker.record_failure(name)
            result = FetchOutcome("failed", error=f"{type(e).__name__}: {e}")
        _record_history(deps, name, data_dict, result, api_calls=deps.client.calls_made - calls_before)

    return fetched


def _log_daily_summary(deps: LoopDeps, day: datetime.date) -> None:
    """Log one line with a finished day's totals: API calls (api_calls.db) and fetches and changes (fetch_history).

    The numbers come from the databases, so a restart during the day does not reset them.
    """
    try:
        calls = deps.api_tracker.calls_on(day)
        totals = deps.history.day_summary(day)
    except Exception as e:
        logger.warning(f"Could not build the daily summary for {day}: {e}")
        return
    over = calls > API_CALL_CEILING
    logger.log(
        logging.WARNING if over else logging.INFO,
        f"Daily summary for {day}: {calls} API calls"
        f"{f' (above the {API_CALL_CEILING}-call ceiling)' if over else ''}; "
        f"{totals['fetches']} fetches ({totals['with_matches']} with matches, {totals['empty']} empty, {totals['failed']} failed); "
        f"events: {totals['created']} created, {totals['updated']} updated, {totals['deleted']} deleted"
    )


def run(queries: dict, deps: LoopDeps, max_cycles: int | None = None) -> None:
    """Run cycles forever (or max_cycles times), waiting MAIN_LOOP_SLEEP_MINUTES after each one.

    The first cycle of a new day first logs the previous day's summary. The heartbeat is pinged after each cycle.
    """
    sleep_minutes = MAIN_LOOP_SLEEP_MINUTES
    cycles = 0
    summary_day = deps.clock.today()
    while max_cycles is None or cycles < max_cycles:
        today = deps.clock.today()
        if today != summary_day:
            _log_daily_summary(deps, summary_day)
            summary_day = today

        fetched = run_cycle(queries, deps)
        deps.heartbeat.ping()
        cycles += 1

        now = deps.clock.now().astimezone(deps.tz)
        deadline = now + datetime.timedelta(minutes=sleep_minutes)
        level = logging.INFO if fetched else logging.DEBUG  # a cycle that only skipped is not worth a line
        logger.log(level, f"{deps.api_tracker.status()} · next cycle at {deadline.hour:02}:{deadline.minute:02}")
        wait(deadline, deps.clock)


def current_version(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str:
    """Return the short git commit of the running code, or 'unknown' (e.g. a copy without .git)."""
    try:
        result = run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True, timeout=5,
                     cwd=os.path.dirname(os.path.abspath(__file__)))
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def install_stop_handler(register: Callable = signal.signal) -> None:
    """On SIGTERM (systemd's stop signal for deploys, restarts and reboots), log it and exit with code 0.

    Without a handler Python dies from the signal, `uv run` exits 143 (128 + 15), and systemd logs every
    deploy as "Failed with result 'exit-code'" at warning priority. A real crash still exits non-zero, so
    Restart=on-failure still restarts it. SystemExit is not an Exception, so run_cycle's handler lets it through,
    and an open SQLite transaction is rolled back.
    """
    def stop(signum: int, frame) -> None:
        logger.info("Stopping: received SIGTERM")
        raise SystemExit(0)

    register(signal.SIGTERM, stop)


def make_heartbeat(env: Mapping[str, str] = os.environ) -> Heartbeat:
    """Build the heartbeat from HEALTHCHECK_URL, and log whether it is on (never the URL: it is a secret)."""
    heartbeat = Heartbeat(env.get("HEALTHCHECK_URL"))
    logger.info("Heartbeat on (healthchecks.io)" if heartbeat.enabled else "Heartbeat off: HEALTHCHECK_URL is not set")
    return heartbeat


def stop_for_login(error: Exception, heartbeat: Heartbeat) -> NoReturn:
    """Log why a Google login is needed and how to renew it, send that as a fail ping, and exit with code 1.

    With code 1, systemd (Restart=on-failure) starts the service again every minute, so it recovers by itself
    once a new token.json is in place, and also after a refusal that was only temporary.
    """
    message = f"Google Calendar login needed: {error}. {RELOGIN_STEPS}"
    logger.error(message)
    heartbeat.fail(message)
    raise SystemExit(1)


def _serve(queries_path: str | None, heartbeat: Heartbeat) -> None:
    """Build the real collaborators and run the loop forever."""
    tz = get_localzone()
    clock = Clock()
    calendar = CalendarConnection(lambda: build("calendar", "v3", credentials=check_calendar_tokens()))
    api_tracker = APICallTracker(clock=clock)
    client = AllSportsClient(api_key=os.environ.get("RAPIDAPI_KEY", ""), tracker=api_tracker)

    if queries_path:
        queries = load_queries(queries_path)
    else:
        queries = build_queries_from_get_ids(client)

    deps = LoopDeps(
        client=client,
        api_tracker=api_tracker,
        fetch_tracker=FetchTracker(clock=clock),
        history=FetchHistory(clock=clock),
        calendar=calendar,
        clock=clock,
        tz=tz,
        heartbeat=heartbeat
    )
    run(queries, deps)


def main(argv: list[str] | None = None) -> None:
    """Composition root: parse args, start the heartbeat, and serve; a token failure stops the service with an alert.

    main() and _serve() are the only places that read .env, the process environment and the system clock, or
    open real connections; everything below them receives its collaborators.
    """
    configure_logging()
    install_stop_handler()
    logger.info(f"Starting sports-calendar {current_version()}")
    load_dotenv()

    parser = argparse.ArgumentParser(description="Sports Calendar Integration")
    parser.add_argument(
        "--queries",
        type=str,
        help="Path to a JSON file containing search queries",
        default=None
    )
    args = parser.parse_args(argv)

    heartbeat = make_heartbeat()
    try:
        _serve(args.queries, heartbeat)
    except TOKEN_ERRORS as e:
        stop_for_login(e, heartbeat)


if __name__ == "__main__":
    main()
