import logging
import random
from dataclasses import dataclass
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from datetime import datetime, timedelta
from sports_calendar.match import Match
from sports_calendar.clock import Clock
from sports_calendar.utils import sanitize_for_log

logger = logging.getLogger(__name__)

ICONS: dict[str, str] = {
    "football": "⚽",
    "baseball": "⚾",
    "basketball": "🏀",
    "volleyball": "🏐",
    "american-football": "🏈",
    "rugby": "🏉",
    "tennis": "🎾",
    "cricket": "🏏",
    "ice-hockey": "🏒",
    "motorsport": "🏎️",
    "esport": "🎮"
}

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 4
_BASE_DELAY_SECONDS = 1.0
_LOOKBACK = timedelta(hours=12)  # events are listed from this long ago, to catch games in progress


def _execute_with_retry(request, clock: Clock) -> dict:
    """Execute a Google API request with exponential backoff on transient errors."""
    for attempt in range(_MAX_RETRIES):
        try:
            return request.execute()
        except HttpError as e:
            if e.status_code not in _RETRYABLE_STATUS_CODES or attempt == _MAX_RETRIES - 1:
                raise
            delay = _BASE_DELAY_SECONDS * (2 ** attempt) + random.uniform(0, 1)
            logger.warning(f"Google API error {e.status_code}, retrying in {delay:.1f}s (attempt {attempt + 1}/{_MAX_RETRIES})")
            clock.sleep(delay)


def _build_summary(game: Match) -> str:
    """Build the event title: '<icon> <side one> vs <side two> - <tournament> (<stage>)'."""
    return f'{ICONS[game.sport]} {game.side_one} vs {game.side_two} - {game.tournament} ({game.stage})'


