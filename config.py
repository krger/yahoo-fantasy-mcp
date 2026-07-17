"""Runtime configuration for the Yahoo Fantasy MCP server.

All league-specific settings come from environment variables — there are no
personal defaults baked into the code, so a fork must set its own
``YAHOO_LEAGUE_ID`` rather than silently querying someone else's league.

Environment variables:
    YAHOO_LEAGUE_ID  (required) Yahoo league id, e.g. "12345". This is the
                     *default* league; tools also accept a per-call
                     ``league_id`` override for accounts in multiple leagues
                     (validated against the account's own leagues).
    YAHOO_SPORT      (optional) Yahoo game code(s); default "mlb". Accepts a
                     comma-separated list (e.g. "mlb,nfl") to serve multiple
                     sports from one deployment — the first is the default
                     sport. Per-call league_id overrides can target any league
                     the authenticated account belongs to across these games.
    YAHOO_SEASON     (optional) Season year, e.g. "2026". If unset, the
                     current season is auto-detected at runtime.
    YAHOO_OAUTH_FILE (optional) Path to the OAuth credentials JSON;
                     default "oauth2.json" in the repo root.
    MCP_ALLOWED_HOSTS (optional) Comma-separated Host header values the MCP
                     transport's DNS-rebinding protection should accept, in
                     addition to the always-allowed loopback hosts. Set this
                     when serving behind a reverse proxy/tunnel that forwards a
                     non-loopback Host (e.g. "example.com,example.com:*"). Unset
                     keeps the stock loopback-only behavior. The value is
                     deployment-specific, so it lives in the environment rather
                     than in the repo.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Resolved server configuration."""
    league_id: str
    sports: tuple[str, ...] = ("mlb",)  # configured Yahoo game codes; [0] is default
    season: int | None = None          # None -> resolve current season at runtime
    oauth_file: str = "oauth2.json"
    allowed_hosts: tuple[str, ...] = ()  # extra Host values for DNS-rebinding allowlist

    @property
    def default_sport(self) -> str:
        """The default sport (first configured game code).

        Used when constructing a league key without discovery (the degraded
        fallback in ``_get_league``) and for the ``yfa.Game`` game-id lookup.
        """
        return self.sports[0]


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

    # YAHOO_SPORT is a comma-separated list of game codes; blanks trimmed.
    # Empty/unset -> the single default sport. The first entry is the default.
    sports = tuple(
        s.strip().lower()
        for s in os.environ.get("YAHOO_SPORT", "mlb").split(",")
        if s.strip()
    ) or ("mlb",)

    default_oauth = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "oauth2.json"
    )

    # Extra Host header values to accept (beyond loopback) when fronted by a
    # reverse proxy/tunnel that forwards a non-loopback Host. Comma-separated;
    # blanks trimmed. Empty -> stock loopback-only DNS-rebinding protection.
    allowed_hosts = tuple(
        h.strip()
        for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    )

    return Config(
        league_id=league_id,
        sports=sports,
        season=season,
        oauth_file=os.environ.get("YAHOO_OAUTH_FILE", default_oauth),
        allowed_hosts=allowed_hosts,
    )
