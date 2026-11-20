import os
import json
import logging
import argparse
import datetime
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from tzlocal import get_localzone
from googleapiclient.discovery import build

from functions.check_calendar_tokens import check_calendar_tokens
from functions.calendar_methods import update_events
from functions.get_next_matches import get_next_matches
from functions.get_ids import get_ids
from functions.utils import wait
from objects.APICallTracker import APICallTracker
from objects.AllSportsClient import AllSportsClient
from objects.FetchTracker import FetchTracker
from objects.Clock import Clock
from config import MAIN_LOOP_SLEEP_MINUTES

logger = logging.getLogger(__name__)


_REQUIRED_QUERY_FIELDS = {"id", "sport", "player_type", "calendar"}


def load_queries(filepath: str, env: Mapping[str, str] = os.environ) -> dict:
    """Load queries from a JSON file, resolving calendar IDs from the given environment."""
    with open(filepath, "r") as f:
        raw = json.load(f)

    for name, data in raw.items():
        missing = _REQUIRED_QUERY_FIELDS - data.keys()
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
    calendar: CalendarConnection
    clock: Clock
    tz: datetime.tzinfo
    fetch_matches: Callable[..., list] = get_next_matches
    sync_events: Callable[..., int] = update_events


def _sync(deps: LoopDeps, data_dict: dict, matches: list) -> int:
    """Write one entity's matches to its calendar and return its event count."""
    return deps.sync_events(
        service=deps.calendar.service,
        calendar_id=data_dict["calendar"],
        game_list=matches,
        time_zone=str(deps.tz),
        team_id=data_dict["id"],
        clock=deps.clock
    )


def _fetch_and_sync(name: str, data_dict: dict, deps: LoopDeps) -> None:
    """Fetch one entity's matches, write them to its calendar, and record the result.

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
        return

    earliest = min((m.start_time for m in matches), default=None)  # default=None avoids ValueError when matches is empty

    match_count = 0
    if matches:
        try:
            match_count = _sync(deps, data_dict, matches)
        except BrokenPipeError:
            logger.warning("Calendar connection lost (post-sleep?). Reconnecting and retrying...")
            deps.calendar.reconnect()
            match_count = _sync(deps, data_dict, matches)  # a second failure goes to run_cycle's handler

    deps.fetch_tracker.record_fetch(name, earliest, match_count)


def run_cycle(queries: dict, deps: LoopDeps) -> None:
    """Fetch and sync every entity that is due, once; stop early when the daily API limit is reached."""
    for name, data_dict in queries.items():

        if deps.api_tracker.is_limit_reached():
            logger.warning(
                f"Daily API limit of {deps.api_tracker.daily_limit} calls reached — "
                f"skipping remaining fetches until tomorrow"
                )
            break

        if not deps.fetch_tracker.should_fetch(name):
            hours_left = deps.fetch_tracker.hours_until_next_fetch(name)
            logger.info(f"Skipping {name} — next fetch in {hours_left:.1f}h")
            logger.info("--------------------------------------------------")
            logger.info("")
            continue

        try:
            _fetch_and_sync(name, data_dict, deps)
        except Exception:  # one entity's failure must not stop the service: a crash would restart, refetch and crash again
            logger.exception(f"Fetch or calendar sync failed for '{name}'")
            deps.fetch_tracker.record_failure(name)

        logger.info("")
        logger.info("--------------------------------------------------")
        logger.info("")


def run(queries: dict, deps: LoopDeps, max_cycles: int | None = None) -> None:
    """Run cycles forever (or max_cycles times), waiting MAIN_LOOP_SLEEP_MINUTES after each one."""
    sleep_minutes = MAIN_LOOP_SLEEP_MINUTES
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        run_cycle(queries, deps)
        cycles += 1

        now = deps.clock.now().astimezone(deps.tz)
        deadline = now + datetime.timedelta(minutes=sleep_minutes)
        logger.info(deps.api_tracker.status())
        logger.info(
            f"Next cycle in {sleep_minutes} minute{'' if sleep_minutes == 1 else 's'} "
            f"(@ {deadline.hour:02}:{deadline.minute:02})"
        )
        wait(deadline, deps.clock)

        logger.info("")
        logger.info("------------------------------------------------------------------------")
        logger.info("")


def main(argv: list[str] | None = None) -> None:
    """Composition root: parse args, build the real collaborators, and run the loop forever.

    This is the only place that reads .env, the process environment and the system clock, or opens
    real connections; everything below it receives its collaborators.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)  # suppress the verbose 'file_cache unavailable' warning emitted by the Google client
    load_dotenv()

    parser = argparse.ArgumentParser(description="Sports Calendar Integration")
    parser.add_argument(
        "--queries",
        type=str,
        help="Path to a JSON file containing search queries",
        default=None
    )
    args = parser.parse_args(argv)

    tz = get_localzone()
    clock = Clock()
    calendar = CalendarConnection(lambda: build("calendar", "v3", credentials=check_calendar_tokens()))
    api_tracker = APICallTracker(clock=clock)
    client = AllSportsClient(api_key=os.environ.get("RAPIDAPI_KEY", ""), tracker=api_tracker)

    if args.queries:
        queries = load_queries(args.queries)
    else:
        queries = build_queries_from_get_ids(client)

    deps = LoopDeps(
        client=client,
        api_tracker=api_tracker,
        fetch_tracker=FetchTracker(clock=clock),
        calendar=calendar,
        clock=clock,
        tz=tz
    )
    run(queries, deps)


if __name__ == "__main__":
    main()