def _private_properties(game: Match) -> dict[str, str]:
    """The match details that an event stores, as strings (Calendar private properties must be strings).

    _needs_update compares the stored ones with these to find a reschedule or a team change.
    """
    return {
        'game_id': str(game.game_id),
        'home_team_id': str(game.home_team_id) if game.home_team_id is not None else '',
        'away_team_id': str(game.away_team_id) if game.away_team_id is not None else '',
        'side_one': game.side_one,
        'side_two': game.side_two,
        'start_time': game.start_time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _build_event_body(game: Match, time_zone: str, updated_at: datetime) -> dict:
    """Build a Google Calendar event body for a given match; updated_at is shown in the description."""
    return {
        'summary': _build_summary(game),
        'description': f'Last updated: {updated_at.strftime("%Y-%m-%d %H:%M:%S")}',
        'start': {
            'dateTime': game.start_time.isoformat(),
            'timeZone': time_zone,
        },
        'end': {
            'dateTime': game.expected_end_time.isoformat(),
            'timeZone': time_zone,
        },
        'extendedProperties': {
            'private': _private_properties(game)
        }
    }


@dataclass(frozen=True)
class SyncResult:
    """What one update_events call did: the team's events left in the calendar, and the changes it made."""
    event_count: int
    created: int = 0
    updated: int = 0
    deleted: int = 0


def _stored_properties(event: dict) -> dict:
    """The private properties stored in a calendar event ({} for an event that has none)."""
    return event.get("extendedProperties", {}).get("private", {})


def _lookback_start(now: datetime) -> str:
    """The time from which existing events are listed, in whole seconds (the Calendar API rejects fractions)."""
    return (now - _LOOKBACK).replace(microsecond=0).isoformat()


def _index_by_game_id(events: list[dict]) -> dict[str, dict]:
    """Map each event's stored game_id to the event; warn about an event that has no game_id."""
    by_game_id: dict[str, dict] = {}
    for event in events:
        game_id = _stored_properties(event).get("game_id")
        if game_id:
            by_game_id[game_id] = event
        else:
            logger.warning(f"Skipping event '{sanitize_for_log(event.get('summary', 'unknown'))}' — no game_id in extendedProperties")
    return by_game_id


def _needs_update(event: dict, game: Match) -> bool:
    """True when the stored details or the title differ from the match.

    The stored details catch a reschedule or a team change; the title catches a renamed tournament or stage,
    which are not stored as private properties. The Calendar's own start time is not compared.
    """
    return _stored_properties(event) != _private_properties(game) or event.get("summary") != _build_summary(game)


def _is_orphan(event: dict, team_id: str, game_ids: set[str], now: datetime) -> bool:
    """True for a future event of this team whose match is no longer in the list (cancelled, or its API id changed).

    An event of another team that shares the calendar is never an orphan. Neither is an event that has started:
    the sports API returns only upcoming games, so a past game is missing from the list even when it was played.
    """
    stored = _stored_properties(event)
    if team_id not in (stored.get("home_team_id"), stored.get("away_team_id")):
        return False
    game_id = stored.get("game_id")
    if not game_id or game_id in game_ids:
        return False
    start = event.get("start", {}).get("dateTime", "")
    return not start or datetime.fromisoformat(start).astimezone() > now


def _count_kept(events: list[dict], game_ids: set[str]) -> int:
    """How many listed events belong to a match in the list (the events that stay; new ones are not counted)."""
    return sum(1 for event in events if _stored_properties(event).get("game_id") in game_ids)


class _CalendarWriter:
    """The Calendar API calls for one calendar, each with retries; it logs each change it makes."""

    def __init__(self, service: Resource, calendar_id: str, time_zone: str, updated_at: datetime, clock: Clock):
        self._events = service.events()
        self._calendar_id = calendar_id
        self._time_zone = time_zone
        self._updated_at = updated_at
        self._clock = clock

    def list_events(self, time_min: str) -> list[dict]:
        """Return every event from time_min on, reading all pages."""
        response = _execute_with_retry(self._events.list(calendarId=self._calendar_id, timeMin=time_min), self._clock)
        events = list(response.get("items", []))
        while response.get("nextPageToken"):
            response = _execute_with_retry(self._events.list(
                calendarId=self._calendar_id,
                timeMin=time_min,
                pageToken=response["nextPageToken"]
            ), self._clock)
            events.extend(response.get("items", []))
        return events

    def create(self, game: Match) -> bool:
        """Insert an event for a new match. Returns False, after logging the error, when the Calendar refuses it."""
        logger.debug("New match upcoming! Creating event...")
        try:
            created_event = _execute_with_retry(self._events.insert(
                calendarId=self._calendar_id,
                body=_build_event_body(game, self._time_zone, self._updated_at)
            ), self._clock)
        except HttpError as error:
            logger.error(f"An error occurred: {error}")
            return False
        logger.info(f'Event created: {sanitize_for_log(str(created_event.get("summary", "")))} — {sanitize_for_log(str(created_event.get("htmlLink", "")))}')
        return True

    def update(self, event: dict, game: Match) -> None:
        """Write the match's details into an existing event, keeping its other fields (e.g. attendees)."""
        logger.debug("Found change in event details. Updating...")
        event_to_update = _execute_with_retry(self._events.get(calendarId=self._calendar_id, eventId=event["id"]), self._clock)  # the full event, to keep fields not in _build_event_body
        event_to_update.update(_build_event_body(game, self._time_zone, self._updated_at))
        updated_event = _execute_with_retry(self._events.update(
            calendarId=self._calendar_id,
            eventId=event['id'],
            body=event_to_update
        ), self._clock)
        logger.info(f'Event updated: {sanitize_for_log(str(updated_event.get("summary", "")))} — {sanitize_for_log(str(updated_event.get("htmlLink", "")))}')

    def delete(self, event: dict) -> bool:
        """Delete an event. Returns False, after logging the error, when the Calendar refuses it."""
        try:
            _execute_with_retry(self._events.delete(calendarId=self._calendar_id, eventId=event["id"]), self._clock)
        except HttpError as error:
            logger.error(f"Error deleting event '{sanitize_for_log(event.get('summary', ''))}': {error}")
            return False
        logger.info(f"Deleted cancelled/orphaned event: '{sanitize_for_log(event.get('summary', ''))}'")
        return True


def _create_or_update(writer: _CalendarWriter, game_list: list[Match], existing_by_game_id: dict[str, dict]) -> tuple[int, int]:
    """Create an event for each new match and update each changed one; return (created, updated)."""
    created = updated = 0
    for game in game_list:
        event = existing_by_game_id.get(str(game.game_id))
        if event is None:
            if writer.create(game):
                created += 1
        elif _needs_update(event, game):
            writer.update(event, game)
            updated += 1
        else:
            logger.debug(f"'{sanitize_for_log(event['summary'])}' already up to date")
    return created, updated


def _delete_orphans(writer: _CalendarWriter, events: list[dict], team_id: str, game_ids: set[str], now: datetime) -> int:
    """Delete this team's orphaned events (see _is_orphan) and return how many were deleted."""
    return sum(1 for event in events if _is_orphan(event, team_id, game_ids, now) and writer.delete(event))


def update_events(service: Resource, calendar_id: str, game_list: list[Match], time_zone: str, team_id: str,
                  clock: Clock | None = None) -> SyncResult:
    """Sync a list of matches to Google Calendar: create new events, update changed ones, and delete orphans."""
    clock = clock or Clock()
    now = clock.now()
    writer = _CalendarWriter(service, calendar_id, time_zone, updated_at=now.astimezone(), clock=clock)  # local time, for the description
    logger.debug("Updating calendar: looking up future events...")
    events = writer.list_events(_lookback_start(now))
    created, updated = _create_or_update(writer, game_list, _index_by_game_id(events))
    game_ids = {str(game.game_id) for game in game_list}
    deleted = _delete_orphans(writer, events, team_id, game_ids, now)
    return SyncResult(_count_kept(events, game_ids) + created, created=created, updated=updated, deleted=deleted)
