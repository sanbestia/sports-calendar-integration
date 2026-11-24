import pytest
import datetime
from unittest.mock import MagicMock
from sports_calendar.google_calendar.sync import SyncResult
from sports_calendar.run import run_cycle, run_once, RunDeps
from google.auth.exceptions import RefreshError
from sports_calendar.google_calendar.auth import LoginRequired
from sports_calendar.heartbeat import Heartbeat
from sports_calendar.storage.fetch_tracker import FetchTracker
from tests.fakes import FakeClock, FakePost


# --- run_cycle / run_once tests ---

ALCARAZ_QUERIES = {
    "Carlos Alcaraz": {
        "id": "275923",
        "sport": "tennis",
        "player_type": "player",
        "calendar": "tennis_calendar_id"
    }
}
EARLY = datetime.datetime(2026, 3, 22, 15, 0, 0)
LATE = datetime.datetime(2026, 3, 29, 15, 0, 0)


def make_fake_match(start_time: datetime.datetime) -> MagicMock:
    fake_match = MagicMock()
    fake_match.start_time = start_time
    return fake_match


def make_deps(*, matches=None, fetch_fails=False, sync_side_effect=(1,), limit_reached=False, should_fetch=True) -> RunDeps:
    """Run dependencies made of mocks and fakes; every collaborator is passed in, nothing is patched."""
    api_tracker = MagicMock(daily_limit=100)
    api_tracker.is_limit_reached.return_value = limit_reached
    api_tracker.status.return_value = "API calls today: 1/100"
    fetch_tracker = MagicMock()
    fetch_tracker.should_fetch.return_value = should_fetch
    fetch_tracker.hours_until_next_fetch.return_value = 3.0
    fetch_tracker.fail_streak.return_value = 0
    sync_results = [SyncResult(event_count=r) if isinstance(r, int) else r for r in sync_side_effect]
    return RunDeps(
        client=MagicMock(calls_made=0),
        api_tracker=api_tracker,
        fetch_tracker=fetch_tracker,
        history=MagicMock(),
        calendar_service=MagicMock(),
        clock=FakeClock(),
        tz=datetime.timezone.utc,
        fetch_matches=MagicMock(return_value=None if fetch_fails else [make_fake_match(EARLY)] if matches is None else matches),
        sync_events=MagicMock(side_effect=sync_results),
    )


def test_fetches_with_client_and_entity_fields():
    """Each due entity is fetched through the injected client with its own id, sport and type."""
    deps = make_deps()
    run_cycle(ALCARAZ_QUERIES, deps)

    deps.fetch_matches.assert_called_once_with(
        deps.client, entity_id="275923", entity_name="Carlos Alcaraz", sport="tennis", player_type="player", time_zone="UTC"
    )


def test_syncs_to_entity_calendar_with_the_runs_service_and_clock():
    """Matches go to the entity's calendar, through the run's calendar service, with the run's clock."""
    deps = make_deps()
    run_cycle(ALCARAZ_QUERIES, deps)

    kwargs = deps.sync_events.call_args.kwargs
    assert kwargs["calendar_id"] == "tennis_calendar_id"
    assert kwargs["entity_id"] == "275923"
    assert kwargs["service"] is deps.calendar_service
    assert kwargs["clock"] is deps.clock


def test_records_earliest_match_and_count():
    """After a sync, the fetch is recorded with the earliest start time and the count from the sync."""
    deps = make_deps(matches=[make_fake_match(LATE), make_fake_match(EARLY)], sync_side_effect=(2,))
    run_cycle(ALCARAZ_QUERIES, deps)

    deps.fetch_tracker.record_fetch.assert_called_once_with("Carlos Alcaraz", EARLY, 2, player_type="player")


def test_skips_calendar_update_when_no_matches():
    """With no matches, the calendar is not touched and the fetch is recorded as empty."""
    deps = make_deps(matches=[])
    run_cycle(ALCARAZ_QUERIES, deps)

    deps.sync_events.assert_not_called()
    deps.fetch_tracker.record_fetch.assert_called_once_with("Carlos Alcaraz", None, 0, player_type="player")


def test_skips_entity_that_is_not_due():
    """An entity that is not due is not fetched and not recorded."""
    deps = make_deps(should_fetch=False)
    run_cycle(ALCARAZ_QUERIES, deps)

    deps.fetch_matches.assert_not_called()
    deps.fetch_tracker.record_fetch.assert_not_called()


