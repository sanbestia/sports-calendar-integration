import logging
from collections.abc import Callable
from sports_calendar.allsports.client import AllSportsClient, AllSportsError
from sports_calendar.allsports.schemas import SearchResponse
from sports_calendar.utils import sanitize_for_log, _ask_yes_no

logger = logging.getLogger(__name__)


def search_entity(client: AllSportsClient, name: str, sport: str) -> list[dict]:
    """Search the AllSports API for a player or team by name and sport.
    Returns a list of result dicts, each containing 'id', 'name', 'type', 'gender', and 'sport'."""
    # football is the API default and has no sport prefix in the path; all other sports are prefixed
    path = f"{'' if sport.lower() == 'football' else sport.lower() + '/'}search/{name.lower()}"

    try:
        parsed = client.get(path, SearchResponse)
    except AllSportsError as e:
        logger.error(f"Search for '{sanitize_for_log(name)}' failed: {e}")
        return []

    if parsed is None:  # empty body: no results
        return []

    hits = []
    for result in parsed.results:
        entity = result.entity
        hits.append({
            "id": str(entity.id),
            "name": entity.name,
            "player_type": "player" if entity.playerTeamInfo is not None else "team",  # API sets playerTeamInfo to a non-None value for players; None means it's a team
            "gender": entity.gender,
            "sport": sport
        })

    return hits


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
            logger.info(f"  {i + 1}: {hit['name']}{gender} — {player_type}")

        while True:
            try:
                index = int(ask(f"Type a number between 1 and {len(hits)}: ")) - 1
                if 0 <= index < len(hits):
                    break
                logger.info(f"Please enter a number between 1 and {len(hits)}.")
            except ValueError:
                logger.info("That's not a valid number, please try again.")

        chosen = hits[index]

    if _ask_yes_no(f"Select '{chosen['name']}' ({chosen['player_type']})? Y/N: ", ask):
        return chosen

    logger.info("Choice discarded.")
    return None