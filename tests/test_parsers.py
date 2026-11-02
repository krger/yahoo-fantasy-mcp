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


def test_parse_matchup_node_category_tie_flag_reads_zero_as_not_tied():
    # A category carrying is_tied "0" (or 0) alongside a winner is a win/loss,
    # not a tie — plain truthiness used to read the string "0" as tied.
    node = {**fx.MATCHUP_NODE, "stat_winners": [
        {"stat_winner": {"stat_id": "7", "winner_team_key": "469.l.1.t.5", "is_tied": "0"}},
        {"stat_winner": {"stat_id": "12", "winner_team_key": "469.l.1.t.7", "is_tied": 0}},
        {"stat_winner": {"stat_id": "13", "is_tied": "1"}},
    ]}
    by_stat = {
        c["stat"]: c["result"]
        for c in parsers._parse_matchup_node(node, SCORING, "469.l.1.t.5")["categories"]
    }
    assert by_stat["R"] == "win"
    assert by_stat["HR"] == "loss"
    assert by_stat["RBI"] == "tie"


def test_is_set_reads_yahoo_flags():
    for v in (1, "1", True):
        assert parsers._is_set(v)
    for v in (0, "0", False, None, "", "true"):
        assert not parsers._is_set(v)


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


def test_parse_matchup_missing_node_raises_no_matchup_error():
    """A bye (no matchup node) is distinguishable from a malformed response.

    The handler validates team_key before parsing, so a missing node means a
    legitimate league state — a playoff bye — and must be reportable as a
    result rather than a "check the ID" error.
    """
    try:
        parsers._parse_matchup({"fantasy_content": {"team": [None, {}]}}, "469.l.1.t.5", SCORING)
    except parsers.NoMatchupError:
        pass
    else:
        raise AssertionError("expected NoMatchupError when matchup node missing")
    # Still a ValueError, so existing broad handling keeps working.
    assert issubclass(parsers.NoMatchupError, ValueError)


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


def test_parse_matchup_node_points_live_shape_settled_week():
    # The real NFL response (league 1529868, week 1, postevent): winner comes
    # through, and stat_lines is empty because Yahoo sends no team_stats for a
    # points league -- an empty list here is correct, not a parse failure.
    m = parsers._parse_matchup_node(
        fx.MATCHUP_NODE_POINTS_LIVE, SCORING_PTS, "470.l.1529868.t.4")
    assert m["status"] == "postevent"
    assert m["result"] == "loss"
    assert m["team"]["points"] == 103.16
    assert m["team"]["projected_points"] == 116.16
    assert m["opponent"]["points"] == 132.10
    assert m["stat_lines"] == []


def test_parse_scoreboard_node_points_live_shape_winner_is_higher_scorer():
    m = parsers._parse_matchup_node(fx.MATCHUP_NODE_POINTS_LIVE, SCORING_PTS)
    assert m["winner"] == "470.l.1529868.t.8"
    pts = {t["team_key"]: t["points"] for t in m["teams"]}
    assert pts[m["winner"]] == max(pts.values())
    assert m["stat_lines"] == []


def test_points_matchup_surfaces_win_probability_in_both_framings():
    # Live NFL team nodes carry win_probability; both framings surface it.
    m = parsers._parse_matchup_node(
        fx.MATCHUP_NODE_POINTS_LIVE, SCORING_PTS, "470.l.1529868.t.4")
    assert m["team"]["win_probability"] == 0
    assert m["opponent"]["win_probability"] == 1
    m = parsers._parse_matchup_node(fx.MATCHUP_NODE_POINTS_LIVE, SCORING_PTS)
    probs = {t["team_key"]: t["win_probability"] for t in m["teams"]}
    assert probs == {"470.l.1529868.t.4": 0, "470.l.1529868.t.8": 1}


