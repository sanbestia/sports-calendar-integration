"""Each console command in pyproject.toml must point to a function that exists: a typo would show only when someone runs it."""
import importlib
import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
COMMANDS = tomllib.loads(PYPROJECT.read_text())["project"]["scripts"]


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_command_points_to_a_function(command):
    module_name, function_name = COMMANDS[command].split(":")
    assert callable(getattr(importlib.import_module(module_name), function_name))
