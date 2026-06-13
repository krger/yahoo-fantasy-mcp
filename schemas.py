"""Pydantic input models for the MCP tools — the server's input contract.

These declare and validate the arguments each ``@mcp.tool`` handler accepts;
FastMCP turns them into the JSON schema advertised to MCP clients, so the
class and field names are part of the public contract (don't rename without
treating it as a breaking change). Pure declarations — no validators and no
dependency on the Yahoo client or parsers — so they live apart from
``server.py``, which imports the ones it annotates handlers with.
"""

from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Shared description for the per-call league override so it reads consistently
# across every tool's schema.
_LEAGUE_ID_DESC = (
    "Yahoo numeric league id (e.g. '60467') to target a specific league. "
    "Omit to use the server's default league. Call yahoo_list_my_leagues to "
    "discover the leagues this account belongs to."
)

# A league id is a bare integer in Yahoo's URLs; enforce that here so a crafted
# value can't inject extra path/filter segments when it's interpolated into a
# Yahoo API URL (the override is otherwise only validated against the account's
# leagues when league discovery succeeds — see _get_league).
_LEAGUE_ID_PATTERN = r"^\d+$"


class LeagueScopedInput(BaseModel):
    """Base for tools that operate on a single league.

    Carries the optional ``league_id`` override (falling back to the server's
    configured default) plus the shared model config, so every league-scoped
    tool advertises the field identically. Subclasses add their own fields.
    """
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    league_id: Optional[str] = Field(
        default=None, description=_LEAGUE_ID_DESC, pattern=_LEAGUE_ID_PATTERN
    )


class GetStandingsInput(LeagueScopedInput):
    """Input for retrieving league standings (league override only)."""


class GetLeagueSettingsInput(LeagueScopedInput):
    """Input for retrieving league settings (league override only)."""


class ListTeamsInput(LeagueScopedInput):
    """Input for listing teams (league override only)."""


class GetRosterInput(LeagueScopedInput):
    """Input for retrieving a team's roster."""

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
    include_stats: bool = Field(
        default=False,
        description=(
            "When true, enrich each rostered player with their season totals "
            "for the league's scoring categories (one extra batched call). "
            "Default false keeps the roster lightweight."
        ),
    )


class SearchFreeAgentsInput(LeagueScopedInput):
    """Input for searching free agents."""

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
            "Sort order. Named values Yahoo accepts: AR (actual rank, default), "
            "OR (overall rank), NAME, PTS, O_AR. Stat abbreviations are "
            "translated to Yahoo stat IDs server-side — supported: R, H, HR, "
            "RBI, SB, BB, K, AVG, OBP, SLG, OPS, TB, 2B, 3B (hitting); IP, W, "
            "L, SV, BS, HLD, ERA, WHIP, K9 (pitching). You may also pass a "
            "numeric Yahoo stat ID directly (e.g. '7' for Runs). "
            "Note: PTS (fantasy points) returns no results in this "
            "head-to-head categories league — Yahoo computes no points "
            "ranking here; use a category like AR or a stat sort instead."
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
    time_period: Optional[Literal["season", "lastweek", "lastmonth", "biweekly"]] = Field(
        default=None,
        description=(
            "Time window for stat/category sorts: season (default), lastweek, "
            "lastmonth, or biweekly (last two weeks). Use lastweek/biweekly to "
            "surface players in good recent form for waiver pickups. Only "
            "affects stat-based sorts; ignored for AR/OR/NAME. (Displayed stats "
            "remain season totals; the window controls the ranking order.)"
        ),
    )


class GetPlayerStatsInput(LeagueScopedInput):
    """Input for getting player statistics."""

    player_name: str = Field(
        ...,
        description="Full or partial player name to search for (e.g. 'Ohtani', 'Juan Soto').",
        min_length=2,
        max_length=100,
    )


class GetPlayerOwnershipInput(LeagueScopedInput):
    """Input for looking up player ownership."""

    player_name: str = Field(
        ...,
        description="Full or partial player name to look up (e.g. 'Ohtani', 'Juan Soto').",
        min_length=2,
        max_length=100,
    )


class GetScoreboardInput(LeagueScopedInput):
    """Input for retrieving the league scoreboard."""

    week: Optional[int] = Field(
        default=None,
        description="Scoring week number. If omitted, returns the current week.",
        ge=1,
        le=26,
    )


class GetMatchupInput(LeagueScopedInput):
    """Input for getting a specific team's matchup details."""

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


class GetTransactionsInput(LeagueScopedInput):
    """Input for retrieving league transaction history."""

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


class GetPlayersBatchInput(LeagueScopedInput):
    """Input for batch player lookup."""

    player_names: List[str] = Field(
        ...,
        description="List of player names to resolve and fetch in one batched Yahoo API call.",
        min_length=1,
        max_length=25,
    )


class GetPlayerNotesInput(LeagueScopedInput):
    """Input for fetching a single player's notes / injury status."""

    player_name: str = Field(
        ...,
        description="Full or partial player name. A player_key (e.g. 'mlb.p.12345') is also accepted.",
        min_length=1,
    )
