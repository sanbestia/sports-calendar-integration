import pytest
from unittest.mock import patch
from functions.get_ids import get_ids
from tests.fakes import ScriptedInput


FAKE_HIT_TEAM = {
    "id": "3429",
    "name": "San Antonio Spurs",
    "player_type": "team",
    "gender": "M",
    "sport": "basketball"
}

FAKE_HIT_PLAYER = {
    "id": "275923",
    "name": "Carlos Alcaraz",
    "player_type": "player",
    "gender": "M",
    "sport": "tennis"
}


# --- Tests ---

def test_returns_correct_dict_on_single_hit():
    """Returns a correctly structured dict when one result is found and confirmed."""
    with patch("functions.get_ids.search_entity", return_value=[FAKE_HIT_TEAM]):
        result = get_ids(ask=ScriptedInput("San Antonio Spurs", "basketball", "Y", "N"))

    assert "3429" in result
    assert result["3429"]["name"] == "San Antonio Spurs"
    assert result["3429"]["sport"] == "basketball"
    assert result["3429"]["player_type"] == "team"


def test_returns_correct_player_type_for_player():
    """Returns player_type 'player' when a player is selected."""
    with patch("functions.get_ids.search_entity", return_value=[FAKE_HIT_PLAYER]):
        result = get_ids(ask=ScriptedInput("Carlos Alcaraz", "tennis", "Y", "N"))

    assert "275923" in result
    assert result["275923"]["player_type"] == "player"


def test_skips_entry_when_not_confirmed():
    """Does not add entry to dict when user rejects the selection."""
    with patch("functions.get_ids.search_entity", return_value=[FAKE_HIT_TEAM]):
        result = get_ids(ask=ScriptedInput("San Antonio Spurs", "basketball", "N", "N"))

    assert result == {}


def test_retries_when_no_results_found():
    """Loops and retries when first search returns no results, succeeds on second."""
    with patch("functions.get_ids.search_entity", side_effect=[[], [FAKE_HIT_TEAM]]):
        result = get_ids(ask=ScriptedInput("zzzzz", "basketball", "San Antonio Spurs", "basketball", "Y", "N"))

    assert "3429" in result


def test_returns_multiple_entries():
    """Correctly builds dict with multiple confirmed entries (same id, so only one in dict)."""
    with patch("functions.get_ids.search_entity", return_value=[FAKE_HIT_TEAM]):
        result = get_ids(ask=ScriptedInput("San Antonio Spurs", "basketball", "Y", "Y", "San Antonio Spurs", "basketball", "Y", "N"))

    assert len(result) == 1  # same id both times


def test_returns_two_different_entries():
    """Correctly builds dict with two different confirmed entries."""
    with patch("functions.get_ids.search_entity", side_effect=[[FAKE_HIT_TEAM], [FAKE_HIT_PLAYER]]):
        result = get_ids(ask=ScriptedInput("San Antonio Spurs", "basketball", "Y", "Y", "Carlos Alcaraz", "tennis", "Y", "N"))

    assert len(result) == 2
    assert "3429" in result
    assert "275923" in result


# --- _ask_non_empty tests ---

from functions.get_ids import _ask_non_empty

def test_ask_non_empty_returns_value_on_valid_input():
    """Returns the input string when it is non-empty."""
    assert _ask_non_empty("Enter: ", ask=ScriptedInput("hello")) == "hello"


def test_ask_non_empty_strips_whitespace():
    """Strips surrounding whitespace from the input."""
    assert _ask_non_empty("Enter: ", ask=ScriptedInput("  hello  ")) == "hello"


def test_ask_non_empty_retries_on_empty_input():
    """Re-prompts when user enters an empty string or only whitespace."""
    assert _ask_non_empty("Enter: ", ask=ScriptedInput("", "   ", "hello")) == "hello"