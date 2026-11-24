import pytest
from sports_calendar.tools.get_ids import get_ids, pick_entity
from tests.fakes import FakeSession, ScriptedInput, make_client


SPURS_SEARCH = {"results": [{"entity": {"id": 3429, "name": "San Antonio Spurs", "gender": "M"}}]}
ALCARAZ_SEARCH = {"results": [{"entity": {"id": 275923, "name": "Carlos Alcaraz", "gender": "M", "playerTeamInfo": {"birthDateTimestamp": 0}}}]}


# --- Tests ---

def test_returns_correct_dict_on_single_hit():
    """Returns a correctly structured dict when one result is found and confirmed."""
    session = FakeSession(SPURS_SEARCH)
    result = get_ids(make_client(session), ask=ScriptedInput("San Antonio Spurs", "basketball", "Y", "N"))

    assert "3429" in result
    assert result["3429"]["name"] == "San Antonio Spurs"
    assert result["3429"]["sport"] == "basketball"
    assert result["3429"]["player_type"] == "team"


def test_returns_correct_player_type_for_player():
    """Returns player_type 'player' when a player is selected."""
    session = FakeSession(ALCARAZ_SEARCH)
    result = get_ids(make_client(session), ask=ScriptedInput("Carlos Alcaraz", "tennis", "Y", "N"))

    assert "275923" in result
    assert result["275923"]["player_type"] == "player"


def test_skips_entry_when_not_confirmed():
    """Does not add entry to dict when user rejects the selection."""
    session = FakeSession(SPURS_SEARCH)
    result = get_ids(make_client(session), ask=ScriptedInput("San Antonio Spurs", "basketball", "N", "N"))

    assert result == {}


def test_retries_when_no_results_found():
    """Loops and retries when first search returns no results, succeeds on second."""
    session = FakeSession("", SPURS_SEARCH)
    result = get_ids(make_client(session), ask=ScriptedInput("zzzzz", "basketball", "San Antonio Spurs", "basketball", "Y", "N"))

    assert "3429" in result


def test_returns_multiple_entries():
    """Correctly builds dict with multiple confirmed entries (same id, so only one in dict)."""
    session = FakeSession(SPURS_SEARCH, SPURS_SEARCH)
    result = get_ids(make_client(session), ask=ScriptedInput("San Antonio Spurs", "basketball", "Y", "Y", "San Antonio Spurs", "basketball", "Y", "N"))

    assert len(result) == 1  # same id both times


def test_returns_two_different_entries():
    """Correctly builds dict with two different confirmed entries."""
    session = FakeSession(SPURS_SEARCH, ALCARAZ_SEARCH)
    result = get_ids(make_client(session), ask=ScriptedInput("San Antonio Spurs", "basketball", "Y", "Y", "Carlos Alcaraz", "tennis", "Y", "N"))

    assert len(result) == 2
    assert "3429" in result
    assert "275923" in result


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
