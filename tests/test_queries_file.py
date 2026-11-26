import json

import pytest

from sports_calendar.queries_file import Query, QueryError, add_query, calendar_keys, load_queries, read_queries

EXISTING = {
    "San Antonio Spurs": {"id": "3429", "sport": "basketball", "player_type": "team", "calendar": "CALENDAR_ID_BASKETBALL"},
    "T1": {"id": "364366", "sport": "esport", "player_type": "team", "calendar": "CALENDAR_ID_ESPORTS"},
}
ENV = {"CALENDAR_ID_BASKETBALL": "basketball-calendar-id", "CALENDAR_ID_ESPORTS": "esports-calendar-id"}
LAKERS = {"id": "3427", "sport": "basketball", "player_type": "team"}


@pytest.fixture
def queries_path(tmp_path):
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(EXISTING, indent=4))
    return str(path)


def test_read_queries_returns_the_raw_entries(queries_path):
    """Calendar values stay env keys; resolving them is load_queries' job."""
    assert read_queries(queries_path) == EXISTING


def test_read_queries_of_a_missing_file_is_empty(tmp_path):
    assert read_queries(str(tmp_path / "queries.json")) == {}


def test_calendar_keys_lists_each_key_once_sorted():
    assert calendar_keys({**EXISTING, "Fever": {**LAKERS, "calendar": "CALENDAR_ID_BASKETBALL"}}) == [
        "CALENDAR_ID_BASKETBALL", "CALENDAR_ID_ESPORTS"
    ]


def test_add_query_appends_the_entry_and_keeps_the_others(queries_path):
    add_query(queries_path, "Los Angeles Lakers", LAKERS, "CALENDAR_ID_BASKETBALL", env=ENV)

    assert read_queries(queries_path) == {
        **EXISTING, "Los Angeles Lakers": {**LAKERS, "calendar": "CALENDAR_ID_BASKETBALL"}
    }


def test_add_query_writes_the_file_in_its_usual_format(queries_path):
    """4-space indent and non-ASCII names as they are, so the diff of a hand-kept file stays small."""
    add_query(queries_path, "Atlético de Madrid", {"id": "2836", "sport": "football", "player_type": "team"},
              "CALENDAR_ID_BASKETBALL", env=ENV)

    text = open(queries_path, encoding="utf-8").read()
    assert '    "Atlético de Madrid": {' in text


def test_add_query_creates_the_file_when_there_is_none(tmp_path):
    path = str(tmp_path / "queries.json")
    add_query(path, "Los Angeles Lakers", LAKERS, "CALENDAR_ID_BASKETBALL", env=ENV)

    assert list(read_queries(path)) == ["Los Angeles Lakers"]


def test_add_query_refuses_a_calendar_key_missing_from_env(queries_path):
    """The service would refuse to start (load_queries) with an unknown key, so refuse it here first."""
    with pytest.raises(QueryError, match="CALENDAR_ID_TENNIS is not set"):
        add_query(queries_path, "Los Angeles Lakers", LAKERS, "CALENDAR_ID_TENNIS", env=ENV)

    assert read_queries(queries_path) == EXISTING


def test_add_query_refuses_an_entity_that_is_already_tracked(queries_path):
    """The same id and sport under another name would fetch and write every match twice."""
    with pytest.raises(QueryError, match="already tracked as 'San Antonio Spurs'"):
        add_query(queries_path, "Spurs", {"id": "3429", "sport": "basketball", "player_type": "team"},
                  "CALENDAR_ID_BASKETBALL", env=ENV)


def test_add_query_refuses_a_name_that_is_already_used(queries_path):
    with pytest.raises(QueryError, match="'T1' is already in"):
        add_query(queries_path, "T1", LAKERS, "CALENDAR_ID_BASKETBALL", env=ENV)


# --- load_queries: the entries for a run ---

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
    result = load_queries(write_queries(tmp_path, FAKE_RAW_QUERIES), FAKE_ENV)

    assert result["San Antonio Spurs"].calendar_id == "real_basketball_calendar_id"
    assert result["T1"].calendar_id == "real_esports_calendar_id"


def test_load_queries_raises_when_env_variable_missing(tmp_path):
    """Raises ValueError when a calendar env key is not set."""
    with pytest.raises(ValueError, match="CALENDAR_ID_BASKETBALL"):
        load_queries(write_queries(tmp_path, FAKE_RAW_QUERIES), {})


def test_load_queries_preserves_other_fields(tmp_path):
    """Preserves id, sport and player_type fields after loading."""
    result = load_queries(write_queries(tmp_path, FAKE_RAW_QUERIES), FAKE_ENV)

    assert result["San Antonio Spurs"] == Query(id="3429", sport="basketball", player_type="team",
                                                calendar_id="real_basketball_calendar_id")


def test_load_queries_raises_when_required_field_missing(tmp_path):
    """Names the query and the missing field when an entry is incomplete."""
    incomplete = {"T1": {"id": "364366", "sport": "esport", "calendar": "CALENDAR_ID_ESPORTS"}}
    with pytest.raises(ValueError, match="'T1' is invalid: player_type: Field required"):
        load_queries(write_queries(tmp_path, incomplete), FAKE_ENV)


@pytest.mark.parametrize("field, value", [
    ("id", 3429),  # an int never equals the string ids that events store, so no orphan would ever be deleted
    ("id", "spurs"),
    ("sport", ""),
    ("player_type", "club"),
    ("calendar", ""),
])
def test_load_queries_refuses_a_bad_value_and_names_its_entry_and_field(tmp_path, field, value):
    bad = {"San Antonio Spurs": {**FAKE_RAW_QUERIES["San Antonio Spurs"], field: value}}

    with pytest.raises(ValueError, match=rf"'San Antonio Spurs'.*{field}"):
        load_queries(write_queries(tmp_path, bad), FAKE_ENV)


def test_load_queries_refuses_a_file_with_no_entries(tmp_path):
    """A run with nothing to track would pass as healthy, so an empty file is a missing input, not an empty schedule."""
    with pytest.raises(ValueError, match="no entries"):
        load_queries(write_queries(tmp_path, {}), FAKE_ENV)


def test_load_queries_raises_when_the_file_is_missing(tmp_path):
    """Unlike read_queries (add_entity starts a new file), a run must not pass as fine with nothing to track."""
    with pytest.raises(FileNotFoundError):
        load_queries(str(tmp_path / "queries.json"), FAKE_ENV)
