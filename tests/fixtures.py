"""Minimal but faithful Yahoo Fantasy API response fixtures.

These reproduce the structural quirks the parsers must survive (see CLAUDE.md
"Yahoo API gotchas"): meta arrays interleaved with empty ``[]`` placeholders,
collections keyed by numeric strings plus a ``count``, stats as
``{stat: {stat_id, value}}`` lists, and rate stats / averages as strings.

Keys use a fake league key ``469.l.1`` and only a handful of categories —
enough to cover counting stats, a rate stat (ERA, where lower wins), an
informational stat (H/AB, stat_id 60, which has no stat_winner), and the
win / loss / tie outcomes.
"""

# --- one team's node inside a (weekly) matchup -----------------------------
# Shape: [ [meta single-key dicts, with [] placeholders], {team_stats, ...} ]

TEAM_NODE_5 = [
    [
        {"team_key": "469.l.1.t.5"},
        {"team_id": "5"},
        {"name": "Poachers"},
        [],  # positional placeholder Yahoo interleaves
        {"is_owned_by_current_login": 1},
        [],
    ],
    {
        "team_stats": {
            "coverage_type": "week",
            "week": "10",
            "stats": [
                {"stat": {"stat_id": "60", "value": "10/40"}},   # H/AB (info)
                {"stat": {"stat_id": "7", "value": 25}},          # R
                {"stat": {"stat_id": "12", "value": 6}},          # HR
                {"stat": {"stat_id": "13", "value": 25}},         # RBI
                {"stat": {"stat_id": "26", "value": "3.94"}},     # ERA
            ],
        },
        "team_points": {"coverage_type": "week", "week": "10", "total": "2"},
        "team_remaining_games": {"total": {"remaining_games": 34}},
    },
]

TEAM_NODE_7 = [
    [
        {"team_key": "469.l.1.t.7"},
        {"team_id": "7"},
        {"name": "Perfect Pitch"},
        [],
        {"is_owned_by_current_login": 0},
    ],
    {
        "team_stats": {
            "coverage_type": "week",
            "week": "10",
            "stats": [
                {"stat": {"stat_id": "60", "value": "12/38"}},
                {"stat": {"stat_id": "7", "value": 20}},
                {"stat": {"stat_id": "12", "value": 13}},
                {"stat": {"stat_id": "13", "value": 25}},
                {"stat": {"stat_id": "26", "value": "4.40"}},
            ],
        },
        "team_points": {"coverage_type": "week", "week": "10", "total": "1"},
    },
]

# A single matchup node: team 5 vs team 7.
# Winners: R -> t5, HR -> t7, RBI -> tie, ERA -> t5. H/AB (60) is unscored.
MATCHUP_NODE = {
    "week": "10",
    "week_start": "2026-05-25",
    "week_end": "2026-05-31",
    "status": "midevent",
    "is_playoffs": "0",
    "stat_winners": [
        {"stat_winner": {"stat_id": "7", "winner_team_key": "469.l.1.t.5"}},
        {"stat_winner": {"stat_id": "12", "winner_team_key": "469.l.1.t.7"}},
        {"stat_winner": {"stat_id": "13", "is_tied": 1}},
        {"stat_winner": {"stat_id": "26", "winner_team_key": "469.l.1.t.5"}},
    ],
    "0": {
        "teams": {
            "count": 2,
            "0": {"team": TEAM_NODE_5},
            "1": {"team": TEAM_NODE_7},
        }
    },
}

# Full raw response from the team-matchup endpoint (Team.matchup wraps this).
MATCHUP_RAW = {
    "fantasy_content": {
        "team": [
            [
                {"team_key": "469.l.1.t.5"},
                {"team_id": "5"},
                {"name": "Poachers"},
            ],
            {"matchups": {"count": 1, "0": {"matchup": MATCHUP_NODE}}},
        ]
    }
}

# Full raw response from the league scoreboard endpoint.
SCOREBOARD_RAW = {
    "fantasy_content": {
        "league": [
            {"league_key": "469.l.1", "name": "Test League"},
            {
                "scoreboard": {
                    "week": "10",
                    "0": {"matchups": {"count": 1, "0": {"matchup": MATCHUP_NODE}}},
                }
            },
        ]
    }
}

