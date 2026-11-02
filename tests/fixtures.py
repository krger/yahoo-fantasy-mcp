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

# --- points-league (fantasy football) matchup ------------------------------
# A points league decides the matchup by each team's single fantasy-points
# total (team_points.total) plus a matchup-level winner_team_key / is_tied --
# there are NO per-category stat_winners. The team_stats lines are informational
# only. Uses real NFL stat_ids (Pass Yds=4, Pass TD=5, Rush Yds=9, Rec TD=13).
# NOTE: assembled on-spec ahead of a drafted NFL league. The winner half is
# now live-confirmed (see MATCHUP_NODE_POINTS_LIVE below), but the team_stats
# block here has NEVER been seen in a live NFL matchup response -- keep this
# fixture as the "if Yahoo ever serves team-level stat lines" case, not as
# evidence that it does.

TEAM_NODE_PTS_3 = [
    [
        {"team_key": "470.l.1.t.3"},
        {"team_id": "3"},
        {"name": "Gridiron Gophers"},
        [],
        {"is_owned_by_current_login": 1},
    ],
    {
        "team_stats": {"coverage_type": "week", "week": "1", "stats": [
            {"stat": {"stat_id": "4", "value": "312"}},   # Pass Yds
            {"stat": {"stat_id": "5", "value": "3"}},     # Pass TD
            {"stat": {"stat_id": "9", "value": "88"}},    # Rush Yds
            {"stat": {"stat_id": "13", "value": "2"}},    # Rec TD
        ]},
        "team_points": {"coverage_type": "week", "week": "1", "total": "112.34"},
        "team_projected_points": {"coverage_type": "week", "week": "1", "total": "104.90"},
    },
]

TEAM_NODE_PTS_8 = [
    [
        {"team_key": "470.l.1.t.8"},
        {"team_id": "8"},
        {"name": "Turf Titans"},
        [],
        {"is_owned_by_current_login": 0},
    ],
    {
        "team_stats": {"coverage_type": "week", "week": "1", "stats": [
            {"stat": {"stat_id": "4", "value": "245"}},
            {"stat": {"stat_id": "5", "value": "1"}},
            {"stat": {"stat_id": "9", "value": "140"}},
            {"stat": {"stat_id": "13", "value": "1"}},
        ]},
        "team_points": {"coverage_type": "week", "week": "1", "total": "98.10"},
        "team_projected_points": {"coverage_type": "week", "week": "1", "total": "101.20"},
    },
]

# Team 3 wins on total points; no stat_winners in a points league.
MATCHUP_NODE_POINTS = {
    "week": "1",
    "week_start": "2026-09-10",
    "week_end": "2026-09-15",
    "status": "postevent",
    "is_playoffs": "0",
    "is_tied": 0,
    "winner_team_key": "470.l.1.t.3",
    "0": {"teams": {
        "count": 2,
        "0": {"team": TEAM_NODE_PTS_3},
        "1": {"team": TEAM_NODE_PTS_8},
    }},
}

MATCHUP_RAW_POINTS = {
    "fantasy_content": {"team": [
        [{"team_key": "470.l.1.t.3"}, {"team_id": "3"}, {"name": "Gridiron Gophers"}],
        {"matchups": {"count": 1, "0": {"matchup": MATCHUP_NODE_POINTS}}},
    ]}
}

SCOREBOARD_RAW_POINTS = {
    "fantasy_content": {"league": [
        {"league_key": "470.l.1", "name": "Test Football League"},
        {"scoreboard": {"week": "1", "0": {"matchups": {
            "count": 1, "0": {"matchup": MATCHUP_NODE_POINTS}}}}},
    ]}
}

# The live-observed points-league matchup node (NFL league 1529868, week 1,
# checked 2026-09-15 once the week had settled to `postevent`). Two things
# differ from the on-spec node above, and both are the real shape:
#   * the team nodes carry team_points / team_projected_points but NO
#     team_stats -- Yahoo serves no team-level stat aggregation for a points
#     league, so `stat_lines` is legitimately empty even after a played week;
#   * winner_team_key / is_tied are populated at postevent, which is what
#     drives `result` / `winner`.
# Reconstructed from the connector's parsed output (real team keys, names and
# totals), so it covers exactly the fields the parser consumes. Each team node
# also carries a `win_probability` (a bare 0-1 number, settled to 0 / 1 here);
# values from the raw team/{key}/matchups response, re-checked 2026-11-02.

TEAM_NODE_PTS_LIVE_4 = [
    [
        {"team_key": "470.l.1529868.t.4"},
        {"team_id": "4"},
        {"name": "Lincolnshire Poachers"},
        [],
        {"is_owned_by_current_login": 1},
    ],
    {
        "win_probability": 0,
        "team_points": {"coverage_type": "week", "week": "1", "total": "103.16"},
        "team_projected_points": {"coverage_type": "week", "week": "1", "total": "116.16"},
    },
]

