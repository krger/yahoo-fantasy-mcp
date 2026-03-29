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
"""

import json
import os
import sys
import logging
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

YAHOO_LEAGUE_ID = os.environ.get("YAHOO_LEAGUE_ID", "12345")
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
    or omit it for your own.

    Args:
        params (GetRosterInput): Validated input parameters containing:
            - team_number (Optional[int]): Team number (1-based), or None for own team.
            - week (Optional[int]): Scoring week, or None for current week.

    Returns:
        str: JSON list of players on the roster.
    """
    try:
        sc = _get_oauth_session()
        lg = _get_league(sc)

        teams = lg.teams()
        team_keys = list(teams.keys())

        if params.team_number is not None:
            idx = params.team_number - 1
            if idx >= len(team_keys):
                return f"Error: Team number {params.team_number} not found. League has {len(team_keys)} teams."
            team_key = team_keys[idx]
        else:
            # Get the user's own team
            team_key = team_keys[0]  # Default; will be overridden below
            for tk, tinfo in teams.items():
                if tinfo.get("is_owned_by_current_login", False):
                    team_key = tk
                    break

        tm = lg.to_team(team_key)
        roster = tm.roster(week=params.week) if params.week else tm.roster()

        team_name = teams[team_key].get("name", f"Team {params.team_number or '?'}")
        formatted = [_format_player(p) for p in roster]

        result = {
            "team_name": team_name,
            "team_key": team_key,
            "week": params.week or "current",
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
        team_keys = list(teams.keys())

        if params.team_number is not None:
            idx = params.team_number - 1
            if idx >= len(team_keys):
                return f"Error: Team number {params.team_number} not found."
            team_key = team_keys[idx]
        else:
            team_key = team_keys[0]
            for tk, tinfo in teams.items():
                if tinfo.get("is_owned_by_current_login", False):
                    team_key = tk
                    break

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
        for idx, (tk, tinfo) in enumerate(teams.items(), start=1):
            team_list.append({
                "team_number": idx,
                "team_key": tk,
                "name": tinfo.get("name", "Unknown"),
                "manager": tinfo.get("managers", [{}])[0].get("nickname", "Unknown")
                if tinfo.get("managers") else "Unknown",
                "is_your_team": tinfo.get("is_owned_by_current_login", False),
            })

        return json.dumps({
            "league_id": YAHOO_LEAGUE_ID,
            "team_count": len(team_list),
            "teams": team_list,
        }, indent=2, default=str)

    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
