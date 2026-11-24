import logging
import os
import sys
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
    """Send log lines to stream (stderr by default) with JournalFormatter.

    systemd sets JOURNAL_STREAM when the output goes to journald; only then are priority prefixes added.
    """
    logger = logger if logger is not None else logging.getLogger()
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JournalFormatter(journal="JOURNAL_STREAM" in env))
    logger.handlers[:] = [handler]
    logger.setLevel(level)
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)  # suppress the verbose 'file_cache unavailable' warning emitted by the Google client
