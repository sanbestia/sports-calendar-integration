import pytest
import requests
from unittest.mock import MagicMock

from sports_calendar.allsports.client import AllSportsClient, AllSportsError, BASE_URL
from sports_calendar.allsports.schemas import SearchResponse
from tests.fakes import FakeSession, make_client

ONE_RESULT = {"results": [{"entity": {"id": 3427, "name": "Los Angeles Lakers", "gender": "M"}}]}


def test_refuses_to_start_without_api_key():
    """A missing key stops the service at startup, before any request."""
    session = FakeSession()
    with pytest.raises(ValueError, match="RAPIDAPI_KEY"):
        AllSportsClient(api_key="", tracker=MagicMock(), session=session)
    assert session.requests == []


def test_sends_key_host_and_timeout_to_full_url():
    """Every request goes to BASE_URL + path with the RapidAPI headers and a 10 s timeout."""
    session = FakeSession(ONE_RESULT)
    AllSportsClient(api_key="secret", tracker=MagicMock(), session=session).get("basketball/search/lakers", SearchResponse)

    request = session.requests[0]
    assert request["url"] == BASE_URL + "basketball/search/lakers"
    assert request["headers"] == {"X-RapidAPI-Key": "secret", "X-RapidAPI-Host": "allsportsapi2.p.rapidapi.com"}
    assert request["timeout"] == 10


def test_returns_validated_model():
    """A JSON body comes back as the requested model."""
    result = make_client(FakeSession(ONE_RESULT)).get("search/lakers", SearchResponse)

    assert result.results[0].entity.name == "Los Angeles Lakers"


def test_returns_none_for_empty_body_and_counts_the_call():
    """An empty body means 'no results', and it still used one API call."""
    tracker = MagicMock()
    result = make_client(FakeSession(""), tracker).get("search/zzz", SearchResponse)

    assert result is None
    tracker.increment.assert_called_once()


@pytest.mark.parametrize("failure", [requests.exceptions.ConnectionError, requests.exceptions.Timeout])
def test_failed_request_raises_and_is_not_counted(failure):
    """A request that never reached the API raises AllSportsError and does not use the budget."""
    tracker = MagicMock()
    with pytest.raises(AllSportsError, match="Couldn't communicate with sports API"):
        make_client(FakeSession(failure), tracker).get("search/lakers", SearchResponse)

    tracker.increment.assert_not_called()


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
