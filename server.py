"""
Yahoo Fantasy Baseball MCP Server

A read-only MCP server that exposes Yahoo Fantasy Baseball data
to Claude Desktop via stdio transport.

Tools:
    - yahoo_get_roster: View any team's roster in the league
    - yahoo_get_standings: League standings
    - yahoo_get_scoreboard: Current/past week matchups
    - yahoo_search_free_agents: Search available free agents
    - yahoo_get_player_stats: Stats for a specific player (includes ownership)
    - yahoo_get_player_ownership: Quick lookup of who owns a player
    - yahoo_get_league_settings: League rules and configuration
    - yahoo_get_matchups: Head-to-head matchup details
    - yahoo_get_transactions: League transaction history (adds, drops, trades)
"""

import json
import os
import sys
import logging
from datetime import date, datetime
from contextlib import asynccontextmanager
from typing import Optional, List
from enum import Enum

from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

import yahoo_fantasy_api as yfa
from yahoo_oauth import OAuth2


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

YAHOO_LEAGUE_ID = os.environ.get("YAHOO_LEAGUE_ID", "60467")
YAHOO_SPORT = os.environ.get("YAHOO_SPORT", "mlb")
CREDENTIALS_FILE = os.environ.get(
    "YAHOO_OAUTH_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "oauth2.json"),
)

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

def _get_oauth_session() -> OAuth2:
    """Create or refresh an OAuth2 session from the credentials file."""
    if not os.path.exists(CREDENTIALS_FILE):
        raise FileNotFoundError(
            f"OAuth credentials file not found at {CREDENTIALS_FILE}. "
            "Create oauth2.json with your consumer_key and consumer_secret."
        )
    sc = OAuth2(None, None, from_file=CREDENTIALS_FILE)
    if not sc.token_is_valid():
        sc.refresh_access_token()
    return sc


def _get_league(sc: OAuth2) -> yfa.League:
    """Get the Yahoo Fantasy league object.

    Attaches ``league_key`` as an attribute on the returned League so
    downstream helpers (e.g. ``_get_player_ownership``) can reference it
    without reconstructing the key.
    """
    gm = yfa.Game(sc, YAHOO_SPORT)
    league_ids = gm.league_ids(year=2026)
    # Try to find the configured league
    for lid in league_ids:
        if YAHOO_LEAGUE_ID in lid:
            lg = gm.to_league(lid)
            lg.league_key = lid          # stash for later use
            return lg
    # Fallback: try constructing the key directly
    game_id = gm.game_id()
    league_key = f"{game_id}.l.{YAHOO_LEAGUE_ID}"
    lg = gm.to_league(league_key)
    lg.league_key = league_key           # stash for later use
    return lg


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
        info["editorial_team_abbr"] = player.get("editorial_team_abbr", "")
        info["player_id"] = player.get("player_id", "")
        info["percent_owned"] = player.get("percent_owned", "")
    return {k: v for k, v in info.items() if v != "" and v != []}


def _get_player_ownership(sc: OAuth2, league_key: str, player_id) -> dict:
    """Look up which fantasy team owns a player via the Yahoo ownership API.

    Uses the /players;player_keys={key}/ownership sub-resource to determine
    whether a player is rostered, on waivers, or a free agent — and if
    rostered, which fantasy team owns them.

    Args:
        sc: Active OAuth2 session.
        league_key: The league key (e.g. '469.l.60467').
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


def _resolve_team_key(lg, teams: dict, team_number: Optional[int]) -> Optional[str]:
    """Resolve a 1-based team_number to a Yahoo team_key.

    Yahoo team_keys are deterministic: ``{league_key}.t.{team_id}``. We
    construct the key directly from team_number rather than indexing into
    ``list(lg.teams().keys())`` — the dict ordering returned by
    ``yahoo_fantasy_api`` is not guaranteed to match 1-based team_ids
    (this was the root cause of the team_number-ignored bug).

    Args:
        lg: The yfa.League object (must have ``league_key`` attached by
            ``_get_league``).
        teams: The dict returned by ``lg.teams()`` — used only to validate
            that the resolved key actually exists in this league.
        team_number: 1-based team number, or None.

    Returns:
        The team_key string if ``team_number`` is given and valid, or the
        authenticated user's team_key if ``team_number`` is None. Returns
        None if the number is out of range (caller should surface an error).
    """
    if team_number is not None:
        candidate = f"{lg.league_key}.t.{team_number}"
        if candidate in teams:
            return candidate
        return None

    # No team_number given: return the authenticated user's team.
    for tk, tinfo in teams.items():
        if tinfo.get("is_owned_by_current_login", False):
            return tk
    # Fallback: first team in the dict. Shouldn't happen in practice.
    keys = list(teams.keys())
    return keys[0] if keys else None


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
        lg = _get_league(sc)
        logger.info(f"Connected to Yahoo Fantasy league {YAHOO_LEAGUE_ID}")
        yield {"sc": sc, "lg": lg}
    except Exception as e:
        logger.error(f"Failed to initialize Yahoo connection: {e}")
        yield {"sc": None, "lg": None, "init_error": str(e)}


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("yahoo_fantasy_mcp", lifespan=app_lifespan)


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class GetRosterInput(BaseModel):
    """Input for retrieving a team's roster."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    team_number: Optional[int] = Field(
        default=None,
        description=(
            "Team number in the league (1-based). "
            "If omitted, returns the authenticated user's team roster."
        ),
        ge=1,
        le=30,
    )
    week: Optional[int] = Field(
        default=None,
        description="Scoring week number. If omitted, returns the current week.",
        ge=1,
        le=26,
    )
    day: Optional[str] = Field(
        default=None,
        description=(
            "Specific date in YYYY-MM-DD format to view the roster as it was "
            "(or is) set for that day. Useful for checking past lineups or "
            "planning future ones. Mutually exclusive with 'week'; if both are "
            "provided, 'day' takes precedence."
        ),
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )


