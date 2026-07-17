"""Unit tests for config.load_config() — env-driven configuration.

Pure (no network); each test isolates the environment with monkeypatch.
"""

import pytest

import config


def test_requires_league_id(monkeypatch):
    monkeypatch.delenv("YAHOO_LEAGUE_ID", raising=False)
    with pytest.raises(RuntimeError):
        config.load_config()


def test_defaults(monkeypatch):
    monkeypatch.setenv("YAHOO_LEAGUE_ID", "12345")
    for k in ("YAHOO_SPORT", "YAHOO_SEASON", "YAHOO_OAUTH_FILE"):
        monkeypatch.delenv(k, raising=False)
    cfg = config.load_config()
    assert cfg.league_id == "12345"
    assert cfg.sports == ("mlb",)
    assert cfg.default_sport == "mlb"
    assert cfg.season is None              # unset -> auto-detect at runtime
    assert cfg.oauth_file.endswith("oauth2.json")


def test_overrides(monkeypatch):
    monkeypatch.setenv("YAHOO_LEAGUE_ID", "999")
    monkeypatch.setenv("YAHOO_SPORT", "nfl")
    monkeypatch.setenv("YAHOO_SEASON", "2025")
    monkeypatch.setenv("YAHOO_OAUTH_FILE", "/tmp/creds.json")
    cfg = config.load_config()
    assert (cfg.sports, cfg.oauth_file) == (("nfl",), "/tmp/creds.json")
    assert cfg.default_sport == "nfl"
    assert cfg.season == 2025 and isinstance(cfg.season, int)


def test_multi_sport_list(monkeypatch):
    # YAHOO_SPORT accepts a comma-separated list; order is preserved and the
    # first entry is the default sport. Blanks/whitespace are trimmed, and the
    # codes are lowercased.
    monkeypatch.setenv("YAHOO_LEAGUE_ID", "60467")
    monkeypatch.setenv("YAHOO_SPORT", " MLB , nfl ,")
    cfg = config.load_config()
    assert cfg.sports == ("mlb", "nfl")
    assert cfg.default_sport == "mlb"


def test_empty_sport_falls_back_to_mlb(monkeypatch):
    # An empty/whitespace-only YAHOO_SPORT collapses to the single default.
    monkeypatch.setenv("YAHOO_LEAGUE_ID", "60467")
    monkeypatch.setenv("YAHOO_SPORT", " , ")
    cfg = config.load_config()
    assert cfg.sports == ("mlb",)


def test_bad_season_is_rejected(monkeypatch):
    monkeypatch.setenv("YAHOO_LEAGUE_ID", "12345")
    monkeypatch.setenv("YAHOO_SEASON", "not-a-year")
    with pytest.raises(RuntimeError):
        config.load_config()
