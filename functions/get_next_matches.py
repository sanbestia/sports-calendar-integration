import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from objects.Match import Match
from objects.AllSportsClient import AllSportsClient, AllSportsError
from objects.schemas import NextPageResponse, NearResponse
from functions.utils import sanitize_for_log

logger = logging.getLogger(__name__)


def _build_api_path(sport: str, player_type: str, team_id: str, endpoint: str, page: int | None = None) -> str:
    """Build an AllSports API path (the part after the base URL) for the given sport, entity, and endpoint."""
    sport_segment = f"{sport}/" if sport != "football" else ""  # football is the AllSports API default — no sport prefix in the URL path
    event_type = "events" if sport == "tennis" else "matches"  # the API uses 'events' for tennis and 'matches' for all other sports
    path = f"{sport_segment}{player_type}/{team_id}/{event_type}/{endpoint}"
    if page is not None:
        path += f"/{page}"
    return path


def _get_stage(round_info: dict | None, fallback: str) -> str:
    """Resolve the match stage from round info, synthesising a name from the round number if needed."""
    if round_info and round_info.get('name'):
        return round_info['name']
    if round_info and round_info.get('round') is not None:
        return f'Round {round_info["round"]}'
    return fallback


def _fetch_events(client: AllSportsClient, sport: str, player_type: str, team_id: str) -> list[dict]:
    """Return the raw upcoming events: every page of 'next', or the 'near' fallback when 'next' has none.

    Raises AllSportsError when a request fails or a response cannot be read.
    """
    events: list[dict] = []
    page = 0
    while True:
        parsed = client.get(_build_api_path(sport, player_type, team_id, "next", page), NextPageResponse)
        if parsed is None:
            if page == 0:  # empty body on page 0 means zero results; on later pages it is an error
                logger.info("No upcoming games found with 'next' endpoint, trying 'near'...")
                break
            raise AllSportsError(f"Empty response on page {page}")
        events += [e.model_dump() for e in parsed.events]  # convert back to dict; subsequent code uses raw key access like event['homeTeam']
        if not parsed.hasNextPage:
            break
        page += 1

    if events:
        return events

    near = client.get(_build_api_path(sport, player_type, team_id, "near"), NearResponse)
    if near is None:
        raise AllSportsError("Empty response from 'near' endpoint")
    if not near.nextEvent:
        logger.info("No upcoming games found using any endpoints")
        return []
    return [e.model_dump() for e in near.nextEvent]  # convert to dict; same reason as in the paginated path above


def get_next_matches(client: AllSportsClient, team_id: str, team_name: str, player_type: str, sport: str, time_zone: str) -> list[Match] | None:
    """Fetch upcoming matches for a team from the AllSports API and return them as Match objects.

    Returns [] when the team has no upcoming matches, and None when the fetch failed, so callers can retry
    a failure soon instead of treating it as an empty schedule.
    """
    logger.info(f"Looking for games featuring {sanitize_for_log(team_name)}...")

    try:
        events = _fetch_events(client, sport, player_type, team_id)
    except AllSportsError as e:
        logger.error(f"Couldn't get matches for {sanitize_for_log(team_name)}: {e}")
        return None

    if not events:
        return []

    next_games: list[Match] = []
    for event in events:
        if event.get('status', {}).get('type') == 'canceled':
            logger.info(f"Skipping cancelled match: {sanitize_for_log(event['homeTeam']['name'])} vs {sanitize_for_log(event['awayTeam']['name'])}")
            continue

        date_time = datetime.fromtimestamp(event['startTimestamp'], tz=timezone.utc)
        date_time = date_time.astimezone(ZoneInfo(time_zone))
        round_info = event.get('roundInfo')
        game = Match(
            side_one=event['homeTeam']['name'],
            side_two=event['awayTeam']['name'],
            tournament=event['season']['name'],
            stage=_get_stage(round_info, event['tournament']['name']),
            game_id=event['id'],
            sport=sport,
            start_time=date_time,
            home_team_id=event['homeTeam']['id'],
            away_team_id=event['awayTeam']['id'],
        )

        logger.debug(f"{sanitize_for_log(str(game))} ({sanitize_for_log(time_zone)})")
        next_games.append(game)

    if next_games:
        first = min(next_games, key=lambda g: g.start_time)
        logger.info(f"Found {len(next_games)} upcoming match{'' if len(next_games) == 1 else 'es'}; "
                    f"next: {sanitize_for_log(str(first))} ({sanitize_for_log(time_zone)})")
    return next_games
