"""The service runs with `uv run --no-dev`, so it must not import a package of the dev or stats groups."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NOT_INSTALLED_ON_THE_SERVER = ("pandas", "numpy", "pytest")


@pytest.mark.parametrize("module", ["sports_calendar.service", "sports_calendar.daily_summary"])
def test_the_service_imports_no_package_of_the_dev_or_stats_groups(module):
    """A new process, because this test process has already imported pytest and, through db_stats, pandas.

    Both units of the service start with --no-dev: the run (service) and the daily summary.
    """
    check = (f"import sys, {module}; "
             f"print(','.join(m for m in {NOT_INSTALLED_ON_THE_SERVER!r} if m in sys.modules))")
    result = subprocess.run([sys.executable, "-c", check], cwd=ROOT, capture_output=True, text=True, check=True, timeout=60)

    assert result.stdout.strip() == ""
