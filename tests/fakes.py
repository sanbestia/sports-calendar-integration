"""Small fakes that tests pass in through dependency injection, instead of patching module globals."""
import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

from objects.AllSportsClient import AllSportsClient


class FakeClock:
    """A Clock whose time moves only when the code sleeps or the test calls advance()."""

    def __init__(self, start: datetime = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)):
        self._now = start
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.date()

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += timedelta(seconds=seconds)

    def advance(self, **delta: float) -> None:
        """Move time forward, e.g. advance(days=1)."""
        self._now += timedelta(**delta)


class ScriptedInput:
    """Stands in for input(): returns the given answers in order and records each prompt."""

    def __init__(self, *answers: str):
        self._answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        if not self._answers:
            raise AssertionError(f"No scripted answer left for prompt: {prompt!r}")
        return self._answers.pop(0)


class FakeResponse:
    """The part of requests.Response that the client reads."""

    def __init__(self, text: str):
        self.text = text


class FakeSession:
    """Stands in for requests.Session: returns queued replies in order and records each request.

    A reply is a dict (sent as its JSON), a str (sent as the raw body; "" is an empty body),
    or an exception instance or class (raised, as a failed request would).
    """

    def __init__(self, *replies):
        self._replies = list(replies)
        self.requests: list[dict] = []

    @property
    def urls(self) -> list[str]:
        return [r["url"] for r in self.requests]

    def get(self, url: str, headers: dict | None = None, timeout: float | None = None) -> FakeResponse:
        self.requests.append({"url": url, "headers": headers, "timeout": timeout})
        if not self._replies:
            raise AssertionError(f"Unexpected request: {url}")
        reply = self._replies.pop(0)
        if isinstance(reply, BaseException) or (isinstance(reply, type) and issubclass(reply, BaseException)):
            raise reply
        return FakeResponse(reply if isinstance(reply, str) else json.dumps(reply))


def make_client(session: FakeSession, tracker=None) -> AllSportsClient:
    """An AllSportsClient with a fake key, the given fake session, and a mock call tracker by default."""
    return AllSportsClient(api_key="test-key", tracker=tracker if tracker is not None else MagicMock(), session=session)
