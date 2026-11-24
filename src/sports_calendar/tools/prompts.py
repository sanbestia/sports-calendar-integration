"""The questions that the tools ask in the terminal. Each one takes `ask` (input by default), so tests pass scripted answers."""
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


def ask_yes_no(prompt: str, ask: Callable[[str], str] = input) -> bool:
    """Prompt the user with a Y/N question, re-asking until a valid answer is given."""
    while True:
        answer = ask(prompt).strip().upper()
        if answer in ("Y", "N"):
            return answer == "Y"
        logger.info("Please answer Y or N.")


def ask_non_empty(prompt: str, ask: Callable[[str], str] = input) -> str:
    """Prompt the user until they enter a non-empty string."""
    while True:
        value = ask(prompt).strip()
        if value:
            return value
        logger.info("This field cannot be empty, please try again.")
