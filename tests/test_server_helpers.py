"""Unit tests for pure helpers in server.py: the free-agent sort resolver
(a historically regression-prone spot) and the error formatter.

`conftest.py` sets a dummy ``YAHOO_LEAGUE_ID`` so importing ``server`` (which
calls ``load_config()`` at import) works without real config. No network
happens at import — the Yahoo session/league are built lazily in the handlers.
"""

import pytest

import server

# --- league resolution (multi-sport, discovery-driven) --------------------
#
# _get_league prefers a discovered league_key (which already encodes the right
# game/season) so one server resolves MLB and NFL leagues alike, and validates
# an explicit override against the account's own leagues. These fakes stand in
# for yfa.Game so the resolution logic is exercised without any network.


class _FakeGame:
    """Minimal stand-in for yfa.Game: records the game code it was built with
    and hands back a fake League from to_league(); the fallback path uses
    league_ids()/game_id()."""

    def __init__(self, sc, code):
        self.code = code

    def to_league(self, league_key):
        lg = type("_FakeLeague", (), {})()
        lg._from_game_code = self.code  # so tests can assert the resolving game
        return lg

    def league_ids(self, year=None):
        return []

    def game_id(self):
        return "999"


_MINE = [
    {"league_id": "12345", "league_key": "458.l.12345", "name": "MLB", "game_code": "mlb"},
    {"league_id": "70000", "league_key": "470.l.70000", "name": "NFL", "game_code": "nfl"},
]


def test_get_league_prefers_discovered_key(monkeypatch):
    # An NFL override resolves via the discovered league_key + its game_code —
    # no per-call sport argument, no default-sport assumption.
    monkeypatch.setattr(server, "_get_my_leagues", lambda sc: _MINE)
    monkeypatch.setattr(server.yfa, "Game", _FakeGame)
    lg = server._get_league(None, "70000")
    assert lg.league_key == "470.l.70000"
    assert lg._from_game_code == "nfl"


def test_get_league_rejects_unknown_override(monkeypatch):
    # An explicit override outside the account's leagues is rejected.
    monkeypatch.setattr(server, "_get_my_leagues", lambda sc: _MINE)
    monkeypatch.setattr(server.yfa, "Game", _FakeGame)
    with pytest.raises(ValueError):
        server._get_league(None, "99999")


def test_get_league_falls_back_when_discovery_empty(monkeypatch):
    # Discovery unavailable ([]): construct the key from the default sport's
    # current game id rather than blocking (single-sport fallback). cfg.league_id
    # is the conftest dummy "12345".
    monkeypatch.setattr(server, "_get_my_leagues", lambda sc: [])
    monkeypatch.setattr(server.yfa, "Game", _FakeGame)
    lg = server._get_league(None, None)
    assert lg.league_key == "999.l.12345"
    assert lg._from_game_code == "mlb"


# --- free-agent sort resolution -------------------------------------------

def test_resolve_sort_named_passthrough():
    # Named sorts Yahoo accepts verbatim; not stat ids.
    assert server._resolve_sort("AR") == ("AR", False)
    assert server._resolve_sort("ar") == ("AR", False)   # case-insensitive
    assert server._resolve_sort("PTS") == ("PTS", False)


def test_resolve_sort_stat_name_to_id():
    assert server._resolve_sort("HR") == ("12", True)
    assert server._resolve_sort("era") == ("26", True)    # case-insensitive -> ERA


def test_resolve_sort_numeric_stat_id_passthrough():
    assert server._resolve_sort("7") == ("7", True)


def test_resolve_sort_empty_is_none():
    assert server._resolve_sort(None) == (None, False)
    assert server._resolve_sort("") == (None, False)


def test_resolve_sort_unknown_passes_through_uppercased():
    # Unknown keys are passed to Yahoo as-is (uppercased), not treated as stat ids.
    assert server._resolve_sort("bogus") == ("BOGUS", False)


def test_resolve_sort_strips_surrounding_whitespace():
    # key = sort_key.strip().upper() — padding must not defeat the lookup.
    assert server._resolve_sort("  hr  ") == ("12", True)
    assert server._resolve_sort(" ar ") == ("AR", False)


def test_resolve_sort_digit_leading_stat_name_not_treated_as_id():
    # "2B"/"3B" start with a digit but isdigit() is False, so they must resolve
    # via the stat-name table, not be passed through as a numeric stat id.
    assert server._resolve_sort("2B") == ("10", True)
    assert server._resolve_sort("3b") == ("11", True)


def test_resolve_sort_aliases_collapse_to_same_id():
    # K and SO are both strikeouts -> stat id 21.
    assert server._resolve_sort("K") == ("21", True)
    assert server._resolve_sort("SO") == ("21", True)


def test_resolve_sort_underscore_named_sort():
    # O_AR (opponent acquisition rank) is a verbatim named sort, not a stat id.
    assert server._resolve_sort("O_AR") == ("O_AR", False)
    assert server._resolve_sort("o_ar") == ("O_AR", False)


# --- error formatting ------------------------------------------------------

def test_handle_error_classifies_auth():
    assert "Authentication failed" in server._handle_error(Exception("401 Unauthorized"))


def test_handle_error_classifies_not_found():
    assert "not found" in server._handle_error(Exception("404 not found")).lower()


def test_handle_error_generic_includes_type_and_message():
    msg = server._handle_error(ValueError("boom"))
    assert "ValueError" in msg and "boom" in msg
