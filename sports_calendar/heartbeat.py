import logging
from collections.abc import Callable
from typing import Any

import requests

logger = logging.getLogger(__name__)

PING_TIMEOUT_SECONDS = 10


class Heartbeat:
    """Pings a healthchecks.io check. When the pings stop, or a fail ping comes, healthchecks.io sends an alert.

    With no URL it does nothing (local runs, tests). The URL is a secret, because anyone who has it can ping
    the check, so it is never logged.
    """

    def __init__(self, url: str | None, post: Callable[..., Any] = requests.post):
        if url and not url.startswith("https://"):
            raise ValueError("HEALTHCHECK_URL must start with https://")  # no value in the text: it is a secret
        self._url = url or None
        self._post = post

    @property
    def enabled(self) -> bool:
        return self._url is not None

    def ping(self) -> None:
        """Tell the check that a cycle finished."""
        self._send("", "")

    def fail(self, message: str) -> None:
        """Tell the check that the service failed. healthchecks.io alerts at once and shows the message."""
        self._send("/fail", message)

    def _send(self, suffix: str, body: str) -> None:
        """POST the body to the check URL plus suffix. A failure is only a warning: a ping must never stop the service."""
        if not self.enabled:
            return
        try:
            response = self._post(f"{self._url}{suffix}", data=body.encode(), timeout=PING_TIMEOUT_SECONDS)
        except requests.RequestException as e:
            logger.warning(f"Heartbeat ping failed ({type(e).__name__})")  # not the error text: requests puts the URL in it
            return
        if response.status_code != 200:
            logger.warning(f"Heartbeat ping got HTTP {response.status_code}")
