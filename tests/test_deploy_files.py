"""Checks on the deploy files, so a later edit cannot quietly undo a fix that is only visible on the VM."""
import configparser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
INSTALL_LINE_START = "sudo install -m 644 "


def read_unit(name: str) -> configparser.ConfigParser:
    unit = configparser.ConfigParser(strict=False, interpolation=None, delimiters=("=",), comment_prefixes=("#", ";"))
    unit.optionxform = str  # systemd keys are case-sensitive
    unit.read(DEPLOY / name)
    return unit


def deploy_lines() -> list[str]:
    return [line.strip() for line in DEPLOY_WORKFLOW.read_text().splitlines()]


def test_unit_signals_only_the_main_process_on_stop():
    """KillMode=mixed: with the default, uv and Python both get SIGTERM and uv exits 143, a false failure on every deploy."""
    assert read_unit("sports-calendar.service")["Service"]["KillMode"] == "mixed"


def test_unit_is_one_run_of_the_script_with_the_queries_file():
    """--no-dev: the service does not install pytest, or pandas (the stats group, which dev includes)."""
    service = read_unit("sports-calendar.service")["Service"]

    assert service["Type"] == "oneshot"
    assert service["ExecStart"].endswith("uv run --no-dev sports-calendar --queries queries.json")
    assert service["WorkingDirectory"] == "/home/ubuntu/sports_calendar_integration"


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
    assert service["ExecStart"].endswith("uv run --no-dev sports-calendar-summary")


def test_deploy_installs_every_unit_file():
    """A new unit file in deploy/ that the deploy does not install would never reach the VM."""
    (install,) = [line for line in deploy_lines() if line.startswith(INSTALL_LINE_START)]
    installed = set(install.removeprefix(INSTALL_LINE_START).split()[:-1])

    assert installed == {f"deploy/{path.name}" for path in DEPLOY.iterdir() if path.suffix in (".service", ".timer")}
    assert install.endswith(" /etc/systemd/system/")


def test_deploy_steps_run_in_order():
    """Install the packages, stop a run in progress, install and load the units, enable the timers, then run once.

    The install runs in the deploy job, so a failed install fails the job instead of a run; --locked also fails the
    job when uv.lock does not match pyproject.toml. The run at the end waits for its result, so a broken deploy
    fails the job.
    """
    lines = deploy_lines()
    steps = [
        "git reset --hard origin/main",
        "/home/ubuntu/.local/bin/uv sync --locked --no-dev",
        "sudo systemctl stop sports-calendar.service",
        next(line for line in lines if line.startswith(INSTALL_LINE_START)),
        "sudo systemctl daemon-reload",
        "sudo systemctl disable sports-calendar.service",
        "sudo systemctl enable --now sports-calendar.timer sports-calendar-summary.timer",
        "sudo systemctl start sports-calendar.service",
    ]
    positions = [lines.index(step) for step in steps]

    assert positions == sorted(positions)


def test_deploy_script_stops_at_the_first_failed_command():
    """Without set -e, the script goes on after a failed command, and the job reports only the last command."""
    lines = deploy_lines()

    assert lines.index("set -e") < lines.index("git fetch origin main")
