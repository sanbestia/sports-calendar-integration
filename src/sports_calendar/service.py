"""The service process: one run per start of the systemd timer (`uv run sports-calendar`).

It builds the real collaborators, stops cleanly on SIGTERM, and alerts when the Google login must be renewed.
"""
import argparse
import logging
import os
import signal
import subprocess
from collections.abc import Callable, Mapping
from typing import NoReturn

from dotenv import load_dotenv
from googleapiclient.discovery import build
from tzlocal import get_localzone

from sports_calendar.allsports.client import AllSportsClient
from sports_calendar.clock import Clock
from sports_calendar.google_calendar.auth import TOKEN_ERRORS, check_calendar_tokens
from sports_calendar.heartbeat import Heartbeat
from sports_calendar.logs import configure_logging
from sports_calendar.queries_file import load_queries
from sports_calendar.run import RunDeps, run_once
from sports_calendar.storage.api_calls import APICallTracker
from sports_calendar.storage.fetch_history import FetchHistory
from sports_calendar.storage.fetch_tracker import FetchTracker

logger = logging.getLogger(__name__)

RELOGIN_STEPS = ("To renew it: on a computer with a browser, run `uv run google-login` in the repo, then copy the new "
                 "token.json into the service's folder on the server. The timer starts a run every 15 minutes, so the "
                 "next run picks up the file by itself (README: 'Renewing the Google login').")


def current_version(run_command: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str:
    """Return the short git commit of the running code, or 'unknown' (e.g. a copy without .git)."""
    try:
        result = run_command(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True, timeout=5,
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

    queries = load_queries(queries_path, os.environ)

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
    load_dotenv(".env")  # the working directory's .env: the state folder on the server, the repo root locally; first, for LOG_LEVEL
    configure_logging()
    install_stop_handler()

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

