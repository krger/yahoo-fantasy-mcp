"""Validation tests for the MCP input schemas (the public tool contract)."""

import pytest
from pydantic import ValidationError

import schemas


def test_free_agents_time_period_accepts_valid_windows():
    for w in ("season", "lastweek", "lastmonth", "biweekly"):
        assert schemas.SearchFreeAgentsInput(time_period=w).time_period == w
    assert schemas.SearchFreeAgentsInput().time_period is None  # default


def test_free_agents_time_period_rejects_unknown():
    with pytest.raises(ValidationError):
        schemas.SearchFreeAgentsInput(time_period="yesterday")


def test_roster_include_stats_defaults_false():
    assert schemas.GetRosterInput().include_stats is False
    assert schemas.GetRosterInput(include_stats=True).include_stats is True
