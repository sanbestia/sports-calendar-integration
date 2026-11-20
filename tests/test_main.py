import pytest
import json
import datetime
from unittest.mock import MagicMock
from config import MAIN_LOOP_SLEEP_MINUTES
from functions.calendar_methods import SyncResult
from main import load_queries, build_queries_from_get_ids, run_cycle, run, LoopDeps, CalendarConnection, current_version, install_stop_handler
import signal
import subprocess
from config import API_CALL_CEILING
from tests.fakes import FakeClock, FakeSession, ScriptedInput, make_client


FAKE_RAW_QUERIES = {
    "San Antonio Spurs": {
        "id": "3429",
        "sport": "basketball",
        "player_type": "team",
        "calendar": "CALENDAR_ID_BASKETBALL"
    },
    "T1": {
        "id": "364366",
        "sport": "esport",
        "player_type": "team",
        "calendar": "CALENDAR_ID_ESPORTS"
    }
}


# --- load_queries tests ---

FAKE_ENV = {
    "CALENDAR_ID_BASKETBALL": "real_basketball_calendar_id",
    "CALENDAR_ID_ESPORTS": "real_esports_calendar_id",
}


def write_queries(tmp_path, queries: dict) -> str:
    """Write a queries file and return its path."""
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(queries))
    return str(path)


def test_load_queries_resolves_env_variables(tmp_path):
    """Resolves calendar env keys to actual IDs from the given environment."""
    result = load_queries(write_queries(tmp_path, FAKE_RAW_QUERIES), env=FAKE_ENV)

    assert result["San Antonio Spurs"]["calendar"] == "real_basketball_calendar_id"
    assert result["T1"]["calendar"] == "real_esports_calendar_id"


def test_load_queries_raises_when_env_variable_missing(tmp_path):
    """Raises ValueError when a calendar env key is not set."""
    with pytest.raises(ValueError, match="CALENDAR_ID_BASKETBALL"):
        load_queries(write_queries(tmp_path, FAKE_RAW_QUERIES), env={})


def test_load_queries_preserves_other_fields(tmp_path):
    """Preserves id, sport and player_type fields after loading."""
    result = load_queries(write_queries(tmp_path, FAKE_RAW_QUERIES), env=FAKE_ENV)

    assert result["San Antonio Spurs"]["id"] == "3429"
    assert result["San Antonio Spurs"]["sport"] == "basketball"
    assert result["San Antonio Spurs"]["player_type"] == "team"


def test_load_queries_raises_when_required_field_missing(tmp_path):
    """Names the query and the missing field when an entry is incomplete."""
    incomplete = {"T1": {"id": "364366", "sport": "esport", "calendar": "CALENDAR_ID_ESPORTS"}}
    with pytest.raises(ValueError, match="'T1' is missing required fields: player_type"):
        load_queries(write_queries(tmp_path, incomplete), env=FAKE_ENV)


# --- build_queries_from_get_ids tests ---

SPURS_SEARCH = {"results": [{"entity": {"id": 3429, "name": "San Antonio Spurs", "gender": "M"}}]}
ALCARAZ_SEARCH = {"results": [{"entity": {"id": 275923, "name": "Carlos Alcaraz", "gender": "M", "playerTeamInfo": {"birthDateTimestamp": 0}}}]}
T1_SEARCH = {"results": [{"entity": {"id": 364366, "name": "T1"}}]}


def test_build_queries_returns_correct_structure():
    """Search, confirm, and the calendar ID answer become one query entry."""
    client = make_client(FakeSession(SPURS_SEARCH))
    ask = ScriptedInput("San Antonio Spurs", "basketball", "Y", "N", "real_basketball_calendar_id")

    result = build_queries_from_get_ids(client, ask=ask)

    assert result == {"San Antonio Spurs": {
        "id": "3429", "sport": "basketball", "player_type": "team", "calendar": "real_basketball_calendar_id"
    }}
    assert ask.prompts[-1] == "Enter calendar id for San Antonio Spurs: "


def test_build_queries_preserves_player_type_for_player():
    """A player hit keeps player_type 'player'."""
    client = make_client(FakeSession(ALCARAZ_SEARCH))
    ask = ScriptedInput("Carlos Alcaraz", "tennis", "Y", "N", "real_tennis_calendar_id")

    result = build_queries_from_get_ids(client, ask=ask)

    assert result["Carlos Alcaraz"]["player_type"] == "player"


