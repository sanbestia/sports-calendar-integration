import logging
import os
from collections.abc import Callable

from dotenv import load_dotenv

from sports_calendar.allsports.client import AllSportsClient
from sports_calendar.allsports.search import search_entity
from sports_calendar.logs import configure_logging, sanitize_for_log
from sports_calendar.storage.api_calls import APICallTracker
from sports_calendar.tools.prompts import ask_non_empty, ask_yes_no

logger = logging.getLogger(__name__)


def pick_entity(hits: list[dict], ask: Callable[[str], str] = input) -> dict | None:
    """Present a list of search hits to the user and return the chosen one, or None if rejected."""
    if not hits:
        logger.info("No results found.")
        return None

    if len(hits) == 1:
        chosen = hits[0]
    else:
        logger.info("Multiple results found:")
        for i, hit in enumerate(hits):
            gender = f" ({hit['gender']})" if hit['gender'] else ""
            player_type = hit['player_type'].capitalize()
            logger.info(f"  {i + 1}: {sanitize_for_log(hit['name'])}{gender} - {player_type}")

        while True:
            try:
                index = int(ask(f"Type a number between 1 and {len(hits)}: ")) - 1
                if 0 <= index < len(hits):
                    break
                logger.info(f"Please enter a number between 1 and {len(hits)}.")
            except ValueError:
                logger.info("That's not a valid number, please try again.")

        chosen = hits[index]

    if ask_yes_no(f"Select '{chosen['name']}' ({chosen['player_type']})? Y/N: ", ask):
        return chosen

    logger.info("Choice discarded.")
    return None


def get_ids(client: AllSportsClient, ask: Callable[[str], str] = input) -> dict[str, dict[str, str]]:
    """Interactively search for players/teams and return a mapping of entity ID to name, sport, and player_type."""
    id_dict: dict[str, dict[str, str]] = {}

    while True:
        name: str = ask_non_empty("Enter player/team name: ", ask)
        sport: str = ask_non_empty("Enter sport to look for: ", ask)

        hits = search_entity(client, name, sport)

        if not hits:
            logger.info("No player/team found with that name, please try again.")
            continue

        chosen = pick_entity(hits, ask)
        if chosen:  # None when user declines; still falls through to the search-again prompt so they can exit without selecting
            id_dict[chosen["id"]] = {  # keyed by entity ID; re-adding the same entity overwrites rather than duplicates
                "name": chosen["name"],
                "sport": chosen["sport"],
                "player_type": chosen["player_type"]
            }

        if not ask_yes_no("Search for another player/team? Y/N: ", ask):
            return id_dict

        logger.info("")


def _log_results(results: dict[str, dict[str, str]]) -> None:
    """Log one line for each chosen entity, with its ID, type and sport."""
    if not results:
        logger.info("No entries were saved.")
        return
    logger.info("\n--- Results ---")
    for entity_id, data in results.items():
        logger.info(f"{sanitize_for_log(data['name'])} | ID: {entity_id} | Type: {data['player_type']} | Sport: {data['sport']}")


def main() -> None:
    """Entry point of `uv run get-ids`: build the real client from .env, search, and log the chosen IDs."""
    configure_logging()
    load_dotenv()  # the same .env as the service, so RAPIDAPI_KEY need not be exported by hand
    client = AllSportsClient(api_key=os.environ.get("RAPIDAPI_KEY", ""), tracker=APICallTracker())
    _log_results(get_ids(client, ask=input))
