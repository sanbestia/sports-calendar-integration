import json

import pytest
import requests

from sports_calendar.allsports.client import AllSportsClient, AllSportsError, BASE_URL
from sports_calendar.allsports.schemas import SearchResponse
from sports_calendar.storage.api_calls import APICallTracker
from tests.fakes import FakeClock, FakeResponse, FakeSession, make_client, mock_tracker

ONE_RESULT = {"results": [{"entity": {"id": 3427, "name": "Los Angeles Lakers", "gender": "M"}}]}


@pytest.fixture
def tracker():
    """A real call tracker on in-memory SQLite, so a test reads the stored count that the next run and the daily summary read."""
    tracker = APICallTracker(":memory:", clock=FakeClock())
    yield tracker
    tracker.close()


def test_refuses_to_start_without_api_key():
    """A missing key stops the service at startup, before any request."""
    session = FakeSession()
    with pytest.raises(ValueError, match="RAPIDAPI_KEY"):
        AllSportsClient(api_key="", tracker=mock_tracker(), session=session)
    assert session.requests == []


def test_sends_key_host_and_timeout_to_full_url():
    """Every request goes to BASE_URL + path with the RapidAPI headers and a 10 s timeout."""
    session = FakeSession(ONE_RESULT)
    AllSportsClient(api_key="secret", tracker=mock_tracker(), session=session).get("basketball/search/lakers", SearchResponse)

    request = session.requests[0]
    assert request["url"] == BASE_URL + "basketball/search/lakers"
    assert request["headers"] == {"X-RapidAPI-Key": "secret", "X-RapidAPI-Host": "allsportsapi2.p.rapidapi.com"}
    assert request["timeout"] == 10


def test_returns_validated_model():
    """A JSON body comes back as the requested model."""
    result = make_client(FakeSession(ONE_RESULT)).get("search/lakers", SearchResponse)

    assert result.results[0].entity.name == "Los Angeles Lakers"


def test_returns_none_for_empty_body_and_counts_the_call(tracker):
    """An empty 204 is the API's answer for 'no results', and it still used one API call."""
    result = make_client(FakeSession(FakeResponse("", status_code=204)), tracker).get("search/zzz", SearchResponse)

    assert result is None
    assert tracker.calls_on(tracker.current_date) == 1


QUOTA_EXCEEDED = json.dumps({"message": "You have exceeded the DAILY quota for Requests on your current plan, BASIC, by the provider."})


@pytest.mark.parametrize("status, body, error", [
    (503, "", "HTTP 503 from search/lakers"),
    (429, QUOTA_EXCEEDED, "HTTP 429 from search/lakers: .*DAILY quota"),
])
def test_error_status_raises_and_is_counted(status, body, error, tracker):
    """A status other than 2xx is an error, also when its body is empty or is JSON that the model accepts, and it used one API call."""
    with pytest.raises(AllSportsError, match=error):
        make_client(FakeSession(FakeResponse(body, status_code=status)), tracker).get("search/lakers", SearchResponse)

    assert tracker.calls_on(tracker.current_date) == 1


@pytest.mark.parametrize("failure", [requests.exceptions.ConnectionError, requests.exceptions.ConnectTimeout])
def test_failed_request_raises_and_is_not_counted(failure):
    """A request that never reached the API raises AllSportsError and does not use the budget."""
    tracker = mock_tracker()
    with pytest.raises(AllSportsError, match="Couldn't communicate with sports API"):
        make_client(FakeSession(failure), tracker).get("search/lakers", SearchResponse)

    tracker.increment.assert_not_called()


def test_a_read_timeout_raises_and_is_counted(tracker):
    """The request reached the API, which counts it, so the budget counts it too."""
    with pytest.raises(AllSportsError, match="Couldn't communicate with sports API"):
        make_client(FakeSession(requests.exceptions.ReadTimeout), tracker).get("search/lakers", SearchResponse)

    assert tracker.calls_on(tracker.current_date) == 1


@pytest.mark.parametrize("headers, stored_count", [
    ({"x-ratelimit-requests-limit": "100", "x-ratelimit-requests-remaining": "60"}, 40),  # RapidAPI saw 40 calls today
    ({}, 1),
    ({"x-ratelimit-requests-limit": "100", "x-ratelimit-requests-remaining": "many"}, 1),
])
def test_the_count_that_rapidapi_sends_raises_the_local_count(headers, stored_count, tracker):
    reply = FakeResponse(json.dumps(ONE_RESULT), headers=headers)
    make_client(FakeSession(reply), tracker).get("search/lakers", SearchResponse)

    assert tracker.calls_on(tracker.current_date) == stored_count


def test_no_request_is_sent_once_the_daily_limit_is_reached(tracker):
    """The run checks the limit only before each entity; one entity's pages must not go past it either."""
    for _ in range(tracker.daily_limit):
        tracker.increment()
    session = FakeSession(ONE_RESULT)

    with pytest.raises(AllSportsError, match="daily API limit"):
        make_client(session, tracker).get("search/lakers", SearchResponse)

    assert session.requests == []
    assert tracker.calls_on(tracker.current_date) == tracker.daily_limit


def test_invalid_json_raises_with_path():
    """A body that is not JSON raises AllSportsError naming the path."""
    with pytest.raises(AllSportsError, match="Invalid JSON response from search/lakers"):
        make_client(FakeSession("<html>Internal Server Error</html>")).get("search/lakers", SearchResponse)


def test_unexpected_structure_raises_with_path():
    """JSON that does not match the model raises AllSportsError naming the path."""
    with pytest.raises(AllSportsError, match="Unexpected response structure from search/lakers"):
        make_client(FakeSession({"results": [{"no_entity": {}}]})).get("search/lakers", SearchResponse)


def test_reuses_one_session_for_all_requests():
    """All requests go through the injected session (one TLS connection in production)."""
    session = FakeSession(ONE_RESULT, ONE_RESULT)
    client = make_client(session)
    client.get("search/a", SearchResponse)
    client.get("search/b", SearchResponse)

    assert [u.rsplit("/", 1)[1] for u in session.urls] == ["a", "b"]
