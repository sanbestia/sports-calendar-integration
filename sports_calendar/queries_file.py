"""Read and add entries in queries.json, the list of teams and players the service tracks.

Each entry is {name: {"id", "sport", "player_type", "calendar"}}, where "calendar" is the name of an
environment variable that holds the Google Calendar ID (resolved at startup by main.load_queries).
"""
import json
import os
from collections.abc import Mapping

REQUIRED_QUERY_FIELDS = {"id", "sport", "player_type", "calendar"}


class QueryError(ValueError):
    """An entry cannot be added to queries.json."""


def read_queries(path: str) -> dict:
    """Return the raw entries (calendar values are env keys), or {} when the file does not exist yet."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def calendar_keys(queries: Mapping[str, dict]) -> list[str]:
    """The calendar env keys the entries already use, each once, sorted."""
    return sorted({entry["calendar"] for entry in queries.values()})


def add_query(path: str, name: str, entity: Mapping[str, str], calendar_key: str, env: Mapping[str, str]) -> None:
    """Check a new entry and append it to queries.json.

    entity holds "id", "sport" and "player_type" (as get_ids returns them). Raises QueryError, and leaves the
    file as it was, when the calendar key is not set in env, the name is taken, or the same id and sport are
    already tracked under another name.
    """
    if not env.get(calendar_key):
        raise QueryError(f"{calendar_key} is not set in the environment (.env); the service would refuse to start")
    queries = read_queries(path)
    if name in queries:
        raise QueryError(f"'{name}' is already in {path}")
    for existing_name, existing in queries.items():
        if (existing["id"], existing["sport"]) == (entity["id"], entity["sport"]):
            raise QueryError(f"{entity['sport']} id {entity['id']} is already tracked as '{existing_name}'")

    queries[name] = {"id": entity["id"], "sport": entity["sport"], "player_type": entity["player_type"], "calendar": calendar_key}
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        f.write(json.dumps(queries, indent=4, ensure_ascii=False))
    os.replace(temporary, path)  # a crash mid-write leaves the old file, never half a file