class SearchFreeAgentsInput(BaseModel):
    """Input for searching free agents."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    position: Optional[str] = Field(
        default=None,
        description=(
            "Filter by position. Examples: C, 1B, 2B, 3B, SS, OF, Util, SP, RP. "
            "If omitted, returns all positions."
        ),
    )
    sort: Optional[str] = Field(
        default="AR",
        description=(
            "Sort stat key. Common values: AR (overall rank), R, HR, RBI, SB, AVG, "
            "W, SV, K, ERA, WHIP. Default: AR (overall rank)."
        ),
    )
    count: Optional[int] = Field(
        default=25,
        description="Number of results to return (default 25, max 50).",
        ge=1,
        le=50,
    )
    status: Optional[str] = Field(
        default="FA",
        description=(
            "Player availability status. FA = free agents only, "
            "W = waivers, A = all available (FA + W). Default: FA."
        ),
    )


class GetPlayerStatsInput(BaseModel):
    """Input for getting player statistics."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    player_name: str = Field(
        ...,
        description="Full or partial player name to search for (e.g. 'Ohtani', 'Juan Soto').",
        min_length=2,
        max_length=100,
    )


class GetPlayerOwnershipInput(BaseModel):
    """Input for looking up player ownership."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    player_name: str = Field(
        ...,
        description="Full or partial player name to look up (e.g. 'Ohtani', 'Juan Soto').",
        min_length=2,
        max_length=100,
    )


class GetScoreboardInput(BaseModel):
    """Input for retrieving the league scoreboard."""
    model_config = ConfigDict(extra="forbid")

    week: Optional[int] = Field(
        default=None,
        description="Scoring week number. If omitted, returns the current week.",
        ge=1,
        le=26,
    )


class GetMatchupInput(BaseModel):
    """Input for getting a specific team's matchup details."""
    model_config = ConfigDict(extra="forbid")

    team_number: Optional[int] = Field(
        default=None,
        description=(
            "Team number (1-based) to get matchup for. "
            "If omitted, returns the authenticated user's matchup."
        ),
        ge=1,
        le=30,
    )
    week: Optional[int] = Field(
        default=None,
        description="Scoring week. If omitted, returns the current week.",
        ge=1,
        le=26,
    )


class TransactionType(str, Enum):
    """Types of transactions to filter by."""
    ADD = "add"
    DROP = "drop"
    ADD_DROP = "add/drop"
    TRADE = "trade"


