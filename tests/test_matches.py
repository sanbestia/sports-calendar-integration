import datetime
import pytest
import json
import requests
from unittest.mock import MagicMock
from tests.fakes import FakeSession, make_client

INVALID_JSON = "<html>Internal Server Error</html>"
from sports_calendar.allsports.matches import get_next_matches, _build_api_path

# Fake event matching the real API structure
FAKE_EVENT = {
    "id": 1,
    "startTimestamp": 1772823600,
    "homeTeam": {"id": 275923, "name": "Carlos Alcaraz"},
    "awayTeam": {"id": 206570, "name": "Jannik Sinner"},
    "season": {"name": "ATP"},
    "roundInfo": {"round": 1, "name": "Finals"},
    "tournament": {"name": "ATP Ultra Finals, Mars"},
    "status": {"type": "notstarted"}
}

FAKE_NEAR_RESPONSE = {
    "nextEvent": FAKE_EVENT
}

FAKE_NEXT_RESPONSE_SINGLE_PAGE = {
    "events": [FAKE_EVENT],
    "hasNextPage": False
}

FAKE_NEXT_RESPONSE_PAGE_1 = {
    "events": [FAKE_EVENT],
    "hasNextPage": True
}

FAKE_NEXT_RESPONSE_PAGE_2 = {
    "events": [{**FAKE_EVENT, "id": 2}],
    "hasNextPage": False
}


# --- Tests ---

@pytest.mark.parametrize("time_zone, utc_offset_hours", [
    ("Etc/UTC", 0),                          # the server's zone, as tzlocal names it
    ("America/Argentina/Buenos_Aires", -3),
    ("Europe/Madrid", 1),                    # winter time on 6 March
])
def test_start_time_is_the_api_timestamp_in_the_given_time_zone(time_zone, utc_offset_hours):
    """The same moment as startTimestamp (2026-03-06 19:00 UTC), with the offset of the given zone."""
    session = FakeSession(FAKE_NEXT_RESPONSE_SINGLE_PAGE)
    [match] = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", time_zone)

    assert match.start_time == datetime.datetime(2026, 3, 6, 19, 0, tzinfo=datetime.timezone.utc)
    assert match.start_time.utcoffset() == datetime.timedelta(hours=utc_offset_hours)


def test_returns_list_of_matches():
    """'next' endpoint returns one page of results."""
    session = FakeSession(FAKE_NEXT_RESPONSE_SINGLE_PAGE)
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert len(result) == 1
    assert result[0].side_one == "Carlos Alcaraz"
    assert result[0].side_two == "Jannik Sinner"
    assert result[0].game_id == "1"
    assert result[0].home_team_id == "275923"
    assert result[0].away_team_id == "206570"


def test_paginates_correctly():
    """'next' endpoint paginates and collects events from all pages."""
    session = FakeSession(
        FAKE_NEXT_RESPONSE_PAGE_1,
        FAKE_NEXT_RESPONSE_PAGE_2
    )
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert len(result) == 2
    assert len(session.requests) == 2


def test_a_game_listed_on_two_pages_is_one_match():
    """A game can move from one page to the next between two requests; a second copy would become a second event."""
    session = FakeSession(FAKE_NEXT_RESPONSE_PAGE_1, {**FAKE_NEXT_RESPONSE_PAGE_2, "events": [FAKE_EVENT]})

    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert [match.game_id for match in result] == ["1"]


def test_falls_back_to_near_endpoint():
    """Falls back to 'near' endpoint when 'next' returns empty."""
    session = FakeSession(
        "",
        FAKE_NEAR_RESPONSE
    )
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert len(result) == 1
    assert result[0].game_id == "1"


def test_returns_none_on_connection_error():
    """Returns None gracefully when a network error occurs."""
    session = FakeSession(requests.exceptions.ConnectionError)
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert result is None  # a failed fetch, not an empty schedule


