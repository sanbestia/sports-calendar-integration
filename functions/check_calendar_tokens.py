# check validity of stored tokens
# if not present, create token file

import logging
import os.path

from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# If modifying these scopes, delete the file token.json.
SCOPES: list[str] = ["https://www.googleapis.com/auth/calendar"]

logger = logging.getLogger(__name__)


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


def check_calendar_tokens(token_path: str = "token.json", credentials_path: str = "credentials.json",
                          auth: GoogleAuth | None = None) -> Credentials:
    """Return valid Google Calendar credentials, refreshing or re-authorising as needed."""
    auth = auth or GoogleAuth()
    logger.info("Checking tokens...")
    creds = None
    # The token file stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first time.
    if os.path.exists(token_path):
        creds = auth.load(token_path)
        logger.info("Tokens exist")
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        logger.info("No valid tokens available, creating new ones")
        refreshed = False
        if creds and creds.expired and creds.refresh_token:
            try:
                auth.refresh(creds)
                logger.info("Creds refreshed.")
                refreshed = True
            except RefreshError as e:
                logger.error("Failed to refresh non-valid tokens.")
                logger.error(e)
                logger.error("Deleting tokens and trying to restart authorization flow...")
                os.remove(token_path)
        if not refreshed:
            creds = auth.run_flow(credentials_path)
        # Save the credentials for the next run
        with open(token_path, "w") as token:
            token.write(creds.to_json())
            logger.info("Tokens created")
    else:
        logger.info("Available tokens are valid")
    logger.info("")
    return creds
