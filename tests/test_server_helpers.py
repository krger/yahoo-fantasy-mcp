"""Unit tests for pure helpers in server.py: the free-agent sort resolver
(a historically regression-prone spot) and the error formatter.

`conftest.py` sets a dummy ``YAHOO_LEAGUE_ID`` so importing ``server`` (which
calls ``load_config()`` at import) works without real config. No network
happens at import — the Yahoo session/league are built lazily in the handlers.
"""

import server

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
