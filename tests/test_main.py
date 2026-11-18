import pytest
import json
import datetime
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from tests.fakes import ScriptedInput
from main import main, load_queries, build_queries_from_get_ids


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

def test_build_queries_returns_correct_structure():
    """Returns correctly structured dict from get_ids output."""
    fake_id_dict = {
        "3429": {"name": "San Antonio Spurs", "sport": "basketball", "player_type": "team"}
    }

    with patch("main.get_ids", return_value=fake_id_dict):
        result = build_queries_from_get_ids(ask=ScriptedInput("real_basketball_calendar_id"))

    assert "San Antonio Spurs" in result
    assert result["San Antonio Spurs"]["id"] == "3429"
    assert result["San Antonio Spurs"]["sport"] == "basketball"
    assert result["San Antonio Spurs"]["player_type"] == "team"
    assert result["San Antonio Spurs"]["calendar"] == "real_basketball_calendar_id"


def test_build_queries_preserves_player_type_for_player():
    """Preserves player_type 'player' when a player entry is built."""
    fake_id_dict = {
        "275923": {"name": "Carlos Alcaraz", "sport": "tennis", "player_type": "player"}
    }

    with patch("main.get_ids", return_value=fake_id_dict):
        result = build_queries_from_get_ids(ask=ScriptedInput("real_tennis_calendar_id"))

    assert result["Carlos Alcaraz"]["player_type"] == "player"


def test_build_queries_handles_multiple_entries():
    """Correctly builds dict with multiple entries."""
    fake_id_dict = {
        "3429":   {"name": "San Antonio Spurs", "sport": "basketball", "player_type": "team"},
        "364366": {"name": "T1",                "sport": "esport",     "player_type": "team"}
    }

    with patch("main.get_ids", return_value=fake_id_dict):
        result = build_queries_from_get_ids(ask=ScriptedInput("real_basketball_calendar_id", "real_esports_calendar_id"))

    assert len(result) == 2
    assert "San Antonio Spurs" in result
    assert "T1" in result


# --- main() loop tests ---

ALCARAZ_QUERIES = {
    "Carlos Alcaraz": {
        "id": "275923",
        "sport": "tennis",
        "player_type": "player",
        "calendar": "tennis_calendar_id"
    }
}


def make_fake_match(start_time: datetime.datetime) -> MagicMock:
    fake_match = MagicMock()
    fake_match.start_time = start_time
    return fake_match


def run_main_one_cycle(queries=ALCARAZ_QUERIES, matches=None, update_side_effect=(1,),
                       limit_reached=False, should_fetch=True, services=None) -> SimpleNamespace:
    """Run main() for exactly one loop cycle with every dependency mocked; wait() ends it with SystemExit.

    Returns the mocks so each test can assert on them.
    """
    if matches is None:
        matches = [make_fake_match(datetime.datetime(2026, 3, 22, 15, 0, 0))]
    fake_args = MagicMock()
    fake_args.queries = "queries.json"

    api_tracker = MagicMock()
    api_tracker.is_limit_reached.return_value = limit_reached
    api_tracker.status.return_value = "API calls today: 1/100"
    fetch_tracker = MagicMock()
    fetch_tracker.should_fetch.return_value = should_fetch
    fetch_tracker.hours_until_next_fetch.return_value = 3.0

    with patch("argparse.ArgumentParser.parse_args", return_value=fake_args), \
         patch("main.get_localzone", return_value=datetime.timezone.utc), \
         patch("main.check_calendar_tokens", return_value=MagicMock()) as check_creds, \
         patch("main.build", side_effect=services) as build, \
         patch("main.APICallTracker", return_value=api_tracker), \
         patch("main.FetchTracker", return_value=fetch_tracker), \
         patch("main.load_queries", return_value=queries), \
         patch("main.get_next_matches", return_value=matches) as get_matches, \
         patch("main.update_events", side_effect=list(update_side_effect)) as update, \
         patch("main.wait", side_effect=SystemExit):
        with pytest.raises(SystemExit):
            main()

    return SimpleNamespace(check_creds=check_creds, build=build, get_matches=get_matches, update=update,
                           api_tracker=api_tracker, fetch_tracker=fetch_tracker)


def test_records_earliest_match_and_count():
    """After a sync, the fetch is recorded with the earliest start time and the count from update_events."""
    early = datetime.datetime(2026, 3, 22, 15, 0, 0)
    late = datetime.datetime(2026, 3, 29, 15, 0, 0)
    mocks = run_main_one_cycle(matches=[make_fake_match(late), make_fake_match(early)], update_side_effect=(2,))

    mocks.fetch_tracker.record_fetch.assert_called_once_with("Carlos Alcaraz", early, 2)


def test_skips_calendar_update_when_no_matches():
    """With no matches, the calendar is not touched and the fetch is recorded as empty."""
    mocks = run_main_one_cycle(matches=[])

    mocks.update.assert_not_called()
    mocks.fetch_tracker.record_fetch.assert_called_once_with("Carlos Alcaraz", None, 0)


def test_skips_entity_that_is_not_due():
    """An entity that is not due is not fetched and not recorded."""
    mocks = run_main_one_cycle(should_fetch=False)

    mocks.get_matches.assert_not_called()
    mocks.fetch_tracker.record_fetch.assert_not_called()


def test_stops_fetching_when_daily_limit_reached():
    """When the daily API limit is reached, no entity is fetched for the rest of the cycle."""
    queries = {**ALCARAZ_QUERIES, "T1": {"id": "364366", "sport": "esport", "player_type": "team", "calendar": "esports_calendar_id"}}
    mocks = run_main_one_cycle(queries=queries, limit_reached=True)

    mocks.get_matches.assert_not_called()
    mocks.fetch_tracker.should_fetch.assert_not_called()


# --- BrokenPipeError reconnection tests ---

def test_reconnects_calendar_service_on_broken_pipe():
    """Rebuilds the calendar service and retries update_events on BrokenPipeError."""
    service_after_reconnect = MagicMock()
    mocks = run_main_one_cycle(update_side_effect=(BrokenPipeError, 1), services=[MagicMock(), service_after_reconnect])

    # Service rebuilt and credentials checked: once at startup + once after BrokenPipeError
    assert mocks.build.call_count == 2
    assert mocks.check_creds.call_count == 2
    # update_events retried with the freshly built service
    assert mocks.update.call_count == 2
    assert mocks.update.call_args_list[1].kwargs["service"] is service_after_reconnect


def test_continues_loop_when_retry_also_fails():
    """Logs the error and continues the loop when the reconnect retry also fails."""
    mocks = run_main_one_cycle(update_side_effect=(BrokenPipeError, OSError("still down")))

    # Both calls were made; the second failure was absorbed and the loop reached wait()
    assert mocks.update.call_count == 2
