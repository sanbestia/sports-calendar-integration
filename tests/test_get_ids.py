import pytest
from sports_calendar.tools.get_ids import get_ids
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


# --- _ask_non_empty tests ---

from sports_calendar.tools.get_ids import _ask_non_empty

def test_ask_non_empty_returns_value_on_valid_input():
    """Returns the input string when it is non-empty."""
    assert _ask_non_empty("Enter: ", ask=ScriptedInput("hello")) == "hello"


def test_ask_non_empty_strips_whitespace():
    """Strips surrounding whitespace from the input."""
    assert _ask_non_empty("Enter: ", ask=ScriptedInput("  hello  ")) == "hello"


def test_ask_non_empty_retries_on_empty_input():
    """Re-prompts when user enters an empty string or only whitespace."""
    assert _ask_non_empty("Enter: ", ask=ScriptedInput("", "   ", "hello")) == "hello"