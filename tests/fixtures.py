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
