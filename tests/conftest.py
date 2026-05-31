"""Pytest setup shared across the test suite.

`server.py` calls `load_config()` at import, which requires `YAHOO_LEAGUE_ID`.
Set a dummy here — conftest loads before any test module — so tests can import
`server` without real configuration. No network happens at import; the Yahoo
session and league are built lazily inside the handlers.
"""

import os

os.environ.setdefault("YAHOO_LEAGUE_ID", "12345")
