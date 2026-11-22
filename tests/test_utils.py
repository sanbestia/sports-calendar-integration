import datetime
import pytest
from sports_calendar.utils import sanitize_for_log, wait
from tests.fakes import FakeClock


# --- ANSI escape codes ---

def test_strips_ansi_color_codes():
    """Removes ANSI color escape sequences."""
    assert sanitize_for_log("\x1b[31mRed Text\x1b[0m") == "Red Text"


def test_strips_ansi_bold():
    """Removes ANSI bold escape sequence."""
    assert sanitize_for_log("\x1b[1mBold\x1b[0m") == "Bold"


def test_strips_ansi_multi_param():
    """Removes ANSI sequences with multiple parameters."""
    assert sanitize_for_log("\x1b[1;32mGreen Bold\x1b[0m") == "Green Bold"


def test_strips_ansi_cursor_movement():
    """Removes ANSI cursor movement sequences."""
    assert sanitize_for_log("\x1b[2Amove up") == "move up"


# --- Newlines and carriage returns ---

def test_replaces_newline_with_space():
    """Replaces newline characters with a space."""
    assert sanitize_for_log("line one\nline two") == "line one line two"


def test_replaces_carriage_return_with_space():
    """Replaces carriage return characters with a space."""
    assert sanitize_for_log("line one\rline two") == "line one line two"


def test_replaces_crlf_with_spaces():
    """Replaces Windows-style CRLF with spaces."""
    assert sanitize_for_log("line one\r\nline two") == "line one  line two"


def test_strips_leading_and_trailing_whitespace():
    """Strips surrounding whitespace after substitutions."""
    assert sanitize_for_log("  hello  ") == "hello"


def test_strips_leading_newline():
    """Strips a leading newline that becomes a leading space after substitution."""
    assert sanitize_for_log("\nhello") == "hello"


# --- Combined cases ---

def test_strips_ansi_and_normalises_newlines():
    """Handles a string with both ANSI codes and newlines."""
    assert sanitize_for_log("\x1b[31mError\x1b[0m\ndetail") == "Error detail"


def test_handles_injected_fake_log_line():
    """Prevents a malicious string from injecting a fake log entry."""
    malicious = "Real Team\nINFO - Fake log entry injected by attacker"
    result = sanitize_for_log(malicious)
    assert "\n" not in result
    assert result == "Real Team INFO - Fake log entry injected by attacker"


# --- Clean strings ---

def test_clean_string_passes_through_unchanged():
    """A normal string with no special characters is returned as-is."""
    assert sanitize_for_log("Carlos Alcaraz") == "Carlos Alcaraz"


def test_empty_string_returns_empty():
    """An empty string returns an empty string."""
    assert sanitize_for_log("") == ""


def test_string_with_unicode_passes_through():
    """Unicode characters in team names are preserved."""
    assert sanitize_for_log("Atlético de Madrid") == "Atlético de Madrid"


# ============================================================
# wait
# ============================================================

DEADLINE_BASE = datetime.datetime(2026, 1, 15, 12, 0, tzinfo=datetime.timezone.utc)


def test_does_not_sleep_when_deadline_already_passed():
    """Does not sleep at all when the deadline is in the past."""
    clock = FakeClock(DEADLINE_BASE)
    wait(DEADLINE_BASE - datetime.timedelta(seconds=1), clock)
    assert clock.sleeps == []


def test_sleeps_once_for_short_remaining_time():
    """Sleeps once with time_remaining + 5 when the deadline is close."""
    clock = FakeClock(DEADLINE_BASE)
    wait(DEADLINE_BASE + datetime.timedelta(seconds=10), clock)
    assert clock.sleeps == [15]  # 10 + 5


def test_sleep_duration_capped_at_180_seconds():
    """Caps each sleep at 180 seconds even when the remaining time is much larger."""
    clock = FakeClock(DEADLINE_BASE)
    wait(DEADLINE_BASE + datetime.timedelta(seconds=3600), clock)
    assert max(clock.sleeps) == 180


def test_sleeps_until_deadline_reached_and_shrinks_last_sleep():
    """Loops in 180-second steps, and the last sleep covers only what is left (+5s)."""
    clock = FakeClock(DEADLINE_BASE)
    deadline = DEADLINE_BASE + datetime.timedelta(seconds=370)
    wait(deadline, clock)
    assert clock.sleeps == [180, 180, 15]  # 370 -> 190 -> 10 left; min(180, 10 + 5) = 15
    assert clock.now() >= deadline


def test_accepts_deadline_in_another_time_zone():
    """Aware datetimes compare by instant, so the deadline's time zone does not matter."""
    clock = FakeClock(DEADLINE_BASE)
    buenos_aires = datetime.timezone(datetime.timedelta(hours=-3))
    wait((DEADLINE_BASE + datetime.timedelta(seconds=10)).astimezone(buenos_aires), clock)
    assert clock.sleeps == [15]
