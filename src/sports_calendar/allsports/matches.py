import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sports_calendar.match import Match
from sports_calendar.allsports.client import AllSportsClient, AllSportsError, sport_path_prefix
from sports_calendar.allsports.schemas import Event, NextPageResponse, NearResponse, RoundInfo
from sports_calendar.logs import sanitize_for_log

logger = logging.getLogger(__name__)

_MAX_PAGES = 10  # a full NBA season is 3 pages; a next page that never ends would spend the day's calls


def _build_api_path(sport: str, player_type: str, entity_id: str, endpoint: str, page: int | None = None) -> str:
    """Build an AllSports API path (the part after the base URL) for the given sport, entity, and endpoint."""
    event_type = "events" if sport == "tennis" else "matches"  # the API uses 'events' for tennis and 'matches' for all other sports
    path = f"{sport_path_prefix(sport)}{player_type}/{entity_id}/{event_type}/{endpoint}"
    if page is not None:
        path += f"/{page}"
    return path


def _stage_name(round_info: RoundInfo | None, fallback: str) -> str:
    """Return the name of the round, else 'Round N' from its number, else the fallback."""
    if round_info is not None and round_info.name:
        return round_info.name
    if round_info is not None and round_info.round is not None:
        return f"Round {round_info.round}"
    return fallback


def _fetch_events(client: AllSportsClient, sport: str, player_type: str, entity_id: str) -> list[Event]:
    """Return the upcoming events: every page of 'next', or the 'near' fallback when 'next' has none.

    Raises AllSportsError when a request fails or a response cannot be read.
    """
    events: list[Event] = []
    page = 0
    while True:
        parsed = client.get(_build_api_path(sport, player_type, entity_id, "next", page), NextPageResponse)
        if parsed is None:
            if page == 0:  # empty body on page 0 means zero results; on later pages it is an error
                logger.info("No upcoming games found with 'next' endpoint, trying 'near'...")
                break
            raise AllSportsError(f"Empty response on page {page}")
        events += parsed.events
        if not parsed.hasNextPage:
            break
        page += 1
        if page == _MAX_PAGES:  # a cut list would make the sync delete the games past the cap
            raise AllSportsError(f"More than {_MAX_PAGES} pages of upcoming games")

    if events:
        return events

    near = client.get(_build_api_path(sport, player_type, entity_id, "near"), NearResponse)
    if near is None:
        raise AllSportsError("Empty response from 'near' endpoint")
    if not near.nextEvent:
        logger.info("No upcoming games found using any endpoints")
        return []
    return near.nextEvent


def _each_game_once(events: list[Event]) -> list[Event]:
    """Keep the first copy of each event id: a game can move to the next page between two page requests."""
    first_by_id: dict[int, Event] = {}
    for event in events:
        first_by_id.setdefault(event.id, event)
    return list(first_by_id.values())


def _is_cancelled(event: Event) -> bool:
    """Return True when the API marks the event 'canceled'. The status is optional: an event with none is not cancelled."""
    return event.status is not None and event.status.type == "canceled"


def _without_cancelled(events: list[Event]) -> list[Event]:
    """Return the events that are not cancelled, and log each cancelled event that it drops."""
    kept: list[Event] = []
    for event in events:
        if _is_cancelled(event):
            logger.info(f"Skipping cancelled match: {sanitize_for_log(event.homeTeam.name)} vs {sanitize_for_log(event.awayTeam.name)}")
        else:
            kept.append(event)
    return kept


def _to_match(event: Event, sport: str, time_zone: str) -> Match:
    """Return the Match of an API event, with its start time in the given time zone."""
    return Match(
        side_one=event.homeTeam.name,
        side_two=event.awayTeam.name,
        tournament=event.season.name,
        stage=_stage_name(event.roundInfo, event.tournament.name),
        game_id=event.id,
        sport=sport,
        start_time=datetime.fromtimestamp(event.startTimestamp, tz=timezone.utc).astimezone(ZoneInfo(time_zone)),
        home_team_id=event.homeTeam.id,
        away_team_id=event.awayTeam.id,
    )


def _log_found(matches: list[Match], time_zone: str) -> None:
    """Log each match at DEBUG, then one INFO line with the count and the next match."""
    for match in matches:
        logger.debug(f"{sanitize_for_log(str(match))} ({sanitize_for_log(time_zone)})")
    if matches:
        next_match = min(matches, key=lambda match: match.start_time)
        logger.info(f"Found {len(matches)} upcoming match{'' if len(matches) == 1 else 'es'}; "
                    f"next: {sanitize_for_log(str(next_match))} ({sanitize_for_log(time_zone)})")


def get_next_matches(client: AllSportsClient, entity_id: str, entity_name: str, player_type: str, sport: str, time_zone: str) -> list[Match] | None:
    """Fetch upcoming matches for an entity (a team or a player) from the AllSports API and return them as Match objects.

    Returns [] when the entity has no upcoming matches, and None when the fetch failed, so callers can retry
    a failure soon instead of treating it as an empty schedule.
    """
    logger.info(f"Looking for games featuring {sanitize_for_log(entity_name)}...")
    try:
        events = _fetch_events(client, sport, player_type, entity_id)
    except AllSportsError as e:
        logger.error(f"Couldn't get matches for {sanitize_for_log(entity_name)}: {e}")
        return None
    matches = [_to_match(event, sport, time_zone) for event in _without_cancelled(_each_game_once(events))]
    _log_found(matches, time_zone)
    return matches
