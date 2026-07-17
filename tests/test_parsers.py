"""Unit tests for the Yahoo response parsers in yahoo_parsers.py.

These exercise the pure parsing/normalization helpers against faithful
fixtures (no network, no credentials). The parsers are the repo's main source
of bugs, so the focus is the documented gotchas: positional/keyed nesting,
stat_id mapping, win/loss/tie resolution, rate-stat ranking direction, and
numeric coercion.
"""

import yahoo_parsers as parsers
from tests import fixtures as fx

# Scoring config built from the league-settings fixture; the parsers that
# label/rank categories take this instead of reading module globals.
SCORING = parsers.build_scoring_config(fx.SETTINGS_RAW)

# Points-league (fantasy football) scoring config for the points-framing tests.
SCORING_PTS = parsers.build_scoring_config(fx.SETTINGS_RAW_POINTS)


# --- scoring config from league settings -----------------------------------

def test_build_scoring_config_from_settings():
    sc = SCORING
    # labels cover scored + informational categories
    assert sc.label("7") == "R"
    assert sc.label("26") == "ERA"
    assert sc.label("60") == "H/AB"
    assert sc.label("999") == "999"            # unknown -> id itself
    # scored categories in display order, informational (IP 50, H/AB 60) excluded
    assert sc.scored_stat_ids == ["7", "12", "13", "16", "3", "28", "32", "42", "26", "27"]
    assert "60" not in sc.scored_stat_ids and "50" not in sc.scored_stat_ids
    # sort_order "0" -> lower is better
    assert sc.lower_is_better == frozenset({"26", "27"})


def test_build_scoring_config_empty_on_bad_shape():
    sc = parsers.build_scoring_config({})
    assert sc.scored_stat_ids == [] and sc.stat_id_to_name == {}
    assert sc.label("7") == "7"                # degrades to the raw id
    assert sc.is_points_league is False        # default framing is categories


def test_categories_league_is_not_points():
    # The baseball settings fixture has no stat_modifiers -> categories framing.
    assert SCORING.is_points_league is False


def test_points_league_detected_from_stat_modifiers():
    # A football-style points league prices stats via stat_modifiers.
    sc = parsers.build_scoring_config(fx.SETTINGS_RAW_POINTS)
    assert sc.is_points_league is True
    # Labels still resolve from the real NFL stat_ids...
    assert sc.label("4") == "Pass Yds"
    assert sc.label("13") == "Rec TD"
    # ...and non-display stats populate scored_stat_ids as usual (their per-
    # category ranking is just unused for a points league's standings).
    assert sc.scored_stat_ids == ["4", "5", "6", "9", "10", "12", "13", "18"]


def test_points_league_detected_from_scoring_type(monkeypatch):
    # scoring_type == "point" is authoritative even without stat_modifiers
    # (season-long points leagues).
    raw = {"fantasy_content": {"league": [
        {"league_key": "470.l.9", "scoring_type": "point"},
        {"settings": [{"stat_categories": {"stats": [
            {"stat": {"stat_id": 4, "display_name": "Pass Yds", "sort_order": "1"}}]}}]},
    ]}}
    sc = parsers.build_scoring_config(raw)
    assert sc.is_points_league is True


# --- numeric coercion ------------------------------------------------------

def test_to_int_coerces_only_integers():
    assert parsers._to_int("25") == 25
    assert parsers._to_int("-3") == -3
    assert parsers._to_int(".583") == ".583"   # not an int -> unchanged
    assert parsers._to_int("3.5") == "3.5"
    assert parsers._to_int(7) == 7


def test_to_number_handles_int_float_and_passthrough():
    assert parsers._to_number("25") == 25
    assert isinstance(parsers._to_number("25"), int)
    assert parsers._to_number(".583") == 0.583
    assert parsers._to_number("3.5") == 3.5
    assert parsers._to_number("-") == "-"          # games_back placeholder
    assert parsers._to_number("10/40") == "10/40"  # ratio stays a string
    assert parsers._to_number("") == ""


