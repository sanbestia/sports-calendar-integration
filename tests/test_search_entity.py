import json
import pytest
import requests
from unittest.mock import MagicMock
from tests.fakes import FakeSession, make_client

INVALID_JSON = "<html>Internal Server Error</html>"
from functions.search_entity import search_entity, pick_entity
from tests.fakes import ScriptedInput


# --- Helpers ---

FAKE_TEAM_ENTITY = {
    "results": [
        {
            "type": "team",
            "entity": {
                "id": 3429,
                "name": "San Antonio Spurs",
                "gender": "M",
                "sport": {"slug": "basketball"}
            }
        }
    ]
}

FAKE_PLAYER_ENTITY = {
    "results": [
        {
            "type": "team",  # API labels tennis players as "team"
            "entity": {
                "id": 275923,
                "name": "Carlos Alcaraz",
                "gender": "M",
                "sport": {"slug": "tennis"},
                "playerTeamInfo": {"id": 3111}
            }
        }
    ]
}

FAKE_MULTIPLE_ENTITIES = {
    "results": [
        {
            "type": "team",
            "entity": {
                "id": 3429,
                "name": "San Antonio Spurs",
                "gender": "M",
                "sport": {"slug": "basketball"}
            }
        },
        {
            "type": "player",
            "entity": {
                "id": 998725,
                "name": "Victor Wembanyama",
                "gender": "M",
                "sport": {"slug": "basketball"},
                "playerTeamInfo": {"id": 3429}
            }
        }
    ]
}


# --- search_entity tests ---

def test_search_entity_returns_list_of_hits():
    """Returns a list of hits for a valid search."""
    session = FakeSession(FAKE_TEAM_ENTITY)
    result = search_entity(make_client(session), "spurs", "basketball")

    assert len(result) == 1
    assert result[0]["id"] == "3429"
    assert result[0]["name"] == "San Antonio Spurs"
    assert result[0]["sport"] == "basketball"


def test_search_entity_sets_player_type_team_when_no_player_team_info():
    """Sets player_type to 'team' when entity has no playerTeamInfo."""
    session = FakeSession(FAKE_TEAM_ENTITY)
    result = search_entity(make_client(session), "spurs", "basketball")

    assert result[0]["player_type"] == "team"


def test_search_entity_sets_player_type_player_when_player_team_info_present():
    """Sets player_type to 'player' when entity has playerTeamInfo."""
    session = FakeSession(FAKE_PLAYER_ENTITY)
    result = search_entity(make_client(session), "alcaraz", "tennis")

    assert result[0]["player_type"] == "player"
    assert result[0]["id"] == "275923"


def test_search_entity_returns_empty_list_on_empty_response():
    """Returns empty list when API returns an empty response."""
    session = FakeSession("")
    result = search_entity(make_client(session), "zzzzz", "basketball")

    assert result == []


def test_search_entity_returns_empty_list_on_invalid_json():
    """Returns empty list when API returns invalid JSON."""
    session = FakeSession(INVALID_JSON)
    result = search_entity(make_client(session), "spurs", "basketball")

    assert result == []


def test_search_entity_returns_empty_list_on_invalid_response_structure():
    """Returns empty list when API returns valid JSON but unexpected structure."""
    session = FakeSession({"results": [{"no_entity_key": True}]})
    result = search_entity(make_client(session), "spurs", "basketball")

    assert result == []


def test_search_entity_returns_empty_list_on_connection_error():
    """Returns empty list gracefully when a network error occurs."""
    session = FakeSession(requests.exceptions.ConnectionError)
    result = search_entity(make_client(session), "spurs", "basketball")

    assert result == []


def test_search_entity_returns_empty_list_on_timeout():
    """Returns empty list gracefully when a timeout occurs."""
    session = FakeSession(requests.exceptions.Timeout)
    result = search_entity(make_client(session), "spurs", "basketball")

    assert result == []


def test_search_entity_increments_tracker():
    """Increments the tracker on a successful request."""
    mock_tracker = MagicMock()
    session = FakeSession(FAKE_TEAM_ENTITY)
    search_entity(make_client(session, mock_tracker), "spurs", "basketball")

    mock_tracker.increment.assert_called_once()


