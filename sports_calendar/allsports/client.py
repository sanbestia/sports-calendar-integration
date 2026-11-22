import json
import logging
from typing import TypeVar

import requests
from pydantic import BaseModel, ValidationError

from sports_calendar.storage.api_calls import APICallTracker
from sports_calendar.utils import sanitize_for_log

logger = logging.getLogger(__name__)

BASE_URL = "https://allsportsapi2.p.rapidapi.com/api/"
_HOST = "allsportsapi2.p.rapidapi.com"
_TIMEOUT_SECONDS = 10

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class AllSportsError(Exception):
    """A request to the AllSports API failed, or its response could not be read."""


class AllSportsClient:
    """The one place that talks to the AllSports API (through RapidAPI).

    It holds the API key and one reused HTTP session, counts every request that reaches the API,
    and turns a response body into a validated model. Callers decide what an empty body means.
    """

    def __init__(self, api_key: str, tracker: APICallTracker, session: requests.Session | None = None):
        """Fail fast when the key is missing, so a bad deploy stops at startup instead of at the first fetch."""
        if not api_key:
            raise ValueError("RAPIDAPI_KEY environment variable is not set")
        self._headers = {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": _HOST}
        self._tracker = tracker
        self._session = session if session is not None else requests.Session()  # reuses the TLS connection across pages
        self.calls_made = 0  # requests that reached the API since this client was created (never resets, unlike the daily count)

    def get(self, path: str, model: type[ResponseModel]) -> ResponseModel | None:
        """GET BASE_URL + path and validate the JSON body as `model`.

        Returns None when the body is empty (the API's way to say "no results").
        Raises AllSportsError when the request fails or the body is not the expected JSON.
        """
        try:
            response = self._session.get(BASE_URL + path, headers=self._headers, timeout=_TIMEOUT_SECONDS)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            raise AllSportsError(f"Couldn't communicate with sports API ({path}): {e}") from e
        self._tracker.increment()  # count only requests that reached the API
        self.calls_made += 1

        if not response.text:
            return None
        try:
            return model.model_validate(json.loads(response.text))
        except json.JSONDecodeError as e:
            raise AllSportsError(f"Invalid JSON response from {path}: {sanitize_for_log(response.text[:200])}") from e
        except ValidationError as e:
            raise AllSportsError(f"Unexpected response structure from {path}: {e}") from e