# --- team summary extraction ----------------------------------------------

def test_extract_team_summary_locates_fields_by_key():
    s = parsers._extract_team_summary(fx.TEAM_NODE_5)
    assert s["team_key"] == "469.l.1.t.5"
    assert s["name"] == "Poachers"
    assert s["category_points"] == "2"
    # stats keyed by stat_id, raw values preserved
    assert s["stats"]["7"] == 25
    assert s["stats"]["26"] == "3.94"
    assert s["stats"]["60"] == "10/40"


# --- matchup node: team/opponent framing ----------------------------------

def test_parse_matchup_node_perspective_results():
    m = parsers._parse_matchup_node(fx.MATCHUP_NODE, SCORING, "469.l.1.t.5")
    assert m["scoring"] == "categories"
    assert m["week"] == 10
    assert m["is_playoffs"] is False
    assert m["team"]["team_key"] == "469.l.1.t.5"
    assert m["team"]["category_points"] == 2
    assert m["opponent"]["team_key"] == "469.l.1.t.7"

    by_stat = {c["stat"]: c for c in m["categories"]}
    assert by_stat["R"]["result"] == "win"
    assert by_stat["HR"]["result"] == "loss"
    assert by_stat["RBI"]["result"] == "tie"
    assert by_stat["ERA"]["result"] == "win"      # rate stat, winner from Yahoo
    # informational stat carries no result and is not scored
    assert by_stat["H/AB"]["result"] is None
    assert by_stat["H/AB"]["scored"] is False
    assert by_stat["R"]["scored"] is True
    # team vs opponent values land on the right side
    assert by_stat["HR"]["team"] == 6
    assert by_stat["HR"]["opponent"] == 13


def test_parse_matchup_node_perspective_flips_for_opponent():
    m = parsers._parse_matchup_node(fx.MATCHUP_NODE, SCORING, "469.l.1.t.7")
    assert m["team"]["team_key"] == "469.l.1.t.7"
    by_stat = {c["stat"]: c for c in m["categories"]}
    # what was a win for t5 is a loss for t7
    assert by_stat["R"]["result"] == "loss"
    assert by_stat["HR"]["result"] == "win"
    assert by_stat["RBI"]["result"] == "tie"


def test_parse_matchup_node_raises_when_team_absent():
    try:
        parsers._parse_matchup_node(fx.MATCHUP_NODE, SCORING, "469.l.1.t.99")
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown perspective team")


# --- matchup node: neutral framing (scoreboard) ---------------------------

def test_parse_matchup_node_neutral_values_and_winner():
    m = parsers._parse_matchup_node(fx.MATCHUP_NODE, SCORING)
    assert m["scoring"] == "categories"
    assert "teams" in m and "team" not in m
    assert {t["team_key"] for t in m["teams"]} == {"469.l.1.t.5", "469.l.1.t.7"}

    by_stat = {c["stat"]: c for c in m["categories"]}
    r = by_stat["R"]
    assert r["values"]["469.l.1.t.5"] == 25
    assert r["values"]["469.l.1.t.7"] == 20
    assert r["winner"] == "469.l.1.t.5"
    assert by_stat["RBI"]["winner"] == "tie"
    assert by_stat["H/AB"]["winner"] is None
    assert by_stat["H/AB"]["scored"] is False


# --- full-response wrappers ------------------------------------------------

def test_parse_matchup_unwraps_team_response():
    m = parsers._parse_matchup(fx.MATCHUP_RAW, "469.l.1.t.5", SCORING)
    assert m["team"]["team_key"] == "469.l.1.t.5"
    assert m["opponent"]["team_key"] == "469.l.1.t.7"


def test_parse_matchup_raises_on_empty():
    try:
        parsers._parse_matchup({"fantasy_content": {"team": [None, {}]}}, "469.l.1.t.5", SCORING)
    except ValueError:
        return
    raise AssertionError("expected ValueError when matchup node missing")


