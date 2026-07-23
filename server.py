"""
Yahoo Fantasy MCP Server

A read-only MCP server that exposes Yahoo Fantasy Sports data (baseball,
football, ...) over a remote, streamable-HTTP MCP endpoint at /mcp (served by
uvicorn). Sport-specific framing (e.g. categories vs points scoring) is derived
from each league's own settings at runtime, so the tools adapt to the league.

Tools:
    - yahoo_get_roster: View any team's roster in the league
    - yahoo_get_standings: League standings
    - yahoo_get_scoreboard: Current/past week matchups
    - yahoo_search_free_agents: Search available free agents
    - yahoo_get_player_stats: Stats for a specific player (includes ownership)
    - yahoo_get_player_ownership: Quick lookup of who owns a player
    - yahoo_get_league_settings: League rules and configuration
    - yahoo_get_matchup: Head-to-head matchup details
    - yahoo_get_transactions: League transaction history (adds, drops, trades)
    - yahoo_list_my_leagues: The leagues the authenticated account belongs to

Tools that operate on a league accept an optional ``league_id`` to target a
league other than the configured default (``cfg.league_id``); see the
``LeagueScopedInput`` schema and ``_get_league``.
"""

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Optional
from urllib.parse import quote

import yahoo_fantasy_api as yfa
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from yahoo_oauth import OAuth2

from config import load_config

# Pydantic input models (the MCP tools' input contract) live in schemas.py;
# import the ones used as handler parameter annotations.
from schemas import (
    GetLeagueSettingsInput,
    GetMatchupInput,
    GetPlayerNotesInput,
    GetPlayerOwnershipInput,
    GetPlayersBatchInput,
    GetPlayerStatsInput,
    GetRosterInput,
    GetScoreboardInput,
    GetStandingsInput,
    GetTakenPlayersInput,
    GetTransactionsInput,
    GetWaiversInput,
    ListTeamsInput,
    SearchFreeAgentsInput,
)

