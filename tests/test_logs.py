import io
import logging

import pytest

from sports_calendar.logs import JournalFormatter, configure_logging, sanitize_for_log


def make_record(level: int, message: str) -> logging.LogRecord:
    return logging.LogRecord("test", level, __file__, 1, message, None, None)


@pytest.mark.parametrize("level, expected", [
    (logging.DEBUG, "<7>detail"),
    (logging.INFO, "<6>detail"),
    (logging.WARNING, "<4>WARNING detail"),
    (logging.ERROR, "<3>ERROR detail"),
    (logging.CRITICAL, "<2>CRITICAL detail"),
])
def test_journal_lines_carry_the_syslog_priority(level, expected):
    """For journald, each line starts with <N>, so `journalctl -p warning` filters by level."""
    assert JournalFormatter(journal=True).format(make_record(level, "detail")) == expected


def test_terminal_lines_have_no_priority_prefix_and_name_only_problems():
    """In a terminal there is no <N> prefix; only WARNING and higher show their level name."""
    formatter = JournalFormatter(journal=False)
    assert formatter.format(make_record(logging.INFO, "Event created")) == "Event created"
    assert formatter.format(make_record(logging.ERROR, "Couldn't get matches")) == "ERROR Couldn't get matches"


def test_every_line_of_a_traceback_keeps_the_priority():
    """journald reads the prefix line by line, so a traceback stays at error priority."""
    try:
        raise ValueError("bad data")
    except ValueError:
        record = logging.LogRecord("test", logging.ERROR, __file__, 1, "Sync failed", None, __import__("sys").exc_info())
    lines = JournalFormatter(journal=True).format(record).split("\n")

    assert len(lines) > 2
    assert all(line.startswith("<3>") for line in lines)


@pytest.mark.parametrize("env, expected", [({"JOURNAL_STREAM": "8:12345"}, "<3>ERROR boom\n"), ({}, "ERROR boom\n")])
def test_configure_logging_uses_journal_format_only_under_journald(env, expected):
    """systemd sets JOURNAL_STREAM when the output goes to journald."""
    stream, logger = io.StringIO(), logging.getLogger("test_configure_logging")
    configure_logging(env=env, stream=stream, logger=logger)
    logger.propagate = False  # keep the line out of pytest's own handlers

    logger.error("boom")

    assert stream.getvalue() == expected


# --- sanitize_for_log ---

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


@pytest.mark.parametrize("raw", [
    "A\x1b]0;title\x07B",  # OSC: sets the terminal title
    "A\x1b]8;;http://example.com\x1b\\B",  # OSC hyperlink, ended by ST
    "A\x1b[?25lB",  # CSI with a private parameter: hides the cursor
    "A\x9b31mB",  # 8-bit CSI
    "A\x08B", "A\x00B", "A\x0bB", "A\x85B", "A B",  # backspace, NUL, vertical tab, NEL, line separator
])
def test_removes_every_escape_sequence_and_control_character(raw):
    """A string from outside must not color, hide, rewrite or split a log line."""
    assert sanitize_for_log(raw).replace(" ", "") == "AB"

@pytest.mark.parametrize("env, level", [({"LOG_LEVEL": "debug"}, logging.DEBUG), ({}, logging.INFO)])
def test_log_level_comes_from_the_environment(env, level):
    logger = logging.getLogger("test_log_level")
    configure_logging(env=env, stream=io.StringIO(), logger=logger)

    assert logger.level == level


def test_an_unknown_log_level_stops_the_start():
    """A typo would otherwise hide the DEBUG lines that someone turned on to look at a problem."""
    with pytest.raises(ValueError, match="LOUD"):
        configure_logging(env={"LOG_LEVEL": "loud"}, stream=io.StringIO(), logger=logging.getLogger("test_log_level"))