def test_parse_scoreboard_returns_list_of_breakdowns():
    out = parsers._parse_scoreboard(fx.SCOREBOARD_RAW, SCORING)
    assert isinstance(out, list) and len(out) == 1
    assert "teams" in out[0]
    assert out[0]["week"] == 10


# --- matchup node: points-league (football) framing -----------------------

def test_parse_matchup_node_points_perspective():
    m = parsers._parse_matchup_node(fx.MATCHUP_NODE_POINTS, SCORING_PTS, "470.l.1.t.3")
    assert m["scoring"] == "points"
    assert m["week"] == 1
    # winner comes from the matchup-level winner_team_key, not per-category.
    assert m["result"] == "win"
    assert m["team"]["team_key"] == "470.l.1.t.3"
    assert m["team"]["points"] == 112.34            # fantasy-points total, coerced
    assert m["team"]["projected_points"] == 104.90
    assert m["opponent"]["team_key"] == "470.l.1.t.8"
    assert m["opponent"]["points"] == 98.10
    # no per-category win/loss framing in a points league
    assert "categories" not in m
    by = {s["stat"]: s for s in m["stat_lines"]}
    assert by["Pass Yds"]["team"] == "312" and by["Pass Yds"]["opponent"] == "245"
    assert "result" not in by["Pass Yds"] and "scored" not in by["Pass Yds"]


def test_parse_matchup_node_points_flips_for_opponent():
    m = parsers._parse_matchup_node(fx.MATCHUP_NODE_POINTS, SCORING_PTS, "470.l.1.t.8")
    assert m["team"]["team_key"] == "470.l.1.t.8"
    assert m["result"] == "loss"                    # t8 lost on total points
    assert m["team"]["points"] == 98.10


def test_parse_matchup_node_points_tie_and_undecided():
    # is_tied wins over winner_team_key; a node with neither -> result None.
    tied = {**fx.MATCHUP_NODE_POINTS, "is_tied": 1, "winner_team_key": None}
    assert parsers._parse_matchup_node(tied, SCORING_PTS, "470.l.1.t.3")["result"] == "tie"
    undecided = {**fx.MATCHUP_NODE_POINTS, "is_tied": 0, "winner_team_key": None}
    assert parsers._parse_matchup_node(undecided, SCORING_PTS, "470.l.1.t.3")["result"] is None


def test_parse_matchup_node_points_neutral_winner():
    m = parsers._parse_matchup_node(fx.MATCHUP_NODE_POINTS, SCORING_PTS)
    assert m["scoring"] == "points"
    assert "teams" in m and "team" not in m
    assert m["winner"] == "470.l.1.t.3"
    pts = {t["team_key"]: t["points"] for t in m["teams"]}
    assert pts == {"470.l.1.t.3": 112.34, "470.l.1.t.8": 98.10}
    by = {s["stat"]: s for s in m["stat_lines"]}
    assert by["Rush Yds"]["values"] == {"470.l.1.t.3": "88", "470.l.1.t.8": "140"}


def test_parse_matchup_points_wrapper_unwraps_team_response():
    m = parsers._parse_matchup(fx.MATCHUP_RAW_POINTS, "470.l.1.t.3", SCORING_PTS)
    assert m["scoring"] == "points" and m["result"] == "win"


def test_parse_scoreboard_points_league():
    out = parsers._parse_scoreboard(fx.SCOREBOARD_RAW_POINTS, SCORING_PTS)
    assert len(out) == 1 and out[0]["scoring"] == "points"
    assert out[0]["winner"] == "470.l.1.t.3"


# --- season stats + ranking ------------------------------------------------