def test_win_probability_coerced_from_string():
    import copy
    node = copy.deepcopy(fx.TEAM_NODE_PTS_LIVE_4)
    node[1]["win_probability"] = "0.73"
    assert parsers._extract_team_summary(node)["win_probability"] == 0.73
    # Yahoo's float noise is rounded away (seen live 2026-11-02).
    node[1]["win_probability"] = 0.010000000000000009
    assert parsers._extract_team_summary(node)["win_probability"] == 0.01


def test_matchup_omits_win_probability_when_yahoo_sends_none():
    # The categories (MLB) league sends none -> no key at all, not a null.
    m = parsers._parse_matchup_node(fx.MATCHUP_NODE, SCORING, "469.l.1.t.5")
    assert "win_probability" not in m["team"]
    assert "win_probability" not in m["opponent"]
    m = parsers._parse_matchup_node(fx.MATCHUP_NODE_POINTS, SCORING_PTS)
    assert all("win_probability" not in t for t in m["teams"])


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


def test_parse_standings_surfaces_streak_when_yahoo_sends_it():
    # Yahoo's points-league team_standings carry {type, value}; re-emitted as
    # {type, length} with the length coerced to a number.
    leader, second = parsers._parse_standings(fx.STANDINGS_LIST_POINTS)
    assert leader["streak"] == {"type": "win", "length": 3}
    assert second["streak"] == {"type": "loss", "length": 1}


def test_parse_standings_omits_streak_when_absent():
    # A categories league sends no streak block -- the key is absent entirely
    # rather than null, matching how points_for/points_against are handled.
    out = parsers._parse_standings(fx.STANDINGS_LIST)
    assert all("streak" not in team for team in out)


def test_parse_standings_points_league_games_back_null_for_every_team():
    # Yahoo omits games_back from points-league standings entirely, so every
    # team -- not just the leader -- reports null rather than a number.
    out = parsers._parse_standings(fx.STANDINGS_LIST_POINTS)
    assert [t["games_back"] for t in out] == [None, None]


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


def test_flatten_raw_yahoo_player_points_league_week_points():
    # A points league sends player_points beside player_stats; it becomes the
    # flat ``points`` (fantasy points for the same coverage as ``stats``).
    flat = parsers._flatten_raw_yahoo_player(fx.PLAYER_ENTRY_POINTS_WEEK, SCORING_PTS)
    assert flat["name"] == "Joe Burrow"
    assert flat["stats"] == {"Pass Yds": 462, "Pass TD": 5, "Int": 3}
    assert flat["points"] == 42.48


def test_flatten_raw_yahoo_player_categories_league_has_no_points():
    # No player_points in a categories league -> no ``points`` key (not 0).
    flat = parsers._flatten_raw_yahoo_player(fx.PLAYER_ENTRY, SCORING)
    assert "points" not in flat


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


# --- percent owned/started + bye week -------------------------------------

def test_flatten_raw_yahoo_player_percent_started_deltas_and_bye():
    flat = parsers._flatten_raw_yahoo_player(fx.PLAYER_ENTRY_TRENDING, SCORING_PTS)
    assert flat["bye_week"] == 6
    assert flat["percent_owned"] == 100
    assert flat["percent_owned_delta"] == 1  # Yahoo sends it as a string
    assert flat["percent_started"] == 91
    assert flat["percent_started_delta"] == 18


def test_flatten_raw_yahoo_player_partial_percent_nodes():
    # Live: a barely-owned player can carry only a delta, and percent_started
    # can be a bare coverage header. Surface what is there, nothing more.
    entry = [fx.PLAYER_ENTRY_POINTS_WEEK[0],
             {"percent_owned": [{"coverage_type": "week", "week": 8}, {"delta": "0"}]},
             {"percent_started": [{"coverage_type": "date", "date": "2026-11-02"}]}]
    flat = parsers._flatten_raw_yahoo_player(entry, SCORING_PTS)
    assert "percent_owned" not in flat
    assert flat["percent_owned_delta"] == 0
    assert "percent_started" not in flat and "percent_started_delta" not in flat


