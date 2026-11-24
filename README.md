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
- Optional email alerts through [healthchecks.io](https://healthchecks.io) when the service stops, hangs, needs a new Google login, or keeps failing to fetch a team or player

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
   This also installs the `dev` group: pytest, and pandas for `src/sports_calendar/tools/db_stats.py` (the `stats` group, which `dev` includes). The service itself needs neither.

3. Set up your environment variables (see [Configuration](#configuration))

4. Set up your Google Calendar API credentials (see [Google Calendar API Setup](#google-calendar-api-setup))

---

## Google Calendar API Setup

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a new project.

2. Navigate to **APIs & Services → Library**, search for "Google Calendar API", and enable it.

3. Go to **APIs & Services → OAuth consent screen**. Choose **External**, fill in the app name and your email, and save. Then set the publishing status to **In production** (**Publish app** on the **Audience** page). In **Testing**, the token of an app with the Calendar scope expires after 7 days, so the service would need a new login every week. For your own account the app does not need Google's verification.

4. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**. Choose **Desktop app**, give it a name, and download the resulting file. Rename it to `credentials.json` and place it in the root directory of the project.

5. Log in once, on a computer with a browser: run `uv run google-login`. A browser window asks you to authorise the app with your Google account. After you confirm, a `token.json` file is created, and later runs reuse it. The service itself never opens a browser: without a valid `token.json` it logs an error and stops (see [Renewing the Google login](#renewing-the-google-login)).

> **Note:** If you see a "Google hasn't verified this app" warning, click **Advanced → Go to (app name)** to proceed. This appears because Google has not verified the app, and is expected behaviour.

---

## Configuration

Create a `.env` file in the root directory (on the server: in `/var/lib/sports-calendar`, see "Local state files") with the following variables:
```env
RAPIDAPI_KEY=your_rapidapi_key_here

CALENDAR_ID_SPORT1=your_calendar_id_here
CALENDAR_ID_SPORT2=your_calendar_id_here
...

# optional: alerts through healthchecks.io (see "Alerts")
HEALTHCHECK_URL=https://hc-ping.com/your-check-uuid
```

You may add as many Google Calendar IDs as you wish.

> **Note:** To find your Google Calendar ID, go to Google Calendar → Settings → select a calendar → scroll down to "Integrate calendar".

You can adjust the fetch thresholds in `src/sports_calendar/config.py`. By default they are:
```python
FETCH_THRESHOLDS = {
    "far":           {"days": 7,  "recheck_hours": 48},  # next match is more than 7 days away
    "medium":        {"days": 3,  "recheck_hours": 24},  # next match is 3-7 days away
    "near":          {"days": 0,  "recheck_hours": 12},   # next match is within 3 days
    "unknown":       {"recheck_hours": 8,                # no upcoming match found: wait 8h for the first grace_days
                      "grace_days": 3,                   # (playoff gaps between rounds, tennis between rounds and draws),
                      "max_recheck_hours": {"player": 12, "team": 48}},  # then double the wait for each empty result, up to the cap
                                                         # (12h for players: a tennis draw can come out <24h before round 1)
    "imminent":      {"hours": 24, "recheck_hours": 4},  # single match within this many hours
    "imminent_close":{"hours": 3,  "recheck_hours": 1}, # single match within this many hours
    "season":        {"min_matches": 10, "min_recheck_hours": 24},  # a full season (10+ upcoming matches) waits at least 24h:
                                                         # its changes are rare, and each fetch is several pages
    "failed":        {"recheck_hours": 1, "max_recheck_hours": 8,  # last fetch failed: retry after 1h, doubling for each failure in a row, up to 8h;
                      "alert_streak": 3}                          # from this many failures in a row (about 3h), each run sends a fail ping
}
```

Each team is re-fetched at different rates depending on how soon their next match is, so the program avoids unnecessary API calls when games are far away, but checks often when matches are approaching. As a special case, if only one match is found the program applies stricter refresh rates: every `imminent.recheck_hours` when the match is within the `imminent` window, and every `imminent_close.recheck_hours` when it is within the closer `imminent_close` window.

A team with a full season published (`season.min_matches`, 10 or more upcoming matches, for example an NBA team) waits at least `season.min_recheck_hours` (24h), whatever its band. Its next game is always within a few days, so the `near` band alone would fetch it every 12h, and each fetch of a full season reads several pages (3 API calls for an NBA season). A full season changes rarely, mostly in titles and team names, so a change can wait up to a day. The rule only makes a wait longer: a season whose first game is more than 7 days away keeps the `far` interval.

When a team has no upcoming match, it is checked every `unknown.recheck_hours` for the first `unknown.grace_days`. That covers the gap between playoff rounds, when the next game is not announced yet, and a tennis player waiting for the next round or draw. After that, each empty result doubles the wait, up to `unknown.max_recheck_hours`: 12h for players, because a tennis draw can come out less than a day before round 1, and 48h for teams in their off-season. A fetch that finds a match resets this.

A failed fetch (a timeout, an error status or an unreadable response from the API, or a Calendar error) is not treated as "no matches". The team is retried after `failed.recheck_hours`, and the wait doubles for each failure in a row up to `failed.max_recheck_hours`, so one broken team cannot use the daily API budget. A successful fetch returns the team to its normal schedule. An error for one team is logged and does not stop the service or the other teams.

The AllSports free tier allows 100 calls per day (`MAX_API_CALLS`, a hard limit). Normal use should not often go above 70 calls per day (`API_CALL_CEILING`), which leaves headroom for outages and retries. There is no floor: fewer calls are fine. Check the ceiling when you change `FETCH_THRESHOLDS` or add teams (`uv run db-stats avg_daily_calls`).

### Local state files

On first run, the program automatically creates two SQLite database files in the working directory: `api_calls.db` to track your daily API usage, and `fetch_log.db` to remember when each team was last fetched. `fetch_log.db` also holds `fetch_history`, one row per fetch: when, which team, the result (`matches`, `empty` or `failed`), the API calls it used, the matches found, the events created, updated and deleted, and a short error text. These are managed automatically and you don't need to touch them.

On the server, the units set the working directory to `/var/lib/sports-calendar` (systemd `StateDirectory=`, mode 0700). That folder holds the two databases, `queries.json`, `token.json` and `.env`, outside the git checkout, so a `git reset` or `git clean` in the checkout cannot delete the data or the secrets. To query the databases there, run `cd /var/lib/sports-calendar && uv run --project /home/ubuntu/sports_calendar_integration db-stats <function>`.

---

## Running the Script

### Looking up team and player IDs

The `id` values in `queries.json` come from the AllSports API. To look them up, run `get-ids`:

```bash
uv run get-ids
```

You will be prompted to enter a name and sport, shown a list of matches to choose from, and asked to confirm your selection. At the end, the script prints a summary of all selected entries with their IDs. API calls made during the search are tracked against your daily limit. Like the main script, it reads `RAPIDAPI_KEY` from `.env`.

### Adding a team or player

To add an entry to `queries.json` without editing the file by hand:

```bash
uv run add-entity
```

It runs the same search as `get-ids`. For each entity you pick, it asks for the name to use in `queries.json` (Enter keeps the API name) and for the calendar: one of the env keys the file already uses, or another key from `.env`. It refuses an entry whose calendar key is not set in `.env`, a name that is already in the file, and an entity (same id and sport) that is already tracked. At the end it offers to upload the file and deploy.

To upload `queries.json` and deploy later, or after editing it by hand:

```bash
uv run push-queries
```

It sets the `QUERIES_JSON_B64` secret from the file (after checking that it is valid JSON) and starts the deploy workflow on `main`, which writes the file to the VM and starts a run. It runs `gh` as the repository owner's account, not as `gh`'s active account.

### Running the script

Once you have your IDs, create a `queries.json` file in the root directory and run:
```bash
uv run sports-calendar
```

It reads `queries.json`. To read another file, add `--queries <path>`. Each start is one run: it fetches and syncs every team that is due, then exits. On the server, a systemd timer starts a run every 15 minutes (see "Continuous integration").

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

### Reading the logs

The script logs to stderr. Under systemd the lines go to journald, and each line carries its syslog priority, so you can filter by level:

```bash
journalctl -u sports-calendar -f                      # follow the log of the runs
journalctl -u sports-calendar -p warning --since -7d  # only warnings and errors from the last 7 days
journalctl -u sports-calendar-summary --since -7d     # the daily summaries
systemctl list-timers 'sports-calendar*'              # when the next run and the next summary start
```

At INFO level you see each fetch, a one-line summary of the matches found, each event created, updated or deleted, and, after a run that did work, the API call count with the version (git commit). Each day at 00:05, the `sports-calendar-summary` unit logs a summary of the previous day (API calls, fetches by result, event changes; a WARNING when the calls were above `API_CALL_CEILING`); to log it by hand, run `uv run sports-calendar-summary`. systemd adds its own lines for each run (`Starting`, `Finished`, the CPU time), about 4 lines every 15 minutes. Warnings and errors start with their level name (`WARNING`, `ERROR`). Per-match details, "already up to date" events, skipped teams, the token check and the heartbeat line are logged at DEBUG.

### Alerts

The service can report to a free [healthchecks.io](https://healthchecks.io) check. It pings the check at the end of each run, every 15 minutes. When a ping does not come in time, or the service sends a fail ping, healthchecks.io sends you an email. So you hear about runs that hang or fail again and again, a service that needs a new Google login, or a server that is down.

A run that does not crash can still fail at its work, for example with a revoked API key. So when the fetch of a tracked team or player has failed 3 times in a row (`alert_streak` in `FETCH_THRESHOLDS`, about 3 hours with the retry waits), each run sends a fail ping instead of the normal ping, and logs a WARNING with the names. The check stays down until that fetch works again. healthchecks.io sends an email only when the state changes: one when the check goes down, and one when it is back up. The fail ping names no team or player.

1. Create a free account at healthchecks.io and add a check. Set the period to 15 minutes (the timer, `deploy/sports-calendar.timer`) and the grace time to 1 hour, so that a few slow or failed runs do not cause an alert. A Google login failure still alerts at once, with its fail ping.
2. On the server, add the check's ping URL to `.env` as `HEALTHCHECK_URL`. The next run uses it; to run at once, use `sudo systemctl start sports-calendar`. The check on healthchecks.io then shows the ping.
3. To test the email, send one fail ping by hand: `curl -fsS -X POST <ping URL>/fail`. The next ping from the service sets the check back to up.

Without `HEALTHCHECK_URL` the heartbeat is off. Anyone who has the ping URL can ping the check, so the service never logs it. A failed ping is only a warning in the log.

### Renewing the Google login

The service refreshes its Google token by itself. When Google refuses the token (for example after you remove the app's access in your Google account, or after 7 days when the app is still in **Testing**), the service logs an ERROR that starts with `Google Calendar login needed`, sends a fail ping, and the run ends as failed. The timer starts the next run within 15 minutes, and each run checks the token again. To renew the token:

1. On a computer with a browser, in the repo folder with your `credentials.json`, run `uv run google-login`. When the stored token does not work, a browser window opens for the login, and a new `token.json` is saved.
2. Copy it to the server: `scp token.json ubuntu@<server>:/var/lib/sports-calendar/token.json`.
3. The next run, within 15 minutes, uses it. To use it at once and check it, run `sudo systemctl start sports-calendar` on the server: the command returns when the run ends, and it shows an error when the login still fails.

### Running tests
```bash
uv run pytest -v
```

The tests need no secrets. Code gets its collaborators passed in (the AllSports client and its HTTP session, the clock, the prompts, the Calendar service, the Google auth calls and file paths, the heartbeat's HTTP post), so tests hand it the small fakes in `tests/fakes.py`. Nothing in the tests is patched. Only `main()` and `_build_and_run()` in `src/sports_calendar/service.py`, and the `main()` of each command, build the real ones.

### Continuous integration

- `.github/workflows/tests.yml` runs the test suite on every pull request. A ruleset on `main` makes its `test` check required before a merge.
- `.github/workflows/deploy.yml` runs on every push to `main`, or by hand (Actions → "Deploy to Oracle VM" → Run workflow, on `main`), for example after you change the `QUERIES_JSON_B64` secret. It calls the same test workflow first and deploys to the Oracle VM only if the tests pass. It does not upgrade system packages: `unattended-upgrades` on the VM installs security updates daily.
- The deploy script stops at the first failed command (`set -e`). It installs the packages with `uv sync --locked --no-dev` before it changes the units, so a failed install fails the job and the timer is not touched. `--locked` also fails the job when `uv.lock` does not match `pyproject.toml`: run `uv lock` and commit the lock file after each change to the dependencies. The service then starts with `uv run --no-dev`, without pytest and pandas.
- The systemd units are versioned in `deploy/`: `sports-calendar.service` is one run, and `sports-calendar.timer` starts it every 15 minutes; `sports-calendar-summary.service` logs the daily summary, and its timer starts it each day at 00:05 (`Persistent=true`: after a day when the VM was down at 00:05, as soon as it is up). Change the units in the repo, not on the VM.
- Each deploy stops a run that is in progress, installs the 4 unit files to `/etc/systemd/system/`, runs `daemon-reload`, and enables both timers. Then it starts one run and waits for its end, so a deploy whose run fails (for example a broken token or a crash at the start) also fails the job.
- The service is `Type=oneshot`. The unit is active while a run lasts, so the timer never starts a second run at the same time. A oneshot has no start timeout by default, so the unit sets `TimeoutStartSec=5min`: a run that hangs is stopped and cannot block the later runs. There is no `Restart=`: the next run retries a failed one.
- When systemd stops a run (a deploy during a run, a reboot, or a run past `TimeoutStartSec`), it sends SIGTERM. The unit sets `KillMode=mixed`, so only `uv` gets the signal and passes it on to Python, which catches it and exits with code 0: systemd logs a clean stop, not a failure. With the default kill mode, Python and `uv` both get SIGTERM at the same moment, and `uv` exits 143 even when Python exits 0.

---

## Database Analytics

`src/sports_calendar/tools/db_stats.py` provides read-only functions for querying both databases (`api_calls.db` and `fetch_log.db`). It needs pandas (the `stats` group). A plain `uv run` installs it with the `dev` group, also on the server, where the next deploy removes it again. It can be used in two ways:

**As a CLI:** call any function by name, with optional positional arguments:
```bash
uv run db-stats                                         # list all available functions
uv run db-stats calls_today
uv run db-stats stale_entities 24
uv run db-stats calls_in_range 2026-01-01 2026-01-31
uv run db-stats calls_per_entity 7                      # API calls per team over the last 7 days
```

**As a module:** import it directly in a Python script or notebook:
```python
from sports_calendar.tools.db_stats import (
    calls_today, remaining_calls_today, calls_on, calls_in_range,
    days_at_max, pct_days_at_max, avg_daily_calls,
    busiest_days, calls_per_period, api_budget_forecast,
    all_calls_df, all_fetches_df, fetch_gap_df,
    entities_due_now, entities_with_no_upcoming_match, stale_entities,
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
entities_due_now()                      # entities the scheduler would fetch right now
stale_entities(hours=48)                # entities not fetched in over 48 hours (DataFrame)
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