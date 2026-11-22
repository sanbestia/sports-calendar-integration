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
    "failed":        {"recheck_hours": 1, "max_recheck_hours": 8}  # last fetch failed: retry after 1h, doubling for each failure in a row, up to 8h
}

MAIN_LOOP_SLEEP_MINUTES = 5

# The AllSports free tier allows 100 calls per day, a hard limit. Normal use should not often go above 70 calls
# per day (API_CALL_CEILING), which leaves headroom for outages and retries. There is no floor: fewer calls are fine. Check the
# ceiling when you change FETCH_THRESHOLDS or add teams (`uv run python -m sports_calendar.tools.db_stats avg_daily_calls`).
MAX_API_CALLS = 100
API_CALL_CEILING = 70  # the daily summary is a WARNING on a day above this

GRAND_SLAM_TOURNAMENTS = ["US Open", "Wimbledon", "Australian Open", "French Open"]

# Additive duration increments (hours); 'default' applies to all sports, sport-specific values stack on top in Match.define_expected_end_time
SPORT_DURATIONS = {
    "default": 2,
    "tennis": 1,
    "grand_slam": 2,
    "esport": 3
}