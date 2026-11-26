"""Upload queries.json as the QUERIES_JSON_B64 GitHub secret and start the deploy of main.

The deploy writes the secret to queries.json on the VM and starts a run, so a pushed change is
live about a minute later. Run it as `uv run push-queries`.
"""
import base64
import json
import logging
import os
import subprocess
from collections.abc import Callable, Mapping

from sports_calendar.logs import configure_logging
from sports_calendar.queries_file import parse_queries

logger = logging.getLogger(__name__)

REPO = "sanbestia/sports-calendar-integration"
SECRET = "QUERIES_JSON_B64"


def push_queries(path: str = "queries.json", repo: str = REPO, account: str | None = None,
                 env: Mapping[str, str] = os.environ, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
    """Set the secret from the file at path, then start deploy.yml on main.

    gh runs as `account` (the repo owner by default), not as gh's active account, which on this machine
    may be a different one. Raises ValueError for a file that is not valid JSON or has a bad entry, before anything is sent,
    and CalledProcessError when a gh command fails.
    """
    content = _read_valid_queries(path)
    gh_env = _env_with_token(account or repo.split("/")[0], env, run)
    run(["gh", "secret", "set", SECRET, "--repo", repo], input=base64.b64encode(content).decode(), text=True,
        check=True, env=gh_env)
    run(["gh", "workflow", "run", "deploy.yml", "--repo", repo, "--ref", "main"], check=True, env=gh_env)
    logger.info(f"Updated {SECRET} and started the deploy of main. Follow it with: gh run watch --repo {repo}")


def _read_valid_queries(path: str) -> bytes:
    """Return the file's bytes; raise ValueError when it is not valid JSON or an entry is bad, because the next deploy
    would install it on the VM, where every run would stop at it."""
    with open(path, "rb") as f:
        content = f.read()
    parse_queries(json.loads(content))
    return content


def _env_with_token(account: str, env: Mapping[str, str], run: Callable[..., subprocess.CompletedProcess]) -> dict[str, str]:
    """Return env plus the GH_TOKEN of the account, so gh runs as that account and not as gh's active account."""
    token = run(["gh", "auth", "token", "--user", account], capture_output=True, text=True, check=True).stdout.strip()
    return {**env, "GH_TOKEN": token}


def main() -> None:
    """Entry point of `uv run push-queries`."""
    configure_logging()
    push_queries()
