# CLAUDE.md

Guidance for Claude Code when working in this repository. Keep this file current — when behavior, structure, or conventions change, update this file in the same commit.

> Maintainer note: environment-specific deployment details and league facts live in a local, gitignored `CLAUDE.local.md` (auto-loaded alongside this file), not in the public repo.

## Project overview

This is a **Model Context Protocol (MCP) server** that exposes Yahoo Fantasy Baseball data and roster operations as tools. It is written in **Python** and serves a **remote MCP endpoint over streamable HTTP** at the `/mcp` path.

- **Upstream API:** Yahoo Fantasy Sports API (OAuth2). The server holds a Yahoo refresh token and mints access tokens to call Yahoo.
- **Python version:** requires `>=3.10` (`pyproject.toml`); CI pins **3.13**. Avoid relying on syntax/stdlib newer than 3.13.
- **Dependency management:** `uv`

## Key files

The codebase is split across four modules along stable seams — this
modularization is **complete**; respect these boundaries rather than
re-consolidating into one file or splitting further without a clear reason.
Keep each module's concern intact: parsers stay pure (no network/OAuth/MCP),
schemas stay declarative, config stays env-only, and the Yahoo client + tool
wiring stay in `server.py`.

- `server.py` — main server and entrypoint: MCP tool definitions, the Yahoo client, OAuth/token handling, free-agent request-building (`_resolve_sort`, `_fetch_free_agents_raw`), `_format_player`, and the `_handle_error` formatter.
- `config.py` — runtime configuration loaded from environment variables (`load_config()` → a frozen `Config` dataclass). `server.py` calls it once at import (`cfg = load_config()`) so misconfiguration fails loudly at startup. `YAHOO_LEAGUE_ID` is **required** (no default — a fork must set its own) and serves as the **default** league; tools also accept a per-call `league_id` override (see below). `YAHOO_SPORT` defaults `mlb`; `YAHOO_SEASON` is optional (auto-detects the current season when unset); `YAHOO_OAUTH_FILE` overrides the creds path. `MCP_ALLOWED_HOSTS` is optional (comma-separated): extra `Host` header values the streamable-HTTP transport's DNS-rebinding protection should accept, for serving behind a reverse proxy/tunnel that forwards a non-loopback `Host`. Loopback hosts are always allowed; unset keeps stock loopback-only behavior. The value is deployment-specific, so it stays in the environment, not the repo (the public hostname never enters version control). **Gotcha:** the built-in loopback allowlist only matches `host:port` forms (`localhost:*`, `127.0.0.1:*`), so a proxy/tunnel that forwards a *bare* host with no port — commonly `Host: localhost` — is **not** covered and must be listed explicitly (add `localhost` to `MCP_ALLOWED_HOSTS`). The symptom of a missing entry is `421` with `Invalid Host header: <value>` in the logs; add exactly that value.
- `yahoo_parsers.py` — the pure Yahoo response parsers/normalizers (no network, no OAuth, no MCP): `_to_int`/`_to_number`, `_flatten_raw_yahoo_player`, `_extract_team_summary`, `_parse_matchup_node`, `_parse_matchup`, `_parse_scoreboard`, `_parse_team_season_stats`, `_rank_season_categories`, `_parse_standings`, `_resolve_team_key`, and `_parse_my_leagues` (flattens the `users/games/leagues` response into the account's `{league_id, league_key, name, season, game_code}` list). Scoring categories are **not** hard-coded: `build_scoring_config()` derives a `ScoringConfig` (labels, scored stat_ids in display order, lower-is-better set) from the league's own `settings` response, and the labeling/ranking parsers take that config as an argument — so the server adapts to any league's categories. This is the unit-test target (the repo's main source of bugs); `server.py` imports from it.
- `schemas.py` — the Pydantic input models (`GetRosterInput`, `SearchFreeAgentsInput`, `GetMatchupInput`, the `TransactionType` enum, etc.): the MCP tools' input contract. They share a `LeagueScopedInput` base carrying the optional `league_id` override, so every league-scoped tool advertises it identically (the otherwise-argument-less tools — standings, settings, list-teams — get `GetStandingsInput`/`GetLeagueSettingsInput`/`ListTeamsInput`, which add nothing but that field). FastMCP turns these into the JSON schema advertised to clients, so class/field names are part of the public contract — renaming is a breaking change. `server.py` imports the models it annotates handlers with.
- **Yahoo OAuth credentials** load from `oauth2.json` in the repo root (override the path with the `YAHOO_OAUTH_FILE` env var). The file holds the `consumer_key`/`consumer_secret` plus the access + refresh tokens; `yahoo_oauth.OAuth2` refreshes the access token automatically when expired (`_get_oauth_session`). It is **gitignored and must never be committed.** Other config comes from env vars via `config.py`: `YAHOO_LEAGUE_ID` is required, `YAHOO_SPORT`/`YAHOO_SEASON`/`YAHOO_OAUTH_FILE` are optional.

