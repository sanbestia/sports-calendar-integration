"""The service runs with `uv run --no-dev`, so it must not import a package of the dev or stats groups."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOT_INSTALLED_ON_THE_SERVER = ("pandas", "numpy", "pytest")


def test_the_service_imports_no_package_of_the_dev_or_stats_groups():
    """A new process, because this test process has already imported pytest and, through db_stats, pandas."""
    check = ("import sys, main; "
             f"print(','.join(m for m in {NOT_INSTALLED_ON_THE_SERVER!r} if m in sys.modules))")
    result = subprocess.run([sys.executable, "-c", check], cwd=ROOT, capture_output=True, text=True, check=True, timeout=60)

    assert result.stdout.strip() == ""
