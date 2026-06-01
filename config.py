"""Runtime configuration for the Yahoo Fantasy MCP server.

All league-specific settings come from environment variables — there are no
personal defaults baked into the code, so a fork must set its own
``YAHOO_LEAGUE_ID`` rather than silently querying someone else's league.

Environment variables:
    YAHOO_LEAGUE_ID  (required) Yahoo league id, e.g. "12345". This is the
                     *default* league; tools also accept a per-call
                     ``league_id`` override for accounts in multiple leagues
                     (validated against the account's own leagues).
    YAHOO_SPORT      (optional) Yahoo game code; default "mlb".
    YAHOO_SEASON     (optional) Season year, e.g. "2026". If unset, the
                     current season is auto-detected at runtime.
    YAHOO_OAUTH_FILE (optional) Path to the OAuth credentials JSON;
                     default "oauth2.json" in the repo root.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Resolved server configuration."""
    league_id: str
    sport: str = "mlb"
    season: int | None = None          # None -> resolve current season at runtime
    oauth_file: str = "oauth2.json"


def load_config() -> Config:
    """Build a Config from the environment, failing loudly on misconfiguration."""
    league_id = os.environ.get("YAHOO_LEAGUE_ID")
    if not league_id:
        raise RuntimeError(
            "YAHOO_LEAGUE_ID is required and has no default. Set it to your "
            "Yahoo league id (the numeric id in the league URL, e.g. 12345). "
            "See README.md for setup."
        )

    season_raw = os.environ.get("YAHOO_SEASON")
    if season_raw:
        try:
            season: int | None = int(season_raw)
        except ValueError as e:
            raise RuntimeError(
                f"YAHOO_SEASON must be a year like '2026', got {season_raw!r}."
            ) from e
    else:
        season = None  # auto-detect current season

    default_oauth = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "oauth2.json"
    )
    return Config(
        league_id=league_id,
        sport=os.environ.get("YAHOO_SPORT", "mlb"),
        season=season,
        oauth_file=os.environ.get("YAHOO_OAUTH_FILE", default_oauth),
    )
