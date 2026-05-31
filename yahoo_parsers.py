"""Pure parsers/normalizers for Yahoo Fantasy API responses.

This module holds the response-parsing helpers extracted from ``server.py``.
They are pure functions: raw Yahoo dict/list shapes in, normalized Python
structures out — no network, no OAuth, no MCP. That makes them the repo's
unit-test target (``tests/test_parsers.py``) and keeps the bug-prone
positional/keyed-nesting logic in one focused place.

Yahoo responses are deeply nested and positional: arrays interleave data
objects with empty ``[]`` placeholders, collections use numeric string keys
(``"0"``, ``"1"``, ...) plus a ``count``, and stats arrive as
``{stat: {stat_id, value}}`` lists. Locate data by key/shape, never by index,
and map stats by ``stat_id`` (labels come from ``ScoringConfig``, built from
the league's own settings rather than a hard-coded table).
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ScoringConfig:
    """The league's scoring categories, derived from its Yahoo settings.

    Replaces the previously hard-coded stat_id tables so the parsers work for
    any league's categories, not just one specific 10-category H2H setup.

    - ``stat_id_to_name``: stat_id (as a string) -> display label, covering
      both scored and informational ("only display") categories.
    - ``scored_stat_ids``: the scored categories in display order (informational
      stats like IP / H-AB excluded); used for standings season-total ranking.
    - ``lower_is_better``: stat_ids where a lower value ranks higher (ERA/WHIP).
    """
    stat_id_to_name: dict[str, str]
    scored_stat_ids: list[str]
    lower_is_better: frozenset[str]

    def label(self, stat_id: str) -> str:
        """Display label for a stat_id, falling back to the id itself."""
        return self.stat_id_to_name.get(stat_id, stat_id)

    @classmethod
    def empty(cls) -> "ScoringConfig":
        """Fallback when league settings are unavailable: ids label as
        themselves and nothing is treated as a scored/ranked category."""
        return cls(stat_id_to_name={}, scored_stat_ids=[], lower_is_better=frozenset())


def build_scoring_config(raw_settings: dict) -> ScoringConfig:
    """Build a ScoringConfig from a raw ``league/{key}/settings`` response.

    Yahoo nests the categories at
    ``fantasy_content.league[1].settings[0].stat_categories.stats`` as a list of
    ``{"stat": {stat_id, display_name, sort_order, is_only_display_stat, ...}}``.
    ``sort_order`` is ``"1"`` when higher is better and ``"0"`` when lower is
    better (ERA/WHIP); ``is_only_display_stat`` is ``"1"`` for informational
    categories (e.g. IP, H/AB) that are shown but not scored. Degrades to
    ``ScoringConfig.empty()`` if the expected shape is absent.
    """
    league = raw_settings.get("fantasy_content", {}).get("league", [])
    settings = league[1].get("settings") if len(league) > 1 else None
    if isinstance(settings, list):
        settings = settings[0] if settings else {}
    if not isinstance(settings, dict):
        return ScoringConfig.empty()
    stats = settings.get("stat_categories", {}).get("stats", [])

    names: dict[str, str] = {}
    scored: list[str] = []
    lower: set[str] = set()
    for entry in stats:
        st = entry.get("stat", {}) if isinstance(entry, dict) else {}
        sid = st.get("stat_id")
        if sid is None:
            continue
        sid = str(sid)
        names[sid] = st.get("display_name", sid)
        # is_only_display_stat is "1" for informational categories, else None.
        if str(st.get("is_only_display_stat")) != "1":
            scored.append(sid)
        if str(st.get("sort_order")) == "0":
            lower.add(sid)
    return ScoringConfig(
        stat_id_to_name=names,
        scored_stat_ids=scored,
        lower_is_better=frozenset(lower),
    )


def _to_int(value):
    """Coerce a Yahoo stringified count to int, leaving other values as-is."""
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return value


def _to_number(value):
    """Coerce a Yahoo stringified number (int or float, e.g. ``".583"``) to a
    number, leaving non-numeric values as-is."""
    if isinstance(value, str):
        s = value.strip()
        if s.lstrip("-").isdigit():
            return int(s)
        try:
            return float(s)
        except ValueError:
            return value
    return value


def _flatten_raw_yahoo_player(player_entry: list, scoring: ScoringConfig) -> dict:
    """Flatten Yahoo's list-of-dicts player representation into the flat
    shape that ``_format_player`` expects.

    Yahoo's /players collection returns each player as::

        [
            [ {"player_key": ...}, {"player_id": ...}, {"name": {...}}, ... ],
            { "percent_owned": {...}, ... }   # present if requested via ;out=
        ]

    This walks that structure and emits the same keys that the
    ``yahoo_fantasy_api`` library produces for ``free_agents()``.
    """
    flat: dict = {}
    if not isinstance(player_entry, list) or not player_entry:
        return flat

    meta = player_entry[0]
    if isinstance(meta, list):
        for item in meta:
            if not isinstance(item, dict):
                continue
            if "name" in item and isinstance(item["name"], dict):
                flat["name"] = item["name"].get("full", "")
            if "player_id" in item and "player_id" not in flat:
                flat["player_id"] = item["player_id"]
            if "editorial_team_abbr" in item:
                flat["editorial_team_abbr"] = item["editorial_team_abbr"]
            if "position_type" in item:
                flat["position_type"] = item["position_type"]
            if "status" in item and isinstance(item["status"], str):
                flat["status"] = item["status"]
            if "status_full" in item:
                flat["status_full"] = item["status_full"]
            if "display_position" in item:
                flat.setdefault("display_position", item["display_position"])
            if "eligible_positions" in item:
                eps = item["eligible_positions"]
                if isinstance(eps, list):
                    flat["eligible_positions"] = [
                        ep.get("position", "")
                        for ep in eps
                        if isinstance(ep, dict) and ep.get("position")
                    ]

    # Sub-resources (percent_owned, player_stats, etc.) live in player_entry[1:]
    for extra in player_entry[1:]:
        if not isinstance(extra, dict):
            continue
        po = extra.get("percent_owned")
        if isinstance(po, dict) and "value" in po:
            flat["percent_owned"] = po["value"]
        elif isinstance(po, list):
            for sub in po:
                if isinstance(sub, dict) and "value" in sub:
                    flat["percent_owned"] = sub["value"]
                    break

        ps = extra.get("player_stats")
        if isinstance(ps, dict):
            stats = {}
            for s in ps.get("stats", []):
                st = s.get("stat", {}) if isinstance(s, dict) else {}
                sid = st.get("stat_id")
                if sid is not None:
                    stats[scoring.label(str(sid))] = _to_number(st.get("value"))
            if stats:
                flat["stats"] = stats

    return flat


def _extract_team_summary(team_node: list) -> dict:
    """Pull team_key, name, stats-by-stat_id, and category points out of one
    team entry inside a matchup's ``teams`` collection.

    Yahoo represents each team as ``[[meta...], {team_stats, team_points, ...}]``
    where meta is a positional list of single-key dicts (locate fields by key,
    never by index).
    """
    info = {}
    for d in team_node[0]:
        if isinstance(d, dict):
            info.update(d)

    stats = {}
    points = None
    for part in team_node[1:]:
        if not isinstance(part, dict):
            continue
        if "team_stats" in part:
            for s in part["team_stats"].get("stats", []):
                st = s.get("stat", {})
                if "stat_id" in st:
                    stats[str(st["stat_id"])] = st.get("value")
        if "team_points" in part:
            points = part["team_points"].get("total")

    return {
        "team_key": info.get("team_key"),
        "name": info.get("name", "Unknown"),
        "stats": stats,
        "category_points": points,
    }


def _parse_matchup_node(
    matchup: dict,
    scoring: ScoringConfig,
    perspective_team_key: Optional[str] = None,
) -> dict:
    """Parse one Yahoo matchup node into a category-by-category breakdown.

    Both the team matchup endpoint and the league scoreboard wrap the same
    node shape (``week``/``stat_winners``/``0.teams``), so this is the shared
    core for ``_parse_matchup`` and ``_parse_scoreboard``.

    With ``perspective_team_key`` set, the result is framed as ``team`` vs
    ``opponent`` with a ``result`` (win/loss/tie) per category. Without it, the
    framing is neutral: a ``teams`` list and per-category ``values`` keyed by
    team_key plus the winning team_key.
    """
    # Winner of each scored category, keyed by stat_id ("tie" when tied).
    winners = {}
    for entry in matchup.get("stat_winners", []):
        sw = entry.get("stat_winner", {})
        sid = str(sw.get("stat_id"))
        winners[sid] = "tie" if sw.get("is_tied") else sw.get("winner_team_key")

    teams_coll = matchup.get("0", {}).get("teams", {})
    summaries = []
    for key, val in teams_coll.items():
        if key == "count" or not isinstance(val, dict) or "team" not in val:
            continue
        summaries.append(_extract_team_summary(val["team"]))

    meta = {
        "week": _to_int(matchup.get("week")),
        "week_start": matchup.get("week_start"),
        "week_end": matchup.get("week_end"),
        "status": matchup.get("status"),
        "is_playoffs": matchup.get("is_playoffs") == "1",
    }

    def _team_info(s):
        return {
            "team_key": s["team_key"],
            "name": s["name"],
            "category_points": _to_int(s["category_points"]),
        }

    if perspective_team_key is not None:
        me = next((s for s in summaries if s["team_key"] == perspective_team_key), None)
        opp = next((s for s in summaries if s["team_key"] != perspective_team_key), None)
        if me is None or opp is None:
            raise ValueError("Could not resolve both teams in matchup")

        categories = []
        for sid, value in me["stats"].items():
            winner_key = winners.get(sid)
            if winner_key == "tie":
                result = "tie"
            elif winner_key == me["team_key"]:
                result = "win"
            elif winner_key == opp["team_key"]:
                result = "loss"
            else:
                result = None  # informational category (not scored)
            categories.append({
                "stat": scoring.label(sid),
                "stat_id": sid,
                "team": value,
                "opponent": opp["stats"].get(sid),
                "scored": sid in winners,
                "result": result,
            })
        return {**meta, "team": _team_info(me), "opponent": _team_info(opp),
                "categories": categories}

    # Neutral framing for the scoreboard (no single perspective team).
    if len(summaries) < 2:
        raise ValueError("Matchup is missing one or both teams")
    a, b = summaries[0], summaries[1]
    categories = []
    for sid in a["stats"]:
        categories.append({
            "stat": scoring.label(sid),
            "stat_id": sid,
            "values": {
                a["team_key"]: a["stats"].get(sid),
                b["team_key"]: b["stats"].get(sid),
            },
            "winner": winners.get(sid),  # team_key, "tie", or None (info stat)
            "scored": sid in winners,
        })
    return {**meta, "teams": [_team_info(a), _team_info(b)], "categories": categories}


def _parse_matchup(raw: dict, my_team_key: str, scoring: ScoringConfig) -> dict:
    """Turn Yahoo's raw team-matchup response into a team-vs-opponent breakdown.

    Yahoo only returns the opponent's team_key from ``Team.matchup()``; the
    full stat breakdown lives in the raw response, so we parse it here.
    """
    matchup = (
        raw.get("fantasy_content", {})
        .get("team", [None, {}])[1]
        .get("matchups", {})
        .get("0", {})
        .get("matchup", {})
    )
    if not matchup:
        raise ValueError("Matchup data not found in Yahoo response")
    return _parse_matchup_node(matchup, scoring, my_team_key)


def _parse_scoreboard(raw: dict, scoring: ScoringConfig) -> list:
    """Turn Yahoo's raw scoreboard response into a list of neutral matchup
    breakdowns, one per head-to-head pairing for the week.
    """
    league = raw.get("fantasy_content", {}).get("league", [None, {}])
    scoreboard = league[1].get("scoreboard", {}) if len(league) > 1 else {}
    matchups = scoreboard.get("0", {}).get("matchups", {})
    out = []
    for key, val in matchups.items():
        if key == "count" or not isinstance(val, dict) or "matchup" not in val:
            continue
        out.append(_parse_matchup_node(val["matchup"], scoring))
    return out


def _parse_team_season_stats(raw: dict) -> dict:
    """Parse a ``league/{key}/teams/stats`` response into
    ``{team_key: {stat_id: numeric_value}}`` of season totals.
    """
    league = raw.get("fantasy_content", {}).get("league", [None, {}])
    teams = league[1].get("teams", {}) if len(league) > 1 else {}
    out = {}
    for key, val in teams.items():
        if key == "count" or not isinstance(val, dict) or "team" not in val:
            continue
        summary = _extract_team_summary(val["team"])
        out[summary["team_key"]] = {
            sid: _to_number(v) for sid, v in summary["stats"].items()
        }
    return out


def _rank_season_categories(stats_by_team: dict, scoring: ScoringConfig) -> dict:
    """Turn ``{team_key: {stat_id: value}}`` into
    ``{team_key: [{stat, stat_id, value, rank}]}`` for the scored categories.

    Rank is 1-based standard competition ranking (ties share a rank), with
    ``scoring.lower_is_better`` categories (e.g. ERA/WHIP) ranked low-first and
    every other category high-first. Teams with a non-numeric value for a
    category are unranked (``rank: null``).
    """
    # Per-category rank lookup: {stat_id: {team_key: rank}}.
    ranks = {}
    for sid in scoring.scored_stat_ids:
        vals = {
            tk: s[sid]
            for tk, s in stats_by_team.items()
            if isinstance(s.get(sid), (int, float))
        }
        lower = sid in scoring.lower_is_better
        ranks[sid] = {
            tk: 1 + sum(
                1 for other in vals.values()
                if (other < v if lower else other > v)
            )
            for tk, v in vals.items()
        }

    out = {}
    for tk, s in stats_by_team.items():
        out[tk] = [
            {
                "stat": scoring.label(sid),
                "stat_id": sid,
                "value": s.get(sid),
                "rank": ranks[sid].get(tk),
            }
            for sid in scoring.scored_stat_ids
        ]
    return out


def _parse_standings(standings: list, season_categories: Optional[dict] = None) -> list:
    """Normalize Yahoo's standings list into numeric records.

    The standings feed carries only season records/ranks, so this coerces the
    stringified numbers and structures each team's record. When
    ``season_categories`` (``{team_key: [category...]}``) is supplied, each
    team's season category totals are merged in under ``categories``.
    """
    out = []
    for entry in standings:
        totals = entry.get("outcome_totals", {})
        gb = entry.get("games_back")
        team_key = entry.get("team_key")
        row = {
            "rank": _to_number(entry.get("rank")),
            "playoff_seed": _to_number(entry.get("playoff_seed")),
            "name": entry.get("name"),
            "team_key": team_key,
            "record": {
                "wins": _to_number(totals.get("wins")),
                "losses": _to_number(totals.get("losses")),
                "ties": _to_number(totals.get("ties")),
                "pct": _to_number(totals.get("percentage")),
            },
            "games_back": None if gb in ("-", "", None) else _to_number(gb),
        }
        if season_categories is not None:
            row["categories"] = season_categories.get(team_key, [])
        out.append(row)
    return out


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
