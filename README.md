# Sports Calendar Integration

Syncs the upcoming matches of your favourite teams and players to Google Calendar. It checks every 15 minutes, and fetches each team or player again from every hour to every two days, by how soon its next match is.

## Features

- Tracks teams and players across sports: football, basketball, tennis, esports and more.
- Creates a calendar event for each upcoming match, and updates it when the time, the teams, the season or the stage change.
- Removes the events of cancelled matches and of matches that disappear from the schedule.
- Stays within the AllSports free tier of 100 API calls a day.
- Can email you through [healthchecks.io](https://healthchecks.io) when it stops, needs a new Google login, or keeps failing to fetch a team.
- Comes with `db-stats`, a command for the API usage and the fetch history.

## Requirements

- Python 3.13 and [uv](https://github.com/astral-sh/uv)
- A [RapidAPI](https://rapidapi.com) key for the [AllSports API](https://rapidapi.com/allsportsapi2/api/allsportsapi2)
- A Google account

## Setup

### 1. Install

```bash
git clone https://github.com/sanbestia/sports-calendar-integration.git
cd sports-calendar-integration
uv sync
```

### 2. Connect Google Calendar

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a project and enable the Google Calendar API (**APIs & Services → Library**).
2. Under **APIs & Services → OAuth consent screen**, choose **External**, fill in the app name and your email, and publish the app (**In production**). An app left in **Testing** loses its login every 7 days. Your own account does not need Google's verification.
3. Under **APIs & Services → Credentials**, create an **OAuth client ID** of type **Desktop app**, and save it as `credentials.json` in the project folder.
4. Run `uv run google-login`. A browser window asks you to allow the app, and then `token.json` is saved for the later runs. If Google warns that it "hasn't verified this app", choose **Advanced → Go to (app name)**.

### 3. Configure

Create `.env` in the project folder:

```env
RAPIDAPI_KEY=your_rapidapi_key
CALENDAR_ID_FOOTBALL=your_calendar_id
CALENDAR_ID_TENNIS=another_calendar_id
# optional: email alerts, see docs/operations.md
HEALTHCHECK_URL=https://hc-ping.com/your-check-uuid
```

Add one `CALENDAR_ID_...` line for each calendar that you want to fill. You find a calendar's ID in Google Calendar under **Settings → (the calendar) → Integrate calendar**.

### 4. Choose the teams and players

The list lives in `queries.json`. The easiest way to fill it is `add-entity`: it searches the API, asks for a name and a calendar, and adds the entry.

```bash
uv run add-entity
```

To only look up IDs, run `uv run get-ids`. Each search uses one API call. You can also write the file by hand:

```json
{
    "Carlos Alcaraz": {"id": "275923", "sport": "tennis", "player_type": "player", "calendar": "CALENDAR_ID_TENNIS"},
    "Argentina - Football National Team": {"id": "4819", "sport": "football", "player_type": "team", "calendar": "CALENDAR_ID_FOOTBALL"}
}
```

`id` is the AllSports ID, as a string of digits. `player_type` is `player` or `team`, and `calendar` is one of the keys in `.env`. A run checks every entry when it starts, and stops with an error that names a bad one.

## Run

```bash
uv run sports-calendar
```

Each run fetches and syncs every team or player that is due, then exits. To read another list, add `--queries <path>`. On a server, a systemd timer starts a run every 15 minutes: see [docs/operations.md](docs/operations.md).

How often each team is fetched depends on its next match. The intervals are `FETCH_THRESHOLDS` in [`src/sports_calendar/config.py`](src/sports_calendar/config.py). When you change them or add teams, check that a normal day stays under 70 calls (`uv run db-stats avg_daily_calls`).

## API usage and history

The runs keep two SQLite files in the working folder: `api_calls.db` (the calls of each day) and `fetch_log.db` (when each team was fetched, and one row for each fetch). `db-stats` reads them:

```bash
uv run db-stats                      # list the functions
uv run db-stats calls_today
uv run db-stats recent_failures 7    # failed fetches with their error, newest first
```

It needs pandas, which `uv sync` installs. The same functions can be imported from `sports_calendar.tools.db_stats`.

## Development

```bash
uv run pytest
```

The tests need no secrets and make no API calls. Every pull request runs them, and a merge to `main` needs them to pass. A merge to `main` also deploys to the server ([docs/operations.md](docs/operations.md)).

Contributions are welcome: fork the repo, make your change on a branch with its tests, and open a pull request.
