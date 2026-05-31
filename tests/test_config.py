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
    assert cfg.sport == "mlb"
    assert cfg.season is None              # unset -> auto-detect at runtime
    assert cfg.oauth_file.endswith("oauth2.json")


def test_overrides(monkeypatch):
    monkeypatch.setenv("YAHOO_LEAGUE_ID", "999")
    monkeypatch.setenv("YAHOO_SPORT", "nfl")
    monkeypatch.setenv("YAHOO_SEASON", "2025")
    monkeypatch.setenv("YAHOO_OAUTH_FILE", "/tmp/creds.json")
    cfg = config.load_config()
    assert (cfg.sport, cfg.oauth_file) == ("nfl", "/tmp/creds.json")
    assert cfg.season == 2025 and isinstance(cfg.season, int)


def test_bad_season_is_rejected(monkeypatch):
    monkeypatch.setenv("YAHOO_LEAGUE_ID", "12345")
    monkeypatch.setenv("YAHOO_SEASON", "not-a-year")
    with pytest.raises(RuntimeError):
        config.load_config()
