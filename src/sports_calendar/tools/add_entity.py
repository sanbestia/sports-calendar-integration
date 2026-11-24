"""Add teams or players to queries.json in one go: search, choose the calendar, save, and optionally deploy.

Run it as `uv run add-entity`. It only connects other functions:
get_ids (search and pick) -> add_query (check and save) -> push_queries (secret + deploy).
"""
import logging
import os
from collections.abc import Callable, Mapping

from dotenv import load_dotenv

from sports_calendar.tools.get_ids import get_ids
from sports_calendar.tools.push_queries import push_queries
from sports_calendar.queries_file import QueryError, add_query, calendar_keys, read_queries
from sports_calendar.tools.prompts import ask_yes_no
from sports_calendar.allsports.client import AllSportsClient
from sports_calendar.logs import configure_logging
from sports_calendar.storage.api_calls import APICallTracker

logger = logging.getLogger(__name__)


def _choose_calendar_key(name: str, keys: list[str], ask: Callable[[str], str]) -> str:
    """Pick one of the calendar env keys already in use, or type another one."""
    logger.info(f"Calendar for {name}:")
    for number, key in enumerate(keys, start=1):
        logger.info(f"  {number}: {key}")
    other = len(keys) + 1
    logger.info(f"  {other}: another env key")
    while True:
        answer = ask(f"Type a number between 1 and {other}: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return keys[int(answer) - 1]
        if answer == str(other):
            key = ask("Env key that holds the calendar ID (for example CALENDAR_ID_TENNIS): ").strip()
            if key:
                return key
        logger.info("Please type one of the numbers.")


def add_entities(client: AllSportsClient, queries_path: str, env: Mapping[str, str],
                 ask: Callable[[str], str] = input, push: Callable[[str], None] = push_queries) -> list[str]:
    """Search and add entities to queries_path, and return the names added.

    When at least one entity was added, offer to push the file (secret + deploy).
    """
    found = get_ids(client, ask=ask)
    keys = calendar_keys(read_queries(queries_path))
    added = []
    for entity_id, data in found.items():
        name = ask(f"Name in queries.json [{data['name']}]: ").strip() or data["name"]
        key = _choose_calendar_key(name, keys, ask)
        try:
            add_query(queries_path, name, {"id": entity_id, "sport": data["sport"], "player_type": data["player_type"]}, key, env)
        except QueryError as e:
            logger.error(f"Not added: {e}")
            continue
        logger.info(f"Added {name} ({data['sport']}, {data['player_type']}) with calendar {key}")
        added.append(name)
        keys = sorted(set(keys) | {key})

    if added and ask_yes_no("Upload queries.json and deploy now? Y/N: ", ask):
        push(queries_path)
    elif added:
        logger.info("Not deployed. Run `uv run push-queries` when you are ready.")
    return added


def main() -> None:
    """Entry point of `uv run add-entity`: build the real client from .env and run the flow on ./queries.json."""
    configure_logging()
    load_dotenv()
    client = AllSportsClient(api_key=os.environ.get("RAPIDAPI_KEY", ""), tracker=APICallTracker())
    add_entities(client, "queries.json", os.environ)
