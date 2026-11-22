import time
from datetime import date, datetime, timezone


class Clock:
    """The real source of time.

    Code that reads the time or waits takes a Clock, so tests can pass a fake one instead of patching.
    """

    def now(self) -> datetime:
        """Return the current time as an aware datetime in UTC."""
        return datetime.now(timezone.utc)

    def today(self) -> date:
        """Return today's date in the machine's local time zone."""
        return date.today()

    def sleep(self, seconds: float) -> None:
        """Block for the given number of seconds."""
        time.sleep(seconds)