# Pure Yahoo response parsers/normalizers (the unit-test target) live in their
# own module; import the ones the tool handlers and free-agent fetch use.
from yahoo_parsers import (
    ScoringConfig,
    _flatten_raw_yahoo_player,
    _parse_matchup,
    _parse_my_leagues,
    _parse_scoreboard,
    _parse_standings,
    _parse_team_season_stats,
    _rank_season_categories,
    _resolve_team_key,
    build_scoring_config,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# League-specific settings come from the environment (see config.py). Loaded
# once at import so misconfiguration (e.g. a missing YAHOO_LEAGUE_ID) fails
# loudly at startup rather than mid-request.
cfg = load_config()

# Logging — stderr only (stdout is reserved for MCP stdio transport)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("yahoo_fantasy_mcp")


# ---------------------------------------------------------------------------
# Yahoo API helpers
# ---------------------------------------------------------------------------

# Warn at most once per process about loose credential-file permissions, so
# the check (run from the per-request _get_oauth_session) doesn't spam the log.
_oauth_perms_checked = False


def _warn_if_oauth_file_loose() -> None:
    """Warn once if the OAuth credentials file is group/world-accessible.

    ``oauth2.json`` holds the consumer secret and refresh token; it should be
    ``0600``. This is defense-in-depth only — we warn rather than fail so a
    slightly-loose mode doesn't take the server down. POSIX-only; permission
    bits are not meaningful on Windows.
    """
    global _oauth_perms_checked
    if _oauth_perms_checked:
        return
    _oauth_perms_checked = True
    try:
        mode = os.stat(cfg.oauth_file).st_mode
    except OSError:
        return
    if mode & 0o077:
        logger.warning(
            "OAuth credentials file %s is group/world-accessible (mode %03o); "
            "it holds the consumer secret and refresh token. Tighten it with: "
            "chmod 600 %s",
            cfg.oauth_file, mode & 0o777, cfg.oauth_file,
        )


def _get_oauth_session() -> OAuth2:
    """Create or refresh an OAuth2 session from the credentials file."""
    if not os.path.exists(cfg.oauth_file):
        raise FileNotFoundError(
            f"OAuth credentials file not found at {cfg.oauth_file}. "
            "Create oauth2.json with your consumer_key and consumer_secret."
        )
    _warn_if_oauth_file_loose()
    sc = OAuth2(None, None, from_file=cfg.oauth_file)
    if not sc.token_is_valid():
        sc.refresh_access_token()
    return sc


# Cached list of the leagues the authenticated account belongs to (parsed via
# _parse_my_leagues). Membership doesn't change mid-session, so fetch once and
# reuse — and use it to validate per-call league_id overrides.
_my_leagues: Optional[list[dict]] = None


def _get_my_leagues(sc: OAuth2) -> list[dict]:
    """Return the account's leagues (id/key/name/season), fetched once.

    Hits ``users/games/leagues?use_login=1`` (filtered to ``cfg.sports``) and
    parses it with ``_parse_my_leagues``. With multiple configured sports the
    result spans games (e.g. an MLB and an NFL league), each entry carrying its
    own ``league_key``/``game_code``. Degrades to ``[]`` on failure — callers
    treat an empty result as "discovery unavailable" and fall back to permissive
    behavior rather than breaking. The empty result is not cached, so a transient
    failure is retried on the next call.
    """
    global _my_leagues
    if _my_leagues is not None:
        return _my_leagues
    try:
        gm = yfa.Game(sc, cfg.default_sport)
        raw = gm.yhandler.get_leagues_raw(game_codes=list(cfg.sports))
        parsed = _parse_my_leagues(raw)
    except Exception as e:
        logger.warning(f"Could not enumerate account leagues: {e}")
        return []
    _my_leagues = parsed
    return _my_leagues


def _get_league(sc: OAuth2, league_id: Optional[str] = None) -> yfa.League:
    """Get the Yahoo Fantasy league object for ``league_id`` (or the default).

    ``league_id`` is the optional per-call override; when omitted we use
    ``cfg.league_id`` (the configured default). An explicit override is
    validated against the account's own leagues (``_get_my_leagues``) so we
    never query a league the token shouldn't see — but if discovery is
    unavailable (returns ``[]``) we skip validation rather than block.

    Attaches ``league_key`` as an attribute on the returned League so
    downstream helpers (e.g. ``_get_player_ownership``) can reference it
    without reconstructing the key.

    Multi-sport resolution rides on league discovery: a discovered league's
    ``league_key`` already encodes the right game (sport) and season, so we
    prefer it and need no per-call ``sport`` argument. When discovery is
    unavailable (returns ``[]``) or doesn't carry the target, we fall back to
    constructing the key from ``cfg.default_sport``'s game id — honoring
    ``cfg.season`` when pinned, else the current season. That fallback is
    single-sport by nature: an override for a non-default sport can only be
    resolved when discovery is working.
    """
    target = league_id or cfg.league_id
    mine = _get_my_leagues(sc)

    # Validate an explicit, non-default override against the account's leagues.
    if league_id is not None and target != cfg.league_id:
        if mine and target not in {lg["league_id"] for lg in mine}:
            available = ", ".join(
                f'{lg["league_id"]} ({lg["name"]})' for lg in mine
            )
            raise ValueError(
                f"League id {target!r} is not one of your leagues. "
                f"Available: {available}. Use yahoo_list_my_leagues to see them."
            )

    # Prefer the discovered league_key — it already carries the correct game
    # code and season, so the same path resolves an MLB or an NFL league.
    match = next(
        (lg for lg in mine if lg["league_id"] == target and lg.get("league_key")),
        None,
    )
    if match is not None:
        league_key = match["league_key"]
        gm = yfa.Game(sc, match.get("game_code") or cfg.default_sport)
        lg = gm.to_league(league_key)
        lg.league_key = league_key           # stash for later use
        return lg

    # Discovery unavailable/incomplete: construct the key from the default
    # sport's game id (single-sport fallback preserving prior behavior).
    gm = yfa.Game(sc, cfg.default_sport)
    if cfg.season is not None:
        # Season pinned: find the league among that year's leagues.
        for lid in gm.league_ids(year=cfg.season):
            if target in lid:
                lg = gm.to_league(lid)
                lg.league_key = lid          # stash for later use
                return lg
    # No season pinned (auto-detect current), or not found in the pinned
    # season: construct the key directly from the current game id.
    game_id = gm.game_id()
    league_key = f"{game_id}.l.{target}"
    lg = gm.to_league(league_key)
    lg.league_key = league_key           # stash for later use
    return lg


def _resolved_league_id(lg: yfa.League) -> str:
    """The bare numeric league id from a League's attached ``league_key``.

    Used in response payloads so a multi-league response self-identifies with
    the league actually queried rather than the configured default.
    """
    key = str(getattr(lg, "league_key", "") or "")
    return key.split(".l.")[-1] if ".l." in key else cfg.league_id


# Cached scoring configs — a league's categories don't change mid-season, so
# fetch the settings once per process and reuse. Keyed by league_key so that
# multiple leagues don't clobber each other (a single league would inherit the
# wrong category labels otherwise).
_scoring_configs: dict[str, ScoringConfig] = {}


def _get_scoring_config(sc: OAuth2, lg: yfa.League) -> ScoringConfig:
    """Return the league's ScoringConfig, fetching league settings once.

    Built from the raw ``league/{key}/settings`` response (yfa's
    ``League.settings()`` drops ``stat_categories``). Cached per league_key.
    Degrades to ``ScoringConfig.empty()`` if the call fails (and does not cache
    the failure), so labeling falls back to raw stat_ids and standings simply
    omit category ranks rather than erroring.
    """
    key = str(getattr(lg, "league_key", "") or "")
    cached = _scoring_configs.get(key)
    if cached is not None:
        return cached
    try:
        url = (
            f"https://fantasysports.yahooapis.com/fantasy/v2/"
            f"league/{lg.league_key}/settings?format=json"
        )
        resp = sc.session.get(url)
        if resp.status_code != 200:
            logger.warning(f"Settings call returned {resp.status_code}; "
                           "using empty scoring config")
            return ScoringConfig.empty()
        scoring = build_scoring_config(resp.json())
    except Exception as e:
        logger.warning(f"Failed to build scoring config: {e}")
        return ScoringConfig.empty()
    _scoring_configs[key] = scoring
    return scoring


def _format_player(player: dict) -> dict:
    """Extract the most useful fields from a Yahoo player dict."""
    # yahoo_fantasy_api returns nested dicts; flatten the useful bits
    info = {}
    if isinstance(player, dict):
        info["name"] = player.get("name", "Unknown")
        info["position_type"] = player.get("position_type", "")
        info["eligible_positions"] = player.get("eligible_positions", [])
        info["selected_position"] = player.get("selected_position", "")
        info["status"] = player.get("status", "")
        info["status_full"] = player.get("status_full", "")
        # pro_team: the player's pro-sports team abbreviation (e.g. "NYY",
        # "KC"), from Yahoo's sport-neutral editorial_team_abbr field. Emitted
        # under a sport-neutral key so it reads correctly across MLB/NFL/etc.
        info["pro_team"] = player.get("editorial_team_abbr", "")
        info["player_id"] = player.get("player_id", "")
        info["percent_owned"] = player.get("percent_owned", "")
    out = {k: v for k, v in info.items() if v != "" and v != []}
    # Per-category stats are present for free agents (fetched with ;out=stats);
    # roster/other callers won't have them, so only surface when non-empty.
    stats = player.get("stats") if isinstance(player, dict) else None
    if stats:
        out["stats"] = stats
    # Owner team is present only for taken players (fetched with ;out=ownership).
    ownership = player.get("ownership") if isinstance(player, dict) else None
    if ownership:
        out["ownership"] = ownership
    return out


# ---------------------------------------------------------------------------
# Free-agent search helpers
# ---------------------------------------------------------------------------
#
# The yahoo_fantasy_api library's League.free_agents() method only accepts a
# 'position' argument — it silently ignores sort and status. To get real
# sorting we bypass the library and call Yahoo's /players collection endpoint
# directly. That endpoint accepts a set of semicolon-separated filters:
#
#   status=FA|W|A            (free agents, waivers, or all available)
#   position=SS|OF|C|...
#   sort=<named>|<stat_id>   (AR/OR/NAME/PTS/O_AR, or a numeric stat ID)
#   sort_type=season|lastweek|lastmonth|biweekly
#   sort_season=YYYY         (required with sort_type=season)
#   count=N                  (Yahoo caps at 25 per page)
#   start=N                  (pagination offset)

# Stat-name -> Yahoo stat ID (baseball). Covers the categories this tool
# advertises plus a few common extras. Batter K and pitcher K share the
# abbreviation but different IDs; Yahoo disambiguates by the player's position
# context. This table stays baseball-specific on purpose: other sports resolve
# their own category labels via the league-derived ScoringConfig fallback in
# _resolve_sort (so a football league's "Pass Yds"/"Rec TD" sort without a
# hard-coded per-sport table), and a numeric stat ID always works for any sport.
_STAT_NAME_TO_ID = {
    # Hitting
    "R": "7", "H": "8", "2B": "10", "3B": "11", "HR": "12", "RBI": "13",
    "SB": "16", "BB": "18", "K": "21", "SO": "21",
    "AVG": "3", "OBP": "4", "SLG": "5", "OPS": "55", "TB": "23",
    # Pitching
    "IP": "50", "W": "28", "L": "29", "SV": "32", "BS": "33", "HLD": "34",
    "ERA": "26", "WHIP": "27", "K9": "74",
}

# Sort keys Yahoo accepts verbatim (no translation needed).
_NAMED_SORTS = {"AR", "OR", "NAME", "PTS", "O_AR"}


def _resolve_sort(
    sort_key: Optional[str], scoring: Optional[ScoringConfig] = None
) -> tuple[Optional[str], bool]:
    """Translate a user-provided sort key to a Yahoo-valid value.

    Returns (yahoo_sort_value, is_stat_id). ``is_stat_id`` is True when the
    resolved value is a numeric stat ID, which means the caller also needs
    to include sort_type / sort_season in the request.

    Resolution order: verbatim named sorts (AR/OR/…), then a numeric stat ID,
    then the baseball ``_STAT_NAME_TO_ID`` aliases, then — when a ``scoring``
    config is supplied — the league's own category labels. That last,
    sport-neutral fallback lets a football league sort by its stat labels
    (e.g. ``"Pass Yds"``) without a hard-coded per-sport table, while leaving
    baseball's resolution unchanged (its aliases match earlier).
    """
    if not sort_key:
        return None, False
    key = sort_key.strip().upper()
    if key in _NAMED_SORTS:
        return key, False
    if key.isdigit():
        return key, True
    if key in _STAT_NAME_TO_ID:
        return _STAT_NAME_TO_ID[key], True
    # Sport-neutral fallback: match the league's own category labels (from the
    # league-derived ScoringConfig). Covers any sport's scored/informational
    # stats, so football category sorts work with no per-sport alias table.
    if scoring is not None:
        for sid, label in scoring.stat_id_to_name.items():
            if label.upper() == key:
                return sid, True
    # Unknown key: pass through and let Yahoo decide (it will usually fall
    # back to AR ordering). Logged so the caller can diagnose.
    logger.warning(f"Unknown sort key '{sort_key}'; passing to Yahoo as-is")
    return key, False


def _fetch_free_agents_raw(
    sc: OAuth2,
    league_key: str,
    scoring: ScoringConfig,
    *,
    status: str = "FA",
    position: Optional[str] = None,
    sort: Optional[str] = "AR",
    count: int = 25,
    time_period: Optional[str] = None,
    out_fields: str = "percent_owned,stats",
) -> list[dict]:
    """Call Yahoo's /players collection directly and return a list of flat
    player dicts matching the shape ``_format_player`` consumes.

    Sort, status, and position are all applied server-side by Yahoo.
    Pagination is handled transparently so counts above Yahoo's 25-per-page
    cap work as expected. ``out_fields`` controls the ``;out=`` sub-resources
    requested inline — taken-player listings add ``ownership`` to surface the
    owning fantasy team, which free agents/waivers lack.
    """
    sort_value, is_stat_id = _resolve_sort(sort, scoring)

    # Yahoo's per-page cap is 25. Paginate if the caller asked for more.
    PAGE = 25
    requested = max(1, int(count))
    season = date.today().year
    filters: list[str] = []

    if status:
        filters.append(f"status={quote(status, safe='')}")
    if position:
        filters.append(f"position={quote(position, safe='')}")
    if sort_value:
        filters.append(f"sort={quote(sort_value, safe='')}")
        if is_stat_id:
            # Stat-ID sorts need a sort_type; default to season-to-date. A
            # recent-form window (lastweek/lastmonth/biweekly) ranks by that
            # period instead; only "season" takes a sort_season.
            period = time_period or "season"
            filters.append(f"sort_type={period}")
            if period == "season":
                filters.append(f"sort_season={season}")

    collected: list[dict] = []
    start = 0
    while len(collected) < requested:
        page_filters = filters + [f"count={PAGE}", f"start={start}"]
        # Request percent_owned + season stats (and, for taken players,
        # ownership) inline so each player carries them without a follow-up call.
        filter_str = ";".join(page_filters) + f";out={out_fields}"
        url = (
            f"https://fantasysports.yahooapis.com/fantasy/v2/"
            f"league/{league_key}/players;{filter_str}?format=json"
        )

        resp = sc.session.get(url)
        if resp.status_code != 200:
            logger.warning(
                f"Players collection returned {resp.status_code}: {resp.text[:200]}"
            )
            break

        data = resp.json()
        league_data = data.get("fantasy_content", {}).get("league", [])
        if not isinstance(league_data, list) or len(league_data) < 2:
            break
        players_block = league_data[1].get("players")
        if not isinstance(players_block, dict):
            break

        page_players = []
        for key, entry in players_block.items():
            if key == "count":
                continue
            if isinstance(entry, dict) and "player" in entry:
                flat = _flatten_raw_yahoo_player(entry["player"], scoring)
                if flat:
                    page_players.append(flat)

        if not page_players:
            break
        collected.extend(page_players)
        if len(page_players) < PAGE:
            break  # reached the end of available results
        start += PAGE

    return collected[:requested]


def _fetch_player_stats_by_keys(
    sc: OAuth2, league_key: str, player_keys: list[str], scoring: ScoringConfig
) -> dict[str, dict]:
    """Fetch season category stats for specific players, keyed by player_id.

    Uses the league players collection with an explicit ``player_keys`` filter
    and ``;out=stats`` — the same response shape ``_flatten_raw_yahoo_player``
    already parses for free agents. Chunked to Yahoo's 25-per-request cap.
    Returns ``{player_id: stats_map}`` for players that had stats.
    """
    out: dict[str, dict] = {}
    PAGE = 25
    for i in range(0, len(player_keys), PAGE):
        chunk = player_keys[i:i + PAGE]
        keys_csv = quote(",".join(chunk), safe=",")
        url = (
            f"https://fantasysports.yahooapis.com/fantasy/v2/"
            f"league/{league_key}/players;player_keys={keys_csv};out=stats?format=json"
        )
        resp = sc.session.get(url)
        if resp.status_code != 200:
            logger.warning(
                f"Roster stats fetch returned {resp.status_code}: {resp.text[:200]}"
            )
            continue
        league_data = resp.json().get("fantasy_content", {}).get("league", [])
        if not isinstance(league_data, list) or len(league_data) < 2:
            continue
        players_block = league_data[1].get("players")
        if not isinstance(players_block, dict):
            continue
        for key, entry in players_block.items():
            if key == "count" or not isinstance(entry, dict) or "player" not in entry:
                continue
            flat = _flatten_raw_yahoo_player(entry["player"], scoring)
            pid = flat.get("player_id")
            if pid is not None and flat.get("stats"):
                out[str(pid)] = flat["stats"]
    return out


def _get_player_ownership(sc: OAuth2, league_key: str, player_id) -> dict:
    """Look up which fantasy team owns a player via the Yahoo ownership API.

    Uses the /players;player_keys={key}/ownership sub-resource to determine
    whether a player is rostered, on waivers, or a free agent — and if
    rostered, which fantasy team owns them.

    Args:
        sc: Active OAuth2 session.
        league_key: The league key (e.g. '469.l.12345').
        player_id: The Yahoo player ID (numeric).

    Returns:
        dict with ownership details: owned (bool), owner_team_key,
        owner_team_name, and ownership_type.
    """
    try:
        game_id = league_key.split(".")[0]
        player_key = f"{game_id}.p.{player_id}"
        url = (
            f"https://fantasysports.yahooapis.com/fantasy/v2/"
            f"league/{league_key}/players;player_keys={player_key}"
            f"/ownership?format=json"
        )
        resp = sc.session.get(url)
        if resp.status_code != 200:
            logger.warning(
                f"Ownership API returned {resp.status_code} for player {player_id}"
            )
            return {"ownership_error": f"HTTP {resp.status_code}"}

        data = resp.json()

        # Navigate Yahoo's nested response structure:
        # fantasy_content.league[1].players."0".player[1].ownership
        fc = data.get("fantasy_content", {})
        league_data = fc.get("league", [])

        if not isinstance(league_data, list) or len(league_data) < 2:
            return {"owned": False, "ownership_type": "unknown"}

        players_block = league_data[1].get("players", {})
        player_entry = players_block.get("0", {}).get("player", [])

        # The player entry is a list; ownership is in one of the dicts
        for item in player_entry:
            if isinstance(item, dict) and "ownership" in item:
                ownership = item["ownership"]
                otype = ownership.get("ownership_type", "")

                if otype == "team":
                    return {
                        "owned": True,
                        "ownership_type": "team",
                        "owner_team_key": ownership.get("owner_team_key", ""),
                        "owner_team_name": ownership.get("owner_team_name", ""),
                    }
                else:
                    return {
                        "owned": False,
                        "ownership_type": otype,  # "freeagents", "waivers", etc.
                    }

            # Sometimes it's nested inside a list within the list
            if isinstance(item, list):
                for sub in item:
                    if isinstance(sub, dict) and "ownership" in sub:
                        ownership = sub["ownership"]
                        otype = ownership.get("ownership_type", "")
                        if otype == "team":
                            return {
                                "owned": True,
                                "ownership_type": "team",
                                "owner_team_key": ownership.get("owner_team_key", ""),
                                "owner_team_name": ownership.get("owner_team_name", ""),
                            }
                        else:
                            return {
                                "owned": False,
                                "ownership_type": otype,
                            }

        return {"owned": False, "ownership_type": "unknown"}

    except Exception as e:
        logger.warning(f"Failed to get ownership for player {player_id}: {e}")
        return {"ownership_error": str(e)}


def _handle_error(e: Exception) -> str:
    """Consistent error formatting."""
    error_type = type(e).__name__
    if "401" in str(e) or "Unauthorized" in str(e):
        return (
            f"Error: Authentication failed ({error_type}). "
            "Your OAuth tokens may have expired. Try deleting oauth2.json "
            "and re-authenticating."
        )
    if "403" in str(e) or "Forbidden" in str(e):
        return (
            f"Error: Access denied ({error_type}). "
            "You may not have permission to access this resource."
        )
    if "404" in str(e) or "not found" in str(e).lower():
        return f"Error: Resource not found ({error_type}). Check the ID or key."
    return f"Error: {error_type} — {e}"


# ---------------------------------------------------------------------------
# Lifespan — initialize Yahoo connection once
# ---------------------------------------------------------------------------

@asynccontextmanager
async def app_lifespan(server):
    """Initialize the Yahoo OAuth session and league on startup."""
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc, None)  # configured default league at startup
        logger.info(f"Connected to Yahoo Fantasy league {cfg.league_id}")
        yield {"sc": sc, "lg": lg}
    except Exception as e:
        logger.error(f"Failed to initialize Yahoo connection: {e}")
        yield {"sc": None, "lg": None, "init_error": str(e)}


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

