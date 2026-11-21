import pytest
from pathlib import Path
from unittest.mock import MagicMock
from google.auth.exceptions import RefreshError
from functions.check_calendar_tokens import check_calendar_tokens, log_in, log_in_if_needed, LoginRequired
from tests.fakes import FakeAuth


def make_creds(valid: bool, token_json: str = '{"token": "x"}') -> MagicMock:
    """Credentials as the auth library returns them: valid ones, or expired ones with a refresh token."""
    creds = MagicMock()
    creds.valid = valid
    creds.expired = not valid
    creds.refresh_token = None if valid else "some_refresh_token"
    creds.to_json.return_value = token_json
    return creds


@pytest.fixture
def paths(tmp_path):
    """Token and client-secret paths in a temporary folder (the token file does not exist yet)."""
    return {"token_path": str(tmp_path / "token.json"), "credentials_path": str(tmp_path / "credentials.json")}


# --- check_calendar_tokens: load and refresh only, never a browser ---

def test_returns_valid_creds_when_token_exists(paths):
    """Valid stored credentials are returned as they are, with no refresh, flow or file write."""
    Path(paths["token_path"]).write_text("stored")
    creds = make_creds(valid=True)
    auth = FakeAuth(stored=creds)

    result = check_calendar_tokens(paths["token_path"], auth=auth)

    assert result is creds
    assert auth.loaded == [paths["token_path"]]
    assert auth.refreshed == [] and auth.flows == []
    assert Path(paths["token_path"]).read_text() == "stored"


def test_refreshes_expired_creds_and_saves_them(paths):
    """Expired credentials with a refresh token are refreshed and written back to the token file."""
    Path(paths["token_path"]).write_text("stored")
    creds = make_creds(valid=False, token_json='{"token": "refreshed"}')
    auth = FakeAuth(stored=creds)

    result = check_calendar_tokens(paths["token_path"], auth=auth)

    assert result is creds
    assert auth.refreshed == [creds]
    assert auth.flows == []
    assert Path(paths["token_path"]).read_text() == '{"token": "refreshed"}'


def test_a_refused_refresh_asks_for_a_login_and_keeps_the_token_file(paths):
    """On the server a browser login would wait forever, so a refused refresh raises instead.

    The token file is kept: the refusal can be temporary, and the next start tries it again.
    """
    Path(paths["token_path"]).write_text("stored")
    auth = FakeAuth(stored=make_creds(valid=False), refresh_error=RefreshError("invalid_grant: Token has been expired or revoked."))

    with pytest.raises(LoginRequired, match="invalid_grant"):
        check_calendar_tokens(paths["token_path"], auth=auth)

    assert auth.flows == []
    assert Path(paths["token_path"]).read_text() == "stored"


def test_no_token_file_asks_for_a_login(paths):
    """With no token file, nothing is loaded and no browser opens."""
    auth = FakeAuth()

    with pytest.raises(LoginRequired, match="no token file"):
        check_calendar_tokens(paths["token_path"], auth=auth)

    assert auth.loaded == [] and auth.flows == []


def test_a_token_without_a_refresh_token_asks_for_a_login(paths):
    """Credentials that are not valid and cannot be refreshed need a new login."""
    Path(paths["token_path"]).write_text("stored")
    creds = make_creds(valid=False)
    creds.refresh_token = None
    auth = FakeAuth(stored=creds)

    with pytest.raises(LoginRequired, match="cannot be refreshed"):
        check_calendar_tokens(paths["token_path"], auth=auth)

    assert auth.refreshed == [] and auth.flows == []


def test_non_refresh_error_propagates(paths):
    """An error that is not a RefreshError (e.g. no network) is raised and the token file is kept."""
    Path(paths["token_path"]).write_text("stored")
    auth = FakeAuth(stored=make_creds(valid=False), refresh_error=OSError("Network error"))

    with pytest.raises(OSError, match="Network error"):
        check_calendar_tokens(paths["token_path"], auth=auth)

    assert Path(paths["token_path"]).read_text() == "stored"
    assert auth.flows == []


# --- log_in and log_in_if_needed: the browser login, for a computer with a browser ---

def test_log_in_runs_the_browser_flow_and_saves_the_token(paths):
    """The new credentials are saved, so the service (here or on the server) can use the file."""
    new_creds = make_creds(valid=True, token_json='{"token": "new"}')
    auth = FakeAuth(flow_creds=new_creds)

    result = log_in(**paths, auth=auth)

    assert result is new_creds
    assert auth.flows == [paths["credentials_path"]]
    assert Path(paths["token_path"]).read_text() == '{"token": "new"}'


def test_log_in_if_needed_keeps_a_valid_token(paths):
    """A token that is valid (or refreshes) is used as it is: no browser opens."""
    Path(paths["token_path"]).write_text("stored")
    creds = make_creds(valid=True)
    auth = FakeAuth(stored=creds)

    assert log_in_if_needed(**paths, auth=auth) is creds
    assert auth.flows == []


def test_log_in_if_needed_opens_the_browser_when_the_token_is_refused(paths):
    """A refused refresh leads to the browser login, which replaces the token file."""
    Path(paths["token_path"]).write_text("stored")
    new_creds = make_creds(valid=True, token_json='{"token": "from flow"}')
    auth = FakeAuth(stored=make_creds(valid=False), refresh_error=RefreshError("Refresh failed"), flow_creds=new_creds)

    assert log_in_if_needed(**paths, auth=auth) is new_creds
    assert auth.flows == [paths["credentials_path"]]
    assert Path(paths["token_path"]).read_text() == '{"token": "from flow"}'