def test_stops_fetching_when_daily_limit_reached():
    """When the daily API limit is reached, no entity is fetched for the rest of the cycle."""
    queries = {**ALCARAZ_QUERIES, "T1": {"id": "364366", "sport": "esport", "player_type": "team", "calendar": "esports_calendar_id"}}
    deps = make_deps(limit_reached=True)
    run_cycle(queries, deps)

    deps.fetch_matches.assert_not_called()
    deps.fetch_tracker.should_fetch.assert_not_called()


TWO_ENTITY_QUERIES = {
    **ALCARAZ_QUERIES,
    "T1": {"id": "364366", "sport": "esport", "player_type": "team", "calendar": "esports_calendar_id"},
}


def test_records_failure_when_fetch_fails():
    """A failed fetch (None) is recorded as a failure, not as 'no matches', and the calendar is left alone."""
    deps = make_deps(fetch_fails=True)
    run_cycle(ALCARAZ_QUERIES, deps)

    deps.fetch_tracker.record_failure.assert_called_once_with("Carlos Alcaraz")
    deps.fetch_tracker.record_fetch.assert_not_called()
    deps.sync_events.assert_not_called()


def test_any_sync_error_is_recorded_and_the_cycle_continues():
    """An unexpected calendar error (e.g. a sport missing from ICONS) does not stop the service.

    Before, it crashed the process; systemd restarted it, and the same entity was fetched and crashed again.
    """
    deps = make_deps(sync_side_effect=(KeyError("handball"), 1))
    run_cycle(TWO_ENTITY_QUERIES, deps)

    deps.fetch_tracker.record_failure.assert_called_once_with("Carlos Alcaraz")
    deps.fetch_tracker.record_fetch.assert_called_once_with("T1", EARLY, 1, player_type="team")


def test_any_fetch_error_is_recorded_and_the_cycle_continues():
    """An unexpected error while fetching is also recorded as a failure, and the next entity still runs."""
    deps = make_deps()
    deps.fetch_matches.side_effect = [ValueError("bad data"), [make_fake_match(EARLY)]]
    run_cycle(TWO_ENTITY_QUERIES, deps)

    deps.fetch_tracker.record_failure.assert_called_once_with("Carlos Alcaraz")
    deps.fetch_tracker.record_fetch.assert_called_once_with("T1", EARLY, 1, player_type="team")


# --- what a run logs at INFO ---

def test_skipped_entities_are_not_logged_at_info(caplog):
    """'Skipping' lines are debug: they were ~40% of all journal lines, and a run starts every 15 minutes."""
    deps = make_deps(should_fetch=False)

    assert run_cycle(ALCARAZ_QUERIES, deps) == 0
    assert "Skipping" not in caplog.text


def test_run_cycle_returns_how_many_entities_it_fetched():
    """The count lets run_once decide whether the run is worth a status line."""
    deps = make_deps()
    assert run_cycle(TWO_ENTITY_QUERIES, deps) == 2


@pytest.mark.parametrize("should_fetch, logged", [(True, True), (False, False)])
def test_status_line_only_after_a_run_that_did_work(caplog, should_fetch, logged):
    """The 'API calls today' line, with the version, is INFO after a run that fetched, and debug after a run that only skipped."""
    deps = make_deps(should_fetch=should_fetch)
    run_once(ALCARAZ_QUERIES, deps, version="2d89ca8")

    assert ("API calls today: 1/100 · version 2d89ca8" in caplog.text) is logged



# --- fetch history ---

def history_row(deps) -> dict:
    """The single history.record call, as {field: value} (positional fields named)."""
    (call,) = deps.history.record.call_args_list
    entity, player_type, outcome = call.args
    return {"entity": entity, "player_type": player_type, "outcome": outcome, **call.kwargs}


def test_history_records_a_fetch_with_matches_and_its_changes():
    """A fetch that found matches records their count and the calendar changes."""
    deps = make_deps(matches=[make_fake_match(LATE), make_fake_match(EARLY)],
                     sync_side_effect=(SyncResult(event_count=2, created=1, updated=1, deleted=0),))
    run_cycle(ALCARAZ_QUERIES, deps)

    assert history_row(deps) == {"entity": "Carlos Alcaraz", "player_type": "player", "outcome": "matches", "api_calls": 0,
                                 "matches": 2, "created": 1, "updated": 1, "deleted": 0, "error": None}


def test_history_records_the_api_calls_the_fetch_used():
    """api_calls is the client's call count after the fetch minus before it."""
    deps = make_deps()

    def fetch_using_three_calls(client, **kwargs):
        client.calls_made += 3
        return [make_fake_match(EARLY)]
    deps.fetch_matches = MagicMock(side_effect=fetch_using_three_calls)
    run_cycle(ALCARAZ_QUERIES, deps)

    assert history_row(deps)["api_calls"] == 3


