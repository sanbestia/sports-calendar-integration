#!/bin/bash
# Update the VM to origin/main and run the service once. The deploy job pipes this script to `ssh vm bash -s`,
# after a line that sets QUERIES_JSON_B64. On any error it puts back the previous commit, its packages,
# queries.json and the timers, so a failed deploy leaves the VM as it was, and the job still fails.
set -Eeuo pipefail

CHECKOUT=${CHECKOUT:-/home/ubuntu/sports-calendar-integration}
STATE=${STATE:-/var/lib/sports-calendar}  # the units' StateDirectory=
UV=${UV:-/home/ubuntu/.local/bin/uv}
CHECK_QUERIES='import json, sys; from sports_calendar.queries_file import parse_queries; parse_queries(json.load(open(sys.argv[1], encoding="utf-8")))'

main() {
	cd "$CHECKOUT"
	previous=$(git rev-parse HEAD)
	trap put_back_previous ERR
	# no run starts or goes on while the code, the packages and the units change
	sudo systemctl stop sports-calendar.timer sports-calendar.service
	git fetch origin main
	install_code origin/main
	replace_queries
	install_units
	# one run now, which waits for the run to end: a run that fails also fails this job
	sudo systemctl start sports-calendar.service
	trap - ERR
	rm -f "$STATE/queries.json.previous"
}

install_code() {
	git reset --hard "$1"
	# --locked fails on a uv.lock that does not match pyproject.toml
	"$UV" sync --locked --no-dev
}

replace_queries() {
	# systemd creates the state folder at the first start of a unit, but the first deploy comes before it
	sudo install -d -o ubuntu -g ubuntu -m 700 "$STATE"
	printf '%s' "$QUERIES_JSON_B64" | base64 -d > "$STATE/queries.json.new"
	check_queries "$STATE/queries.json.new"
	if [ -f "$STATE/queries.json" ]; then cp -p "$STATE/queries.json" "$STATE/queries.json.previous"; fi
	mv "$STATE/queries.json.new" "$STATE/queries.json"
}

check_queries() {
	# the new code's own parser; its message names entries, so it stays out of the job log, which is public
	if ! "$UV" run --locked --no-dev --project "$CHECKOUT" python -I -c "$CHECK_QUERIES" "$1" 2>/dev/null; then
		echo "The QUERIES_JSON_B64 secret is not a valid queries.json: run push-queries on it to see why" >&2
		return 1
	fi
}

install_units() {
	sudo install -m 644 deploy/sports-calendar.service deploy/sports-calendar.timer deploy/sports-calendar-summary.service deploy/sports-calendar-summary.timer /etc/systemd/system/
	sudo systemctl daemon-reload
	# remove the boot link of the service from before the timer; the timer starts each run now (no-op after the first time)
	sudo systemctl disable sports-calendar.service
	sudo systemctl enable --now sports-calendar.timer sports-calendar-summary.timer
}

put_back_previous() {
	trap - ERR
	set +e  # put back as much as possible, and start the timers again even when a step fails
	echo "Deploy failed: putting back $previous" >&2
	if [ -f "$STATE/queries.json.previous" ]; then mv "$STATE/queries.json.previous" "$STATE/queries.json"; fi
	rm -f "$STATE/queries.json.new"
	install_code "$previous" || echo "Could not put back the code of $previous: fix the VM by hand" >&2
	install_units
	exit 1
}

main  # the whole script is read before main starts, so no command in it can read the rest of stdin
