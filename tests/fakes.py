"""Small fakes that tests pass in through dependency injection, instead of patching module globals."""
from datetime import date, datetime, timedelta, timezone


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