TEAM_NODE_PTS_LIVE_8 = [
    [
        {"team_key": "470.l.1529868.t.8"},
        {"team_id": "8"},
        {"name": "Raw-Lee"},
        [],
        {"is_owned_by_current_login": 0},
    ],
    {
        "win_probability": 1,
        "team_points": {"coverage_type": "week", "week": "1", "total": "132.10"},
        "team_projected_points": {"coverage_type": "week", "week": "1", "total": "105.70"},
    },
]

MATCHUP_NODE_POINTS_LIVE = {
    "week": "1",
    "week_start": "2026-09-09",
    "week_end": "2026-09-14",
    "status": "postevent",
    "is_playoffs": "0",
    "is_tied": 0,
    "winner_team_key": "470.l.1529868.t.8",
    "0": {"teams": {
        "count": 2,
        "0": {"team": TEAM_NODE_PTS_LIVE_4},
        "1": {"team": TEAM_NODE_PTS_LIVE_8},
    }},
}


# Points-league standings entries carry points_for / points_against (merged
# from Yahoo's team_standings by yfa) instead of category totals. They carry
# NO games_back -- not even the leader's "-" -- confirmed against the raw
# league/{key}/standings response for NFL league 1529868 on 2026-09-15.
STANDINGS_LIST_POINTS = [
    {
        "team_key": "470.l.1.t.3",
        "name": "Gridiron Gophers",
        "rank": "1",
        "playoff_seed": "1",
        "outcome_totals": {"wins": "10", "losses": "3", "ties": "0", "percentage": ".769"},
        "streak": {"type": "win", "value": "3"},
        "points_for": "1543.22",
        "points_against": "1402.88",
    },
    {
        "team_key": "470.l.1.t.8",
        "name": "Turf Titans",
        "rank": "2",
        "playoff_seed": "2",
        "outcome_totals": {"wins": "9", "losses": "4", "ties": "0", "percentage": ".692"},
        "streak": {"type": "loss", "value": "1"},
        "points_for": "1498.10",
        "points_against": "1450.05",
    },
]


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


# A points-league (NFL) player from players;player_keys=…/stats;type=week;week=8,
# shaped like the live response for league 1529868 (raw-checked 2026-11-02):
# player_points sits beside player_stats with the same coverage. Values are
# Joe Burrow's real week-8 line. A categories league sends no player_points.
PLAYER_ENTRY_POINTS_WEEK = [
    [
        {"player_key": "470.p.32671"},
        {"player_id": "32671"},
        {"name": {"full": "Joe Burrow", "first": "Joe", "last": "Burrow"}},
        {"editorial_team_abbr": "Cin"},
        {"display_position": "QB"},
        {"position_type": "O"},
        {"bye_weeks": {"week": "6"}},
        {"eligible_positions": [{"position": "QB"}]},
    ],
    {
        "player_stats": {
            "0": {"coverage_type": "week", "week": "8"},
            "stats": [
                {"stat": {"stat_id": "4", "value": "462"}},  # Pass Yds
                {"stat": {"stat_id": "5", "value": "5"}},    # Pass TD
                {"stat": {"stat_id": "6", "value": "3"}},    # Int
            ],
        },
        "player_points": {
            "0": {"coverage_type": "week", "week": "8"},
            "total": "42.48",
        },
    },
]


# percent_owned / percent_started sub-resources in their live list form
# (players;…;out=percent_owned,percent_started, NFL 1529868, 2026-11-02):
# a coverage header then single-key value/delta dicts. Burrow's real figures.
PLAYER_ENTRY_TRENDING = [
    PLAYER_ENTRY_POINTS_WEEK[0],
    {"percent_owned": [{"coverage_type": "week", "week": "8"},
                       {"value": 100}, {"delta": "1"}]},
    {"percent_started": [{"coverage_type": "week", "week": 8},
                         {"value": 91}, {"delta": 18}]},
]


# --- full-season team/{key}/matchups (raw-checked 2026-11-02) --------------
# Every week in one "0".."N" + count collection of {"matchup": node}.
# Points (NFL 1529868, team 4): settled week 1 carries matchup_grades and
# is_consolation; future week 15 is preevent with real projections and win
# probabilities but zero points. Values are the live response's.

def _team_node_pts(team_key, name, week, wp, pts, proj):
    return [
        [{"team_key": team_key}, {"team_id": team_key.rsplit(".", 1)[1]},
         {"name": name}, [], {"is_owned_by_current_login": int(team_key.endswith(".t.4"))}],
        {
            "win_probability": wp,
            "team_points": {"coverage_type": "week", "week": week, "total": pts},
            "team_projected_points": {"coverage_type": "week", "week": week, "total": proj},
        },
    ]


