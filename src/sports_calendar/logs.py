"""Log lines: their format for a terminal or for journald, the setup of the root logger, and safe text from outside sources."""
import logging
import os
import re
import sys
import unicodedata
from collections.abc import Mapping
from typing import TextIO

# syslog priorities that journald reads from a "<N>" prefix at the start of each line
_JOURNAL_PRIORITY = {logging.DEBUG: 7, logging.INFO: 6, logging.WARNING: 4, logging.ERROR: 3, logging.CRITICAL: 2}


class JournalFormatter(logging.Formatter):
    """Formats log lines for a terminal or for journald.

    WARNING and higher lines start with their level name, so problems stand out in any output. When the
    output is journald, every line (including each line of a traceback) also starts with "<N>", the syslog
    priority, so `journalctl -p warning` shows only warnings and errors.
    """

    def __init__(self, journal: bool):
        super().__init__("%(message)s")
        self._journal = journal

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if record.levelno >= logging.WARNING:
            text = f"{record.levelname} {text}"
        if self._journal:
            priority = _JOURNAL_PRIORITY.get(record.levelno, 6)
            text = "\n".join(f"<{priority}>{line}" for line in text.split("\n"))
        return text


def configure_logging(env: Mapping[str, str] = os.environ, stream: TextIO | None = None,
                      logger: logging.Logger | None = None, level: int = logging.INFO) -> None:
    """Send log lines to stream (stderr by default) with JournalFormatter, at the level that LOG_LEVEL names, else level.

    systemd sets JOURNAL_STREAM when the output goes to journald; only then are priority prefixes added.
    """
    logger = logger if logger is not None else logging.getLogger()
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JournalFormatter(journal="JOURNAL_STREAM" in env))
    logger.handlers[:] = [handler]
    logger.setLevel(_log_level(env, level))
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)  # suppress the verbose 'file_cache unavailable' warning emitted by the Google client



def _log_level(env: Mapping[str, str], default: int) -> int:
    """The level that LOG_LEVEL names (DEBUG, INFO, WARNING, ERROR), or the default. An unknown name stops the start:
    a typo would hide the DEBUG lines that someone turned on to look at a problem."""
    name = env.get("LOG_LEVEL", "").strip().upper()
    if not name:
        return default
    if name not in logging.getLevelNamesMapping():
        raise ValueError(f"LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR, not {name!r}")
    return logging.getLevelNamesMapping()[name]

# CSI (7-bit and 8-bit), OSC up to BEL or ST, and the other two-character escapes; CSI and OSC first, as ESC [ and
# ESC ] would also match the last part
_ESCAPE_SEQUENCE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x9b[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-_]")
_LINE_BREAKING = {"Cc", "Zl", "Zp"}  # control characters, line and paragraph separators


def sanitize_for_log(value: str) -> str:
    """Remove terminal escape sequences, and turn every other control character or line break into a space.

    A string from an outside source (an API answer, an event title) can then neither color, hide, rewrite nor
    split a log line, so it cannot spoof another entry.
    """
    value = _ESCAPE_SEQUENCE.sub("", value)
    value = "".join(" " if unicodedata.category(char) in _LINE_BREAKING else char for char in value)
    return value.strip()