def test_search_entity_does_not_increment_tracker_on_error():
    """Does not increment the tracker when a network error occurs."""
    mock_tracker = MagicMock()
    session = FakeSession(requests.exceptions.ConnectionError)
    search_entity(make_client(session, mock_tracker), "spurs", "basketball")

    mock_tracker.increment.assert_not_called()


def test_search_entity_omits_sport_segment_for_football():
    """Builds URL without sport segment for football."""
    session = FakeSession(FAKE_TEAM_ENTITY)
    search_entity(make_client(session), "argentina", "football")

    called_url = session.urls[-1]
    assert "/football/" not in called_url
    assert "/search/argentina" in called_url


def test_search_entity_includes_sport_segment_for_non_football():
    """Builds URL with sport segment for non-football sports."""
    session = FakeSession(FAKE_TEAM_ENTITY)
    search_entity(make_client(session), "spurs", "basketball")

    called_url = session.urls[-1]
    assert "/basketball/search/spurs" in called_url


def test_search_entity_returns_multiple_hits():
    """Returns all hits when API returns multiple results."""
    session = FakeSession(FAKE_MULTIPLE_ENTITIES)
    result = search_entity(make_client(session), "spurs", "basketball")

    assert len(result) == 2


# --- pick_entity tests ---

FAKE_HITS = [
    {"id": "3429", "name": "San Antonio Spurs", "player_type": "team", "gender": "M", "sport": "basketball"},
    {"id": "9999", "name": "San Antonio FC",    "player_type": "team", "gender": "M", "sport": "basketball"},
]

FAKE_SINGLE_HIT = [
    {"id": "3429", "name": "San Antonio Spurs", "player_type": "team", "gender": "M", "sport": "basketball"},
]


def test_pick_entity_returns_none_on_empty_list():
    """Returns None when hits list is empty."""
    result = pick_entity([])
    assert result is None


def test_pick_entity_returns_chosen_when_confirmed():
    """Returns the chosen hit when user confirms."""
    result = pick_entity(FAKE_HITS, ask=ScriptedInput("1", "Y"))

    assert result["id"] == "3429"


def test_pick_entity_returns_none_when_rejected():
    """Returns None when user rejects the selection."""
    result = pick_entity(FAKE_HITS, ask=ScriptedInput("1", "N"))

    assert result is None


def test_pick_entity_skips_selection_prompt_for_single_hit():
    """Skips the number selection prompt when there is only one result."""
    ask = ScriptedInput("Y")
    result = pick_entity(FAKE_SINGLE_HIT, ask=ask)

    assert result["id"] == "3429"
    assert len(ask.prompts) == 1


def test_pick_entity_returns_correct_hit_when_second_option_chosen():
    """Returns the second hit when user selects option 2."""
    result = pick_entity(FAKE_HITS, ask=ScriptedInput("2", "Y"))

    assert result["id"] == "9999"
    assert result["name"] == "San Antonio FC"


# --- _ask_yes_no tests ---

from functions.utils import _ask_yes_no

def test_ask_yes_no_returns_true_on_y():
    assert _ask_yes_no("Continue? Y/N: ", ask=ScriptedInput("Y")) is True


def test_ask_yes_no_returns_false_on_n():
    assert _ask_yes_no("Continue? Y/N: ", ask=ScriptedInput("N")) is False


def test_ask_yes_no_is_case_insensitive():
    assert _ask_yes_no("Continue? Y/N: ", ask=ScriptedInput("y")) is True
    assert _ask_yes_no("Continue? Y/N: ", ask=ScriptedInput("n")) is False


def test_ask_yes_no_retries_on_invalid_input():
    result = _ask_yes_no("Continue? Y/N: ", ask=ScriptedInput("maybe", "yes", "Y"))
    assert result is True


# --- pick_entity input validation tests ---

def test_pick_entity_retries_on_non_numeric_input():
    result = pick_entity(FAKE_HITS, ask=ScriptedInput("abc", "1", "Y"))
    assert result["id"] == "3429"


def test_pick_entity_retries_on_out_of_range_input():
    result = pick_entity(FAKE_HITS, ask=ScriptedInput("0", "99", "1", "Y"))
    assert result["id"] == "3429"


def test_pick_entity_retries_on_invalid_confirmation():
    result = pick_entity(FAKE_HITS, ask=ScriptedInput("1", "maybe", "Y"))
    assert result["id"] == "3429"
