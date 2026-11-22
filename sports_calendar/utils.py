import datetime
import re
import logging
from collections.abc import Callable

from sports_calendar.clock import Clock

logger = logging.getLogger(__name__)


def wait(deadline: datetime.datetime, clock: Clock) -> None:
    """Sleep until the (aware) deadline, waking at most every 3 minutes to re-check."""
    now = clock.now()
    while now < deadline:
        time_remaining = int((deadline - now).total_seconds())
        clock.sleep(min(180, time_remaining + 5))  # cap at 3 min so the deadline is re-checked periodically; +5s buffer avoids waking slightly early
        now = clock.now()


def sanitize_for_log(value: str) -> str:
    """Strip ANSI escape codes and normalize newlines from external strings before logging.

    Prevents log injection from malicious or malformed API responses containing
    escape sequences or embedded newlines that could spoof log entries.
    """
    value = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', value)  # ANSI escape codes
    value = value.replace('\n', ' ').replace('\r', ' ')
    return value.strip()

def _ask_yes_no(prompt: str, ask: Callable[[str], str] = input) -> bool:
    """Prompt the user with a Y/N question, re-asking until a valid answer is given."""
    while True:
        answer = ask(prompt).strip().upper()
        if answer in ("Y", "N"):
            return answer == "Y"
        logger.info("Please answer Y or N.")