def test_flatten_raw_yahoo_player_dict_percent_owned_still_parses():
    # The pre-existing dict form keeps working, now with an optional delta.
    assert parsers._flatten_raw_yahoo_player(fx.PLAYER_ENTRY, SCORING)["percent_owned"] == 24
    assert "percent_owned_delta" not in parsers._flatten_raw_yahoo_player(fx.PLAYER_ENTRY, SCORING)


def test_flatten_raw_yahoo_player_baseball_has_no_bye_week():
    assert "bye_week" not in parsers._flatten_raw_yahoo_player(fx.PLAYER_ENTRY, SCORING)


# --- full-season schedule ---------------------------------------------------

def test_team_schedule_points_league_sorted_with_results_and_grades():
    weeks = parsers._parse_team_schedule(
        fx.SCHEDULE_RAW_POINTS, "470.l.1529868.t.4", SCORING_PTS)
    assert [w["week"] for w in weeks] == [1, 15]
    w1, w15 = weeks
    assert w1["status"] == "postevent" and w1["result"] == "loss"
    assert w1["team"]["points"] == 103.16 and w1["opponent"]["points"] == 132.10
    assert w1["grades"] == {"team": "D+", "opponent": "A+"}
    assert w1["is_consolation"] is False and w1["is_playoffs"] is False
    # A future week: undecided, zero points, but real projections + odds.
    assert w15["status"] == "preevent" and w15["result"] is None
    assert w15["opponent"]["name"] == "Quick Snap Robert"
    assert w15["team"]["points"] == 0.0
    assert w15["team"]["projected_points"] == 93.74
    assert w15["team"]["win_probability"] == 0.49
    assert "grades" not in w15
    # Compact: no per-stat detail in a schedule entry.
    assert "stat_lines" not in w1 and "categories" not in w1


def test_team_schedule_categories_league_overall_result_and_tie():
    weeks = parsers._parse_team_schedule(
        fx.SCHEDULE_RAW_CATEGORIES, "469.l.1.t.5", SCORING)
    # The unresolvable week-15 node is skipped, not fatal.
    assert [w["week"] for w in weeks] == [10, 14]
    won, tied = weeks
    assert won["result"] == "win"
    assert won["team"]["category_points"] == 2
    assert won["opponent"]["name"] == "Perfect Pitch"
    assert tied["result"] == "tie"
    assert "win_probability" not in won["team"]


def test_team_schedule_tolerates_bad_shapes():
    assert parsers._parse_team_schedule({}, "t", SCORING) == []
    raw = {"fantasy_content": {"team": [[], {"matchups": []}]}}
    assert parsers._parse_team_schedule(raw, "t", SCORING) == []


def test_overall_result_reads_winner_tie_and_undecided():
    assert parsers._overall_result({"winner_team_key": "a"}, "a", "b") == "win"
    assert parsers._overall_result({"winner_team_key": "b"}, "a", "b") == "loss"
    assert parsers._overall_result({"is_tied": "1"}, "a", "b") == "tie"
    # is_tied "0" is NOT a tie (the truthiness bug #66 fixed elsewhere)
    assert parsers._overall_result({"is_tied": "0"}, "a", "b") is None


# --- draft results + draft analysis ----------------------------------------

def test_parse_draft_results_live_shape_in_pick_order():
    picks = parsers._parse_draft_results(fx.DRAFT_RESULTS_RAW)
    assert [p["pick"] for p in picks] == [1, 2, 3]
    assert picks[1] == {"pick": 2, "round": 1, "team_key": "470.l.1529868.t.4",
                        "player_key": "470.p.40055"}
    assert all("cost" not in p for p in picks)  # not an auction draft


def test_parse_draft_results_sorts_and_keeps_auction_cost():
    raw = {"fantasy_content": {"league": [{}, {"draft_results": {
        "1": {"draft_result": {"pick": "2", "round": "1", "team_key": "t.2",
                               "player_key": "p.2", "cost": "41"}},
        "0": {"draft_result": {"pick": "1", "round": "1", "team_key": "t.1",
                               "player_key": "p.1", "cost": "55"}},
        "count": 2,
    }}]}}
    picks = parsers._parse_draft_results(raw)
    assert [p["pick"] for p in picks] == [1, 2]
    assert [p["cost"] for p in picks] == [55, 41]


