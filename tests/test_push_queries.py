import base64
import json
import subprocess

import pytest

from sports_calendar.tools.push_queries import push_queries

QUERIES = {"Los Angeles Lakers": {"id": "3427", "sport": "basketball", "player_type": "team", "calendar": "CALENDAR_ID_BASKETBALL"}}


class FakeRun:
    """Stands in for subprocess.run: records each command, answers `gh auth token` with a fake token."""

    def __init__(self, fail_on: str | None = None):
        self.calls: list[tuple[list[str], dict]] = []
        self.fail_on = fail_on

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.fail_on and self.fail_on in args:
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0, stdout="fake-token\n" if args[:3] == ["gh", "auth", "token"] else "")

    @property
    def commands(self) -> list[list[str]]:
        return [args for args, _ in self.calls]


@pytest.fixture
def queries_path(tmp_path):
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(QUERIES, indent=4))
    return str(path)


def test_sets_the_secret_then_starts_the_deploy_of_main(queries_path):
    run = FakeRun()
    push_queries(queries_path, repo="owner/repo", env={}, run=run)

    assert run.commands == [
        ["gh", "auth", "token", "--user", "owner"],
        ["gh", "secret", "set", "QUERIES_JSON_B64", "--repo", "owner/repo"],
        ["gh", "workflow", "run", "deploy.yml", "--repo", "owner/repo", "--ref", "main"],
    ]


def test_the_secret_is_the_file_in_base64(queries_path):
    """The deploy decodes it with base64 -d into queries.json on the VM."""
    run = FakeRun()
    push_queries(queries_path, repo="owner/repo", env={}, run=run)

    secret_input = run.calls[1][1]["input"]
    assert json.loads(base64.b64decode(secret_input)) == QUERIES


def test_gh_runs_as_the_given_account_not_the_active_one(queries_path):
    """The token of the chosen account goes to GH_TOKEN for the secret and the deploy."""
    run = FakeRun()
    push_queries(queries_path, repo="owner/repo", account="someone", env={"PATH": "/bin"}, run=run)

    assert run.commands[0] == ["gh", "auth", "token", "--user", "someone"]
    assert all(kwargs["env"] == {"PATH": "/bin", "GH_TOKEN": "fake-token"} for _, kwargs in run.calls[1:])


def test_refuses_a_broken_file_before_sending_anything(tmp_path):
    path = tmp_path / "queries.json"
    path.write_text('{"Lakers": ')
    run = FakeRun()

    with pytest.raises(ValueError):
        push_queries(str(path), repo="owner/repo", env={}, run=run)
    assert run.commands == []


def test_a_failed_secret_update_does_not_start_a_deploy(queries_path):
    run = FakeRun(fail_on="secret")

    with pytest.raises(subprocess.CalledProcessError):
        push_queries(queries_path, repo="owner/repo", env={}, run=run)
    assert ["gh", "workflow", "run", "deploy.yml", "--repo", "owner/repo", "--ref", "main"] not in run.commands