def test_history_records_an_empty_schedule():
    deps = make_deps(matches=[])
    run_cycle(ALCARAZ_QUERIES, deps)

    assert history_row(deps)["outcome"] == "empty"


def test_history_records_a_failed_fetch():
    deps = make_deps(fetch_fails=True)
    run_cycle(ALCARAZ_QUERIES, deps)

    row = history_row(deps)
    assert (row["outcome"], row["error"]) == ("failed", "fetch failed (see the log)")


def test_history_records_an_unexpected_error_with_its_type():
    deps = make_deps(sync_side_effect=(KeyError("handball"),))
    run_cycle(ALCARAZ_QUERIES, deps)

    row = history_row(deps)
    assert (row["outcome"], row["error"]) == ("failed", "KeyError: 'handball'")


def test_a_failed_history_write_does_not_turn_a_good_fetch_into_a_failure(caplog):
    """The history is a record, not part of the sync: a write error is a warning, and the fetch stays a success."""
    deps = make_deps(sync_side_effect=(1, 1))
    deps.history.record.side_effect = OSError("disk full")
    run_cycle(TWO_ENTITY_QUERIES, deps)

    assert deps.fetch_tracker.record_fetch.call_count == 2
    deps.fetch_tracker.record_failure.assert_not_called()
    assert "Could not write fetch history for 'Carlos Alcaraz': disk full" in caplog.text



# --- heartbeat and token failures ---

PING_URL = "https://hc-ping.com/0f1e2d3c-secret-uuid"


def test_a_run_pings_the_heartbeat_once_at_its_end():
    """healthchecks.io alerts when the pings stop: a hang, runs that fail again and again, or a dead VM."""
    post = FakePost()
    deps = make_deps()
    deps.heartbeat = Heartbeat(PING_URL, post=post)

    run_once(ALCARAZ_QUERIES, deps, version="2d89ca8")

    assert [c["url"] for c in post.calls] == [PING_URL]


def test_the_heartbeat_fails_while_an_entity_has_failed_three_times_in_a_row():
    """A run that does not crash is not always a good run: with a revoked API key every fetch fails. From the third
    failure in a row (about 3 hours, with the retry waits), each run sends a fail ping, also a run with nothing due,
    so the check stays down until that entity's fetch works again. The ping names no entity: healthchecks.io is an
    outside service."""
    post = FakePost()
    deps = make_deps(fetch_fails=True)
    deps.fetch_tracker = FetchTracker(":memory:", clock=deps.clock)
    deps.heartbeat = Heartbeat(PING_URL, post=post)

    for wait_hours in (0, 1, 2):  # the retry waits after the first and the second failure
        deps.clock.advance(hours=wait_hours)
        run_once(ALCARAZ_QUERIES, deps, version="2d89ca8")
    deps.clock.advance(minutes=15)  # the next run: the entity is not due yet
    run_once(ALCARAZ_QUERIES, deps, version="2d89ca8")

    assert [c["url"] for c in post.calls] == [PING_URL, PING_URL, f"{PING_URL}/fail", f"{PING_URL}/fail"]
    assert b"Carlos Alcaraz" not in post.calls[-1]["data"]

    deps.fetch_matches.return_value = [make_fake_match(datetime.datetime(2026, 2, 1, 15, 0, tzinfo=datetime.timezone.utc))]
    deps.clock.advance(hours=4)
    run_once(ALCARAZ_QUERIES, deps, version="2d89ca8")
    assert post.calls[-1]["url"] == PING_URL


def test_a_run_stopped_by_a_token_error_sends_no_ping():
    """Only a run that ends pings; main() sends the fail ping for a token error (stop_for_login)."""
    post = FakePost()
    deps = make_deps(sync_side_effect=(RefreshError("invalid_grant"),))
    deps.heartbeat = Heartbeat(PING_URL, post=post)

    with pytest.raises(RefreshError):
        run_once(ALCARAZ_QUERIES, deps, version="2d89ca8")

    assert post.calls == []


@pytest.mark.parametrize("error", [RefreshError("invalid_grant"), LoginRequired("no token file (token.json)")])
def test_a_token_error_stops_the_cycle_and_is_not_an_entity_failure(error):
    """No entity can sync without a token. A RefreshError comes from a Calendar call whose token refresh fails,
    LoginRequired from a reconnect. Both go up to main(), which reports them, instead of one entity's backoff."""
    deps = make_deps(sync_side_effect=(error,))

    with pytest.raises(type(error)):
        run_cycle(ALCARAZ_QUERIES, deps)

    deps.fetch_tracker.record_failure.assert_not_called()
