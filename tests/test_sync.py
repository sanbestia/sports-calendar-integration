import datetime
import pytest
from unittest.mock import MagicMock
from googleapiclient.errors import HttpError
from sports_calendar.match import Match
from sports_calendar.google_calendar.sync import update_events, _build_event_body, _execute_with_retry, _MAX_RETRIES, SyncResult, DEFAULT_ICON
from tests.fakes import FakeClock


START_TIME = datetime.datetime(2025, 6, 15, 14, 0, 0, tzinfo=datetime.timezone.utc)
FUTURE_START_TIME = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=30)
UPDATED_AT = datetime.datetime(2026, 1, 15, 9, 30, 0)


def make_match(game_id="123", side_one="Team A", side_two="Team B", start_time=FUTURE_START_TIME,
               home_team_id="h1", away_team_id="a1", season="Some League", stage="Round 1"):
    return Match(
        side_one=side_one,
        side_two=side_two,
        season=season,
        stage=stage,
        game_id=game_id,
        sport="football",
        start_time=start_time,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
    )


def make_existing_event(game_id="123", side_one="Team A", side_two="Team B", start_time=FUTURE_START_TIME,
                        home_team_id="h1", away_team_id="a1", season="Some League", stage="Round 1"):
    """Helper to create a fake existing calendar event, shaped like one this app created for a football match."""
    return {
        "id": "google_event_id_abc",
        "summary": f"⚽ {side_one} vs {side_two} - {season} ({stage})",
        "description": "Last updated: 2025-01-01 00:00:00",
        "start": {"dateTime": start_time.isoformat()},
        "extendedProperties": {
            "private": {
                "game_id": str(game_id),
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
                "side_one": side_one,
                "side_two": side_two,
                "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        }
    }


def make_mock_service(existing_events: list) -> MagicMock:
    """Helper to create a fake Google Calendar service."""
    mock_service = MagicMock()
    mock_service.events().list().execute.return_value = {"items": existing_events}  # no nextPageToken: a single page
    mock_service.events().get().execute.return_value = existing_events[0] if existing_events else {}
    return mock_service


# --- update_events tests ---

def test_skips_event_missing_extended_properties():
    """Does not crash when an existing event has no extendedProperties game_id."""
    match = make_match(game_id="999")
    event_without_props = {
        "id": "google_event_id_xyz",
        "summary": "Some manually created event",
    }
    mock_service = make_mock_service(existing_events=[event_without_props])

    result = update_events(
        service=mock_service,
        calendar_id="fake_calendar_id",
        game_list=[match],
        time_zone="UTC",
        entity_id="h1"
    )

    mock_service.events().insert.assert_called_once()
    assert result.event_count == 1


def test_creates_new_event_when_no_existing():
    """Creates a new event when the game doesn't exist in the calendar yet."""
    match = make_match(game_id="999")
    mock_service = make_mock_service(existing_events=[])

    result = update_events(
        service=mock_service,
        calendar_id="fake_calendar_id",
        game_list=[match],
        time_zone="UTC",
        entity_id="h1"
    )

    mock_service.events().insert.assert_called_once()
    assert result.event_count == 1


def test_does_not_create_event_when_already_exists_and_unchanged():
    """Skips creation and update when event already exists and nothing has changed."""
    match = make_match(game_id="123", home_team_id="h1", away_team_id="a1")
    existing = make_existing_event(game_id="123", home_team_id="h1", away_team_id="a1")
    mock_service = make_mock_service(existing_events=[existing])

    result = update_events(
        service=mock_service,
        calendar_id="fake_calendar_id",
        game_list=[match],
        time_zone="UTC",
        entity_id="h1"
    )

    mock_service.events().insert.assert_not_called()
    mock_service.events().update.assert_not_called()
    assert result.event_count == 1


def test_updates_event_when_start_time_changed():
    """Updates an existing event when the start time has changed."""
    original_time = START_TIME
    new_time = START_TIME + datetime.timedelta(hours=3)

    existing = make_existing_event(game_id="123", start_time=original_time, home_team_id="h1", away_team_id="a1")
    match = make_match(game_id="123", start_time=new_time, home_team_id="h1", away_team_id="a1")
    mock_service = make_mock_service(existing_events=[existing])

    result = update_events(
        service=mock_service,
        calendar_id="fake_calendar_id",
        game_list=[match],
        time_zone="UTC",
        entity_id="h1"
    )

    mock_service.events().update.assert_called_once()
    mock_service.events().insert.assert_not_called()
    assert result.event_count == 1


def test_updates_event_when_team_name_changed():
    """Updates an existing event when a team name has changed."""
    existing = make_existing_event(game_id="123", side_one="Old Team A", home_team_id="h1", away_team_id="a1")
    match = make_match(game_id="123", side_one="New Team A", home_team_id="h1", away_team_id="a1")
    mock_service = make_mock_service(existing_events=[existing])

    result = update_events(
        service=mock_service,
        calendar_id="fake_calendar_id",
        game_list=[match],
        time_zone="UTC",
        entity_id="h1"
    )

    mock_service.events().update.assert_called_once()
    mock_service.events().insert.assert_not_called()
    assert result.event_count == 1


def test_updates_event_when_team_ids_missing():
    """Patches an existing event missing home/away team IDs (migration path for old events)."""
    existing = make_existing_event(game_id="123")
    del existing["extendedProperties"]["private"]["home_team_id"]
    del existing["extendedProperties"]["private"]["away_team_id"]
    match = make_match(game_id="123", home_team_id="h1", away_team_id="a1")
    mock_service = make_mock_service(existing_events=[existing])

    result = update_events(
        service=mock_service,
        calendar_id="fake_calendar_id",
        game_list=[match],
        time_zone="UTC",
        entity_id="h1"
    )

    mock_service.events().update.assert_called_once()
    mock_service.events().insert.assert_not_called()
    assert result.event_count == 1


def test_handles_multiple_matches():
    """Correctly processes a list of multiple matches."""
    match_1 = make_match(game_id="111")
    match_2 = make_match(game_id="222")
    mock_service = make_mock_service(existing_events=[])

    result = update_events(
        service=mock_service,
        calendar_id="fake_calendar_id",
        game_list=[match_1, match_2],
        time_zone="UTC",
        entity_id="h1"
    )

    assert mock_service.events().insert.call_count == 2
    assert result.event_count == 2


def test_returns_correct_count_with_multiple_existing_events():
    """Returns the total number of existing calendar events."""
    match = make_match(game_id="999", home_team_id="h1", away_team_id="a1")
    existing_1 = make_existing_event(game_id="111", home_team_id="other_h", away_team_id="other_a")
    existing_2 = make_existing_event(game_id="222", home_team_id="other_h", away_team_id="other_a")
    mock_service = make_mock_service(existing_events=[existing_1, existing_2])

    result = update_events(
        service=mock_service,
        calendar_id="fake_calendar_id",
        game_list=[match],
        time_zone="UTC",
        entity_id="h1"
    )

    assert result.event_count == 1


# --- orphan deletion tests ---

def test_deletes_orphaned_event_for_same_team():
    """Deletes a future event whose game_id is no longer in the game list (same team)."""
    match = make_match(game_id="new_id", home_team_id="h1", away_team_id="a1")
    orphan = make_existing_event(game_id="old_id", home_team_id="h1", away_team_id="a1")
    mock_service = make_mock_service(existing_events=[orphan])

    update_events(
        service=mock_service,
        calendar_id="fake_calendar_id",
        game_list=[match],
        time_zone="UTC",
        entity_id="h1"
    )

    mock_service.events().delete.assert_called_once_with(
        calendarId="fake_calendar_id", eventId="google_event_id_abc"
    )


def test_deletes_orphaned_event_when_queried_team_is_away():
    """Deletes a future orphaned event when the queried team is the away side."""
    match = make_match(game_id="new_id", home_team_id="h1", away_team_id="a1")
    orphan = make_existing_event(game_id="old_id", home_team_id="h1", away_team_id="a1")
    mock_service = make_mock_service(existing_events=[orphan])

    update_events(
        service=mock_service,
        calendar_id="fake_calendar_id",
        game_list=[match],
        time_zone="UTC",
        entity_id="a1"  # the tracked entity is the away side
    )

    mock_service.events().delete.assert_called_once_with(
        calendarId="fake_calendar_id", eventId="google_event_id_abc"
    )


def test_does_not_delete_event_for_different_team():
    """Does not delete a future event belonging to a different team."""
    match = make_match(game_id="new_id", home_team_id="h1", away_team_id="a1")
    other_team_event = make_existing_event(game_id="other_id", home_team_id="other_h", away_team_id="other_a")
    mock_service = make_mock_service(existing_events=[other_team_event])

    update_events(
        service=mock_service,
        calendar_id="fake_calendar_id",
        game_list=[match],
        time_zone="UTC",
        entity_id="h1"
    )

    mock_service.events().delete.assert_not_called()


def test_does_not_delete_event_without_team_ids():
    """Does not delete a future event that has no team IDs (old manually created events)."""
    match = make_match(game_id="new_id")
    legacy_event = make_existing_event(game_id="legacy_id")
    del legacy_event["extendedProperties"]["private"]["home_team_id"]
    del legacy_event["extendedProperties"]["private"]["away_team_id"]
    mock_service = make_mock_service(existing_events=[legacy_event])

    update_events(
        service=mock_service,
        calendar_id="fake_calendar_id",
        game_list=[match],
        time_zone="UTC",
        entity_id="h1"
    )

    mock_service.events().delete.assert_not_called()


def test_does_not_delete_past_event():
    """Does not delete an event that has already started, even if it's not in the game list.

    The sports API only returns upcoming games, so past events will never appear in game_list
    regardless of whether they were cancelled. Deleting them based on absence from game_list
    would incorrectly remove completed matches from the calendar.
    """
    match = make_match(game_id="new_id", home_team_id="h1", away_team_id="a1")
    past_event = make_existing_event(game_id="old_id", home_team_id="h1", away_team_id="a1",
                                     start_time=START_TIME)  # START_TIME is in the past
    mock_service = make_mock_service(existing_events=[past_event])

    update_events(
        service=mock_service,
        calendar_id="fake_calendar_id",
        game_list=[match],
        time_zone="UTC",
        entity_id="h1"
    )

    mock_service.events().delete.assert_not_called()


def test_an_empty_game_list_deletes_only_the_entitys_future_events():
    """run passes no matches when an entity's schedule stays empty: its future events go, and a game that has
    started and another entity's game stay."""
    future = make_existing_event(game_id="cancelled_id", home_team_id="h1", away_team_id="a1")
    started = make_existing_event(game_id="played_id", home_team_id="h1", away_team_id="a1", start_time=START_TIME)
    other_entity = make_existing_event(game_id="other_id", home_team_id="other_h", away_team_id="other_a")
    for event, event_id in ((future, "future_event"), (started, "started_event"), (other_entity, "other_event")):
        event["id"] = event_id
    mock_service = make_mock_service(existing_events=[future, started, other_entity])

    result = update_events(service=mock_service, calendar_id="fake_calendar_id", game_list=[], time_zone="UTC",
                           entity_id="a1")

    mock_service.events().delete.assert_called_once_with(calendarId="fake_calendar_id", eventId="future_event")
    assert result == SyncResult(event_count=0, deleted=1)


# --- update_events: paging and error paths ---

def make_http_error(status: int = 403) -> HttpError:
    """A non-retryable HttpError by default, so _execute_with_retry does not sleep."""
    return HttpError(resp=MagicMock(status=status), content=b"error")


def test_reads_every_page_of_existing_events():
    """Events on a later page of events().list count as existing, so they are not created again."""
    page_one_event = make_existing_event(game_id="111")
    page_two_event = make_existing_event(game_id="222")
    mock_service = MagicMock()
    mock_service.events().list().execute.side_effect = [
        {"items": [page_one_event], "nextPageToken": "page-2"},
        {"items": [page_two_event]},
    ]

    result = update_events(
        service=mock_service, calendar_id="cal", time_zone="UTC", entity_id="h1",
        game_list=[make_match(game_id="111"), make_match(game_id="222")],
    )

    mock_service.events().insert.assert_not_called()
    assert mock_service.events().list.call_args.kwargs["pageToken"] == "page-2"
    assert result.event_count == 2


def test_continues_and_does_not_count_when_insert_fails(caplog):
    """A failed insert is logged, is not counted, and does not stop the other matches."""
    mock_service = make_mock_service([])
    mock_service.events().insert().execute.side_effect = [make_http_error(), {"summary": "ok"}]

    result = update_events(
        service=mock_service, calendar_id="cal", time_zone="UTC", entity_id="h1",
        game_list=[make_match(game_id="1"), make_match(game_id="2")],
    )

    assert (result.event_count, result.created, result.failed) == (1, 1, 1)
    assert "Could not create event" in caplog.text


def test_continues_when_delete_fails(caplog):
    """A failed delete of an orphaned event is logged and does not raise."""
    orphan = make_existing_event(game_id="999")
    mock_service = make_mock_service([orphan])
    mock_service.events().delete().execute.side_effect = make_http_error()

    result = update_events(
        service=mock_service, calendar_id="cal", time_zone="UTC", entity_id="h1",
        game_list=[make_match(game_id="123")],
    )

    assert result.event_count == 1  # the new match is created; the orphan is not counted
    assert (result.deleted, result.failed) == (0, 1)
    assert "Error deleting event" in caplog.text


def test_a_failed_update_is_counted_and_the_sync_goes_on(caplog):
    """One event that always fails to update must not block the other changes, the orphan deletes included."""
    changed = make_existing_event(game_id="123", side_one="Old Name")
    orphan = make_existing_event(game_id="999")
    orphan["id"] = "orphan_event"
    mock_service = make_mock_service([changed, orphan])
    mock_service.events().update().execute.side_effect = make_http_error()

    result = update_events(
        service=mock_service, calendar_id="cal", time_zone="UTC", entity_id="h1",
        game_list=[make_match(game_id="123")],
    )

    mock_service.events().delete.assert_called_with(calendarId="cal", eventId="orphan_event")
    assert (result.updated, result.deleted, result.failed) == (0, 1, 1)
    assert "Could not update event" in caplog.text


def test_extra_copies_of_a_game_are_deleted():
    """A copy that a past run created twice is never an orphan (its game is listed), so without this it stays forever.
    The first copy that the Calendar lists stays; another entity's copies are its own sync's job."""
    first, second = make_existing_event(game_id="123"), make_existing_event(game_id="123")
    first["id"], second["id"] = "copy_1", "copy_2"
    other_1, other_2 = (make_existing_event(game_id="777", home_team_id="x", away_team_id="y") for _ in range(2))
    mock_service = make_mock_service([first, second, other_1, other_2])

    result = update_events(service=mock_service, calendar_id="cal", time_zone="UTC", entity_id="h1",
                           game_list=[make_match(game_id="123")])

    mock_service.events().delete.assert_called_once_with(calendarId="cal", eventId="copy_2")
    assert (result.event_count, result.deleted, result.failed) == (1, 1, 0)


@pytest.mark.parametrize("error_status, tries, created", [(503, 1, 0), (429, 2, 1)])
def test_an_insert_is_retried_only_after_a_rate_limit(error_status, tries, created):
    """After a 5xx the event may exist already, and a second insert would make a copy; the next fetch sees which.
    A rate limit refused the request, so a retry is safe."""
    mock_service = make_mock_service([])
    mock_service.events().insert().execute.side_effect = [make_http_error(error_status), {"summary": "ok"}]

    result = update_events(service=mock_service, calendar_id="cal", time_zone="UTC", entity_id="h1",
                           game_list=[make_match(game_id="1")], clock=FakeClock())

    assert mock_service.events().insert().execute.call_count == tries
    assert (result.created, result.failed) == (created, 1 - created)


def test_count_excludes_deleted_orphan():
    """The returned count holds the kept events plus the created ones, not the deleted orphans."""
    kept = make_existing_event(game_id="123")
    orphan = make_existing_event(game_id="999")
    orphan["id"] = "orphan_event_id"
    mock_service = make_mock_service([kept, orphan])

    result = update_events(
        service=mock_service, calendar_id="cal", time_zone="UTC", entity_id="h1",
        game_list=[make_match(game_id="123")],
    )

    mock_service.events().delete.assert_called_with(calendarId="cal", eventId="orphan_event_id")
    assert result.event_count == 1


def test_update_keeps_fields_not_in_event_body():
    """An update starts from the full stored event, so fields such as attendees are kept."""
    existing = make_existing_event(game_id="123")
    full_event = {**existing, "attendees": [{"email": "friend@example.com"}]}
    mock_service = make_mock_service([existing])
    mock_service.events().get().execute.return_value = full_event
    moved = make_match(game_id="123", start_time=FUTURE_START_TIME + datetime.timedelta(hours=2))

    update_events(service=mock_service, calendar_id="cal", time_zone="UTC", entity_id="h1", game_list=[moved])

    body = mock_service.events().update.call_args.kwargs["body"]
    assert body["attendees"] == [{"email": "friend@example.com"}]
    assert body["start"]["dateTime"] == moved.start_time.isoformat()


# --- _build_event_body ---

def test_event_body_summary_has_sport_icon_and_match_details():
    """The summary is '<icon> <side one> vs <side two> - <tournament> (<stage>)'."""
    body = _build_event_body(make_match(), "UTC", UPDATED_AT)

    assert body["summary"] == "⚽ Team A vs Team B - Some League (Round 1)"


def test_event_body_uses_start_and_expected_end_in_given_time_zone():
    """Start and end come from the match, and both carry the given time zone."""
    match = make_match()
    body = _build_event_body(match, "America/Argentina/Buenos_Aires", UPDATED_AT)

    assert body["start"] == {"dateTime": match.start_time.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"}
    assert body["end"] == {"dateTime": match.expected_end_time.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"}


def test_event_body_private_properties_match_the_reschedule_check():
    """The private properties hold the values that update_events compares later."""
    match = make_match(game_id=123, home_team_id=10, away_team_id=20)
    props = _build_event_body(match, "UTC", UPDATED_AT)["extendedProperties"]["private"]

    assert props == {
        "game_id": "123",
        "home_team_id": "10",
        "away_team_id": "20",
        "side_one": "Team A",
        "side_two": "Team B",
        "start_time": match.start_time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def test_event_body_stores_empty_string_for_missing_team_ids():
    """Missing team IDs are stored as '' because Calendar private properties must be strings."""
    props = _build_event_body(make_match(home_team_id=None, away_team_id=None), "UTC", UPDATED_AT)["extendedProperties"]["private"]

    assert props["home_team_id"] == ""
    assert props["away_team_id"] == ""


# --- update_events: title changes ---

def test_updates_event_when_tournament_changed():
    """A new tournament name updates the title (seen in prod: an 'NBA 26/27' game renamed to 'NBA Cup 2026')."""
    mock_service = make_mock_service([make_existing_event(season="NBA 26/27")])

    update_events(service=mock_service, calendar_id="cal", time_zone="UTC", entity_id="h1",
                  game_list=[make_match(season="NBA Cup 2026")])

    body = mock_service.events().update.call_args.kwargs["body"]
    assert body["summary"] == "⚽ Team A vs Team B - NBA Cup 2026 (Round 1)"


def test_updates_event_when_stage_changed():
    """A new stage name (for example a round that gets its real name) updates the title."""
    mock_service = make_mock_service([make_existing_event(stage="Round 4")])

    update_events(service=mock_service, calendar_id="cal", time_zone="UTC", entity_id="h1",
                  game_list=[make_match(stage="Quarterfinal")])

    mock_service.events().update.assert_called_once()


def test_does_not_update_when_title_and_details_unchanged():
    """An event whose title and stored details all match is left alone, so no Calendar call is wasted."""
    mock_service = make_mock_service([make_existing_event(season="NBA 26/27", stage="NBA")])

    update_events(service=mock_service, calendar_id="cal", time_zone="UTC", entity_id="h1",
                  game_list=[make_match(season="NBA 26/27", stage="NBA")])

    mock_service.events().update.assert_not_called()
    mock_service.events().insert.assert_not_called()


def test_event_body_description_shows_update_time():
    """The description tells when the app last wrote the event."""
    body = _build_event_body(make_match(), "UTC", UPDATED_AT)

    assert body["description"] == "Last updated: 2026-01-15 09:30:00"


def test_update_events_uses_clock_for_lookback_window():
    """Existing events are listed from 12 hours before the clock's current time."""
    clock = FakeClock(datetime.datetime(2026, 1, 15, 12, 0, 0, 123456, tzinfo=datetime.timezone.utc))
    mock_service = make_mock_service([])

    update_events(service=mock_service, calendar_id="cal", time_zone="UTC", entity_id="h1",
                  game_list=[make_match()], clock=clock)

    assert mock_service.events().list.call_args.kwargs["timeMin"] == "2026-01-15T00:00:00+00:00"


def test_reports_what_it_created_updated_and_deleted():
    """The result counts each change, so the fetch history can show how often schedules really change."""
    moved = make_existing_event(game_id="1")
    moved["id"] = "moved_event_id"
    orphan = make_existing_event(game_id="999")
    orphan["id"] = "orphan_event_id"
    mock_service = make_mock_service([moved, orphan])
    mock_service.events().get().execute.return_value = moved
    games = [
        make_match(game_id="1", start_time=FUTURE_START_TIME + datetime.timedelta(hours=2)),  # rescheduled
        make_match(game_id="2"),                                                              # new
    ]

    result = update_events(service=mock_service, calendar_id="cal", time_zone="UTC", entity_id="h1", game_list=games)

    assert result == SyncResult(event_count=2, created=1, updated=1, deleted=1)


# --- update_events: what this app writes, it reads back as up to date ---

@pytest.mark.parametrize("home_team_id, away_team_id", [("h1", "a1"), (None, None)])
def test_an_event_this_app_wrote_is_up_to_date_at_the_next_sync(home_team_id, away_team_id):
    """The stored properties are written and compared by the same code, so they cannot drift apart.

    Missing team IDs are stored as '', and a match without them must not look changed at every sync.
    """
    match = make_match(home_team_id=home_team_id, away_team_id=away_team_id)
    written = {**_build_event_body(match, "UTC", UPDATED_AT), "id": "event_written_before"}
    mock_service = make_mock_service([written])

    result = update_events(service=mock_service, calendar_id="cal", time_zone="UTC", entity_id="h1", game_list=[match])

    mock_service.events().update.assert_not_called()
    mock_service.events().insert.assert_not_called()
    mock_service.events().delete.assert_not_called()
    assert result == SyncResult(event_count=1)


def test_a_sport_without_an_icon_gets_the_default_icon():
    """add_entity accepts any sport; a sport missing from ICONS must not stop the sync."""
    match = make_match()
    match.sport = "darts"
    mock_service = make_mock_service([])

    result = update_events(service=mock_service, calendar_id="cal", time_zone="UTC", entity_id="h1", game_list=[match])

    body = mock_service.events().insert.call_args.kwargs["body"]
    assert body["summary"] == f"{DEFAULT_ICON} Team A vs Team B - Some League (Round 1)"
    assert result.created == 1


# --- _execute_with_retry ---

def make_request(return_value=None, side_effect=None) -> MagicMock:
    """Helper to create a fake Google API request object."""
    mock = MagicMock()
    if side_effect is not None:
        mock.execute.side_effect = side_effect
    else:
        mock.execute.return_value = return_value
    return mock


def test_returns_result_on_first_try():
    """Returns the result directly when execute succeeds."""
    clock = FakeClock()
    request = make_request(return_value={"id": "abc"})
    result = _execute_with_retry(request, clock)
    assert result == {"id": "abc"}
    request.execute.assert_called_once()
    assert clock.sleeps == []


def test_retries_on_429():
    """Retries on rate limit and returns result on eventual success."""
    clock = FakeClock()
    error = make_http_error(429)
    request = make_request(side_effect=[error, error, {"id": "abc"}])
    result = _execute_with_retry(request, clock)
    assert result == {"id": "abc"}
    assert request.execute.call_count == 3
    assert len(clock.sleeps) == 2


def test_retries_on_503():
    """Retries on 503 Service Unavailable."""
    clock = FakeClock()
    error = make_http_error(503)
    request = make_request(side_effect=[error, {"id": "abc"}])
    result = _execute_with_retry(request, clock)
    assert result == {"id": "abc"}
    assert request.execute.call_count == 2
    assert len(clock.sleeps) == 1


def test_raises_after_max_retries():
    """Raises HttpError after exhausting all retries."""
    clock = FakeClock()
    error = make_http_error(500)
    request = make_request(side_effect=error)
    with pytest.raises(HttpError):
        _execute_with_retry(request, clock)
    assert request.execute.call_count == _MAX_RETRIES


def make_403(reason: str) -> HttpError:
    """A 403 with the body that Google sends, whose reason says whether it is a rate limit."""
    body = '{"error": {"code": 403, "message": "m", "errors": [{"domain": "d", "reason": "%s"}]}}' % reason
    return HttpError(resp=MagicMock(status=403), content=body.encode())


@pytest.mark.parametrize("reason, tries", [("rateLimitExceeded", 2), ("userRateLimitExceeded", 2), ("forbidden", 1)])
def test_a_403_is_retried_only_when_it_is_a_rate_limit(reason, tries):
    """Google sends its rate limits as 403 as well as 429; a 403 for a missing permission does not go away."""
    clock = FakeClock()
    request = make_request(side_effect=[make_403(reason), {"id": "abc"}])

    try:
        _execute_with_retry(request, clock)
    except HttpError:
        pass

    assert request.execute.call_count == tries


def test_raises_immediately_on_non_retryable_http_error():
    """Does not retry on 403 Forbidden: it is not a transient error."""
    clock = FakeClock()
    error = make_http_error(403)
    request = make_request(side_effect=error)
    with pytest.raises(HttpError):
        _execute_with_retry(request, clock)
    request.execute.assert_called_once()
    assert clock.sleeps == []


def test_raises_on_broken_pipe_immediately():
    """Propagates BrokenPipeError without retrying: the caller reconnects."""
    clock = FakeClock()
    request = make_request(side_effect=BrokenPipeError)
    with pytest.raises(BrokenPipeError):
        _execute_with_retry(request, clock)
    request.execute.assert_called_once()
    assert clock.sleeps == []


def test_raises_on_generic_exception():
    """Propagates arbitrary exceptions immediately."""
    clock = FakeClock()
    request = make_request(side_effect=ValueError("unexpected"))
    with pytest.raises(ValueError):
        _execute_with_retry(request, clock)
    request.execute.assert_called_once()
    assert clock.sleeps == []