# --- season teams/stats response (league/{key}/teams/stats) -----------------
# Two teams with season totals; ERA present (lower better), H/AB empty for the
# season as Yahoo returns it.

def _season_team(team_key, name, r, hr, era):
    return [
        [{"team_key": team_key}, {"team_id": team_key[-1]}, {"name": name}],
        {
            "team_stats": {
                "coverage_type": "season",
                "stats": [
                    {"stat": {"stat_id": "60", "value": ""}},
                    {"stat": {"stat_id": "7", "value": str(r)}},
                    {"stat": {"stat_id": "12", "value": str(hr)}},
                    {"stat": {"stat_id": "26", "value": era}},
                ],
            },
            "team_points": {"total": "0"},
        },
    ]


TEAMS_STATS_RAW = {
    "fantasy_content": {
        "league": [
            {"league_key": "469.l.1"},
            {
                "teams": {
                    "count": 2,
                    "0": {"team": _season_team("469.l.1.t.5", "Poachers", 308, 62, "3.79")},
                    "1": {"team": _season_team("469.l.1.t.7", "Perfect Pitch", 307, 92, "4.05")},
                }
            },
        ]
    }
}

# --- standings list (shape of yfa League.standings() entries) ---------------
STANDINGS_LIST = [
    {
        "team_key": "469.l.1.t.5",
        "name": "Poachers",
        "rank": "1",
        "playoff_seed": "1",
        "outcome_totals": {"wins": "50", "losses": "35", "ties": "5", "percentage": ".583"},
        "games_back": "-",
    },
    {
        "team_key": "469.l.1.t.7",
        "name": "Perfect Pitch",
        "rank": "2",
        "playoff_seed": "2",
        "outcome_totals": {"wins": "46", "losses": "37", "ties": "7", "percentage": ".550"},
        "games_back": "3.5",
    },
]

# --- a single player entry from the /players collection (with stats) --------
PLAYER_ENTRY = [
    [
        {"player_key": "469.p.1"},
        {"player_id": "1"},
        {"name": {"full": "Test Hitter", "first": "Test", "last": "Hitter"}},
        {"editorial_team_abbr": "NYY"},
        {"display_position": "1B,OF"},
        {"position_type": "B"},
        {"eligible_positions": [
            {"position": "1B"}, {"position": "OF"}, {"position": "Util"},
        ]},
    ],
    {"percent_owned": {"value": 24}},
    {"player_stats": {"stats": [
        {"stat": {"stat_id": "60", "value": "39/144"}},  # ratio, kept as string
        {"stat": {"stat_id": "7", "value": "21"}},
        {"stat": {"stat_id": "12", "value": "12"}},
        {"stat": {"stat_id": "3", "value": ".271"}},
    ]}},
]


# A taken (rostered) player: same shape as PLAYER_ENTRY plus an ownership
# sub-resource (requested via ;out=ownership) naming the owning fantasy team.
PLAYER_ENTRY_OWNED = [
    [
        {"player_key": "469.p.2"},
        {"player_id": "2"},
        {"name": {"full": "Owned Closer", "first": "Owned", "last": "Closer"}},
        {"editorial_team_abbr": "LAD"},
        {"display_position": "RP"},
        {"position_type": "P"},
        {"eligible_positions": [{"position": "RP"}, {"position": "P"}]},
    ],
    {"percent_owned": {"value": 98}},
    {"ownership": {
        "ownership_type": "team",
        "owner_team_key": "469.l.1.t.5",
        "owner_team_name": "Lincolnshire Poachers",
    }},
    {"player_stats": {"stats": [
        {"stat": {"stat_id": "32", "value": "14"}},  # SV
        {"stat": {"stat_id": "42", "value": "31"}},  # K
    ]}},
]


# --- raw league/{key}/settings response (for build_scoring_config) ----------
# stat_categories.stats lists each category with display_name, sort_order
# ("1" high-first, "0" low-first), and is_only_display_stat ("1" for
# informational stats like IP / H-AB). Mirrors the league's 10 scored
# categories plus the two informational ones, in Yahoo's display order.

def _stat(stat_id, display_name, sort_order, only_display=None):
    s = {"stat_id": stat_id, "display_name": display_name, "sort_order": sort_order}
    if only_display is not None:
        s["is_only_display_stat"] = only_display
    return {"stat": s}