def test_parse_team_season_stats_coerces_values():
    by_team = parsers._parse_team_season_stats(fx.TEAMS_STATS_RAW)
    assert set(by_team) == {"469.l.1.t.5", "469.l.1.t.7"}
    assert by_team["469.l.1.t.5"]["7"] == 308          # int
    assert by_team["469.l.1.t.5"]["26"] == 3.79        # float
    assert by_team["469.l.1.t.7"]["60"] == ""          # empty season H/AB


def test_rank_season_categories_direction_and_ties():
    stats = {
        "A": {"12": 30, "26": 3.0},   # HR high, ERA mid
        "B": {"12": 20, "26": 4.0},   # HR tied-low, ERA worst
        "C": {"12": 20, "26": 2.0},   # HR tied-low, ERA best
    }
    ranked = parsers._rank_season_categories(stats, SCORING)

    def rank(team, stat):
        return next(c["rank"] for c in ranked[team] if c["stat"] == stat)

    # HR: higher is better -> A first, B & C tie for 2nd (competition ranking)
    assert rank("A", "HR") == 1
    assert rank("B", "HR") == 2
    assert rank("C", "HR") == 2
    # ERA: lower is better -> C best, A second, B worst
    assert rank("C", "ERA") == 1
    assert rank("A", "ERA") == 2
    assert rank("B", "ERA") == 3


def test_rank_season_categories_unranked_when_non_numeric():
    stats = {"A": {"12": 30}, "B": {"12": ""}}
    ranked = parsers._rank_season_categories(stats, SCORING)
    b_hr = next(c for c in ranked["B"] if c["stat"] == "HR")
    assert b_hr["rank"] is None
    assert b_hr["value"] == ""


# --- standings -------------------------------------------------------------

def test_parse_standings_records_only():
    out = parsers._parse_standings(fx.STANDINGS_LIST)
    leader, second = out
    assert leader["rank"] == 1
    assert leader["record"] == {"wins": 50, "losses": 35, "ties": 5, "pct": 0.583}
    assert leader["games_back"] is None              # "-" -> None
    assert second["games_back"] == 3.5               # fractional coercion
    assert "categories" not in leader                # not requested


def test_parse_standings_merges_categories_by_team_key():
    season = {"469.l.1.t.5": [{"stat": "HR", "value": 62, "rank": 9}]}
    out = parsers._parse_standings(fx.STANDINGS_LIST, season)
    leader = next(t for t in out if t["team_key"] == "469.l.1.t.5")
    other = next(t for t in out if t["team_key"] == "469.l.1.t.7")
    assert leader["categories"] == [{"stat": "HR", "value": 62, "rank": 9}]
    assert other["categories"] == []                 # missing -> empty list


def test_parse_standings_points_league_surfaces_points_for_against():
    # A points league carries points_for/points_against (no category totals);
    # the handler passes season_categories=None so no "categories" key appears.
    out = parsers._parse_standings(fx.STANDINGS_LIST_POINTS)
    leader = next(t for t in out if t["team_key"] == "470.l.1.t.3")
    assert leader["record"] == {"wins": 10, "losses": 3, "ties": 0, "pct": 0.769}
    assert leader["points_for"] == 1543.22           # coerced to float
    assert leader["points_against"] == 1402.88
    assert "categories" not in leader


# --- free-agent player flattening -----------------------------------------

def test_flatten_raw_yahoo_player_meta_ownership_and_stats():
    flat = parsers._flatten_raw_yahoo_player(fx.PLAYER_ENTRY, SCORING)
    assert flat["name"] == "Test Hitter"
    assert flat["player_id"] == "1"
    assert flat["editorial_team_abbr"] == "NYY"
    assert flat["eligible_positions"] == ["1B", "OF", "Util"]
    assert flat["percent_owned"] == 24
    # stats labeled via _STAT_ID_TO_NAME, numbers coerced, ratio kept as string
    assert flat["stats"] == {"H/AB": "39/144", "R": 21, "HR": 12, "AVG": 0.271}


