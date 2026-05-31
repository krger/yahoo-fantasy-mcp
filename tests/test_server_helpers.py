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


# --- error formatting ------------------------------------------------------

def test_handle_error_classifies_auth():
    assert "Authentication failed" in server._handle_error(Exception("401 Unauthorized"))


def test_handle_error_classifies_not_found():
    assert "not found" in server._handle_error(Exception("404 not found")).lower()


def test_handle_error_generic_includes_type_and_message():
    msg = server._handle_error(ValueError("boom"))
    assert "ValueError" in msg and "boom" in msg
