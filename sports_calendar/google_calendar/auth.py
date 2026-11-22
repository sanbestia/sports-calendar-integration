# check validity of stored tokens
# a new token comes only from the browser login: `uv run python -m sports_calendar.google_calendar.auth`

import logging
import os.path

from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from sports_calendar.log_setup import configure_logging

# If modifying these scopes, delete the file token.json.
SCOPES: list[str] = ["https://www.googleapis.com/auth/calendar"]

logger = logging.getLogger(__name__)


class LoginRequired(Exception):
    """The stored Google token is missing or cannot be refreshed: a person must log in again in a browser."""


# The errors that mean "no valid token". A RefreshError can also come later, from a Calendar call whose
# automatic token refresh fails.
TOKEN_ERRORS: tuple[type[Exception], ...] = (LoginRequired, RefreshError)


class GoogleAuth:
    """The three calls into Google's auth libraries. Tests pass a fake with the same methods."""

    def load(self, token_path: str) -> Credentials:
        """Read stored credentials from the token file."""
        return Credentials.from_authorized_user_file(filename=token_path, scopes=SCOPES)

    def refresh(self, creds: Credentials) -> None:
        """Get a new access token with the stored refresh token (raises RefreshError if Google refuses)."""
        creds.refresh(Request())

    def run_flow(self, credentials_path: str) -> Credentials:
        """Run the browser consent flow and return new credentials."""
        flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file=credentials_path, scopes=SCOPES)
        return flow.run_local_server(port=0)


def _save(creds: Credentials, token_path: str) -> None:
    """Write the credentials to the token file, for the next start."""
    with open(token_path, "w") as token:
        token.write(creds.to_json())


def check_calendar_tokens(token_path: str = "token.json", auth: GoogleAuth | None = None) -> Credentials:
    """Return valid Google Calendar credentials from the token file, refreshing and saving them if they expired.

    Raises LoginRequired when there is no token file or the token cannot be refreshed. It never opens a
    browser: on the server, the login flow would wait forever for a person who is not there.
    """
    auth = auth or GoogleAuth()
    logger.info("Checking tokens...")
    if not os.path.exists(token_path):
        raise LoginRequired(f"no token file ({token_path})")
    creds = auth.load(token_path)
    if creds.valid:
        logger.info("Available tokens are valid")
        return creds
    if not (creds.expired and creds.refresh_token):
        raise LoginRequired("the stored token cannot be refreshed")
    try:
        auth.refresh(creds)
    except RefreshError as e:  # the file is kept: the refusal can be temporary, and the next start tries again
        reason = e.args[0] if e.args else e  # str(e) would print the whole tuple of arguments
        raise LoginRequired(f"Google refused to refresh the token: {reason}") from e
    _save(creds, token_path)
    logger.info("Creds refreshed.")
    return creds


def log_in(token_path: str = "token.json", credentials_path: str = "credentials.json",
           auth: GoogleAuth | None = None) -> Credentials:
    """Open the browser login, then save the new token. Run it on a computer with a browser."""
    auth = auth or GoogleAuth()
    creds = auth.run_flow(credentials_path)
    _save(creds, token_path)
    logger.info(f"Logged in: saved a new token to {token_path}")
    return creds


def log_in_if_needed(token_path: str = "token.json", credentials_path: str = "credentials.json",
                     auth: GoogleAuth | None = None) -> Credentials:
    """Check the stored token, and open the browser login only when the check asks for a login."""
    try:
        return check_calendar_tokens(token_path, auth)
    except LoginRequired as e:
        logger.info(f"Login needed: {e}. Opening the browser login...")
        return log_in(token_path, credentials_path, auth)


if __name__ == "__main__":
    configure_logging()
    log_in_if_needed()
