import pytest
from pathlib import Path
from unittest.mock import MagicMock
from google.auth.exceptions import RefreshError
from functions.check_calendar_tokens import check_calendar_tokens
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


def test_returns_valid_creds_when_token_exists(paths):
    """Valid stored credentials are returned as they are, with no refresh, flow or file write."""
    Path(paths["token_path"]).write_text("stored")
    creds = make_creds(valid=True)
    auth = FakeAuth(stored=creds)

    result = check_calendar_tokens(**paths, auth=auth)

    assert result is creds
    assert auth.loaded == [paths["token_path"]]
    assert auth.refreshed == [] and auth.flows == []
    assert Path(paths["token_path"]).read_text() == "stored"


def test_refreshes_expired_creds_and_saves_them(paths):
    """Expired credentials with a refresh token are refreshed and written back to the token file."""
    Path(paths["token_path"]).write_text("stored")
    creds = make_creds(valid=False, token_json='{"token": "refreshed"}')
    auth = FakeAuth(stored=creds)

    result = check_calendar_tokens(**paths, auth=auth)

    assert result is creds
    assert auth.refreshed == [creds]
    assert auth.flows == []
    assert Path(paths["token_path"]).read_text() == '{"token": "refreshed"}'


def test_deletes_token_and_runs_flow_when_refresh_fails(paths):
    """When Google refuses the refresh, the old token is dropped and the consent flow runs."""
    Path(paths["token_path"]).write_text("stored")
    new_creds = make_creds(valid=True, token_json='{"token": "from flow"}')
    auth = FakeAuth(stored=make_creds(valid=False), refresh_error=RefreshError("Refresh failed"), flow_creds=new_creds)

    result = check_calendar_tokens(**paths, auth=auth)

    assert result is new_creds
    assert auth.flows == [paths["credentials_path"]]
    assert Path(paths["token_path"]).read_text() == '{"token": "from flow"}'


def test_runs_auth_flow_when_no_token_file(paths):
    """With no token file, the stored credentials are not read and the consent flow runs."""
    new_creds = make_creds(valid=True)
    auth = FakeAuth(flow_creds=new_creds)

    result = check_calendar_tokens(**paths, auth=auth)

    assert result is new_creds
    assert auth.loaded == []
    assert auth.flows == [paths["credentials_path"]]


def test_saves_token_file_after_auth_flow(paths):
    """The credentials from the flow are saved, so the next start does not need the browser."""
    auth = FakeAuth(flow_creds=make_creds(valid=True, token_json='{"token": "new"}'))

    check_calendar_tokens(**paths, auth=auth)

    assert Path(paths["token_path"]).read_text() == '{"token": "new"}'


def test_non_refresh_error_propagates(paths):
    """An error that is not a RefreshError (e.g. no network) is raised and the token file is kept."""
    Path(paths["token_path"]).write_text("stored")
    auth = FakeAuth(stored=make_creds(valid=False), refresh_error=OSError("Network error"))

    with pytest.raises(OSError, match="Network error"):
        check_calendar_tokens(**paths, auth=auth)

    assert Path(paths["token_path"]).read_text() == "stored"
    assert auth.flows == []
