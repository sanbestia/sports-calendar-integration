import logging
import signal
import subprocess

import pytest

from sports_calendar.google_calendar.auth import LoginRequired
from sports_calendar.heartbeat import Heartbeat
from sports_calendar.service import current_version, install_stop_handler, make_heartbeat, stop_for_login
from tests.fakes import FakePost

PING_URL = "https://hc-ping.com/0f1e2d3c-secret-uuid"


# --- version ---

def test_current_version_is_the_short_git_commit():
    def fake_git(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="ceb2b35\n", stderr="")
    assert current_version(run_command=fake_git) == "ceb2b35"


@pytest.mark.parametrize("failure", [FileNotFoundError("git"), subprocess.CalledProcessError(128, "git")])
def test_current_version_is_unknown_without_git(failure):
    """A copy without .git or without the git command still starts."""
    def failing_git(*args, **kwargs):
        raise failure
    assert current_version(run_command=failing_git) == "unknown"


# --- clean stop on SIGTERM ---


def test_installs_a_sigterm_handler():
    """systemd stops the service with SIGTERM on every deploy, reboot and restart."""
    registered = {}
    install_stop_handler(register=lambda signum, handler: registered.setdefault(signum, handler))

    assert list(registered) == [signal.SIGTERM]


def test_sigterm_handler_exits_with_code_0(caplog):
    """Exit code 0 means systemd logs a clean stop, not 'Failed with result exit-code' (status 143)."""
    registered = {}
    install_stop_handler(register=lambda signum, handler: registered.setdefault(signum, handler))

    with pytest.raises(SystemExit) as stop:
        registered[signal.SIGTERM](signal.SIGTERM, None)

    assert stop.value.code == 0
    assert "Stopping: received SIGTERM" in caplog.text


# --- login alert and heartbeat setup ---

def test_stop_for_login_logs_the_steps_sends_a_fail_ping_and_exits_1(caplog):
    """Exit code 1 marks the run as failed; the timer starts the next one within 15 minutes, so the service
    recovers once a new token.json is in place."""
    post = FakePost()

    with pytest.raises(SystemExit) as stop:
        stop_for_login(LoginRequired("no token file (token.json)"), Heartbeat(PING_URL, post=post))

    assert stop.value.code == 1
    error_lines = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert len(error_lines) == 1
    assert "no token file" in error_lines[0]
    assert "uv run google-login" in error_lines[0]
    assert [c["url"] for c in post.calls] == [f"{PING_URL}/fail"]
    assert post.calls[0]["data"].decode() == error_lines[0]


@pytest.mark.parametrize("env, enabled, line, level", [
    ({"HEALTHCHECK_URL": PING_URL}, True, "Heartbeat on", "DEBUG"),
    ({}, False, "Heartbeat off", "INFO"),
])
def test_make_heartbeat_logs_whether_it_is_on_but_never_the_url(caplog, env, enabled, line, level):
    """The line shows whether the .env has the URL; "on" is DEBUG, because every run logs it. The URL is a secret."""
    caplog.set_level(logging.DEBUG)
    heartbeat = make_heartbeat(env)

    assert heartbeat.enabled is enabled
    (record,) = [r for r in caplog.records if r.getMessage().startswith(line)]
    assert record.levelname == level
    assert "secret-uuid" not in caplog.text