SETTINGS_RAW = {
    "fantasy_content": {
        "league": [
            {"league_key": "469.l.1", "name": "Test League"},
            {"settings": [{"stat_categories": {"stats": [
                _stat(60, "H/AB", "1", only_display="1"),   # informational
                _stat(7, "R", "1"),
                _stat(12, "HR", "1"),
                _stat(13, "RBI", "1"),
                _stat(16, "SB", "1"),
                _stat(3, "AVG", "1"),
                _stat(50, "IP", "1", only_display="1"),      # informational
                _stat(28, "W", "1"),
                _stat(32, "SV", "1"),
                _stat(42, "K", "1"),
                _stat(26, "ERA", "0"),                       # lower is better
                _stat(27, "WHIP", "0"),                      # lower is better
            ]}}]},
        ]
    }
}


# --- points-league settings (fantasy football) -----------------------------
# A points league prices each stat via a stat_modifiers block, which a
# categories league lacks -- the signal build_scoring_config keys is_points_league
# on (scoring_type == "point" is the other authoritative signal). Uses real NFL
# stat_ids/names sourced from the Yahoo `nfl` game stat_categories resource
# (game 470): Pass Yds=4, Pass TD=5, Int=6, Rush Yds=9, Rush TD=10, Rec Yds=12,
# Rec TD=13, Fum Lost=18. NOTE: assembled on-spec ahead of a drafted NFL league;
# re-verify the live matchup/settings shape once a real league exists.

def _modifier(stat_id, value):
    return {"stat": {"stat_id": stat_id, "value": value}}


SETTINGS_RAW_POINTS = {
    "fantasy_content": {
        "league": [
            {"league_key": "470.l.1", "name": "Test Football League",
             "game_code": "nfl", "scoring_type": "head"},
            {"settings": [{
                "stat_categories": {"stats": [
                    _stat(4, "Pass Yds", "1"),
                    _stat(5, "Pass TD", "1"),
                    _stat(6, "Int", "1"),
                    _stat(9, "Rush Yds", "1"),
                    _stat(10, "Rush TD", "1"),
                    _stat(12, "Rec Yds", "1"),
                    _stat(13, "Rec TD", "1"),
                    _stat(18, "Fum Lost", "1"),
                ]},
                "stat_modifiers": {"stats": [
                    _modifier(4, "0.04"), _modifier(5, "4"), _modifier(6, "-1"),
                    _modifier(9, "0.1"), _modifier(10, "6"), _modifier(12, "0.1"),
                    _modifier(13, "6"), _modifier(18, "-2"),
                ]},
            }]},
        ]
    }
}


# --- users/games/leagues?use_login=1 (account's leagues) -------------------
# Source for _parse_my_leagues. Reproduces the real nesting: users ->
# user[guid, games] -> games keyed "0".."N"+count -> game[meta, leagues] ->
# leagues keyed "0".."N"+count -> league[meta_dict]. Two games (two seasons)
# and three leagues total exercise multi-game walking, the count sentinels,
# and season/code extraction.

def _league_node(league_key, league_id, name, season, game_code="mlb"):
    return {"league": [{
        "league_key": league_key,
        "league_id": league_id,
        "name": name,
        "season": season,
        "game_code": game_code,
        "scoring_type": "head",
        "num_teams": 10,
    }]}


MY_LEAGUES_RAW = {
    "fantasy_content": {
        "users": {
            "0": {
                "user": [
                    {"guid": "VXFKDXTYW7O5BKPQEA3VW45I2M"},
                    {"games": {
                        "0": {"game": [
                            {"game_key": "469", "game_id": "469", "name": "Baseball",
                             "code": "mlb", "season": "2026"},
                            {"leagues": {
                                "0": _league_node("469.l.1", "1", "Keeper Klassic", "2026"),
                                "1": _league_node("469.l.2", "2", "Dynasty Dudes", "2026"),
                                "count": 2,
                            }},
                        ]},
                        "1": {"game": [
                            {"game_key": "458", "game_id": "458", "name": "Baseball",
                             "code": "mlb", "season": "2025"},
                            {"leagues": {
                                "0": _league_node("458.l.99", "99", "Last Year League", "2025"),
                                "count": 1,
                            }},
                        ]},
                        "count": 2,
                    }},
                ]
            },
            "count": 1,
        }
    }
}
