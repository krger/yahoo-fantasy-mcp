# CLAUDE.md

Guidance for Claude Code when working in this repository. Keep this file current — when behavior, structure, or conventions change, update this file in the same commit.

## Project overview

This is a **Model Context Protocol (MCP) server** that exposes Yahoo Fantasy Baseball data and roster operations as tools. It is written in **Python** and serves a **remote MCP endpoint over streamable HTTP** at the `/mcp` path.

- **Runtime host:** the deploy host (Debian VM, part of the a hypervisor stack on the hypervisor)
- **Public endpoint:** `https://fantasy.example.com/mcp`
- **Edge security:** the endpoint sits behind a Cloudflare Tunnel, with Cloudflare Access + Managed OAuth in front. The MCP server itself does **not** implement OAuth — Access handles authentication and only forwards authenticated requests. Do not add an OAuth/auth layer to the server without coordinating; it would conflict with the edge config.
- **Upstream API:** Yahoo Fantasy Sports API (OAuth2). The server holds a Yahoo refresh token and mints access tokens to call Yahoo.

- **Python version:** requires `>=3.10` (`pyproject.toml`). the deploy host (production) runs **3.13.5**; CI pins **3.13** to match it. Dev machines may run newer (e.g. 3.14.5) — avoid relying on syntax/stdlib newer than 3.13.
- **Dependency management:** `uv`

## Key files

The codebase is split across four modules along stable seams — this
modularization is **complete**; respect these boundaries rather than
re-consolidating into one file or splitting further without a clear reason.
Keep each module's concern intact: parsers stay pure (no network/OAuth/MCP),
schemas stay declarative, config stays env-only, and the Yahoo client + tool
wiring stay in `server.py`.