def test_flatten_raw_yahoo_player_handles_no_subresources():
    flat = parsers._flatten_raw_yahoo_player([fx.PLAYER_ENTRY[0]], SCORING)
    assert flat["name"] == "Test Hitter"
    assert "stats" not in flat
    assert "percent_owned" not in flat
    # free agents carry no ownership block
    assert "ownership" not in flat


def test_flatten_raw_yahoo_player_extracts_owner_team():
    # taken players (;out=ownership) carry the owning fantasy team
    flat = parsers._flatten_raw_yahoo_player(fx.PLAYER_ENTRY_OWNED, SCORING)
    assert flat["name"] == "Owned Closer"
    assert flat["player_id"] == "2"
    assert flat["percent_owned"] == 98
    assert flat["ownership"] == {
        "ownership_type": "team",
        "owner_team_key": "469.l.1.t.5",
        "owner_team_name": "Lincolnshire Poachers",
    }
    # stats still parse alongside ownership
    assert flat["stats"] == {"SV": 14, "K": 31}


# --- team-number resolution ------------------------------------------------

class _FakeLeague:
    league_key = "469.l.1"


_TEAMS = {
    "469.l.1.t.5": {"name": "Poachers", "is_owned_by_current_login": 1},
    "469.l.1.t.7": {"name": "Perfect Pitch"},
}


def test_resolve_team_key_constructs_and_validates():
    assert parsers._resolve_team_key(_FakeLeague(), _TEAMS, 7) == "469.l.1.t.7"


def test_resolve_team_key_out_of_range_returns_none():
    assert parsers._resolve_team_key(_FakeLeague(), _TEAMS, 99) is None


def test_resolve_team_key_none_returns_owned_team():
    assert parsers._resolve_team_key(_FakeLeague(), _TEAMS, None) == "469.l.1.t.5"


def test_resolve_team_key_none_without_owner_falls_back_to_first():
    teams = {"469.l.1.t.3": {"name": "A"}, "469.l.1.t.9": {"name": "B"}}
    assert parsers._resolve_team_key(_FakeLeague(), teams, None) == "469.l.1.t.3"


# --- account league discovery ----------------------------------------------

def test_parse_my_leagues_walks_games_and_leagues():
    leagues = parsers._parse_my_leagues(fx.MY_LEAGUES_RAW)
    # Three leagues across two games (count sentinels skipped, not parsed).
    assert [lg["league_id"] for lg in leagues] == ["1", "2", "99"]
    by_id = {lg["league_id"]: lg for lg in leagues}
    assert by_id["1"]["name"] == "Keeper Klassic"
    assert by_id["1"]["league_key"] == "469.l.1"
    assert by_id["1"]["season"] == "2026"
    # Second game / season is reached too.
    assert by_id["99"]["season"] == "2025"
    assert by_id["99"]["league_key"] == "458.l.99"
    assert all(lg["game_code"] == "mlb" for lg in leagues)


def test_parse_my_leagues_empty_on_bad_shape():
    assert parsers._parse_my_leagues({}) == []
    assert parsers._parse_my_leagues({"fantasy_content": {"users": {"count": 0}}}) == []


def test_parse_my_leagues_tolerates_dict_league_node():
    # Some responses give `league` as a bare dict instead of [dict].
    raw = {"fantasy_content": {"users": {"0": {"user": [
        {"guid": "g"},
        {"games": {"0": {"game": [
            {"code": "mlb"},
            {"leagues": {"0": {"league": {
                "league_key": "469.l.7", "league_id": "7",
                "name": "Solo", "season": "2026",
            }}, "count": 1}},
        ]}, "count": 1}},
    ]}, "count": 1}}}
    leagues = parsers._parse_my_leagues(raw)
    assert len(leagues) == 1
    assert leagues[0]["league_id"] == "7"
    # game_code falls back to the game meta's `code` when absent on the league.
    assert leagues[0]["game_code"] == "mlb"
