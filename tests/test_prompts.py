from sports_calendar.tools.prompts import ask_non_empty, ask_yes_no
from tests.fakes import ScriptedInput


# --- ask_yes_no ---

def test_ask_yes_no_returns_true_on_y():
    assert ask_yes_no("Continue? Y/N: ", ask=ScriptedInput("Y")) is True


def test_ask_yes_no_returns_false_on_n():
    assert ask_yes_no("Continue? Y/N: ", ask=ScriptedInput("N")) is False


def test_ask_yes_no_is_case_insensitive():
    assert ask_yes_no("Continue? Y/N: ", ask=ScriptedInput("y")) is True
    assert ask_yes_no("Continue? Y/N: ", ask=ScriptedInput("n")) is False


def test_ask_yes_no_retries_on_invalid_input():
    result = ask_yes_no("Continue? Y/N: ", ask=ScriptedInput("maybe", "yes", "Y"))
    assert result is True


# --- ask_non_empty ---

def test_ask_non_empty_returns_value_on_valid_input():
    """Returns the input string when it is non-empty."""
    assert ask_non_empty("Enter: ", ask=ScriptedInput("hello")) == "hello"


def test_ask_non_empty_strips_whitespace():
    """Strips surrounding whitespace from the input."""
    assert ask_non_empty("Enter: ", ask=ScriptedInput("  hello  ")) == "hello"


def test_ask_non_empty_retries_on_empty_input():
    """Re-prompts when user enters an empty string or only whitespace."""
    assert ask_non_empty("Enter: ", ask=ScriptedInput("", "   ", "hello")) == "hello"