## Running locally

```
# from the repo root, with oauth2.json present
YAHOO_LEAGUE_ID=<your-league-id> python server.py
```

This launches the streamable-HTTP server (uvicorn) on `0.0.0.0:8000`, serving MCP at `/mcp`. **`YAHOO_LEAGUE_ID` is required** — the server exits at startup without it (no baked-in default). Optionally set `YAHOO_SPORT`, `YAHOO_SEASON`, or `YAHOO_OAUTH_FILE`. The server does no authentication of its own; for non-local use, front it with a TLS-terminating reverse proxy or tunnel that enforces access control.

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
quirks (including `SETTINGS_RAW` for `build_scoring_config` and `MY_LEAGUES_RAW`,
a multi-game/multi-league `users/games/leagues` response for `_parse_my_leagues`);
`tests/test_parsers.py` covers `build_scoring_config` (label/scored/lower-is-better
derivation + empty fallback), `_to_int`/`_to_number`, `_extract_team_summary`,
`_parse_matchup_node` (both framings), `_parse_matchup`, `_parse_scoreboard`,
`_parse_team_season_stats`, `_rank_season_categories` (ranking direction + ties),
`_parse_standings`, `_flatten_raw_yahoo_player`, `_resolve_team_key`, and
`_parse_my_leagues` (multi-league walk, empty fallback, dict-vs-list league node)
— all imported from `yahoo_parsers` (the test imports that module directly, not
`server`). **Add a case here when you touch a parser** — especially new stat_ids
or response shapes.

Automated tests don't hit Yahoo, so there's still no substitute for exercising
the actual tools against the live league for anything API-facing. After a
change:

1. Start the server locally.
2. Call the affected tool(s) and confirm the JSON shape is intact and values are correct.
3. For anything touching rosters or matchups, verify against the Yahoo web UI for your league.

## Deployment

Deploys are manual and intentional — CI (`.github/workflows/test.yml`) **only lints and tests; it never deploys.** The server is a long-running streamable-HTTP process (`python server.py`); run it under a process manager (e.g. systemd) behind a TLS-terminating proxy/tunnel. The maintainer's environment-specific runbook is in `CLAUDE.local.md` (gitignored).

**Multi-league:** `YAHOO_LEAGUE_ID` sets only the *default* league. Targeting another league the authenticated account already belongs to needs **no redeploy or config change** — clients pass a per-call `league_id` (see "Tools exposed"), validated against the account's own leagues. Only changing the *default* requires updating the env var (and a restart).

## Tools exposed

Keep tool names and input schemas stable — they are the server's public contract with MCP clients. Renaming a tool or changing a parameter is a breaking change.

**Multi-league:** every league-scoped tool takes an optional `league_id` (the bare numeric id) to target a league other than the configured default (`cfg.league_id`). `_get_league(sc, league_id)` resolves it and validates any explicit override against the account's own leagues (`_get_my_leagues` → `_parse_my_leagues`, cached per process), raising a clear error for a league the token can't see; if discovery is unavailable it degrades permissive rather than blocking. Response payloads echo the *resolved* league via `_resolved_league_id(lg)`, and the scoring-config cache (`_scoring_configs`) is keyed by `league_key` so leagues don't clobber each other's category labels.