- `server.py` — main server and entrypoint: MCP tool definitions, the Yahoo client, OAuth/token handling, free-agent request-building (`_resolve_sort`, `_fetch_free_agents_raw`), `_format_player`, and the `_handle_error` formatter.
- `config.py` — runtime configuration loaded from environment variables (`load_config()` → a frozen `Config` dataclass). `server.py` calls it once at import (`cfg = load_config()`) so misconfiguration fails loudly at startup. `YAHOO_LEAGUE_ID` is **required** (no default — a fork must set its own); `YAHOO_SPORT` defaults `mlb`; `YAHOO_SEASON` is optional (auto-detects the current season when unset); `YAHOO_OAUTH_FILE` overrides the creds path.
- `yahoo_parsers.py` — the pure Yahoo response parsers/normalizers (no network, no OAuth, no MCP): `_to_int`/`_to_number`, `_flatten_raw_yahoo_player`, `_extract_team_summary`, `_parse_matchup_node`, `_parse_matchup`, `_parse_scoreboard`, `_parse_team_season_stats`, `_rank_season_categories`, `_parse_standings`, and `_resolve_team_key`. Scoring categories are **not** hard-coded: `build_scoring_config()` derives a `ScoringConfig` (labels, scored stat_ids in display order, lower-is-better set) from the league's own `settings` response, and the labeling/ranking parsers take that config as an argument — so the server adapts to any league's categories. This is the unit-test target (the repo's main source of bugs); `server.py` imports from it.
- `schemas.py` — the Pydantic input models (`GetRosterInput`, `SearchFreeAgentsInput`, `GetMatchupInput`, the `TransactionType` enum, etc.): the MCP tools' input contract. FastMCP turns these into the JSON schema advertised to clients, so class/field names are part of the public contract — renaming is a breaking change. `server.py` imports the models it annotates handlers with.
- **Yahoo OAuth credentials** load from `oauth2.json` in the repo root (override the path with the `YAHOO_OAUTH_FILE` env var). The file holds the `consumer_key`/`consumer_secret` plus the access + refresh tokens; `yahoo_oauth.OAuth2` refreshes the access token automatically when expired (`_get_oauth_session`). It is **gitignored and must never be committed.** Other config comes from env vars via `config.py` (see the `config.py` bullet above): `YAHOO_LEAGUE_ID` is required, `YAHOO_SPORT`/`YAHOO_SEASON`/`YAHOO_OAUTH_FILE` are optional.

## Running locally (dev machine, not the deploy host)

Develop on a workstation, never directly against the live deployed copy. The live runtime updates via `git pull` on the deploy host (see Deployment).

```
# from the repo root, with oauth2.json present
YAHOO_LEAGUE_ID=12345 python server.py
```

This launches the streamable-HTTP server (uvicorn) on `0.0.0.0:8000`, serving MCP at `/mcp`. **`YAHOO_LEAGUE_ID` is required** — the server now exits at startup without it (no baked-in default). On the deploy host the same command runs under systemd via the repo's venv (`venv/bin/python server.py`), with `YAHOO_LEAGUE_ID` supplied through the unit's environment (see Deployment). Optionally set `YAHOO_SPORT`, `YAHOO_SEASON`, or `YAHOO_OAUTH_FILE`.

## Testing / verifying changes

**Automated unit tests** cover the parsers — the repo's main source of bugs.
They run offline against faithful fixtures (no network, no credentials):

```
uv run pytest          # or: .venv/bin/python -m pytest
uv run ruff check .     # lint: unused imports, undefined names, import order
uv run ruff check --fix .   # auto-fix the fixable ones
```

Ruff config lives in `pyproject.toml` (`[tool.ruff]`): defaults (pyflakes `F`
+ `E4/E7/E9`) plus `I` for import sorting, targeting py3.13. Keep it green —
CI runs `ruff check` before pytest.

`tests/fixtures.py` holds minimal Yahoo responses reproducing the positional
quirks (including `SETTINGS_RAW` for `build_scoring_config`); `tests/test_parsers.py`
covers `build_scoring_config` (label/scored/lower-is-better derivation + empty
fallback), `_to_int`/`_to_number`, `_extract_team_summary`, `_parse_matchup_node`
(both framings), `_parse_matchup`, `_parse_scoreboard`, `_parse_team_season_stats`,
`_rank_season_categories` (ranking direction + ties), `_parse_standings`,
`_flatten_raw_yahoo_player`, and `_resolve_team_key` — all imported from
`yahoo_parsers` (the test imports that module directly, not `server`). **Add a
case here when you touch a parser** — especially new stat_ids or response shapes.

Automated tests don't hit Yahoo, so there's still no substitute for exercising
the actual tools against the live league for anything API-facing. After a
change:

1. Start the server locally.
2. Call the affected tool(s) and confirm the JSON shape is intact and values are correct.
3. For anything touching rosters or matchups, verify against the Yahoo web UI for league 12345.

## Deployment (pull-on-the deploy host)

Deploys are manual and intentional. CI (`.github/workflows/test.yml`) runs
`ruff check` then the pytest suite on every push to `main` and on PRs, but it
**only lints and tests — it never deploys**. Deployment is always a hand
`git pull` on the deploy host.

```
# on your dev machine
git add -p && git commit && git push

# on the deploy host (repo lives at /srv/yahoo-fantasy-mcp, runs as user krg).
# Connect with the `deploy-host` SSH host alias (in ~/.ssh/config); systemctl
# works without a sudo password prompt.
ssh deploy-host
cd /srv/yahoo-fantasy-mcp
git pull --ff-only
sudo systemctl restart yahoo-fantasy-mcp.service
systemctl is-active yahoo-fantasy-mcp.service   # expect: active
```

The server is a systemd unit, `yahoo-fantasy-mcp.service` (enabled, `ExecStart=.../venv/bin/python server.py`). The public endpoint is fronted by `cloudflared-tunnel.service` (the Cloudflare Tunnel); leave that one alone. Check logs with `journalctl -u yahoo-fantasy-mcp.service -n 50`.

**Required env on the deploy host:** `YAHOO_LEAGUE_ID` has no code default, so the unit must supply it or the service exits at startup. It's set via a systemd drop-in, `/etc/systemd/system/yahoo-fantasy-mcp.service.d/override.conf`:

```
[Service]
Environment=YAHOO_LEAGUE_ID=12345
```

After editing the drop-in run `sudo systemctl daemon-reload` before restarting. `YAHOO_SEASON` is intentionally left unset (the server auto-detects the current season).

Before starting an editing session, make sure the deploy host and the repo are in sync (`git fetch && git status` on the deploy host) so you're editing from the same baseline that's actually running. Hand-edits made on the deploy host without committing will show as uncommitted changes — resolve those first.

## League facts (league 12345)

- **Format:** 10-team, head-to-head **categories** league (`scoring_type: head`), Yahoo game code `mlb`, season 2026.
- **Scoring categories (10):**
  - Hitting: R, HR, RBI, SB, AVG
  - Pitching: W, SV, K, ERA, WHIP
- **Yahoo `stat_id` mapping** for *this* league (the API returns stats keyed by numeric `stat_id`; map them explicitly rather than by position). Note: the server no longer hard-codes these — `build_scoring_config` derives labels, scored set, and ranking direction from the league's `settings` at runtime (see `yahoo_parsers.py`). This table is the expected result for league 12345 and a reference when reading fixtures/tests:
  - `7` = R, `12` = HR, `13` = RBI, `16` = SB, `3` = AVG
  - `28` = W, `32` = SV, `42` = K, `26` = ERA, `27` = WHIP
  - Informational (not scored, `is_only_display_stat`): `50` = IP, `60` = H/AB
- **Owner team:** team **#5**, "[team]."
- **Team-number map** (1–10): 1=[team], 2=[team], 3=[team], 4=[team], 5=[team], 6=[team], 7=[team], 8=[team], 9=[team], 10=[team].
  - **Convention:** treat this map as a hint, not a source of truth. Resolve team identity at runtime via `yahoo_list_teams` rather than hardcoding numbers — Yahoo team numbering is league-specific and the server should not assume it.

## Tools exposed

Keep tool names and input schemas stable — they are the server's public contract with MCP clients. Renaming a tool or changing a parameter is a breaking change.

- `yahoo_list_teams` — list all teams (numbers, keys, managers)
- `yahoo_get_standings` — league standings. Returns `standings` as a normalized list (via `_parse_standings`): each team has numeric `rank`, `playoff_seed`, a structured `record` (`wins`/`losses`/`ties`/`pct`), `games_back` (`null` for the leader), and a `categories` list of season totals for the 10 scoring categories, each with the team's league `rank` in that category (ERA/WHIP ranked low-first). The standings feed itself has no category stats; they come from a separate `league/{key}/teams/stats` call that degrades gracefully (standings still return without `categories` if it fails).
- `yahoo_get_scoreboard` — all matchups for a week. Returns `matchups` as a list of parsed breakdowns (same core parser as `yahoo_get_matchup`, neutral framing): each has matchup meta, a `teams` list (`name`, `team_key`, `category_points`), and a `categories` list where each entry carries per-stat `values` keyed by `team_key`, the winning `team_key` (or `"tie"`/`null` for informational stats), and a `scored` flag. `week` is resolved to the numeric current week when omitted.
- `yahoo_get_matchup` — one team's H2H matchup detail. Returns a structured `matchup` object (not just the opponent key): matchup meta (`week`, `week_start`/`week_end`, `status`, `is_playoffs`), both teams (`team`/`opponent` with `name`, `team_key`, `category_points`), and a `categories` list giving each side's value per stat plus `result` (`win`/`loss`/`tie`, from Yahoo's `stat_winner`) and a `scored` flag (informational stats like H/AB and IP are `scored: false`). Parsed by `_parse_matchup` from the raw response, since yfa's `Team.matchup()` only yields the opponent's key.
- `yahoo_get_roster` — a team's roster (supports `day` / `week`)
- `yahoo_get_league_settings` — config, rules, scoring
- `yahoo_get_transactions` — recent transaction history
- `yahoo_search_free_agents` — available free agents. Each player includes a `stats` map of season totals per scoring category (labeled via `_STAT_ID_TO_NAME`, numbers coerced; H/AB kept as a ratio string), fetched inline via `;out=percent_owned,stats` and parsed in `_flatten_raw_yahoo_player`. Hitters get R/HR/RBI/SB/AVG, pitchers W/SV/K/ERA/WHIP.
- `yahoo_get_player_stats` — single player lookup
- `yahoo_get_players_batch` — multiple players in one call
- `yahoo_get_player_notes` — news / injury notes
- `yahoo_get_player_ownership` — who owns a player

## Yahoo API gotchas

The Yahoo Fantasy API response format is the main source of bugs in this repo. Be defensive:

- Responses are **deeply nested and positional** — arrays interleave data objects with empty `[]` placeholders, and collections use **numeric string keys** (`"0"`, `"1"`, ...) plus a `count`. Never assume a fixed index; locate data by key/shape, not by position.
- Stats arrive as `{stat: {stat_id, value}}` lists — map by `stat_id` (see table above), never by order.
- Some endpoints require parameters that look optional. **Default them explicitly in the handler** rather than relying on the upstream call to fill them in.

## Known issues / fragile areas

- Historically regression-prone handlers (verify these still work after changes): transactions handler, free-agent search **sort** parameter, player-notes endpoint, and **roster team-number resolution**.
- **`yahoo_search_free_agents` with `sort=PTS` returns zero results** — this is a head-to-head **categories** league, so Yahoo computes no fantasy-points ranking. Expected behavior, not a bug; the tool description documents it and steers callers to AR or a stat/category sort. Don't "fix" it.

## Conventions & etiquette

- **Make surgical, reviewable diffs.** Do not rewrite `server.py` wholesale; edit the specific functions that need changing.
- **Atomic commits** with clear messages — one logical change per commit.
- **Preserve tool contracts.** Don't rename tools or change parameter names/types without explicit instruction; downstream MCP clients depend on them.
- **No secrets in the repo.** Yahoo tokens, client IDs/secrets, and any Cloudflare credentials stay out of version control.
- **Match existing style.** Follow the patterns already in the file (naming, error handling, response parsing helpers) rather than introducing new ones.
