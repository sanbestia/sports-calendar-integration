"""Upload queries.json as the QUERIES_JSON_B64 GitHub secret and start the deploy of main.

The deploy writes the secret to queries.json on the VM and restarts the service, so a pushed change is
live about a minute later. Run it as `uv run python -m sports_calendar.tools.push_queries`.
"""
import base64
import json
import logging
import os
import subprocess
from collections.abc import Callable, Mapping

logger = logging.getLogger(__name__)

REPO = "sanbestia/sports_calendar_integration"
SECRET = "QUERIES_JSON_B64"


def push_queries(path: str = "queries.json", repo: str = REPO, account: str | None = None,
                 env: Mapping[str, str] = os.environ, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
    """Set the secret from the file at path, then start deploy.yml on main.

    gh runs as `account` (the repo owner by default), not as gh's active account, which on this machine
    may be a different one. Raises ValueError for a file that is not valid JSON, before anything is sent,
    and CalledProcessError when a gh command fails.
    """
    with open(path, "rb") as f:
        raw = f.read()
    json.loads(raw)  # never upload a broken file: the next deploy would install it on the VM
    account = account or repo.split("/")[0]
    token = run(["gh", "auth", "token", "--user", account], capture_output=True, text=True, check=True).stdout.strip()
    gh_env = {**env, "GH_TOKEN": token}
    run(["gh", "secret", "set", SECRET, "--repo", repo], input=base64.b64encode(raw).decode(), text=True,
        check=True, env=gh_env)
    run(["gh", "workflow", "run", "deploy.yml", "--repo", repo, "--ref", "main"], check=True, env=gh_env)
    logger.info(f"Updated {SECRET} and started the deploy of main. Follow it with: gh run watch --repo {repo}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    push_queries()


if __name__ == "__main__":
    main()