class GetTransactionsInput(BaseModel):
    """Input for retrieving league transaction history."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    transaction_types: Optional[List[TransactionType]] = Field(
        default=None,
        description=(
            "Filter by transaction type(s). Options: add, drop, add/drop, trade. "
            "If omitted, returns all transaction types."
        ),
    )
    team_number: Optional[int] = Field(
        default=None,
        description=(
            "Filter to transactions involving a specific team (1-based). "
            "If omitted, returns transactions for all teams."
        ),
        ge=1,
        le=30,
    )
    count: Optional[int] = Field(
        default=25,
        description="Number of transactions to return (default 25, max 50).",
        ge=1,
        le=50,
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
async def yahoo_get_roster(params: GetRosterInput) -> str:
    """Get the roster for a team in the fantasy baseball league.

    Returns each player's name, position, eligible positions, MLB team,
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
        lg = _get_league(sc)

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
async def yahoo_get_standings() -> str:
    """Get current league standings including win-loss records and rankings.

    Returns all teams ranked by their current standing with W/L/T records
    and category stats if available.

    Returns:
        str: JSON array of teams sorted by standing.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc)
        standings = lg.standings()

        result = {
            "league_id": YAHOO_LEAGUE_ID,
            "team_count": len(standings),
            "standings": standings,
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
async def yahoo_get_scoreboard(params: GetScoreboardInput) -> str:
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
        lg = _get_league(sc)

        if params.week:
            scoreboard = lg.matchups(week=params.week)
        else:
            scoreboard = lg.matchups()

        result = {
            "league_id": YAHOO_LEAGUE_ID,
            "week": params.week or "current",
            "matchups": scoreboard,
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
async def yahoo_search_free_agents(params: SearchFreeAgentsInput) -> str:
    """Search for available free agents in the league.

    Filter by position and sort by various stat categories to find
    pickup targets. Returns player name, positions, MLB team, ownership
    percentage, and key stats.

    Args:
        params (SearchFreeAgentsInput): Validated input containing:
            - position (Optional[str]): Position filter (C, 1B, 2B, SS, OF, SP, RP, etc.)
            - sort (Optional[str]): Sort stat key (AR, HR, RBI, AVG, ERA, etc.)
            - count (Optional[int]): Number of results (default 25)
            - status (Optional[str]): FA, W, or A (default FA)

    Returns:
        str: JSON list of available players sorted by the specified stat.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc)

        kwargs = {}
        if params.position:
            kwargs["position"] = params.position
        if params.sort:
            kwargs["sort"] = params.sort
        if params.status and params.status != "FA":
            kwargs["status"] = params.status

        fa = lg.free_agents(None)  # None = current scoring period
        # The API may not support all kwargs directly; handle gracefully
        # Some versions of yahoo_fantasy_api accept position as a filter
        try:
            if params.position:
                fa = lg.free_agents(None, position=params.position)
        except TypeError:
            # If the library version doesn't support position kwarg, filter manually
            if params.position:
                fa = [
                    p for p in fa
                    if params.position in p.get("eligible_positions", [])
                ]

        # Limit results
        fa = fa[: params.count]
        formatted = [_format_player(p) for p in fa]

        result = {
            "position_filter": params.position or "all",
            "sort_by": params.sort,
            "status": params.status,
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
    eligible positions, and MLB team.

    Args:
        params (GetPlayerStatsInput): Validated input containing:
            - player_name (str): Full or partial player name to search.

    Returns:
        str: JSON object with player details, stats, and ownership info,
             or an error if the player is not found.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc)

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
                    logger.info(f"player_stats GET {stats_url} -> {sresp.status_code} body[:200]={sresp.text[:200]!r}")
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
        str: JSON object with player name, MLB team, and ownership details
             (owner team name/key if rostered, or availability status).
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc)

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
            "mlb_team": info.get("editorial_team_abbr", ""),
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
async def yahoo_get_league_settings() -> str:
    """Get the league's configuration, rules, and scoring settings.

    Returns roster positions, stat categories, scoring type, number of teams,
    trade deadline, playoff settings, and other league rules.

    Returns:
        str: JSON object with complete league settings.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc)
        settings = lg.settings()

        return json.dumps({
            "league_id": YAHOO_LEAGUE_ID,
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
async def yahoo_get_matchup(params: GetMatchupInput) -> str:
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
        lg = _get_league(sc)

        teams = lg.teams()

        team_key = _resolve_team_key(lg, teams, params.team_number)
        if team_key is None:
            return (
                f"Error: Team number {params.team_number} not found. "
                f"League has {len(teams)} teams."
            )

        tm = lg.to_team(team_key)

        if params.week:
            matchup = tm.matchup(week=params.week)
        else:
            matchup = tm.matchup()

        team_name = teams[team_key].get("name", "Unknown")

        result = {
            "team_name": team_name,
            "team_key": team_key,
            "week": params.week or "current",
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
async def yahoo_get_transactions(params: GetTransactionsInput) -> str:
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
        lg = _get_league(sc)

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
                        player_info["mlb_team"] = d["editorial_team_abbr"]
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
            "league_id": YAHOO_LEAGUE_ID,
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
async def yahoo_list_teams() -> str:
    """List all teams in the league with their names, keys, and managers.

    Useful for finding team numbers to use with other tools like
    yahoo_get_roster or yahoo_get_matchup.

    Returns:
        str: JSON array of teams with number, name, key, and manager info.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc)
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
            "league_id": YAHOO_LEAGUE_ID,
            "team_count": len(team_list),
            "teams": team_list,
        }, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Batch players + player notes (read-only)
# ---------------------------------------------------------------------------

class GetPlayersBatchInput(BaseModel):
    """Input for batch player lookup."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    player_names: List[str] = Field(
        ...,
        description="List of player names to resolve and fetch in one batched Yahoo API call.",
        min_length=1,
        max_length=25,
    )


class GetPlayerNotesInput(BaseModel):
    """Input for fetching a single player's notes / injury status."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    player_name: str = Field(
        ...,
        description="Full or partial player name. A player_key (e.g. 'mlb.p.12345') is also accepted.",
        min_length=1,
    )


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
    # Yahoo MLB player_key format: <game_code>.p.<player_id>
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
        lg = _get_league(sc)

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
                logger.info(f"players_batch GET {url} -> {resp.status_code} body[:200]={resp.text[:200]!r}")
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
        lg = _get_league(sc)

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
                            for fld in ("name", "editorial_team_abbr", "status",
                                         "status_full", "injury_note",
                                         "on_disabled_list"):
                                if fld in m:
                                    player_info[fld] = m[fld]
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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