- `yahoo_list_my_leagues` — the leagues the authenticated account belongs to (`league_id`, `name`, `season`, `is_default`), plus `default_league_id`. Use it to discover the ids accepted by the other tools' `league_id` parameter.
- `yahoo_list_teams` — list all teams (numbers, keys, managers)
- `yahoo_get_standings` — league standings. Returns `standings` as a normalized list (via `_parse_standings`): each team has numeric `rank`, `playoff_seed`, a structured `record` (`wins`/`losses`/`ties`/`pct`), `games_back` (`null` for the leader), and a `categories` list of season totals for the scoring categories, each with the team's league `rank` in that category (rate stats like ERA/WHIP ranked low-first). The standings feed itself has no category stats; they come from a separate `league/{key}/teams/stats` call that degrades gracefully (standings still return without `categories` if it fails).
- `yahoo_get_scoreboard` — all matchups for a week. Returns `matchups` as a list of parsed breakdowns (same core parser as `yahoo_get_matchup`, neutral framing): each has matchup meta, a `teams` list (`name`, `team_key`, `category_points`), and a `categories` list where each entry carries per-stat `values` keyed by `team_key`, the winning `team_key` (or `"tie"`/`null` for informational stats), and a `scored` flag. `week` is resolved to the numeric current week when omitted.
- `yahoo_get_matchup` — one team's H2H matchup detail. Returns a structured `matchup` object (not just the opponent key): matchup meta (`week`, `week_start`/`week_end`, `status`, `is_playoffs`), both teams (`team`/`opponent` with `name`, `team_key`, `category_points`), and a `categories` list giving each side's value per stat plus `result` (`win`/`loss`/`tie`, from Yahoo's `stat_winner`) and a `scored` flag (informational stats like H/AB and IP are `scored: false`). Parsed by `_parse_matchup` from the raw response, since yfa's `Team.matchup()` only yields the opponent's key.
- `yahoo_get_roster` — a team's roster (supports `day` / `week`; `include_stats=true` enriches each player with season category totals via one batched `players;player_keys=…;out=stats` call, parsed by `_flatten_raw_yahoo_player` and merged by player_id)
- `yahoo_get_league_settings` — config, rules, scoring
- `yahoo_get_transactions` — recent transaction history
- `yahoo_search_free_agents` — available free agents. Each player includes a `stats` map of season totals per scoring category (labeled via the league-derived `ScoringConfig`, numbers coerced; H/AB kept as a ratio string), fetched inline via `;out=percent_owned,stats` and parsed in `_flatten_raw_yahoo_player`. `time_period` (`season`/`lastweek`/`lastmonth`/`biweekly`) sets the Yahoo `sort_type` for stat-based sorts — used to surface recent-form pickups; only `season` sends `sort_season`. The displayed `stats` remain season totals; the window only controls ranking order.
- `yahoo_get_player_stats` — single player lookup
- `yahoo_get_players_batch` — multiple players in one call
- `yahoo_get_player_notes` — news / injury notes
- `yahoo_get_player_ownership` — who owns a player

## Prompts

`@mcp.prompt` templates (in `server.py`, after the tools) that orchestrate the tools for common multi-step questions: `analyze_matchup`, `waiver_help`, `weekly_recap`. **Design rule:** the orchestration/strategy lives in the prompt text (it tells Claude which tools to chain and how to reason), keeping the tools themselves a thin read-only data layer. Put new "do X for me" workflows here as prompts rather than baking strategy into tool handlers. `waiver_help` leans on `yahoo_search_free_agents`' `time_period=lastweek` for recent form.

## Yahoo API gotchas

The Yahoo Fantasy API response format is the main source of bugs in this repo. Be defensive:

- Responses are **deeply nested and positional** — arrays interleave data objects with empty `[]` placeholders, and collections use **numeric string keys** (`"0"`, `"1"`, ...) plus a `count`. Never assume a fixed index; locate data by key/shape, not by position.
- Stats arrive as `{stat: {stat_id, value}}` lists — map by `stat_id` (labels/scoring come from the league-derived `ScoringConfig`), never by order.
- Some endpoints require parameters that look optional. **Default them explicitly in the handler** rather than relying on the upstream call to fill them in.

## Known issues / fragile areas

- Historically regression-prone handlers (verify these still work after changes): transactions handler, free-agent search **sort** parameter, player-notes endpoint, and **roster team-number resolution**.
- **`yahoo_search_free_agents` with `sort=PTS` returns zero results** in a head-to-head **categories** league — Yahoo computes no fantasy-points ranking there. Expected behavior, not a bug; the tool description documents it and steers callers to AR or a stat/category sort. Don't "fix" it.

## Conventions & etiquette

- **Make surgical, reviewable diffs.** Do not rewrite `server.py` wholesale; edit the specific functions that need changing.
- **Atomic commits** with clear messages — one logical change per commit.
- **Preserve tool contracts.** Don't rename tools or change parameter names/types without explicit instruction; downstream MCP clients depend on them.
- **No secrets in the repo.** Yahoo tokens, client IDs/secrets, and any edge/proxy credentials stay out of version control.
- **Match existing style.** Follow the patterns already in the file (naming, error handling, response parsing helpers) rather than introducing new ones.
