"""Checks on the deploy files, so a later edit cannot quietly undo a fix that is only visible on the VM."""
import configparser
import re
import shlex
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
TESTS_WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
UPDATE_SCRIPT = DEPLOY / "update-vm.sh"
INSTALL_LINE_START = "sudo install -m 644 "


def read_unit(name: str) -> configparser.ConfigParser:
    unit = configparser.ConfigParser(strict=False, interpolation=None, delimiters=("=",), comment_prefixes=("#", ";"))
    unit.optionxform = str  # systemd keys are case-sensitive
    unit.read(DEPLOY / name)
    return unit


def deploy_lines() -> list[str]:
    return [line.strip() for line in DEPLOY_WORKFLOW.read_text().splitlines()]


def script_lines() -> list[str]:
    return [line.strip() for line in UPDATE_SCRIPT.read_text().splitlines()]


def uv_sync_flags(path: Path) -> set[str]:
    """The words of the one `uv sync` command in a workflow or a script ("$UV" sync), without its comment."""
    (command,) = [line.split("#")[0] for line in path.read_text().splitlines() if re.search(r'\buv"? sync\b', line.split("#")[0], re.I)]
    return set(command.split())


def test_unit_signals_only_the_main_process_on_stop():
    """KillMode=mixed: with the default, uv and Python both get SIGTERM and uv exits 143, a false failure on every deploy."""
    assert read_unit("sports-calendar.service")["Service"]["KillMode"] == "mixed"


def test_unit_is_one_run_of_the_script_with_the_queries_file():
    """--no-dev: the service does not install pytest, or pandas (the stats group, which dev includes)."""
    service = read_unit("sports-calendar.service")["Service"]

    assert service["Type"] == "oneshot"
    assert service["ExecStart"].endswith("uv run --locked --project /home/ubuntu/sports_calendar_integration --no-dev sports-calendar --queries queries.json")


def test_both_units_keep_their_data_in_the_state_folder_outside_the_checkout():
    """The databases, queries.json, token.json and .env must not live in the git checkout, where a git clean deletes them."""
    for name in ("sports-calendar.service", "sports-calendar-summary.service"):
        service = read_unit(name)["Service"]
        assert service["StateDirectory"] == "sports-calendar"
        assert service["StateDirectoryMode"] == "0700"
        assert service["WorkingDirectory"] == "/var/lib/sports-calendar"


def test_deploy_writes_queries_to_the_state_folder():
    """The run reads queries.json from its working directory, so the update script must write it there."""
    state_directory = read_unit("sports-calendar.service")["Service"]["StateDirectory"]

    assert f"STATE=${{STATE:-/var/lib/{state_directory}}}  # the units' StateDirectory=" in script_lines()


def test_unit_has_a_start_timeout():
    """A oneshot has no start timeout by default (probed on the VM: infinity), so a hung run would block every later run."""
    assert "TimeoutStartSec" in read_unit("sports-calendar.service")["Service"]


def test_only_the_timer_starts_the_service():
    """No Restart= (the next run retries a failed one) and no [Install] (no start at boot outside the timer)."""
    unit = read_unit("sports-calendar.service")

    assert "Restart" not in unit["Service"]
    assert not unit.has_section("Install")


def test_timer_starts_a_run_every_15_minutes():
    timer = read_unit("sports-calendar.timer")

    assert timer["Timer"]["OnCalendar"] == "*:0/15"
    assert timer["Install"]["WantedBy"] == "timers.target"


def test_summary_runs_each_day_and_after_a_day_the_vm_was_down():
    timer = read_unit("sports-calendar-summary.timer")
    service = read_unit("sports-calendar-summary.service")["Service"]

    assert timer["Timer"]["OnCalendar"] == "*-*-* 00:05:00"
    assert timer["Timer"]["Persistent"] == "true"
    assert service["ExecStart"].endswith("uv run --locked --project /home/ubuntu/sports_calendar_integration --no-dev sports-calendar-summary")


def test_deploy_installs_every_unit_file():
    """A new unit file in deploy/ that the deploy does not install would never reach the VM."""
    (install,) = [line for line in script_lines() if line.startswith(INSTALL_LINE_START)]
    installed = set(install.removeprefix(INSTALL_LINE_START).split()[:-1])

    assert installed == {f"deploy/{path.name}" for path in DEPLOY.iterdir() if path.suffix in (".service", ".timer")}
    assert install.endswith(" /etc/systemd/system/")


def test_ci_checks_the_lock_file_like_the_deploy():
    """With --frozen, CI installs a stale uv.lock and passes; only the deploy's --locked install fails, after the merge."""
    lock_checks = {"--locked", "--frozen"}

    assert uv_sync_flags(TESTS_WORKFLOW) & lock_checks == uv_sync_flags(UPDATE_SCRIPT) & lock_checks == {"--locked"}


def test_the_deploy_runs_the_update_script_which_stops_at_its_first_failed_command():
    """Without set -e, a script goes on after a failed command; tests/test_update_vm.py runs the script itself."""
    commands = [line for line in script_lines() if line and not line.startswith("#")]

    assert commands[0] == "set -Eeuo pipefail"
    assert """{ printf "QUERIES_JSON_B64='%s'\\n" "$QUERIES_JSON_B64"; cat deploy/update-vm.sh; } | ssh vm bash -s""" in deploy_lines()


def test_deploys_run_one_at_a_time():
    """Two deploys at once (two merges, or push-queries during a merge) would reset the checkout under each other."""
    lines = deploy_lines()

    assert lines[lines.index("concurrency:") + 2] == "cancel-in-progress: false"


def ssh_config() -> dict[str, str]:
    """The ssh options that the deploy job writes to ~/.ssh/config, as {option: value}."""
    text = DEPLOY_WORKFLOW.read_text()
    command = text[text.index("printf '%s\\n' \"Host vm\""):text.index("> ~/.ssh/config")]
    return dict(word.split(maxsplit=1) for word in shlex.split(command.replace("\\\n", " "))[2:])


def test_the_deploy_accepts_only_the_vm_host_keys_in_the_repo():
    """Without a host key check, a host that takes over the VM's address gets the deploy key and the secrets."""
    config = ssh_config()
    hosts = {line.split()[0] for line in (DEPLOY / "known_hosts").read_text().splitlines() if line}

    assert config["StrictHostKeyChecking"] == "yes"
    assert config["GlobalKnownHostsFile"] == "/dev/null"
    assert config["UserKnownHostsFile"] == "$GITHUB_WORKSPACE/deploy/known_hosts"
    assert hosts == {config["HostKeyAlias"]}
    assert all("actions/checkout@" in line for line in deploy_lines() if line.startswith("- uses:"))


@pytest.mark.parametrize("name", ["sports-calendar.service", "sports-calendar-summary.service"])
def test_units_cannot_gain_privileges_or_write_the_system(name):
    """User=ubuntu has passwordless sudo on the VM, so without NoNewPrivileges code in a run could become root."""
    service = read_unit(name)["Service"]

    assert (service.get("NoNewPrivileges"), service.get("PrivateTmp"), service.get("ProtectSystem")) == ("yes", "yes", "full")
