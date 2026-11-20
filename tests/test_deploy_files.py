"""Checks on the deploy files, so a later edit cannot quietly undo a fix that is only visible on the VM."""
import configparser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNIT = ROOT / "deploy" / "sports-calendar.service"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"


def read_unit() -> configparser.ConfigParser:
    unit = configparser.ConfigParser(strict=False, interpolation=None, comment_prefixes=("#", ";"))
    unit.optionxform = str  # systemd keys are case-sensitive
    unit.read(UNIT)
    return unit


def test_unit_signals_only_the_main_process_on_stop():
    """KillMode=mixed: with the default, uv and Python both get SIGTERM and uv exits 143, a false failure on every deploy."""
    assert read_unit()["Service"]["KillMode"] == "mixed"


def test_unit_runs_the_script_with_the_queries_file_and_restarts_on_failure():
    service = read_unit()["Service"]

    assert service["ExecStart"].endswith("uv run python main.py --queries queries.json")
    assert service["Restart"] == "on-failure"
    assert service["WorkingDirectory"] == "/home/ubuntu/sports_calendar_integration"


def test_deploy_installs_the_unit_and_reloads_before_the_restart():
    """The new unit must be loaded before the restart, or the restart still uses the old settings."""
    lines = [line.strip() for line in DEPLOY_WORKFLOW.read_text().splitlines()]
    install = lines.index("sudo install -m 644 deploy/sports-calendar.service /etc/systemd/system/sports-calendar.service")
    reload = lines.index("sudo systemctl daemon-reload")
    restart = lines.index("sudo systemctl restart sports-calendar")

    assert install < reload < restart
