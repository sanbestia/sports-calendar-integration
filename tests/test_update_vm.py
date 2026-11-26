"""Runs deploy/update-vm.sh with stand-in sudo and uv, against a real git checkout, to prove that a failed deploy
leaves the VM as it was."""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "update-vm.sh"
OLD_QUERIES = {"Old Team": {"id": "1", "sport": "football", "player_type": "team", "calendar": "CALENDAR_ID_FOOTBALL"}}
NEW_QUERIES = {"New Team": {"id": "2", "sport": "football", "player_type": "team", "calendar": "CALENDAR_ID_FOOTBALL"}}

# sudo only logs (no systemd in a test); it fails for the one command that FAIL_SUDO names
FAKE_SUDO = """#!/bin/bash
echo "sudo $*" >> "$LOG"
[ "$*" != "${FAIL_SUDO:-}" ]
"""
# uv sync fails while the checkout is at FAIL_SYNC_AT; uv run ... python runs the real Python
FAKE_UV = """#!/bin/bash
echo "uv $*" >> "$LOG"
if [ "$1" = sync ]; then [ "$(git rev-parse HEAD)" != "${FAIL_SYNC_AT:-}" ]; exit; fi
while [ "$1" != python ]; do shift; done
shift
exec "$REAL_PYTHON" "$@"
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(checkout: Path, version: str) -> str:
    (checkout / "version").write_text(version)
    git(checkout, "add", "version")
    git(checkout, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", version)
    return git(checkout, "rev-parse", "HEAD")


@pytest.fixture
def vm(tmp_path):
    """A checkout at the previous commit whose origin/main has a new one, a state folder, and the stand-ins."""
    origin, checkout, state, bin_dir = (tmp_path / name for name in ("origin.git", "checkout", "state", "bin"))
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", "-q", str(origin), str(checkout))
    previous = commit(checkout, "previous")
    git(checkout, "push", "-q", "origin", "HEAD:main")
    new = commit(checkout, "new")
    git(checkout, "push", "-q", "origin", "HEAD:main")
    git(checkout, "reset", "-q", "--hard", previous)
    state.mkdir()
    (state / "queries.json").write_text(json.dumps(OLD_QUERIES))
    bin_dir.mkdir()
    for name, text in (("sudo", FAKE_SUDO), ("uv", FAKE_UV)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    return {"checkout": checkout, "state": state, "bin": bin_dir, "log": tmp_path / "log", "previous": previous, "new": new}


def deploy(vm, queries: object = NEW_QUERIES, **fail) -> subprocess.CompletedProcess:
    """Pipe the script to bash as the deploy job does: a line that sets the secret, then the script."""
    secret = base64.b64encode(json.dumps(queries).encode()).decode()
    env = {**os.environ, "CHECKOUT": str(vm["checkout"]), "STATE": str(vm["state"]), "UV": str(vm["bin"] / "uv"),
           "PATH": f"{vm['bin']}:{os.environ['PATH']}", "LOG": str(vm["log"]), "REAL_PYTHON": sys.executable, **fail}
    stdin = f"QUERIES_JSON_B64='{secret}'\n" + SCRIPT.read_text()
    return subprocess.run(["bash", "-s"], input=stdin, env=env, capture_output=True, text=True)


def logged(vm) -> list[str]:
    return vm["log"].read_text().splitlines()


def test_a_deploy_stops_the_timer_updates_everything_then_runs_once(vm):
    result = deploy(vm)

    assert result.returncode == 0, result.stderr
    assert git(vm["checkout"], "rev-parse", "HEAD") == vm["new"]
    assert json.loads((vm["state"] / "queries.json").read_text()) == NEW_QUERIES
    log = logged(vm)
    assert log[0] == "sudo systemctl stop sports-calendar.timer sports-calendar.service"
    assert log.index("uv sync --locked --no-dev") < log.index("sudo systemctl daemon-reload")
    assert log[-2:] == ["sudo systemctl enable --now sports-calendar.timer sports-calendar-summary.timer",
                        "sudo systemctl start sports-calendar.service"]
    assert sorted(path.name for path in vm["state"].iterdir()) == ["queries.json"]


@pytest.mark.parametrize("queries, fail", [
    (NEW_QUERIES, {"FAIL_SYNC_AT": "new"}),  # the new uv.lock does not match pyproject.toml
    ({}, {}),  # an empty secret: the service would track nothing and still ping healthy
    ({"New Team": {**NEW_QUERIES["New Team"], "id": 2}}, {}),  # a bad entry
    (NEW_QUERIES, {"FAIL_SUDO": "systemctl start sports-calendar.service"}),  # the run with the new code fails
])
def test_a_failed_deploy_puts_back_the_previous_code_queries_and_timers(vm, queries, fail):
    fail = {key: vm["new"] if value == "new" else value for key, value in fail.items()}
    result = deploy(vm, queries, **fail)

    assert result.returncode != 0
    assert git(vm["checkout"], "rev-parse", "HEAD") == vm["previous"]
    assert json.loads((vm["state"] / "queries.json").read_text()) == OLD_QUERIES
    assert logged(vm)[-1] == "sudo systemctl enable --now sports-calendar.timer sports-calendar-summary.timer"
    assert sorted(path.name for path in vm["state"].iterdir()) == ["queries.json"]
    assert "New Team" not in result.stdout + result.stderr  # the job log is public