SCHEDULE_RAW_POINTS = {"fantasy_content": {"team": [
    [{"team_key": "470.l.1529868.t.4"}, {"name": "Lincolnshire Poachers"}],
    {"matchups": {
        # Yahoo's own order is by week; keyed out of order here so the
        # parser's sort is exercised.
        "1": {"matchup": {
            "week": 15, "week_start": "2026-12-15", "week_end": "2026-12-21",
            "status": "preevent", "is_playoffs": "0", "is_consolation": "0",
            "0": {"teams": {"count": 2,
                "0": {"team": _team_node_pts("470.l.1529868.t.4", "Lincolnshire Poachers",
                                             15, 0.49, "0.00", "93.74")},
                "1": {"team": _team_node_pts("470.l.1529868.t.10", "Quick Snap Robert",
                                             15, 0.51, "0.00", "95.07")},
            }},
        }},
        "0": {"matchup": {
            **MATCHUP_NODE_POINTS_LIVE,
            "is_consolation": "0",
            "matchup_grades": [
                {"matchup_grade": {"team_key": "470.l.1529868.t.4", "grade": "D+"}},
                {"matchup_grade": {"team_key": "470.l.1529868.t.8", "grade": "A+"}},
            ],
        }},
        "count": 2,
    }},
]}}

# Categories (shape of MLB 60467): matchup nodes carry an overall
# winner_team_key / is_tied too once settled. Week 10 is won by team 5; a
# tied week mirrors live week 14 (is_tied 1, no winner key, 5-5).
SCHEDULE_RAW_CATEGORIES = {"fantasy_content": {"team": [
    [{"team_key": "469.l.1.t.5"}, {"name": "Poachers"}],
    {"matchups": {
        "0": {"matchup": {**MATCHUP_NODE, "status": "postevent", "is_tied": 0,
                          "winner_team_key": "469.l.1.t.5"}},
        "1": {"matchup": {**MATCHUP_NODE, "week": "14", "status": "postevent",
                          "is_tied": 1}},
        "2": {"matchup": {"week": "15", "0": {"teams": {"count": 0}}}},  # unresolvable
        "count": 3,
    }},
]}}


# --- draft (live NFL 1529868 shapes, raw-checked 2026-11-02) ---------------
# league/{key}/draftresults: picks as a "0".."N" + count collection of
# {"draft_result": {...}}; real first three picks of the league's draft.
DRAFT_RESULTS_RAW = {"fantasy_content": {"league": [
    {"league_key": "470.l.1529868", "league_id": "1529868"},
    {"draft_results": {
        "0": {"draft_result": {"pick": 1, "round": 1,
                               "team_key": "470.l.1529868.t.7", "player_key": "470.p.40059"}},
        "1": {"draft_result": {"pick": 2, "round": 1,
                               "team_key": "470.l.1529868.t.4", "player_key": "470.p.40055"}},
        "2": {"draft_result": {"pick": 3, "round": 1,
                               "team_key": "470.l.1529868.t.1", "player_key": "470.p.33393"}},
        "count": 3,
    }},
]}}

# Roster-rule flags in live player meta (NFL 1529868, 2026-11-02): every
# player carries is_undroppable ("0"/"1") and is_keeper (all false outside
# keeper leagues). Josh Allen is one of the league's 20 undroppable players.
PLAYER_ENTRY_UNDROPPABLE = [[
    {"player_key": "470.p.30977"},
    {"player_id": "30977"},
    {"name": {"full": "Josh Allen", "first": "Josh", "last": "Allen"}},
    {"is_keeper": {"status": False, "cost": False, "kept": False}},
    {"editorial_team_abbr": "Buf"},
    {"is_undroppable": "1"},
    {"position_type": "O"},
    {"eligible_positions": [{"position": "QB"}]},
]]


# A player from players;player_keys=…/draft_analysis: meta plus the
# draft_analysis list of single-key dicts (Bijan Robinson's real figures).
PLAYER_ENTRY_DRAFT_ANALYSIS = [
    [
        {"player_key": "470.p.40055"},
        {"player_id": "40055"},
        {"name": {"full": "Bijan Robinson", "first": "Bijan", "last": "Robinson"}},
        {"editorial_team_abbr": "Atl"},
        {"display_position": "RB"},
        {"position_type": "O"},
        {"eligible_positions": [{"position": "RB"}, {"position": "W/R/T"}]},
    ],
    {"draft_analysis": [
        {"average_pick": "2.1"}, {"average_round": "1.0"},
        {"average_cost": "70.9"}, {"percent_drafted": "1.00"},
        {"preseason_average_pick": "2.1"}, {"preseason_average_round": "1.0"},
        {"preseason_average_cost": "70.9"}, {"preseason_percent_drafted": "1.0"},
    ]},
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
