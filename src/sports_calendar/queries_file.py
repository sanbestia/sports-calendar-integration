"""Read and add entries in queries.json, the list of teams and players the service tracks.

Each entry is {name: {"id", "sport", "player_type", "calendar"}}, where "calendar" is the name of an
environment variable that holds the Google Calendar ID (load_queries resolves it for each run).
"""
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sports_calendar.files import write_atomically


class QueryEntry(BaseModel):
    """One entry as queries.json holds it. `calendar` is the name of the env variable that holds the calendar ID."""
    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[0-9]+$")  # a string, as the events store it: an int would never match one
    sport: str = Field(min_length=1)
    player_type: Literal["team", "player"]
    calendar: str = Field(min_length=1)


@dataclass(frozen=True)
class Query:
    """A tracked team or player as a run uses it: its AllSports id, sport and type, and the ID of its calendar."""
    id: str
    sport: str
    player_type: str
    calendar_id: str


class QueryError(ValueError):
    """An entry cannot be added to queries.json."""


def read_queries(path: str) -> dict:
    """Return the raw entries (calendar values are env keys), or {} when the file does not exist yet."""
    if not os.path.exists(path):
        return {}
    return _read_entries(path)


def load_queries(path: str, env: Mapping[str, str]) -> dict[str, Query]:
    """Return the checked entries for a run, each with the calendar ID that env holds under its calendar key.

    A missing file raises FileNotFoundError, unlike read_queries: a run with no entries would only hide the mistake.
    """
    return {name: Query(entry.id, entry.sport, entry.player_type, _calendar_id(name, entry.calendar, env))
            for name, entry in parse_queries(_read_entries(path)).items()}


def parse_queries(raw: Mapping[str, object]) -> dict[str, QueryEntry]:
    """Check each entry of a parsed queries.json; raise ValueError that names the entry and its bad or missing fields."""
    return {name: _parse_entry(name, entry) for name, entry in raw.items()}


def _parse_entry(name: str, entry: object) -> QueryEntry:
    try:
        return QueryEntry.model_validate(entry)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, error['loc'])) or 'entry'}: {error['msg']}" for error in e.errors())
        raise ValueError(f"Query '{name}' is invalid: {problems}") from None


def _read_entries(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _calendar_id(name: str, env_key: str, env: Mapping[str, str]) -> str:
    """Return the calendar ID that env holds under env_key; raise ValueError when it is not set."""
    calendar_id = env.get(env_key)
    if not calendar_id:
        raise ValueError(f"Environment variable '{env_key}' not found for '{name}'")
    return calendar_id


def calendar_keys(queries: Mapping[str, dict]) -> list[str]:
    """The calendar env keys the entries already use, each once, sorted."""
    return sorted({entry["calendar"] for entry in queries.values()})


def add_query(path: str, name: str, entity: Mapping[str, str], calendar_key: str, env: Mapping[str, str]) -> None:
    """Check a new entry and append it to queries.json.

    entity holds "id", "sport" and "player_type" (as get_ids returns them). Raises QueryError, and leaves the
    file as it was, when the calendar key is not set in env, the name is taken, or the same id and sport are
    already tracked under another name.
    """
    _check_calendar_key(calendar_key, env)
    queries = read_queries(path)
    _check_not_tracked(queries, path, name, entity)
    queries[name] = {"id": entity["id"], "sport": entity["sport"], "player_type": entity["player_type"], "calendar": calendar_key}
    _write_queries(path, queries)


def _check_calendar_key(calendar_key: str, env: Mapping[str, str]) -> None:
    """Raise QueryError when env does not hold the calendar key, because the service would then refuse to start."""
    if not env.get(calendar_key):
        raise QueryError(f"{calendar_key} is not set in the environment (.env); the service would refuse to start")


def _check_not_tracked(queries: Mapping[str, dict], path: str, name: str, entity: Mapping[str, str]) -> None:
    """Raise QueryError when the name is taken, or when the same id and sport are tracked under another name."""
    if name in queries:
        raise QueryError(f"'{name}' is already in {path}")
    for existing_name, existing in queries.items():
        if (existing["id"], existing["sport"]) == (entity["id"], entity["sport"]):
            raise QueryError(f"{entity['sport']} id {entity['id']} is already tracked as '{existing_name}'")


def _write_queries(path: str, queries: Mapping[str, dict]) -> None:
    write_atomically(path, json.dumps(queries, indent=4, ensure_ascii=False))