def test_returns_none_on_timeout():
    """Returns None gracefully when a timeout occurs."""
    session = FakeSession(requests.exceptions.Timeout)
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert result is None  # a failed fetch, not an empty schedule


def test_match_sport_is_set_correctly():
    """Match objects are created with the correct sport."""
    session = FakeSession(FAKE_NEXT_RESPONSE_SINGLE_PAGE)
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert result[0].sport == "tennis"


def test_round_info_without_name_uses_round_number():
    """When roundInfo has no 'name', it falls back to 'Round X' format."""
    event_no_round_name = {**FAKE_EVENT, "roundInfo": {"round": 5}}
    session = FakeSession({
        "events": [event_no_round_name],
        "hasNextPage": False
    })
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert result[0].stage == "Round 5"


def test_no_round_info_uses_tournament_name():
    """When roundInfo is absent, stage falls back to tournament name."""
    event_no_round = {**FAKE_EVENT, "roundInfo": None}
    session = FakeSession({
        "events": [event_no_round],
        "hasNextPage": False
    })
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert result[0].stage == "ATP Ultra Finals, Mars"


def test_returns_none_on_empty_near_response():
    """Returns None when 'near' endpoint returns an empty response."""
    session = FakeSession(
        "",
        ""
    )
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert result is None  # a failed fetch, not an empty schedule


def test_returns_none_on_invalid_json_in_pagination():
    """Returns None when a paginated response contains invalid JSON."""
    session = FakeSession(
        FAKE_NEXT_RESPONSE_PAGE_1,
        INVALID_JSON
    )
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert result is None  # a failed fetch, not an empty schedule


def test_returns_none_on_invalid_json_from_near_endpoint():
    """Returns None when 'near' endpoint returns invalid JSON."""
    session = FakeSession(
        "",
        INVALID_JSON
    )
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert result is None  # a failed fetch, not an empty schedule


