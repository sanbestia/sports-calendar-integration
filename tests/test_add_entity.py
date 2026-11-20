import json
from unittest.mock import MagicMock

import pytest

from functions.add_entity import add_entities
from functions.queries_file import read_queries
from tests.fakes import FakeSession, ScriptedInput, make_client

LAKERS_SEARCH = {"results": [{"entity": {"id": 3427, "name": "Los Angeles Lakers", "gender": "M"}}]}
SPURS_SEARCH = {"results": [{"entity": {"id": 3429, "name": "San Antonio Spurs", "gender": "M"}}]}
EXISTING = {
    "San Antonio Spurs": {"id": "3429", "sport": "basketball", "player_type": "team", "calendar": "CALENDAR_ID_BASKETBALL"},
    "T1": {"id": "364366", "sport": "esport", "player_type": "team", "calendar": "CALENDAR_ID_ESPORTS"},
}
ENV = {"CALENDAR_ID_BASKETBALL": "basketball-id", "CALENDAR_ID_ESPORTS": "esports-id", "CALENDAR_ID_NBA": "nba-id"}
SEARCH_LAKERS = ("Los Angeles Lakers", "basketball", "Y", "N")  # get_ids: name, sport, confirm the hit, no other search


@pytest.fixture
def queries_path(tmp_path):
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(EXISTING, indent=4))
    return str(path)


def run_flow(queries_path, *answers, search=LAKERS_SEARCH, push=None):
    """Run add_entities with a fake search reply and scripted answers; return (names added, push mock)."""
    push = push if push is not None else MagicMock()
    added = add_entities(make_client(FakeSession(search)), queries_path, ENV, ask=ScriptedInput(*answers), push=push)
    return added, push


def test_adds_the_entity_with_its_api_name_and_a_known_calendar(queries_path):
    """Empty name answer keeps the API name; '1' picks the first calendar key already in the file."""
    added, push = run_flow(queries_path, *SEARCH_LAKERS, "", "1", "N")

    assert added == ["Los Angeles Lakers"]
    assert read_queries(queries_path)["Los Angeles Lakers"] == {
        "id": "3427", "sport": "basketball", "player_type": "team", "calendar": "CALENDAR_ID_BASKETBALL"
    }
    push.assert_not_called()


def test_a_typed_name_replaces_the_api_name(queries_path):
    run_flow(queries_path, *SEARCH_LAKERS, "Lakers", "1", "N")

    assert "Lakers" in read_queries(queries_path)


def test_another_env_key_can_be_typed(queries_path):
    """The last menu number lets you name a key that no entry uses yet."""
    run_flow(queries_path, *SEARCH_LAKERS, "", "3", "CALENDAR_ID_NBA", "N")

    assert read_queries(queries_path)["Los Angeles Lakers"]["calendar"] == "CALENDAR_ID_NBA"


def test_a_menu_answer_out_of_range_is_asked_again(queries_path):
    run_flow(queries_path, *SEARCH_LAKERS, "", "9", "x", "2", "N")

    assert read_queries(queries_path)["Los Angeles Lakers"]["calendar"] == "CALENDAR_ID_ESPORTS"


def test_pushes_when_you_say_yes(queries_path):
    _, push = run_flow(queries_path, *SEARCH_LAKERS, "", "1", "Y")

    push.assert_called_once_with(queries_path)


def test_an_entity_already_tracked_is_not_added_and_nothing_is_offered(queries_path, caplog):
    """A refused entry is logged; with nothing added there is no push question (ScriptedInput would fail on it)."""
    added, push = run_flow(queries_path, "San Antonio Spurs", "basketball", "Y", "N", "Spurs", "1", search=SPURS_SEARCH)

    assert added == []
    assert "Not added: basketball id 3429 is already tracked as 'San Antonio Spurs'" in caplog.text
    assert read_queries(queries_path) == EXISTING
    push.assert_not_called()


def test_nothing_selected_adds_nothing(queries_path):
    added, push = run_flow(queries_path, "Los Angeles Lakers", "basketball", "N", "N")

    assert added == []
    assert read_queries(queries_path) == EXISTING