def test_build_queries_handles_multiple_entries():
    """Two searches give two entries, each with its own calendar ID."""
    client = make_client(FakeSession(SPURS_SEARCH, T1_SEARCH))
    ask = ScriptedInput("San Antonio Spurs", "basketball", "Y", "Y", "T1", "esport", "Y", "N",
                        "real_basketball_calendar_id", "real_esports_calendar_id")

    result = build_queries_from_get_ids(client, ask=ask)

    assert {name: q["calendar"] for name, q in result.items()} == {
        "San Antonio Spurs": "real_basketball_calendar_id", "T1": "real_esports_calendar_id"
    }


# --- run_cycle / run tests ---

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


def make_deps(*, matches=None, fetch_fails=False, sync_side_effect=(1,), limit_reached=False, should_fetch=True, services=None, clock=None) -> LoopDeps:
    """Loop dependencies made of mocks and fakes; every collaborator is passed in, nothing is patched."""
    api_tracker = MagicMock(daily_limit=100)
    api_tracker.is_limit_reached.return_value = limit_reached
    api_tracker.status.return_value = "API calls today: 1/100"
    fetch_tracker = MagicMock()
    fetch_tracker.should_fetch.return_value = should_fetch
    fetch_tracker.hours_until_next_fetch.return_value = 3.0
    service_iter = iter(services if services is not None else [MagicMock()])
    sync_results = [SyncResult(event_count=r) if isinstance(r, int) else r for r in sync_side_effect]
    return LoopDeps(
        client=MagicMock(calls_made=0),
        api_tracker=api_tracker,
        fetch_tracker=fetch_tracker,
        history=MagicMock(),
        calendar=CalendarConnection(lambda: next(service_iter)),
        clock=clock or FakeClock(),
        tz=datetime.timezone.utc,
        fetch_matches=MagicMock(return_value=None if fetch_fails else [make_fake_match(EARLY)] if matches is None else matches),
        sync_events=MagicMock(side_effect=sync_results),
    )


def test_fetches_with_client_and_entity_fields():
    """Each due entity is fetched through the injected client with its own id, sport and type."""
    deps = make_deps()
    run_cycle(ALCARAZ_QUERIES, deps)

    deps.fetch_matches.assert_called_once_with(
        deps.client, team_id="275923", team_name="Carlos Alcaraz", sport="tennis", player_type="player", time_zone="UTC"
    )


def test_syncs_to_entity_calendar_with_current_service_and_clock():
    """Matches go to the entity's calendar, through the current service, with the loop's clock."""
    deps = make_deps()
    run_cycle(ALCARAZ_QUERIES, deps)

    kwargs = deps.sync_events.call_args.kwargs
    assert kwargs["calendar_id"] == "tennis_calendar_id"
    assert kwargs["team_id"] == "275923"
    assert kwargs["service"] is deps.calendar.service
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


def test_reconnects_calendar_service_on_broken_pipe():
    """A BrokenPipeError rebuilds the calendar service and retries the sync with the new one."""
    first, second = MagicMock(), MagicMock()
    deps = make_deps(sync_side_effect=(BrokenPipeError, 1), services=[first, second])
    run_cycle(ALCARAZ_QUERIES, deps)

    assert deps.calendar.service is second
    assert [c.kwargs["service"] for c in deps.sync_events.call_args_list] == [first, second]
    deps.fetch_tracker.record_fetch.assert_called_once_with("Carlos Alcaraz", EARLY, 1, player_type="player")


def test_records_failure_when_retry_after_reconnect_also_fails():
    """If the retry after reconnecting also fails, the calendar was not updated, so it is a failure."""
    deps = make_deps(sync_side_effect=(BrokenPipeError, OSError("still down")), services=[MagicMock(), MagicMock()])
    run_cycle(ALCARAZ_QUERIES, deps)

    assert deps.sync_events.call_count == 2
    deps.fetch_tracker.record_failure.assert_called_once_with("Carlos Alcaraz")
    deps.fetch_tracker.record_fetch.assert_not_called()


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


def test_run_waits_between_cycles():
    """run() repeats the cycle and waits MAIN_LOOP_SLEEP_MINUTES after each one."""
    deps = make_deps(sync_side_effect=(1, 1))
    start = deps.clock.now()

    run(ALCARAZ_QUERIES, deps, max_cycles=2)

    assert deps.fetch_matches.call_count == 2
    assert deps.clock.now() - start >= datetime.timedelta(minutes=2 * MAIN_LOOP_SLEEP_MINUTES)


# --- what the loop logs at INFO ---

def test_skipped_entities_are_not_logged_at_info(caplog):
    """'Skipping' lines are debug: they were ~40% of all journal lines, every 5 minutes."""
    deps = make_deps(should_fetch=False)

    assert run_cycle(ALCARAZ_QUERIES, deps) == 0
    assert "Skipping" not in caplog.text