# DNS-rebinding protection for the streamable-HTTP transport. The MCP library
# auto-enables a loopback-only allowlist when transport_security is omitted; we
# pass an explicit one only when MCP_ALLOWED_HOSTS adds deployment-specific
# hostnames (e.g. the public name a reverse proxy/tunnel forwards). Loopback
# hosts/origins are always included so local access (the documented healthcheck,
# local dev) keeps working; an unset MCP_ALLOWED_HOSTS leaves the stock
# behavior. The deployment's hostname lives in the environment, not the repo.
_LOOPBACK_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_LOOPBACK_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]

_transport_security: Optional[TransportSecuritySettings] = None
if cfg.allowed_hosts:
    _transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_LOOPBACK_HOSTS + list(cfg.allowed_hosts),
        allowed_origins=_LOOPBACK_ORIGINS,
    )

mcp = FastMCP(
    "yahoo_fantasy_mcp",
    lifespan=app_lifespan,
    transport_security=_transport_security,
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="yahoo_get_roster",
    annotations={
        "title": "Get Team Roster",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def yahoo_get_roster(params: GetRosterInput = GetRosterInput()) -> str:
    """Get the roster for a team in the fantasy league.

    Returns each player's name, position, eligible positions, pro team,
    and injury status. Use team_number to view another manager's roster,
    or omit it for your own. Pass 'day' (YYYY-MM-DD) to see the roster
    as set for a specific date (past or future), or 'week' for a scoring
    week. If both are given, 'day' wins.

    Args:
        params (GetRosterInput): Validated input parameters containing:
            - team_number (Optional[int]): Team number (1-based), or None for own team.
            - week (Optional[int]): Scoring week, or None for current week.
            - day (Optional[str]): Date string YYYY-MM-DD for a specific day's lineup.

    Returns:
        str: JSON list of players on the roster.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc, params.league_id)

        teams = lg.teams()

        team_key = _resolve_team_key(lg, teams, params.team_number)
        if team_key is None:
            return (
                f"Error: Team number {params.team_number} not found. "
                f"League has {len(teams)} teams (valid range: 1-{len(teams)})."
            )

        tm = lg.to_team(team_key)

        # 'day' takes precedence over 'week' when both are provided.
        day_obj = None
        if params.day:
            try:
                day_obj = datetime.strptime(params.day, "%Y-%m-%d").date()
            except ValueError:
                return f"Error: Invalid day '{params.day}'. Expected YYYY-MM-DD."

        if day_obj is not None:
            roster = tm.roster(day=day_obj)
        elif params.week is not None:
            roster = tm.roster(week=params.week)
        else:
            roster = tm.roster()

        team_name = teams[team_key].get("name", f"Team {params.team_number or '?'}")
        formatted = [_format_player(p) for p in roster]

        # Optionally enrich each player with season category totals (one
        # batched call), merged in by player_id.
        if params.include_stats and formatted:
            game_id = lg.league_key.split(".")[0]
            pkeys = [
                f"{game_id}.p.{p['player_id']}"
                for p in formatted
                if p.get("player_id")
            ]
            stats_by_id = _fetch_player_stats_by_keys(
                sc, lg.league_key, pkeys, _get_scoring_config(sc, lg)
            )
            for p in formatted:
                stats = stats_by_id.get(str(p.get("player_id", "")))
                if stats:
                    p["stats"] = stats

        result = {
            "team_name": team_name,
            "team_key": team_key,
            "week": params.week if params.day is None else None,
            "day": params.day,
            "scope": "day" if params.day else ("week" if params.week else "current"),
            "roster_count": len(formatted),
            "players": formatted,
        }
        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="yahoo_get_standings",
    annotations={
        "title": "Get League Standings",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def yahoo_get_standings(params: GetStandingsInput = GetStandingsInput()) -> str:
    """Get current league standings including win-loss records and rankings.

    Returns all teams ranked by their current standing, each with a numeric
    ``record`` (wins/losses/ties/pct), rank, playoff seed, games_back, and a
    ``categories`` list of season totals per scoring category with a league
    ``rank`` (ERA/WHIP ranked low-first). Category totals come from a separate
    Yahoo call; if it fails, standings still return without ``categories``.

    Args:
        params (GetStandingsInput): Validated input containing:
            - league_id (Optional[str]): League override, or None for default.

    Returns:
        str: JSON array of teams sorted by standing.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc, params.league_id)
        standings = lg.standings()

        # Season category totals come from a separate teams/stats call; degrade
        # gracefully to records-only if it fails rather than dropping standings.
        # Skip it for a points league (fantasy football): per-category ranking
        # isn't meaningful there — standings rank by record + points-for, which
        # _parse_standings surfaces directly from the standings entries.
        scoring = _get_scoring_config(sc, lg)
        season_categories = None
        if not scoring.is_points_league:
            try:
                raw_stats = lg.yhandler.get(f"league/{lg.league_key}/teams/stats")
                season_categories = _rank_season_categories(
                    _parse_team_season_stats(raw_stats), scoring,
                )
            except Exception as e:
                logger.warning(f"Could not fetch season category totals: {e}")

        result = {
            "league_id": _resolved_league_id(lg),
            "team_count": len(standings),
            "standings": _parse_standings(standings, season_categories),
        }
        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="yahoo_get_scoreboard",
    annotations={
        "title": "Get League Scoreboard",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def yahoo_get_scoreboard(params: GetScoreboardInput = GetScoreboardInput()) -> str:
    """Get the league scoreboard showing all matchups for a given week.

    Shows each head-to-head matchup with team names and scores/categories.

    Args:
        params (GetScoreboardInput): Validated input containing:
            - week (Optional[int]): Scoring week, or None for current week.

    Returns:
        str: JSON object with all matchups for the week.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc, params.league_id)

        week = params.week if params.week is not None else lg.current_week()
        scoreboard = lg.matchups(week=week)

        result = {
            "league_id": _resolved_league_id(lg),
            "week": week,
            "matchups": _parse_scoreboard(scoreboard, _get_scoring_config(sc, lg)),
        }
        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="yahoo_search_free_agents",
    annotations={
        "title": "Search Free Agents",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def yahoo_search_free_agents(
    params: SearchFreeAgentsInput = SearchFreeAgentsInput(),
) -> str:
    """Search for available free agents in the league.

    Filter by position and sort by various stat categories to find
    pickup targets. Returns player name, positions, pro team, ownership
    percentage, and a ``stats`` map of the player's season totals per
    scoring category (derived from the league's own settings — e.g. baseball
    R/HR/RBI/SB/AVG and W/SV/K/ERA/WHIP, or football passing/rushing/receiving
    yards and TDs).

    Args:
        params (SearchFreeAgentsInput): Validated input containing:
            - position (Optional[str]): Position filter using the league's
              position codes (baseball: C, 1B, ..., SP, RP; football: QB, RB,
              WR, TE, K, DEF). If omitted, returns all positions.
            - sort (Optional[str]): Sort key. Named values Yahoo accepts
              verbatim: AR (actual rank, default), OR (overall rank), NAME,
              PTS, O_AR. Baseball stat abbreviations translate to Yahoo stat
              IDs server-side, and any league's own category labels also
              resolve (e.g. football 'Pass Yds', 'Rec TD'); a numeric Yahoo
              stat ID always works. Note: PTS returns no results in a
              head-to-head *categories* league (e.g. baseball) — sort by AR
              or a stat there — but is the natural sort in a *points* league
              (typical football).
            - count (Optional[int]): Number of results (default 25, max 50).
              Yahoo caps each request at 25 players, so larger counts
              paginate automatically.
            - status (Optional[str]): Availability. FA = free agents only
              (default), W = waivers only, A = all available (FA + W).

    Returns:
        str: JSON list of available players sorted by the specified key.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc, params.league_id)

        # Bypass yahoo_fantasy_api.League.free_agents() — it only accepts
        # 'position' and silently drops sort/status. Go to the Yahoo
        # /players collection endpoint directly so all three filters apply
        # server-side.
        fa = _fetch_free_agents_raw(
            sc,
            lg.league_key,
            _get_scoring_config(sc, lg),
            status=params.status or "FA",
            position=params.position,
            sort=params.sort or "AR",
            count=params.count or 25,
            time_period=params.time_period,
        )

        formatted = [_format_player(p) for p in fa]

        result = {
            "position_filter": params.position or "all",
            "sort_by": params.sort,
            "time_period": params.time_period or "season",
            "status": params.status,
            "count": len(formatted),
            "players": formatted,
        }
        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="yahoo_get_waivers",
    annotations={
        "title": "Get Waiver-Wire Players",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def yahoo_get_waivers(params: GetWaiversInput = GetWaiversInput()) -> str:
    """List the players currently on waivers.

    Waivers are players who have been dropped and are serving a waiver
    period before clearing to free agency — distinct from immediately
    addable free agents (``yahoo_search_free_agents``). Surfacing them lets
    you spot a just-dropped useful player before he clears and time a claim
    (place the claim in the Yahoo UI — this server is read-only). Returns the
    same per-player shape as the free-agent search: name, positions, pro
    team, percent owned, and a ``stats`` map of season category totals.

    Args:
        params (GetWaiversInput): Validated input containing:
            - position (Optional[str]): Position filter (league's position
              codes, e.g. SP/RP or QB/RB/WR).
            - sort (Optional[str]): Sort key (AR default, or a stat — same
              vocabulary as yahoo_search_free_agents).
            - count (Optional[int]): Number of results (default 25, max 50).
            - time_period (Optional[str]): Stat-sort window (season default,
              lastweek/lastmonth/biweekly for recent form).

    Returns:
        str: JSON list of players on waivers, sorted by the given key.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc, params.league_id)

        # Waivers are the Yahoo /players collection with status=W — the same
        # path as free agents, narrowed to the waiver wire.
        players = _fetch_free_agents_raw(
            sc,
            lg.league_key,
            _get_scoring_config(sc, lg),
            status="W",
            position=params.position,
            sort=params.sort or "AR",
            count=params.count or 25,
            time_period=params.time_period,
        )

        formatted = [_format_player(p) for p in players]

        result = {
            "league_id": _resolved_league_id(lg),
            "position_filter": params.position or "all",
            "sort_by": params.sort,
            "time_period": params.time_period or "season",
            "count": len(formatted),
            "players": formatted,
        }
        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="yahoo_get_taken_players",
    annotations={
        "title": "Get Taken (Rostered) Players",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def yahoo_get_taken_players(
    params: GetTakenPlayersInput = GetTakenPlayersInput(),
) -> str:
    """List every rostered (taken) player across the league, with owner team.

    The inverse of free agents: all players currently owned by a team,
    each annotated with the fantasy team that holds them (``ownership``:
    ``owner_team_key`` / ``owner_team_name``) — a league-wide ownership map
    no other tool produces. Use it to find trade targets by category need
    (e.g. who holds the closers/SB sources, or the RB depth) without
    fetching all teams' rosters one by one. Each player also carries
    positions, pro team, percent owned, and a ``stats`` map of season
    category totals.

    Args:
        params (GetTakenPlayersInput): Validated input containing:
            - position (Optional[str]): Position filter (league's position
              codes, e.g. SP/RP or QB/RB/WR).
            - sort (Optional[str]): Sort key (AR default, or a stat — same
              vocabulary as yahoo_search_free_agents).
            - count (Optional[int]): Number of results (default 300 to cover
              every rostered player; max 500).
            - time_period (Optional[str]): Stat-sort window (season default).

    Returns:
        str: JSON list of rostered players, each with its owning team.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc, params.league_id)

        # Taken players are the /players collection with status=T. Add
        # ownership to ;out= so each player carries its owning fantasy team —
        # the field that distinguishes this from a free-agent listing.
        players = _fetch_free_agents_raw(
            sc,
            lg.league_key,
            _get_scoring_config(sc, lg),
            status="T",
            position=params.position,
            sort=params.sort or "AR",
            count=params.count or 300,
            time_period=params.time_period,
            out_fields="percent_owned,stats,ownership",
        )

        formatted = [_format_player(p) for p in players]

        result = {
            "league_id": _resolved_league_id(lg),
            "position_filter": params.position or "all",
            "sort_by": params.sort,
            "time_period": params.time_period or "season",
            "count": len(formatted),
            "players": formatted,
        }
        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="yahoo_get_player_stats",
    annotations={
        "title": "Get Player Stats",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def yahoo_get_player_stats(params: GetPlayerStatsInput) -> str:
    """Search for a player by name and return their stats and details.

    Searches across all players (rostered and free agents) by name.
    Returns stats, ownership info (including which fantasy team owns them),
    eligible positions, and pro team.

    Args:
        params (GetPlayerStatsInput): Validated input containing:
            - player_name (str): Full or partial player name to search.

    Returns:
        str: JSON object with player details, stats, and ownership info,
             or an error if the player is not found.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc, params.league_id)

        # yahoo_fantasy_api's player search
        try:
            results = lg.player_details(params.player_name)
        except Exception:
            # Fallback: try searching free agents
            results = None

        if not results:
            return json.dumps({
                "error": f"No player found matching '{params.player_name}'.",
                "suggestion": "Try a shorter or different spelling of the name.",
            })

        # If results is a list, return all matches; if dict, wrap it
        if isinstance(results, dict):
            results = [results]

        formatted = []
        for player in results[:5]:  # Cap at 5 results
            info = _format_player(player) if isinstance(player, dict) else player

            # Fetch ownership for each matched player
            pid = None
            if isinstance(info, dict):
                pid = info.get("player_id")
            elif isinstance(player, dict):
                pid = player.get("player_id")

            if pid:
                ownership = _get_player_ownership(sc, lg.league_key, pid)
                # BUGFIX: lg.player_details() never hits /stats, so fetch it explicitly.
                try:
                    game_key = str(lg.league_key).split(".")[0]
                    pkey = f"{game_key}.p.{pid}"
                    stats_url = (
                        "https://fantasysports.yahooapis.com/fantasy/v2/"
                        f"players;player_keys={pkey}/stats?format=json"
                    )
                    sresp = sc.session.get(stats_url)
                    sresp.raise_for_status()
                    sdata = sresp.json()
                    if isinstance(info, dict):
                        info["player_stats"] = sdata.get("fantasy_content", sdata)
                except Exception as se:
                    logger.error(f"player_stats fetch failed for {pid}: {se}")
                    if isinstance(info, dict):
                        info["player_stats_error"] = str(se)
                if isinstance(info, dict):
                    info["ownership"] = ownership
                else:
                    info = {"player": info, "ownership": ownership}

            formatted.append(info)

        return json.dumps({
            "query": params.player_name,
            "matches": len(formatted),
            "players": formatted,
        }, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="yahoo_get_player_ownership",
    annotations={
        "title": "Get Player Ownership",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def yahoo_get_player_ownership(params: GetPlayerOwnershipInput) -> str:
    """Look up which fantasy team owns a specific player.

    Searches for a player by name, then checks the Yahoo ownership API
    to determine if they are rostered (and by whom), on waivers, or a
    free agent. Faster than scanning rosters manually.

    Args:
        params (GetPlayerOwnershipInput): Validated input containing:
            - player_name (str): Full or partial player name to look up.

    Returns:
        str: JSON object with player name, pro team, and ownership details
             (owner team name/key if rostered, or availability status).
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc, params.league_id)

        # Find the player first
        try:
            results = lg.player_details(params.player_name)
        except Exception:
            results = None

        if not results:
            return json.dumps({
                "error": f"No player found matching '{params.player_name}'.",
                "suggestion": "Try a shorter or different spelling of the name.",
            })

        if isinstance(results, dict):
            results = [results]

        # Look up ownership for the first (best) match
        player = results[0]
        info = _format_player(player) if isinstance(player, dict) else {}
        pid = info.get("player_id") or (
            player.get("player_id") if isinstance(player, dict) else None
        )

        if not pid:
            return json.dumps({
                "error": "Could not determine player ID for ownership lookup.",
                "player": info,
            })

        ownership = _get_player_ownership(sc, lg.league_key, pid)

        return json.dumps({
            "query": params.player_name,
            "player_name": info.get("name", params.player_name),
            "player_id": pid,
            "pro_team": info.get("pro_team", ""),
            "eligible_positions": info.get("eligible_positions", []),
            "ownership": ownership,
        }, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="yahoo_get_league_settings",
    annotations={
        "title": "Get League Settings",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def yahoo_get_league_settings(
    params: GetLeagueSettingsInput = GetLeagueSettingsInput(),
) -> str:
    """Get the league's configuration, rules, and scoring settings.

    Returns roster positions, stat categories, scoring type, number of teams,
    trade deadline, playoff settings, and other league rules.

    Args:
        params (GetLeagueSettingsInput): Validated input containing:
            - league_id (Optional[str]): League override, or None for default.

    Returns:
        str: JSON object with complete league settings.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc, params.league_id)
        settings = lg.settings()

        return json.dumps({
            "league_id": _resolved_league_id(lg),
            "settings": settings,
        }, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="yahoo_get_matchup",
    annotations={
        "title": "Get Matchup Details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def yahoo_get_matchup(params: GetMatchupInput = GetMatchupInput()) -> str:
    """Get detailed head-to-head matchup information for a specific team.

    Shows the matchup opponent, category-by-category breakdown,
    and current score for the specified week.

    Args:
        params (GetMatchupInput): Validated input containing:
            - team_number (Optional[int]): Team number, or None for own team.
            - week (Optional[int]): Scoring week, or None for current week.

    Returns:
        str: JSON object with matchup details including both teams' stats.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc, params.league_id)

        teams = lg.teams()

        team_key = _resolve_team_key(lg, teams, params.team_number)
        if team_key is None:
            return (
                f"Error: Team number {params.team_number} not found. "
                f"League has {len(teams)} teams."
            )

        tm = lg.to_team(team_key)

        # Team.matchup() requires an explicit week; default to the league's
        # current week when the caller omits one. It only returns the
        # opponent's key, so parse the raw response for the full breakdown.
        week = params.week if params.week is not None else lg.current_week()
        raw = tm.yhandler.get_matchup_raw(team_key, week)
        matchup = _parse_matchup(raw, team_key, _get_scoring_config(sc, lg))

        team_name = teams[team_key].get("name", "Unknown")

        result = {
            "team_name": team_name,
            "team_key": team_key,
            "week": week,
            "matchup": matchup,
        }
        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="yahoo_get_transactions",
    annotations={
        "title": "Get League Transactions",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def yahoo_get_transactions(
    params: GetTransactionsInput = GetTransactionsInput(),
) -> str:
    """Get recent transaction history for the league.

    Returns adds, drops, add/drops, and trades across the league so you
    can see what other managers are doing.  Filter by transaction type
    or by a specific team.

    Args:
        params (GetTransactionsInput): Validated input containing:
            - transaction_types (Optional[list]): Filter by add, drop, add/drop, trade.
            - team_number (Optional[int]): Filter to a specific team (1-based).
            - count (Optional[int]): Number of results (default 25, max 50).

    Returns:
        str: JSON list of recent transactions with player/team details.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc, params.league_id)

        # Build the transactions API URL
        url = (
            f"https://fantasysports.yahooapis.com/fantasy/v2/"
            f"league/{lg.league_key}/transactions"
        )

        # Add type filter if specified
        if params.transaction_types:
            types_str = ",".join(t.value for t in params.transaction_types)
            url += f";types={types_str}"

        # Add count
        url += f";count={params.count}"

        url += "?format=json"

        resp = sc.session.get(url)
        if resp.status_code != 200:
            return json.dumps({
                "error": f"Yahoo API returned HTTP {resp.status_code}",
            })

        data = resp.json()

        # Dump the raw response structure for debugging
        logger.debug(
            "Raw transactions response:\n%s",
            json.dumps(data, indent=2, default=str)[:5000],
        )

        # Navigate Yahoo's nested response:
        # fantasy_content.league[1].transactions
        fc = data.get("fantasy_content", {})
        league_data = fc.get("league", [])

        if not isinstance(league_data, list) or len(league_data) < 2:
            return json.dumps({"transactions": [], "count": 0})

        # league_data[1] is usually a dict, but Yahoo sometimes wraps
        # it in a list of dicts — handle both.
        raw_txns = league_data[1]
        if isinstance(raw_txns, dict):
            txns_block = raw_txns.get("transactions", {})
        elif isinstance(raw_txns, list):
            txns_block = {}
            for item in raw_txns:
                if isinstance(item, dict) and "transactions" in item:
                    txns_block = item["transactions"]
                    break
        else:
            txns_block = {}

        # If filtering by team, resolve team_key
        filter_team_key = None
        if params.team_number is not None:
            teams = lg.teams()
            filter_team_key = _resolve_team_key(lg, teams, params.team_number)
            if filter_team_key is None:
                return json.dumps({
                    "error": (
                        f"Team number {params.team_number} not found. "
                        f"League has {len(teams)} teams."
                    ),
                })

        # Parse each transaction
        transactions = []
        for key, txn_data in txns_block.items():
            if key == "count":
                continue
            if not isinstance(txn_data, dict):
                continue

            txn_entry = txn_data.get("transaction", [])
            if not isinstance(txn_entry, list) or len(txn_entry) < 2:
                continue

            # First element has transaction metadata.
            # For add/drop transactions Yahoo may wrap metadata in a list
            # of dicts instead of a single dict.
            raw_meta = txn_entry[0]
            if isinstance(raw_meta, dict):
                meta = raw_meta
            elif isinstance(raw_meta, list):
                meta = {}
                for m in raw_meta:
                    if isinstance(m, dict):
                        meta.update(m)
            else:
                meta = {}
            txn_type = meta.get("type", "")
            timestamp = meta.get("timestamp", "")
            status = meta.get("status", "")

            # Second element has the players involved.
            # For add/drop transactions Yahoo may return a list instead
            # of a dict, so we need to search for the "players" key.
            raw_players = txn_entry[1] if len(txn_entry) > 1 else {}
            if isinstance(raw_players, dict):
                players_data = raw_players.get("players", {})
            elif isinstance(raw_players, list):
                players_data = {}
                for item in raw_players:
                    if isinstance(item, dict) and "players" in item:
                        players_data = item["players"]
                        break
            else:
                players_data = {}

            players = []
            if not isinstance(players_data, dict):
                # If players_data is a list, try to find dicts with player keys
                if isinstance(players_data, list):
                    converted = {}
                    for idx_p, pd in enumerate(players_data):
                        if isinstance(pd, dict):
                            converted[str(idx_p)] = pd
                    players_data = converted
                else:
                    players_data = {}
            for pkey, pval in players_data.items():
                if pkey == "count":
                    continue
                if not isinstance(pval, dict):
                    continue

                player_entry = pval.get("player", [])
                if not isinstance(player_entry, list):
                    continue

                # Extract player info from the nested lists
                player_info = {}
                transaction_data = {}

                def _extract_name(val):
                    """Yahoo returns name as a dict or a plain string."""
                    if isinstance(val, dict):
                        return val.get("full", str(val))
                    return str(val)

                def _extract_player_fields(d):
                    """Pull player fields from a dict, guarding types."""
                    if "name" in d:
                        player_info["name"] = _extract_name(d["name"])
                    if "editorial_team_abbr" in d:
                        player_info["pro_team"] = d["editorial_team_abbr"]
                    if "display_position" in d:
                        player_info["position"] = d["display_position"]

                def _extract_txn_data(d):
                    """Pull transaction_data from a dict, guarding types."""
                    td = d["transaction_data"]
                    if not isinstance(td, dict):
                        # Sometimes wrapped in a list of dicts
                        if isinstance(td, list):
                            merged = {}
                            for item_td in td:
                                if isinstance(item_td, dict):
                                    merged.update(item_td)
                            td = merged
                        else:
                            return
                    transaction_data["action"] = td.get("type", "")
                    dest = td.get("destination_team_name", "")
                    src = td.get("source_team_name", "")
                    dest_key = td.get("destination_team_key", "")
                    src_key = td.get("source_team_key", "")
                    if dest:
                        transaction_data["destination_team"] = dest
                        transaction_data["destination_team_key"] = dest_key
                    if src:
                        transaction_data["source_team"] = src
                        transaction_data["source_team_key"] = src_key

                for item in player_entry:
                    if isinstance(item, list):
                        for sub in item:
                            if isinstance(sub, dict):
                                _extract_player_fields(sub)
                    elif isinstance(item, dict):
                        _extract_player_fields(item)
                        if "transaction_data" in item:
                            _extract_txn_data(item)

                players.append({**player_info, **transaction_data})

            # If filtering by team, check if this team is involved
            if filter_team_key:
                team_involved = any(
                    p.get("destination_team_key") == filter_team_key
                    or p.get("source_team_key") == filter_team_key
                    for p in players
                )
                if not team_involved:
                    continue

            transactions.append({
                "type": txn_type,
                "status": status,
                "timestamp": timestamp,
                "players": players,
            })

        return json.dumps({
            "league_id": _resolved_league_id(lg),
            "filters": {
                "types": [t.value for t in params.transaction_types]
                if params.transaction_types else "all",
                "team_number": params.team_number,
            },
            "count": len(transactions),
            "transactions": transactions,
        }, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="yahoo_list_teams",
    annotations={
        "title": "List All Teams",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def yahoo_list_teams(params: ListTeamsInput = ListTeamsInput()) -> str:
    """List all teams in the league with their names, keys, and managers.

    Useful for finding team numbers to use with other tools like
    yahoo_get_roster or yahoo_get_matchup.

    Args:
        params (ListTeamsInput): Validated input containing:
            - league_id (Optional[str]): League override, or None for default.

    Returns:
        str: JSON array of teams with number, name, key, and manager info.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc, params.league_id)
        teams = lg.teams()

        team_list = []
        for tk, tinfo in teams.items():
            # Parse the real team_id out of the team_key suffix (.t.{N})
            # rather than using enumerate order, which is not guaranteed
            # to match Yahoo's 1-based team_ids.
            try:
                team_number = int(tk.rsplit(".t.", 1)[-1])
            except (ValueError, IndexError):
                team_number = None
            team_list.append({
                "team_number": team_number,
                "team_key": tk,
                "name": tinfo.get("name", "Unknown"),
                "manager": tinfo.get("managers", [{}])[0].get("nickname", "Unknown")
                if tinfo.get("managers") else "Unknown",
                "is_your_team": tinfo.get("is_owned_by_current_login", False),
            })
        # Sort by team_number for a stable, intuitive display order.
        team_list.sort(key=lambda t: (t["team_number"] is None, t["team_number"]))

        return json.dumps({
            "league_id": _resolved_league_id(lg),
            "team_count": len(team_list),
            "teams": team_list,
        }, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="yahoo_list_my_leagues",
    annotations={
        "title": "List My Leagues",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def yahoo_list_my_leagues() -> str:
    """List the Yahoo leagues the authenticated account belongs to.

    Use this to discover the ``league_id`` values accepted by the other tools'
    optional ``league_id`` parameter when you play in more than one league.
    Each entry has the numeric ``league_id``, ``name``, ``season``, and an
    ``is_default`` flag marking the league used when ``league_id`` is omitted.

    Returns:
        str: JSON object with ``default_league_id`` and a ``leagues`` list.
    """
    try:
        sc = _get_oauth_session()
        leagues = _get_my_leagues(sc)
        out = [
            {
                "league_id": lg["league_id"],
                "name": lg["name"],
                "season": lg["season"],
                "is_default": lg["league_id"] == cfg.league_id,
            }
            for lg in leagues
        ]
        return json.dumps({
            "default_league_id": cfg.league_id,
            "count": len(out),
            "leagues": out,
        }, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Batch players + player notes (read-only)
# ---------------------------------------------------------------------------

def _resolve_player_key(lg: "yfa.League", name_or_key: str) -> Optional[str]:
    """Resolve a name to a Yahoo player_key. Pass-through if already a key."""
    if "." in name_or_key and name_or_key.split(".")[0] in ("mlb", "nfl", "nba", "nhl"):
        return name_or_key
    try:
        details = lg.player_details(name_or_key)
    except Exception:
        return None
    if isinstance(details, dict):
        details = [details]
    if not isinstance(details, list) or not details:
        return None
    first = details[0]
    if not isinstance(first, dict):
        return None
    pid = first.get("player_id")
    if pid is None:
        return None
    # Yahoo player_key format: <game_code>.p.<player_id> (game_code from the league key)
    game_code = lg.league_key.split(".")[0] if "." in lg.league_key else "mlb"
    return f"{game_code}.p.{pid}"


@mcp.tool(
    name="yahoo_get_players_batch",
    annotations={
        "title": "Get Players (Batch)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def yahoo_get_players_batch(params: GetPlayersBatchInput) -> str:
    """Fetch stats and details for multiple players in a single Yahoo API call.

    Resolves each input name to a player_key, then issues ONE request using
    ;player_keys=key1,key2,... — far cheaper than calling yahoo_get_player_stats
    in a loop. Partial failures are tolerated: unresolved names are returned
    in the 'unresolved' list and do not abort the batch.

    Args:
        params (GetPlayersBatchInput): Validated input containing:
            - player_names (List[str]): 1-25 player names (or player_keys).

    Returns:
        str: JSON object with 'players' (list, one entry per resolved player),
             'unresolved' (list of names that could not be resolved), and
             'requested' count.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc, params.league_id)

        resolved: list = []
        unresolved: list = []
        name_by_key: dict = {}
        for name in params.player_names:
            # Per-name guard: Yahoo occasionally returns empty/non-JSON
            # bodies for individual name lookups, which can raise
            # JSONDecodeError from inside yfa's parser. Catch per-name so
            # one bad response cannot abort the whole batch — push failures
            # into `unresolved` instead. Same class of bug as the
            # transactions handler fix (inconsistent Yahoo response types).
            key = None
            try:
                key = _resolve_player_key(lg, name)
            except (json.JSONDecodeError, ValueError, TypeError, KeyError) as e:
                logger.warning(
                    f"Resolve failed for '{name}' (bad/empty Yahoo response): "
                    f"{type(e).__name__}: {e}"
                )
            except Exception as e:
                logger.warning(
                    f"Unexpected resolve error for '{name}': "
                    f"{type(e).__name__}: {e}"
                )
            if key:
                resolved.append(key)
                name_by_key[key] = name
            else:
                unresolved.append(name)

        players_out: list = []
        if resolved:
            keys_csv = ",".join(resolved)
            url = (
                "https://fantasysports.yahooapis.com/fantasy/v2/"
                f"players;player_keys={keys_csv}/stats?format=json"
            )
            try:
                # BUGFIX: do NOT pass params={"format":"json"} — URL already has it,
                # and the extra kwarg on an OAuth1 session was producing an XML error
                # page that blew up json.loads with "Expecting value: line 1 column 1".
                resp = sc.session.get(url)
                resp.raise_for_status()
                if not resp.text.strip():
                    raise ValueError("Empty response body from Yahoo batch endpoint")
                data = resp.json()
            except Exception as e:
                logger.error(f"Batch fetch failed: {e}")
                return _handle_error(e)

            fc = data.get("fantasy_content", {}) if isinstance(data, dict) else {}
            players_node = fc.get("players", {}) if isinstance(fc, dict) else {}
            if isinstance(players_node, list):
                # Some responses wrap players in a list
                for item in players_node:
                    if isinstance(item, dict) and "players" in item:
                        players_node = item["players"]
                        break

            if isinstance(players_node, dict):
                count = players_node.get("count", 0)
                for i in range(int(count) if isinstance(count, (int, str)) and str(count).isdigit() else 0):
                    pval = players_node.get(str(i))
                    if not isinstance(pval, dict):
                        continue
                    pentry = pval.get("player")
                    if not isinstance(pentry, list) or not pentry:
                        continue
                    meta = pentry[0]
                    flat: dict = {}
                    if isinstance(meta, list):
                        for m in meta:
                            if isinstance(m, dict):
                                flat.update(m)
                    elif isinstance(meta, dict):
                        flat.update(meta)
                    info = _format_player(flat) if flat else {}
                    # Attach stats block if present (second element)
                    if len(pentry) > 1 and isinstance(pentry[1], dict):
                        info["stats_raw"] = pentry[1]
                    players_out.append(info)

        return json.dumps({
            "requested": len(params.player_names),
            "resolved": len(resolved),
            "unresolved": unresolved,
            "players": players_out,
        }, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="yahoo_get_player_notes",
    annotations={
        "title": "Get Player Notes & Injury Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def yahoo_get_player_notes(params: GetPlayerNotesInput) -> str:
    """Fetch recent news, notes, and injury status for a single player.

    Pulls Yahoo's player notes sub-resource plus the status / status_full /
    injury_note fields on the base player resource. Single player only —
    batching notes is not supported.

    Args:
        params (GetPlayerNotesInput): Validated input containing:
            - player_name (str): Name or player_key.

    Returns:
        str: JSON object with 'player' (name/team/status/status_full/
             injury_note), 'notes' (list of {timestamp, note}), and 'count'.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc, params.league_id)

        key = _resolve_player_key(lg, params.player_name)
        if not key:
            return json.dumps({
                "error": f"Could not resolve '{params.player_name}' to a player_key.",
            })

        # Note: Yahoo's Fantasy API does not expose ;out=notes on the player
        # resource (returns HTTP 400 "Invalid player resource notes requested").
        # Use the base player resource, which exposes status / status_full /
        # injury_note directly — the authoritative injury status.
        base_url = f"https://fantasysports.yahooapis.com/fantasy/v2/player/{key}"
        try:
            resp = sc.session.get(base_url, params={"format": "json"})
        except Exception as e:
            logger.error(f"Notes fetch failed for {key}: {e}")
            return _handle_error(e)

        if not resp.ok:
            return json.dumps({
                "error": f"Yahoo returned HTTP {resp.status_code} for {key}",
                "body": resp.text[:500],
            })

        ctype = resp.headers.get("Content-Type", "")
        if "json" not in ctype.lower():
            logger.error(f"Non-JSON response for {key}: ctype={ctype}")
            return json.dumps({
                "error": "Yahoo returned non-JSON response (likely XML error or throttle)",
                "content_type": ctype,
                "body": resp.text[:500],
            })

        try:
            data = resp.json()
        except ValueError as e:
            logger.error(f"JSON decode failed for {key}: {e}")
            return json.dumps({
                "error": f"Failed to parse Yahoo response as JSON: {e}",
                "body": resp.text[:500],
            })

        if not isinstance(data, dict):
            return json.dumps({
                "error": "Unexpected Yahoo response shape (not a dict)",
                "type": type(data).__name__,
            })
        fc = data.get("fantasy_content", {}) if isinstance(data, dict) else {}
        pnode = fc.get("player") if isinstance(fc, dict) else None

        player_info: dict = {"player_key": key}

        # player node is typically a list: [meta_list, ...] where meta_list
        # contains dicts with fields like name, status, status_full, injury_note.
        if isinstance(pnode, list):
            for section in pnode:
                if isinstance(section, list):
                    for m in section:
                        if isinstance(m, dict):
                            for fld in ("name", "status", "status_full",
                                         "injury_note", "on_disabled_list"):
                                if fld in m:
                                    player_info[fld] = m[fld]
                            # Sport-neutral output key (see _format_player).
                            if "editorial_team_abbr" in m:
                                player_info["pro_team"] = m["editorial_team_abbr"]
                elif isinstance(section, dict):
                    for fld in ("status", "status_full", "injury_note"):
                        if fld in section:
                            player_info[fld] = section[fld]

        return json.dumps({
            "player": player_info,
            "status": player_info.get("status"),
            "status_full": player_info.get("status_full"),
            "injury_note": player_info.get("injury_note"),
        }, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Prompts — one-click templates that orchestrate the tools above for the
# common multi-step questions. The strategy lives in the prompt text (it tells
# Claude which tools to chain), keeping the tools themselves a thin data layer.
# ---------------------------------------------------------------------------

@mcp.prompt(title="Analyze my matchup")
def analyze_matchup(team_number: str = "") -> str:
    """Summarize the current head-to-head matchup (categories or points)."""
    whose = f"team {team_number}" if team_number else "my team"
    arg = f" with team_number={team_number}" if team_number else ""
    return (
        f"Analyze {whose}'s current head-to-head matchup. Call yahoo_get_matchup{arg}. "
        "The result's `scoring` field says how the league is scored. If "
        "`categories`: summarize which scoring categories are being won, lost, and "
        "tied, with current values and margins, call out the closest and the "
        "effectively-decided ones, and give the projected category score. If "
        "`points`: report both teams' fantasy-points totals (and projected_points), "
        "the current margin, and whether it's a likely win, loss, or tossup. "
        "Finish with one sentence on where the matchup will be won or lost. "
        "If I mention a specific league, call yahoo_list_my_leagues first and pass "
        "the matching league_id to the tools."
    )


@mcp.prompt(title="Waiver wire help")
def waiver_help(team_number: str = "") -> str:
    """Find free-agent pickups that target the categories I'm losing."""
    whose = f"team {team_number}" if team_number else "my team"
    arg = f" with team_number={team_number}" if team_number else ""
    return (
        f"Help me find waiver pickups for {whose}.\n"
        f"1. Call yahoo_get_matchup{arg} and note the `scoring` field.\n"
        "2. If a categories league: identify the scoring categories I'm losing or "
        "only narrowly winning, then for each weak category call "
        "yahoo_search_free_agents sorted by that category's stat with "
        "time_period=lastweek to surface players in good recent form (e.g. sort=SB "
        "for steals, sort=HR for power, or the football stat label for the need). "
        "If a points league: identify my weakest starting spots (injuries, byes, "
        "low output), then call yahoo_search_free_agents by position (sorted by AR "
        "or a relevant stat, time_period=lastweek) to find the best available "
        "upgrades.\n"
        "3. Recommend 3-5 available players who would most improve my team, each "
        "with a one-line rationale and their recent numbers.\n"
        "If I mention a specific league, call yahoo_list_my_leagues first and pass "
        "the matching league_id to every tool call."
    )


@mcp.prompt(title="Weekly recap")
def weekly_recap() -> str:
    """Recap standings, my matchup, and recent league activity."""
    return (
        "Give me a weekly recap. Call yahoo_get_standings for the current standings, "
        "yahoo_get_matchup for my matchup status this week, and yahoo_get_transactions "
        "for recent league activity. Summarize my standing and record, how my matchup "
        "is going (the categories that will decide it, or the points margin — per the "
        "matchup's `scoring` field), and any notable adds, drops, or trades around the "
        "league."
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    # The transport validates the Host header itself (see _transport_security);
    # any forwarded public hostname must be listed in MCP_ALLOWED_HOSTS.
    #
    # Bind loopback only: the server has no auth of its own (edge auth fronts
    # it) and the reverse proxy/tunnel connects over localhost, so there is no
    # reason to expose the port on other interfaces.
    uvicorn.run(mcp.streamable_http_app(), host="127.0.0.1", port=8000)