def test_increments_tracker_on_successful_request():
    """Increments the tracker on each successful API call."""
    mock_tracker = MagicMock()
    session = FakeSession(FAKE_NEXT_RESPONSE_SINGLE_PAGE)
    get_next_matches(make_client(session, mock_tracker), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    mock_tracker.increment.assert_called_once()


def test_does_not_increment_tracker_on_connection_error():
    """Does not increment tracker when a network error occurs."""
    mock_tracker = MagicMock()
    session = FakeSession(requests.exceptions.ConnectionError)
    get_next_matches(make_client(session, mock_tracker), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    mock_tracker.increment.assert_not_called()


def test_returns_none_on_invalid_response_structure():
    """Returns None when API response has valid JSON but unexpected structure."""
    session = FakeSession({"unexpected_key": "unexpected_value"})
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert result is None  # a failed fetch, not an empty schedule


def test_skips_cancelled_matches():
    """Does not create Match objects for events with status type 'canceled'."""
    cancelled_event = {**FAKE_EVENT, "status": {"type": "canceled"}}
    session = FakeSession({"events": [cancelled_event], "hasNextPage": False})
    result = get_next_matches(make_client(session), "4819", "Argentina", "team", "football", "UTC")

    assert result == []


def test_includes_notstarted_matches():
    """Includes events with status type 'notstarted'."""
    session = FakeSession(FAKE_NEXT_RESPONSE_SINGLE_PAGE)
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert len(result) == 1


def test_includes_matches_with_no_status():
    """The status is optional in the API response; only an event marked 'canceled' is skipped."""
    event_with_no_status = {key: value for key, value in FAKE_EVENT.items() if key != "status"}
    session = FakeSession({"events": [event_with_no_status], "hasNextPage": False})
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert [match.game_id for match in result] == ["1"]


def test_near_endpoint_returns_none_on_invalid_structure(caplog):
    """Returns None and logs an error when the 'near' event fails validation."""
    session = FakeSession(
        "",
        {"nextEvent": {"id": 1}}  # event without its required fields
    )
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert result is None  # a failed fetch, not an empty schedule
    assert "Unexpected response structure from tennis/player/206570/events/near" in caplog.text

def test_returns_none_on_empty_later_page():
    """An empty body after page 0 is an error, so the partial result is dropped."""
    session = FakeSession(FAKE_NEXT_RESPONSE_PAGE_1, "")
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert result is None  # a failed fetch, not an empty schedule


def test_returns_none_on_near_connection_error():
    """A connection error on the 'near' fallback returns an empty list."""
    session = FakeSession("", requests.exceptions.ConnectionError())
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert result is None  # a failed fetch, not an empty schedule


def test_near_endpoint_accepts_a_list_of_events():
    """The 'near' fallback also works when nextEvent is a list."""
    session = FakeSession("", {"nextEvent": [FAKE_EVENT, {**FAKE_EVENT, "id": 2}]})
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert [m.game_id for m in result] == ["1", "2"]


def test_counts_one_api_call_per_request():
    """Each page and the 'near' fallback count as one API call each."""
    paged_tracker, fallback_tracker = MagicMock(), MagicMock()
    session = FakeSession(FAKE_NEXT_RESPONSE_PAGE_1, FAKE_NEXT_RESPONSE_PAGE_2)
    get_next_matches(make_client(session, paged_tracker), "206570", "Jannik Sinner", "player", "tennis", "UTC")
    session = FakeSession("", FAKE_NEAR_RESPONSE)
    get_next_matches(make_client(session, fallback_tracker), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert paged_tracker.increment.call_count == 2
    assert fallback_tracker.increment.call_count == 2


def test_converts_start_time_to_given_time_zone():
    """The match start is the API timestamp, expressed in the requested time zone."""
    session = FakeSession(FAKE_NEXT_RESPONSE_SINGLE_PAGE)
    result = get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "America/Argentina/Buenos_Aires")

    start = result[0].start_time
    assert start.timestamp() == FAKE_EVENT["startTimestamp"]
    assert start.utcoffset() == datetime.timedelta(hours=-3)


@pytest.mark.parametrize("sport, player_type, entity_id, endpoint, page, expected_path", [
    ("football", "team", "4819", "next", 0, "team/4819/matches/next/0"),          # football has no sport prefix
    ("tennis", "player", "206570", "next", 2, "tennis/player/206570/events/next/2"),  # tennis uses 'events'
    ("basketball", "team", "3427", "near", None, "basketball/team/3427/matches/near"),
])
def test_build_api_path(sport, player_type, entity_id, endpoint, page, expected_path):
    """Paths follow the AllSports rules for sport prefix, event type and page."""
    assert _build_api_path(sport, player_type, entity_id, endpoint, page) == expected_path


@pytest.mark.parametrize("near_body", [{}, {"nextEvent": None}, {"previousEvent": FAKE_EVENT}])
def test_returns_empty_list_when_near_has_no_next_event(near_body, caplog):
    """The usual off-season case: 'next' is empty and 'near' has no next event."""
    session = FakeSession("", near_body)
    result = get_next_matches(make_client(session), "3452", "Indiana Fever", "team", "basketball", "UTC")

    assert result == []
    assert "No upcoming games found using any endpoints" in caplog.text


def test_logs_one_summary_line_instead_of_every_match(caplog):
    """A full NBA season is ~65 matches; INFO gets one line with the count and the next match, the rest is debug."""
    second = {**FAKE_EVENT, "id": 2, "startTimestamp": FAKE_EVENT["startTimestamp"] + 86400}
    session = FakeSession({"events": [second, FAKE_EVENT], "hasNextPage": False})

    get_next_matches(make_client(session), "206570", "Jannik Sinner", "player", "tennis", "UTC")

    assert "Found 2 upcoming matches; next: Carlos Alcaraz vs Jannik Sinner" in caplog.text
    assert caplog.text.count(" vs Jannik Sinner") == 1  # the per-match lines are not at INFO
