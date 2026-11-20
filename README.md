# Sports Calendar Integration

A Python tool that automatically syncs upcoming sports matches to your Google Calendar. It fetches schedules for your favourite teams and players across multiple sports and keeps your calendar up to date, refreshing every few hours.

---

## Index

- [Features](#features)
- [Setup & Installation](#setup--installation)
- [Google Calendar API Setup](#google-calendar-api-setup)
- [Configuration](#configuration)
- [Running the Script](#running-the-script)
- [Database Analytics](#database-analytics)
- [Contributing](#contributing)

---

## Features

- Tracks multiple teams and players across different sports (football, basketball, tennis, esports and more)
- Automatically creates and updates Google Calendar events with match details, including when the time, the teams, the tournament or the stage change
- Automatically removes calendar events for cancelled or rescheduled matches (detected via API status or disappearance from the schedule)
- Falls back to alternative API endpoints when the primary one returns no results
- Supports predefined query files for fully automated runs
- Interactive mode to search for teams and players on the fly
- Built-in analytics for querying API usage and fetch history via a standalone CLI or Python module

---

## Setup & Installation

### Requirements
- Python 3.13+
- A [RapidAPI](https://rapidapi.com) account with access to the [AllSports API](https://rapidapi.com/allsportsapi2/api/allsportsapi2)
- A Google account with the [Google Calendar API](https://console.cloud.google.com/) enabled

### Installation

1. Clone the repository:
```bash
    git clone https://github.com/your-username/sports-calendar-integration.git
    cd sports-calendar-integration
```

2. Install dependencies using [uv](https://github.com/astral-sh/uv):
```bash
    uv sync
```

3. Set up your environment variables (see [Configuration](#configuration))

4. Set up your Google Calendar API credentials (see [Google Calendar API Setup](#google-calendar-api-setup))

---

## Google Calendar API Setup

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a new project.

2. Navigate to **APIs & Services → Library**, search for "Google Calendar API", and enable it.

3. Go to **APIs & Services → OAuth consent screen**. Choose **External**, fill in the app name and your email, and save. You don't need to publish the app; leaving it in testing mode is fine for personal use.

4. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**. Choose **Desktop app**, give it a name, and download the resulting file. Rename it to `credentials.json` and place it in the root directory of the project.

5. On your first run, a browser window will open asking you to authorise the app with your Google account. After you confirm, a `token.json` file will be created automatically and reused in future runs.

> **Note:** If you see a "Google hasn't verified this app" warning, click **Advanced → Go to (app name)** to proceed. This appears because the app is in testing mode and is expected behaviour.

---

## Configuration

Create a `.env` file in the root directory with the following variables:
```env
RAPIDAPI_KEY=your_rapidapi_key_here

CALENDAR_ID_SPORT1=your_calendar_id_here
CALENDAR_ID_SPORT2=your_calendar_id_here
...
```

You may add as many Google Calendar IDs as you wish.

> **Note:** To find your Google Calendar ID, go to Google Calendar → Settings → select a calendar → scroll down to "Integrate calendar".

You can adjust the refresh interval and fetch thresholds in `config.py`. By default they are:
```python
MAIN_LOOP_SLEEP_MINUTES = 5  # how often the main loop runs

FETCH_THRESHOLDS = {
    "far":           {"days": 7,  "recheck_hours": 48},  # next match is more than 7 days away
    "medium":        {"days": 3,  "recheck_hours": 24},  # next match is 3-7 days away
    "near":          {"days": 0,  "recheck_hours": 12},   # next match is within 3 days
    "unknown":       {"recheck_hours": 8,                # no upcoming match found: wait 8h for the first grace_days
                      "grace_days": 3,                   # (playoff gaps between rounds, tennis between rounds and draws),
                      "max_recheck_hours": {"player": 12, "team": 48}},  # then double the wait for each empty result, up to the cap
    "imminent":      {"hours": 24, "recheck_hours": 4},  # single match within this many hours
    "imminent_close":{"hours": 3,  "recheck_hours": 1}, # single match within this many hours
    "failed":        {"recheck_hours": 1, "max_recheck_hours": 8}  # last fetch failed: retry after 1h, doubling for each failure in a row, up to 8h
}
```

Each team is re-fetched at different rates depending on how soon their next match is, so the program avoids unnecessary API calls when games are far away, but checks often when matches are approaching. As a special case, if only one match is found the program applies stricter refresh rates: every `imminent.recheck_hours` when the match is within the `imminent` window, and every `imminent_close.recheck_hours` when it is within the closer `imminent_close` window.

When a team has no upcoming match, it is checked every `unknown.recheck_hours` for the first `unknown.grace_days`. That covers the gap between playoff rounds, when the next game is not announced yet, and a tennis player waiting for the next round or draw. After that, each empty result doubles the wait, up to `unknown.max_recheck_hours`: 12h for players, because a tennis draw can come out less than a day before round 1, and 48h for teams in their off-season. A fetch that finds a match resets this.

A failed fetch (a timeout, an unreadable response, or a Calendar error) is not treated as "no matches". The team is retried after `failed.recheck_hours`, and the wait doubles for each failure in a row up to `failed.max_recheck_hours`, so one broken team cannot use the daily API budget. A successful fetch returns the team to its normal schedule. An error for one team is logged and does not stop the service or the other teams.

The AllSports free tier allows 100 calls per day (`MAX_API_CALLS`, a hard limit). Normal use should not often go above 70 calls per day (`API_CALL_CEILING`), which leaves headroom for outages and retries. There is no floor: fewer calls are fine. Check the ceiling when you change `FETCH_THRESHOLDS` or add teams (`uv run python functions/db_stats.py avg_daily_calls`).

### Local state files

On first run, the program automatically creates two SQLite database files in the working directory: `api_calls.db` to track your daily API usage, and `fetch_log.db` to remember when each team was last fetched. `fetch_log.db` also holds `fetch_history`, one row per fetch: when, which team, the result (`matches`, `empty` or `failed`), the API calls it used, the matches found, the events created, updated and deleted, and a short error text. These are managed automatically and you don't need to touch them.

---

## Running the Script

### Looking up team and player IDs

The `id` values in `queries.json` come from the AllSports API. To look them up, run `get_ids` as a standalone script:

```bash
uv run python -m functions.get_ids
```

You will be prompted to enter a name and sport, shown a list of matches to choose from, and asked to confirm your selection. At the end, the script prints a summary of all selected entries with their IDs. API calls made during the search are tracked against your daily limit. Like the main script, it reads `RAPIDAPI_KEY` from `.env`.

### Adding a team or player

To add an entry to `queries.json` without editing the file by hand:

```bash
uv run python -m functions.add_entity
```

It runs the same search as `get_ids`. For each entity you pick, it asks for the name to use in `queries.json` (Enter keeps the API name) and for the calendar: one of the env keys the file already uses, or another key from `.env`. It refuses an entry whose calendar key is not set in `.env`, a name that is already in the file, and an entity (same id and sport) that is already tracked. At the end it offers to upload the file and deploy.

To upload `queries.json` and deploy later, or after editing it by hand:

```bash
uv run python -m functions.push_queries
```

It sets the `QUERIES_JSON_B64` secret from the file (after checking that it is valid JSON) and starts the deploy workflow on `main`, which writes the file to the VM and restarts the service. It runs `gh` as the repository owner's account, not as `gh`'s active account.

### Running the script with a predefined query file

Once you have your IDs, create a `queries.json` file in the root directory and run:
```bash
uv run python main.py --queries queries.json
```

**`queries.json` format example:**
```json
{
    "Carlos Alcaraz": {
        "id": "275923",
        "sport": "tennis",
        "player_type": "player",
        "calendar": "CALENDAR_ID_TENNIS"
    },
    "Argentina - Football National Team": {
        "id": "4819",
        "sport": "football",
        "player_type": "team",
        "calendar": "CALENDAR_ID_FOOTBALL"
    }
}
```

The `calendar` field should match one of the key names defined in your `.env` file. The `player_type` field should be `"player"` for individuals or `"team"` for organizations.

### Running the script in interactive mode

Run without arguments to search for teams and players interactively, without needing a `queries.json` file:
```bash
uv run python main.py
```

You will be prompted to enter team/player names, select from results, and provide calendar IDs. This uses the same search flow as `get_ids` but feeds directly into the main loop.

### Reading the logs

The script logs to stderr. Under systemd the lines go to journald, and each line carries its syslog priority, so you can filter by level:

```bash
journalctl -u sports-calendar -f                      # follow the log
journalctl -u sports-calendar -p warning --since -7d  # only warnings and errors from the last 7 days
```

At INFO level you see the version (git commit) at startup, each fetch, a one-line summary of the matches found, each event created, updated or deleted, the API call count after a cycle that did work, and after midnight a daily summary of the previous day (API calls, fetches by result, event changes; a WARNING when the calls were above `API_CALL_CEILING`). Warnings and errors start with their level name (`WARNING`, `ERROR`). Per-match details, "already up to date" events and skipped teams are logged at DEBUG.

### Running tests
```bash
uv run pytest -v
```

The tests need no secrets. Code gets its collaborators passed in (the AllSports client and its HTTP session, the clock, the prompts, the Calendar service, the Google auth calls and file paths), so tests hand it the small fakes in `tests/fakes.py`. Nothing in the tests is patched. `main()` is the only place that builds the real ones.

### Continuous integration

- `.github/workflows/tests.yml` runs the test suite on every pull request. A ruleset on `main` makes its `test` check required before a merge.
- `.github/workflows/deploy.yml` runs on every push to `main`, or by hand (Actions → "Deploy to Oracle VM" → Run workflow, on `main`), for example after you change the `QUERIES_JSON_B64` secret. It calls the same test workflow first and deploys to the Oracle VM only if the tests pass. It does not upgrade system packages: `unattended-upgrades` on the VM installs security updates daily.
- The systemd unit is versioned in `deploy/sports-calendar.service`. Each deploy installs it to `/etc/systemd/system/` and runs `daemon-reload` before the restart, so change the unit in the repo, not on the VM.
- On a restart (each deploy, a reboot), systemd stops the service with SIGTERM. The unit sets `KillMode=mixed`, so only `uv` gets the signal and passes it on to Python, which catches it and exits with code 0: systemd logs a clean stop, not a failure. With the default kill mode, Python and `uv` both get SIGTERM at the same moment, and `uv` exits 143 even when Python exits 0.

---

## Database Analytics

`functions/db_stats.py` provides read-only functions for querying both databases (`api_calls.db` and `fetch_log.db`). It can be used in two ways:

**As a CLI** — call any function by name, with optional positional arguments:
```bash
uv run python functions/db_stats.py                        # list all available functions
uv run python functions/db_stats.py calls_today
uv run python functions/db_stats.py stale_teams 24
uv run python functions/db_stats.py calls_in_range 2026-01-01 2026-01-31
uv run python functions/db_stats.py calls_per_entity 7      # API calls per team over the last 7 days
```

**As a module** — import directly in a Python script or notebook:
```python
from functions.db_stats import (
    calls_today, remaining_calls_today, calls_on, calls_in_range,
    days_at_max, pct_days_at_max, avg_daily_calls,
    busiest_days, calls_per_period, api_budget_forecast,
    all_calls_df, all_fetches_df, fetch_gap_df,
    teams_due_now, teams_with_no_upcoming_match, stale_teams,
    calls_per_entity, outcomes_per_entity, recent_failures,
)

# Examples
calls_today()                        # API calls made today
remaining_calls_today()              # calls left before hitting the daily limit
calls_in_range(date(2026,1,1), date(2026,1,31))  # total calls in January
pct_days_at_max()                    # % of days the limit was reached
busiest_days(5)                      # top 5 days by call volume (DataFrame)
calls_per_period("ME")               # monthly totals (DataFrame)
api_budget_forecast()                # projected end-of-day total based on current rate
teams_due_now()                      # teams the scheduler would fetch right now
stale_teams(hours=48)                # teams not fetched in over 48 hours (DataFrame)
calls_per_entity(7)                  # API calls and fetches per team, last 7 days (DataFrame)
outcomes_per_entity(30)              # matches / empty / failed fetches per team, last 30 days (DataFrame)
recent_failures(7)                   # failed fetches with their error, newest first (DataFrame)
```

Functions that return datetimes, percentages, or floats format their output for readability by default (datetimes as `DD-MM-YYYY HH:MM:SS +00:00`, percentages with `%`, floats rounded to 2 decimal places). Pass `raw=True` to get native Python types for programmatic use:
```python
pct_days_at_max()            # "50.00%"
pct_days_at_max(raw=True)    # 50.0
days_at_max()                # ["02-01-2026", "04-01-2026"]
days_at_max(raw=True)        # [datetime.date(2026, 1, 2), datetime.date(2026, 1, 4)]
```

---

## Contributing

Contributions are welcome! If you'd like to add support for a new sport, improve the calendar logic, or fix a bug:

1. Fork the repository
2. Create a new branch (`git checkout -b feature/your-feature`)
3. Make your changes and make sure all tests pass (`uv run pytest -v`)
4. Submit a pull request

Please make sure any new functionality is covered by tests.

---