def test_run_cycle_returns_how_many_entities_it_fetched():
    """The count lets run() decide whether the cycle is worth a status line."""
    deps = make_deps()
    assert run_cycle(TWO_ENTITY_QUERIES, deps) == 2


@pytest.mark.parametrize("should_fetch, logged", [(True, True), (False, False)])
def test_status_line_only_after_a_cycle_that_did_work(caplog, should_fetch, logged):
    """The 'API calls today' line is INFO after a cycle that fetched, and debug after a cycle that only skipped."""
    deps = make_deps(should_fetch=should_fetch)
    run(ALCARAZ_QUERIES, deps, max_cycles=1)

    assert ("API calls today: 1/100 · next cycle at" in caplog.text) is logged



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



# --- daily summary and version line ---

DAY_SUMMARY = {"fetches": 25, "with_matches": 5, "empty": 18, "failed": 2, "api_calls": 31, "created": 1, "updated": 3, "deleted": 0}


def make_deps_at_midnight(calls_that_day: int) -> LoopDeps:
    """Deps whose clock starts 3 minutes before midnight, so one 5-minute wait crosses into the next day."""
    deps = make_deps(should_fetch=False, clock=FakeClock(datetime.datetime(2026, 1, 15, 23, 57, tzinfo=datetime.timezone.utc)))
    deps.history.day_summary.return_value = DAY_SUMMARY
    deps.api_tracker.calls_on.return_value = calls_that_day
    return deps


def test_logs_a_summary_of_the_previous_day_after_midnight(caplog):
    """The first cycle of a new day logs yesterday's totals, read from api_calls.db and fetch_history."""
    deps = make_deps_at_midnight(calls_that_day=32)
    run(ALCARAZ_QUERIES, deps, max_cycles=2)

    deps.history.day_summary.assert_called_once_with(datetime.date(2026, 1, 15))
    assert ("Daily summary for 2026-01-15: 32 API calls; 25 fetches (5 with matches, 18 empty, 2 failed); "
            "events: 1 created, 3 updated, 0 deleted") in caplog.text


def test_daily_summary_warns_above_the_call_ceiling(caplog):
    """A day above the ceiling is a WARNING, so it shows in `journalctl -p warning`."""
    deps = make_deps_at_midnight(calls_that_day=API_CALL_CEILING + 5)
    run(ALCARAZ_QUERIES, deps, max_cycles=2)

    (record,) = [r for r in caplog.records if r.getMessage().startswith("Daily summary")]
    assert record.levelname == "WARNING"
    assert f"above the {API_CALL_CEILING}-call ceiling" in record.getMessage()


def test_no_summary_while_the_day_has_not_changed():
    deps = make_deps(should_fetch=False)
    run(ALCARAZ_QUERIES, deps, max_cycles=2)

    deps.history.day_summary.assert_not_called()


def test_a_failed_summary_is_a_warning_and_the_loop_goes_on(caplog):
    deps = make_deps_at_midnight(calls_that_day=10)
    deps.history.day_summary.side_effect = OSError("database is locked")
    run(ALCARAZ_QUERIES, deps, max_cycles=2)

    assert "Could not build the daily summary for 2026-01-15: database is locked" in caplog.text


def test_current_version_is_the_short_git_commit():
    def fake_git(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="ceb2b35\n", stderr="")
    assert current_version(run=fake_git) == "ceb2b35"


@pytest.mark.parametrize("failure", [FileNotFoundError("git"), subprocess.CalledProcessError(128, "git")])
def test_current_version_is_unknown_without_git(failure):
    """A copy without .git or without the git command still starts."""
    def failing_git(*args, **kwargs):
        raise failure
    assert current_version(run=failing_git) == "unknown"


# --- clean stop on SIGTERM ---


def test_installs_a_sigterm_handler():
    """systemd stops the service with SIGTERM on every deploy, reboot and restart."""
    registered = {}
    install_stop_handler(register=lambda signum, handler: registered.setdefault(signum, handler))

    assert list(registered) == [signal.SIGTERM]


def test_sigterm_handler_exits_with_code_0(caplog):
    """Exit code 0 means systemd logs a clean stop, not 'Failed with result exit-code' (status 143)."""
    registered = {}
    install_stop_handler(register=lambda signum, handler: registered.setdefault(signum, handler))

    with pytest.raises(SystemExit) as stop:
        registered[signal.SIGTERM](signal.SIGTERM, None)

    assert stop.value.code == 0
    assert "Stopping: received SIGTERM" in caplog.text
