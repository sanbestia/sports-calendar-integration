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

from sports_calendar.google_calendar.auth import check_calendar_tokens, TOKEN_ERRORS
from sports_calendar.google_calendar.sync import update_events, SyncResult
from sports_calendar.allsports.matches import get_next_matches
from sports_calendar.queries_file import REQUIRED_QUERY_FIELDS
from sports_calendar.log_setup import configure_logging
from sports_calendar.storage.api_calls import APICallTracker
from sports_calendar.allsports.client import AllSportsClient
from sports_calendar.storage.fetch_tracker import FetchTracker
from sports_calendar.storage.fetch_history import FetchHistory
from sports_calendar.clock import Clock
from sports_calendar.heartbeat import Heartbeat

logger = logging.getLogger(__name__)

RELOGIN_STEPS = ("To renew it: on a computer with a browser, run `uv run python -m sports_calendar.google_calendar.auth` "
                 "in the repo, then copy the new token.json into the service's folder on the server. The timer starts "
                 "a run every 15 minutes, so the next run picks up the file by itself (README: 'Renewing the Google login').")


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


@dataclass
class RunDeps:
    """Everything one run talks to. main() builds the real ones; tests pass fakes."""
    client: AllSportsClient
    api_tracker: APICallTracker
    fetch_tracker: FetchTracker
    history: FetchHistory
    calendar_service: Any  # a Google Calendar service, built from fresh credentials at the start of each run
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


def _sync(deps: RunDeps, data_dict: dict, matches: list) -> SyncResult:
    """Write one entity's matches to its calendar and return what changed."""
    return deps.sync_events(
        service=deps.calendar_service,
        calendar_id=data_dict["calendar"],
        game_list=matches,
        time_zone=str(deps.tz),
        team_id=data_dict["id"],
        clock=deps.clock
    )


def _fetch_and_sync(name: str, data_dict: dict, deps: RunDeps) -> FetchOutcome:
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
    sync = _sync(deps, data_dict, matches) if matches else None
    deps.fetch_tracker.record_fetch(name, earliest, sync.event_count if sync else 0, player_type=data_dict["player_type"])
    return FetchOutcome("matches", matches=len(matches), sync=sync) if matches else FetchOutcome("empty")


def _record_history(deps: RunDeps, name: str, data_dict: dict, result: FetchOutcome, api_calls: int) -> None:
    """Add the fetch to fetch_history. A failed write is only a warning: it must not turn a good fetch into a failure."""
    sync = result.sync or SyncResult(event_count=0)
    try:
        deps.history.record(name, data_dict["player_type"], result.outcome, api_calls=api_calls, matches=result.matches,
                            created=sync.created, updated=sync.updated, deleted=sync.deleted, error=result.error)
    except Exception as e:
        logger.warning(f"Could not write fetch history for '{name}': {e}")


def run_cycle(queries: dict, deps: RunDeps) -> int:
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
        except Exception as e:  # one entity's failure must not end the run: the next run would refetch it and crash again
            logger.exception(f"Fetch or calendar sync failed for '{name}'")
            deps.fetch_tracker.record_failure(name)
            result = FetchOutcome("failed", error=f"{type(e).__name__}: {e}")
        _record_history(deps, name, data_dict, result, api_calls=deps.client.calls_made - calls_before)

    return fetched


def report_run(deps: RunDeps, fetched: int, version: str) -> None:
    """Log the API call count and the version (at INFO only after a run that fetched), then ping the heartbeat."""
    level = logging.INFO if fetched else logging.DEBUG  # a run that only skipped is not worth a line: there are 96 runs a day
    logger.log(level, f"{deps.api_tracker.status()} · version {version}")
    deps.heartbeat.ping()


def run_once(queries: dict, deps: RunDeps, version: str) -> None:
    """Fetch and sync every entity that is due, then report the run. The systemd timer starts a run every 15 minutes."""
    report_run(deps, run_cycle(queries, deps), version)


def current_version(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str:
    """Return the short git commit of the running code, or 'unknown' (e.g. a copy without .git)."""
    try:
        result = run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True, timeout=5,
                     cwd=os.path.dirname(os.path.abspath(__file__)))
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def install_stop_handler(register: Callable = signal.signal) -> None:
    """On SIGTERM (systemd's stop signal for a deploy, a reboot, or a run longer than TimeoutStartSec), log it and exit 0.

    Without a handler Python dies from the signal, `uv run` exits 143 (128 + 15), and systemd logs every
    deploy as "Failed with result 'exit-code'" at warning priority. A real crash still exits non-zero, so
    systemd logs that run as failed. SystemExit is not an Exception, so run_cycle's handler lets it through,
    and an open SQLite transaction is rolled back.
    """
    def stop(signum: int, frame) -> None:
        logger.info("Stopping: received SIGTERM")
        raise SystemExit(0)

    register(signal.SIGTERM, stop)


def make_heartbeat(env: Mapping[str, str] = os.environ) -> Heartbeat:
    """Build the heartbeat from HEALTHCHECK_URL, and log whether it is on (never the URL: it is a secret).

    "on" is a DEBUG line, because each of the 96 runs a day logs it; "off" stays INFO, because then no alert can come.
    """
    heartbeat = Heartbeat(env.get("HEALTHCHECK_URL"))
    if heartbeat.enabled:
        logger.debug("Heartbeat on (healthchecks.io)")
    else:
        logger.info("Heartbeat off: HEALTHCHECK_URL is not set")
    return heartbeat


def stop_for_login(error: Exception, heartbeat: Heartbeat) -> NoReturn:
    """Log why a Google login is needed and how to renew it, send that as a fail ping, and exit with code 1.

    With code 1, systemd logs the run as failed. The timer starts the next run within 15 minutes, so the service
    recovers by itself once a new token.json is in place, and also after a refusal that was only temporary.
    """
    message = f"Google Calendar login needed: {error}. {RELOGIN_STEPS}"
    logger.error(message)
    heartbeat.fail(message)
    raise SystemExit(1)


def _build_and_run(queries_path: str, heartbeat: Heartbeat) -> None:
    """Build the real collaborators and run once."""
    tz = get_localzone()
    clock = Clock()
    calendar_service = build("calendar", "v3", credentials=check_calendar_tokens())
    api_tracker = APICallTracker(clock=clock)
    client = AllSportsClient(api_key=os.environ.get("RAPIDAPI_KEY", ""), tracker=api_tracker)

    queries = load_queries(queries_path)

    deps = RunDeps(
        client=client,
        api_tracker=api_tracker,
        fetch_tracker=FetchTracker(clock=clock),
        history=FetchHistory(clock=clock),
        calendar_service=calendar_service,
        clock=clock,
        tz=tz,
        heartbeat=heartbeat
    )
    run_once(queries, deps, version=current_version())


def main(argv: list[str] | None = None) -> None:
    """Composition root: parse args, start the heartbeat, and run once; a token failure ends the run with an alert.

    main() and _build_and_run() are the only places that read .env, the process environment and the system clock,
    or open real connections; everything below them receives its collaborators.
    """
    configure_logging()
    install_stop_handler()
    load_dotenv()

    parser = argparse.ArgumentParser(description="Sports Calendar Integration")
    parser.add_argument(
        "--queries",
        type=str,
        help="Path to the JSON file with the teams and players to track (default: queries.json)",
        default="queries.json"
    )
    args = parser.parse_args(argv)

    heartbeat = make_heartbeat()
    try:
        _build_and_run(args.queries, heartbeat)
    except TOKEN_ERRORS as e:
        stop_for_login(e, heartbeat)


if __name__ == "__main__":
    main()
