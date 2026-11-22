import pytest
from unittest.mock import MagicMock
from googleapiclient.errors import HttpError
from sports_calendar.google_calendar.sync import _execute_with_retry, _MAX_RETRIES
from tests.fakes import FakeClock


def make_request(return_value=None, side_effect=None) -> MagicMock:
    """Helper to create a fake Google API request object."""
    mock = MagicMock()
    if side_effect is not None:
        mock.execute.side_effect = side_effect
    else:
        mock.execute.return_value = return_value
    return mock


def make_http_error(status: int) -> HttpError:
    resp = MagicMock()
    resp.status = status
    resp.status_code = status
    return HttpError(resp=resp, content=b"error")


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


def test_raises_immediately_on_non_retryable_http_error():
    """Does not retry on 403 Forbidden — not a transient error."""
    clock = FakeClock()
    error = make_http_error(403)
    request = make_request(side_effect=error)
    with pytest.raises(HttpError):
        _execute_with_retry(request, clock)
    request.execute.assert_called_once()
    assert clock.sleeps == []


def test_raises_on_broken_pipe_immediately():
    """Propagates BrokenPipeError without retrying — reconnection is the caller's responsibility."""
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
