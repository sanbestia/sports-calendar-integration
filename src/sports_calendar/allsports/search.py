import logging
from sports_calendar.allsports.client import AllSportsClient, AllSportsError, sport_path_prefix
from sports_calendar.allsports.schemas import SearchResponse
from sports_calendar.logs import sanitize_for_log

logger = logging.getLogger(__name__)


def search_entity(client: AllSportsClient, name: str, sport: str) -> list[dict]:
    """Search the AllSports API for a player or team by name and sport.
    Returns a list of result dicts, each containing 'id', 'name', 'player_type', 'gender', and 'sport'
    (the sport in lowercase, the form that the API paths use)."""
    sport_slug = sport.lower()
    path = f"{sport_path_prefix(sport_slug)}search/{name.lower()}"

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
            "sport": sport_slug
        })

    return hits
