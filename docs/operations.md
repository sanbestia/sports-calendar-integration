# Operations

How the service runs on the server, and the tasks that come up. The deploy files are in [`deploy/`](../deploy) and [`.github/workflows/`](../.github/workflows).

## Where things are

- The code is a git checkout in `/home/ubuntu/sports-calendar-integration`. Each deploy resets it to `main`, so do not edit it on the server.
- The data and the secrets are in `/var/lib/sports-calendar` (mode 0700), outside the checkout: `.env`, `token.json`, `queries.json`, `api_calls.db` and `fetch_log.db`.
- `sports-calendar.timer` starts one run of `sports-calendar.service` every 15 minutes, and `sports-calendar-summary.timer` logs the summary of the day before at 00:05. Change the units in `deploy/`, not on the server.

## Deploy

Each merge to `main` runs the tests and then deploys. To deploy by hand, open Actions → "Deploy to Oracle VM" → Run workflow, on `main`. The deploy updates the code and its packages, writes `queries.json` from the `QUERIES_JSON_B64` secret, installs the units and starts one run. If a step fails, that run included, it puts the server back as it was, and the job fails. Only one deploy runs at a time.

When a deploy fails, read the job's log in GitHub Actions:

- An error about `uv.lock`: run `uv lock` and commit the lock file.
- `Host key verification failed`: the server has new host keys, for example after a rebuild. Put its public host keys in `deploy/known_hosts`, under the name `sports-calendar-vm`.
- "The QUERIES_JSON_B64 secret is not a valid queries.json": run `uv run push-queries` on your local file to see why. The job log does not show the reason, because it is public.

## Add or change a team or player

Run `uv run add-entity` locally. At the end it offers to upload `queries.json` and deploy. After a hand edit of `queries.json`, run `uv run push-queries`: it checks the file, sets the `QUERIES_JSON_B64` secret and starts a deploy. About a minute later, `journalctl -u sports-calendar` shows the new events.

## Read the logs

```bash
journalctl -u sports-calendar -f                      # follow the runs
journalctl -u sports-calendar -p warning --since -7d  # only warnings and errors, last 7 days
journalctl -u sports-calendar-summary --since -7d     # the daily summaries
systemctl list-timers 'sports-calendar*'              # when the next runs start
```

A run logs each fetch, the matches found and each event change. To see more, set `LOG_LEVEL=DEBUG` in `/var/lib/sports-calendar/.env`; the next run uses it.

## Alerts

The service pings a [healthchecks.io](https://healthchecks.io) check at the end of each run. healthchecks.io emails you when the pings stop, or when a run sends a fail ping: after a Google login failure, or while a team's fetch has failed 3 times in a row.

1. Create a free check with a period of 15 minutes and a grace time of 1 hour.
2. Add its ping URL to `/var/lib/sports-calendar/.env` as `HEALTHCHECK_URL`. Keep the URL secret: anyone who has it can ping the check.
3. To test the email, send a fail ping: `curl -fsS -X POST <ping URL>/fail`. The next run's ping sets the check back to up.

## Renew the Google login

When the log shows `Google Calendar login needed`, the token no longer works, for example after you removed the app's access, or while the app is still in **Testing**:

1. On a computer with a browser, in the repo folder with `credentials.json`, run `uv run google-login`.
2. Copy the new token to the server: `scp token.json ubuntu@<server>:/var/lib/sports-calendar/token.json`.
3. To check it at once, run `sudo systemctl start sports-calendar` on the server. The command returns when the run ends, with an error if the login still fails.

## The API budget

The AllSports free tier allows 100 calls a day, and the service sends no request after that. A normal day should stay under 70 calls, which leaves room for retries. To see the numbers on the server:

```bash
cd /var/lib/sports-calendar && uv run --project /home/ubuntu/sports-calendar-integration db-stats calls_today
```

`db-stats` needs pandas, so this installs the dev packages into the server's environment. The next deploy removes them again.
