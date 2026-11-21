import pytest
import requests

from objects.Heartbeat import Heartbeat, PING_TIMEOUT_SECONDS
from tests.fakes import FakePost

URL = "https://hc-ping.com/0f1e2d3c-secret-uuid"


def test_ping_posts_to_the_check_url():
    """Each ping tells healthchecks.io that a cycle finished."""
    post = FakePost()

    Heartbeat(URL, post=post).ping()

    assert post.calls == [{"url": URL, "data": b"", "timeout": PING_TIMEOUT_SECONDS}]


def test_fail_posts_the_message_to_the_fail_url():
    """A fail ping makes healthchecks.io alert at once, and its dashboard shows the message."""
    post = FakePost()

    Heartbeat(URL, post=post).fail("Google Calendar login needed")

    assert post.calls == [{"url": f"{URL}/fail", "data": b"Google Calendar login needed", "timeout": PING_TIMEOUT_SECONDS}]


@pytest.mark.parametrize("url", [None, ""])
def test_without_a_url_nothing_is_sent(url):
    """Local runs and tests have no HEALTHCHECK_URL: the heartbeat is off and sends nothing."""
    post = FakePost()
    heartbeat = Heartbeat(url, post=post)

    heartbeat.ping()
    heartbeat.fail("message")

    assert not heartbeat.enabled
    assert post.calls == []


def test_a_failed_ping_is_a_warning_that_does_not_show_the_url(caplog):
    """A ping must never stop the service. requests puts the URL in its error text, and the URL is a secret."""
    post = FakePost(error=requests.ConnectionError(f"Max retries exceeded with url: {URL}"))

    Heartbeat(URL, post=post).ping()

    assert "WARNING" in [r.levelname for r in caplog.records]
    assert "ConnectionError" in caplog.text
    assert "secret-uuid" not in caplog.text


def test_an_error_reply_is_a_warning_with_its_status(caplog):
    """A 404 means the check's URL is wrong; healthchecks.io answers 200 to a good ping."""
    Heartbeat(URL, post=FakePost(status_code=404)).ping()

    assert "WARNING" in [r.levelname for r in caplog.records]
    assert "404" in caplog.text
    assert "secret-uuid" not in caplog.text


def test_a_url_that_is_not_https_is_refused_without_showing_it():
    """A wrong value in .env stops the start, and the error does not print the value."""
    with pytest.raises(ValueError) as error:
        Heartbeat("hc-ping.com/secret-uuid")

    assert "secret-uuid" not in str(error.value)