def test_parse_draft_results_predraft_and_empty_shapes():
    # Pre-draft picks can lack a player; an empty collection can be a bare [].
    raw = {"fantasy_content": {"league": [{}, {"draft_results": {
        "0": {"draft_result": {"pick": 1, "round": 1, "team_key": "t.1"}},
        "count": 1,
    }}]}}
    assert parsers._parse_draft_results(raw)[0]["player_key"] is None
    empty = {"fantasy_content": {"league": [{}, {"draft_results": []}]}}
    assert parsers._parse_draft_results(empty) == []
    assert parsers._parse_draft_results({}) == []


def test_flatten_raw_yahoo_player_draft_analysis():
    flat = parsers._flatten_raw_yahoo_player(fx.PLAYER_ENTRY_DRAFT_ANALYSIS, SCORING_PTS)
    assert flat["name"] == "Bijan Robinson"
    assert flat["draft_analysis"] == {
        "average_pick": 2.1, "average_round": 1.0, "average_cost": 70.9,
        "percent_drafted": 1.0, "preseason_average_pick": 2.1,
        "preseason_average_round": 1.0, "preseason_average_cost": 70.9,
        "preseason_percent_drafted": 1.0,
    }


def test_flatten_raw_yahoo_player_draft_analysis_drops_dashes():
    entry = [fx.PLAYER_ENTRY_DRAFT_ANALYSIS[0],
             {"draft_analysis": [{"average_pick": "-"}, {"percent_drafted": "0.00"}]}]
    flat = parsers._flatten_raw_yahoo_player(entry, SCORING_PTS)
    assert flat["draft_analysis"] == {"percent_drafted": 0.0}


# --- team waiver/transaction/draft fields (lg.teams() entries) -------------
# Shaped like the live lg.teams() entries for NFL 1529868 / MLB 60467
# (raw-checked 2026-11-02): ints arrive as ints, roster_adds as a coverage dict.

_TEAM_NFL = {
    "team_key": "470.l.1529868.t.4", "name": "Lincolnshire Poachers",
    "waiver_priority": 7, "number_of_moves": 9, "number_of_trades": 0,
    "roster_adds": {"coverage_type": "week", "coverage_value": 8, "value": "2"},
    "draft_position": 2, "draft_grade": "A+", "has_draft_grade": 1,
}


def test_team_activity_normalizes_live_nfl_entry():
    assert parsers._team_activity(_TEAM_NFL) == {
        "waiver_priority": 7, "number_of_moves": 9, "number_of_trades": 0,
        "draft_position": 2, "draft_grade": "A+",
        "roster_adds": {"week": 8, "count": 2},
    }


def test_team_activity_clinched_flag_and_string_counts():
    t = {"waiver_priority": "5", "number_of_moves": "43", "clinched_playoffs": 1}
    out = parsers._team_activity(t)
    assert out["waiver_priority"] == 5 and out["number_of_moves"] == 43
    assert out["clinched_playoffs"] is True
    assert parsers._team_activity({"clinched_playoffs": "0"})["clinched_playoffs"] is False


def test_team_activity_omits_fields_yahoo_did_not_send():
    # No FAAB / auction / grade in these leagues -> keys absent, not zero.
    out = parsers._team_activity(_TEAM_NFL)
    for k in ("faab_balance", "auction_budget_total", "auction_budget_spent",
              "clinched_playoffs"):
        assert k not in out
    assert parsers._team_activity({}) == {}


def test_team_activity_faab_and_non_weekly_adds():
    out = parsers._team_activity({
        "faab_balance": "87", "auction_budget_total": "200",
        "roster_adds": {"coverage_type": "date", "coverage_value": "2026-11-01",
                        "value": "1"},
    })
    assert out["faab_balance"] == 87 and out["auction_budget_total"] == 200
    assert out["roster_adds"] == {"coverage_type": "date",
                                  "coverage_value": "2026-11-01", "count": 1}


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
