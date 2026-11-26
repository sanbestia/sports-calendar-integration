import datetime
from dataclasses import dataclass

BASE_DURATION_HOURS = 2  # the expected length of a match in any sport
EXTRA_HOURS_BY_SPORT = {"tennis": 1, "esport": 3}  # added to the base for these sports
GRAND_SLAM_EXTRA_HOURS = 2  # added for tennis at a Grand Slam, where men play best of five sets
GRAND_SLAM_TOURNAMENTS = ("US Open", "Wimbledon", "Australian Open", "French Open")


def expected_duration_hours(sport: str, season: str) -> int:
    """Return how many hours a match of this sport and season is expected to last."""
    hours = BASE_DURATION_HOURS + EXTRA_HOURS_BY_SPORT.get(sport, 0)
    if sport == "tennis" and any(name in season for name in GRAND_SLAM_TOURNAMENTS):  # the API adds the year: "Australian Open 2025"
        hours += GRAND_SLAM_EXTRA_HOURS
    return hours


@dataclass
class Match:
    """One upcoming match, as its calendar event shows it.

    The API sends the IDs as int or str; they are stored as text, so they compare equal to the calendar event's properties.
    """
    side_one: str
    side_two: str
    season: str  # the API's season name, for example "NBA 25/26" or "French Open 2026"
    stage: str
    game_id: int | str
    sport: str
    start_time: datetime.datetime
    home_team_id: int | str | None = None
    away_team_id: int | str | None = None

    def __post_init__(self) -> None:
        self.game_id = str(self.game_id)
        self.home_team_id = str(self.home_team_id) if self.home_team_id is not None else None
        self.away_team_id = str(self.away_team_id) if self.away_team_id is not None else None

    @property
    def expected_end_time(self) -> datetime.datetime:
        """The start time plus the expected length of the match."""
        return self.start_time + datetime.timedelta(hours=expected_duration_hours(self.sport, self.season))

    def __str__(self) -> str:
        """Return a one-line summary: sides, season, stage, and start time."""
        return (f'{self.side_one} vs {self.side_two} - {self.season} ({self.stage}) @ '
                f'{self.start_time.strftime("%Y-%m-%d %H:%M:%S")}')
