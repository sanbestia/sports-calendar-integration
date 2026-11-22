import io
import logging

import pytest

from sports_calendar.log_setup import JournalFormatter, configure_logging


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
