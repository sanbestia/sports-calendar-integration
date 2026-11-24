"""One run of the service: fetch and sync each entity that is due, record what happened, and report the run."""
import datetime
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sports_calendar.allsports.client import AllSportsClient
from sports_calendar.allsports.matches import get_next_matches
from sports_calendar.clock import Clock
from sports_calendar.google_calendar.auth import TOKEN_ERRORS
from sports_calendar.google_calendar.sync import SyncResult, update_events
from sports_calendar.heartbeat import Heartbeat
from sports_calendar.storage.api_calls import APICallTracker
from sports_calendar.storage.fetch_history import FetchHistory
from sports_calendar.storage.fetch_tracker import FetchTracker

logger = logging.getLogger(__name__)


@dataclass
class RunDeps:
    """Everything one run talks to. _build_and_run() builds the real ones; tests pass fakes."""
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
    """What one entity's fetch and sync ended with; it updates the fetch log and becomes one fetch_history row."""
    outcome: str  # 'matches', 'empty' or 'failed'
    matches: int = 0
    sync: SyncResult | None = None
    earliest: datetime.datetime | None = None  # the first match's start time, for the next fetch time
    error: str | None = None


def _fetch(deps: RunDeps, name: str, entry: dict) -> list | None:
    """Fetch one entity's upcoming matches; None when the fetch failed (the fetch logs why)."""
    return deps.fetch_matches(
        deps.client,
        team_id=entry["id"],
        team_name=name,
        sport=entry["sport"],
        player_type=entry["player_type"],
        time_zone=str(deps.tz)
    )


def _sync(deps: RunDeps, entry: dict, matches: list) -> SyncResult:
    """Write one entity's matches to its calendar and return what changed."""
    return deps.sync_events(
        service=deps.calendar_service,
        calendar_id=entry["calendar"],
        game_list=matches,
        time_zone=str(deps.tz),
        team_id=entry["id"],
        clock=deps.clock
    )


def _fetch_and_sync(name: str, entry: dict, deps: RunDeps) -> FetchOutcome:
    """Fetch one entity's matches and write them to its calendar. A failed fetch is a failure, not an empty schedule."""
    matches = _fetch(deps, name, entry)
    if matches is None:
        return FetchOutcome("failed", error="fetch failed (see the log)")
    if not matches:
        return FetchOutcome("empty")
    return FetchOutcome("matches", matches=len(matches), sync=_sync(deps, entry, matches),
                        earliest=min(match.start_time for match in matches))


def _record_fetch(deps: RunDeps, name: str, entry: dict, result: FetchOutcome) -> None:
    """Record the result in the fetch log, which sets when the entity is fetched next (sooner after a failure)."""
    if result.outcome == "failed":
        deps.fetch_tracker.record_failure(name)
        return
    event_count = result.sync.event_count if result.sync else 0
    deps.fetch_tracker.record_fetch(name, result.earliest, event_count, player_type=entry["player_type"])


def _fetch_sync_and_record(name: str, entry: dict, deps: RunDeps) -> FetchOutcome:
    """Fetch, sync and record one entity. An error other than a token error is logged and becomes a failure."""
    try:
        result = _fetch_and_sync(name, entry, deps)
        _record_fetch(deps, name, entry, result)
        return result
    except TOKEN_ERRORS:
        raise  # no entity can sync without a token: main() reports it, instead of one entity's backoff
    except Exception as e:  # one entity's failure must not end the run: the next run would refetch it and crash again
        logger.exception(f"Fetch or calendar sync failed for '{name}'")
        deps.fetch_tracker.record_failure(name)
        return FetchOutcome("failed", error=f"{type(e).__name__}: {e}")


def _record_history(deps: RunDeps, name: str, entry: dict, result: FetchOutcome, api_calls: int) -> None:
    """Add the fetch to fetch_history. A failed write is only a warning: it must not turn a good fetch into a failure."""
    sync = result.sync or SyncResult(event_count=0)
    try:
        deps.history.record(name, entry["player_type"], result.outcome, api_calls=api_calls, matches=result.matches,
                            created=sync.created, updated=sync.updated, deleted=sync.deleted, error=result.error)
    except Exception as e:
        logger.warning(f"Could not write fetch history for '{name}': {e}")


def _process_entity(name: str, entry: dict, deps: RunDeps) -> None:
    """Fetch, sync and record one entity, then add it to fetch_history with the API calls it used."""
    calls_before = deps.client.calls_made
    result = _fetch_sync_and_record(name, entry, deps)
    _record_history(deps, name, entry, result, api_calls=deps.client.calls_made - calls_before)


def run_cycle(queries: dict, deps: RunDeps) -> int:
    """Fetch and sync every entity that is due, once; stop early when the daily API limit is reached.

    Returns how many entities were fetched (0 when every entity was skipped).
    """
    fetched = 0
    for name, entry in queries.items():

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

        _process_entity(name, entry, deps)
        fetched += 1

    return fetched


def report_run(deps: RunDeps, fetched: int, version: str) -> None:
    """Log the API call count and the version (at INFO only after a run that fetched), then ping the heartbeat."""
    level = logging.INFO if fetched else logging.DEBUG  # a run that only skipped is not worth a line: there are 96 runs a day
    logger.log(level, f"{deps.api_tracker.status()} · version {version}")
    deps.heartbeat.ping()


def run_once(queries: dict, deps: RunDeps, version: str) -> None:
    """Fetch and sync every entity that is due, then report the run. The systemd timer starts a run every 15 minutes."""
    report_run(deps, run_cycle(queries, deps), version)
