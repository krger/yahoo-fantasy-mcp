# CLAUDE.md

Guidance for Claude Code when working in this repository. Keep this file
current — when behavior, structure, or conventions change, update this file in
the same commit.

> Maintainer note: environment-specific deployment details and league facts
> live in a local, gitignored `CLAUDE.local.md` (auto-loaded alongside this
> file), not in the public repo.

## Project overview

This is a **Model Context Protocol (MCP) server** that exposes Yahoo Fantasy
Sports data as read-only tools — **baseball and football** (the design is
sport-agnostic; other Yahoo games follow the same paths). It is written in
**Python** and serves a **remote MCP endpoint over streamable HTTP** at the
`/mcp` path. Sport-specific framing (head-to-head **categories** vs **points**
scoring) is derived from each league's own settings at runtime, not hard-coded —
see `build_scoring_config` / `is_points_league` below.

- **Upstream API:** Yahoo Fantasy Sports API (OAuth2). The server holds a Yahoo refresh token and mints access tokens to call Yahoo.
- **Python version:** requires `>=3.13` (`pyproject.toml`), matching CI and the deploy host; avoid relying on syntax/stdlib newer than 3.13.
- **Dependency management:** `uv`

## Key files

The codebase is split across four modules along stable seams — this
modularization is **complete**; respect these boundaries rather than
re-consolidating into one file or splitting further without a clear reason.
Keep each module's concern intact: parsers stay pure (no network/OAuth/MCP),
schemas stay declarative, config stays env-only, and the Yahoo client + tool
wiring stay in `server.py`.

- `server.py` — main server and entrypoint: MCP tool definitions, the Yahoo client, OAuth/token handling, free-agent request-building (`_resolve_sort`, `_fetch_free_agents_raw`), `_format_player`, and the `_handle_error` formatter.
- `config.py` — runtime configuration loaded from environment variables (`load_config()` → a frozen `Config` dataclass). `server.py` calls it once at import (`cfg = load_config()`) so misconfiguration fails loudly at startup. `YAHOO_LEAGUE_ID` is **required** (no default — a fork must set its own) and serves as the **default** league; tools also accept a per-call `league_id` override (see below). `YAHOO_SPORT` is a comma-separated list of game codes → `cfg.sports` (a tuple; default `("mlb",)`), with a `default_sport` property (the first entry, used to construct a league key when discovery is unavailable and for the `yfa.Game` game-id lookup). Set it to e.g. `mlb,nfl` to serve both sports from one deployment. `YAHOO_SEASON` is optional (auto-detects the current season when unset); `YAHOO_OAUTH_FILE` overrides the creds path. `MCP_ALLOWED_HOSTS` is optional (comma-separated): extra `Host` header values the streamable-HTTP transport's DNS-rebinding protection should accept, for serving behind a reverse proxy/tunnel that forwards a non-loopback `Host`. Loopback hosts are always allowed; unset keeps stock loopback-only behavior. The value is deployment-specific, so it stays in the environment, not the repo (the public hostname never enters version control). **Gotcha:** the built-in loopback allowlist only matches `host:port` forms (`localhost:*`, `127.0.0.1:*`), so a proxy/tunnel that forwards a *bare* host with no port — commonly `Host: localhost` — is **not** covered and must be listed explicitly (add `localhost` to `MCP_ALLOWED_HOSTS`). The symptom of a missing entry is `421` with `Invalid Host header: <value>` in the logs; add exactly that value.
- `yahoo_parsers.py` — the pure Yahoo response parsers/normalizers (no network, no OAuth, no MCP): `_to_int`/`_to_number`, `_flatten_raw_yahoo_player`, `_extract_team_summary`, `_parse_matchup_node`, `_points_matchup_node`, `_parse_matchup`, `_parse_scoreboard`, `_parse_team_season_stats`, `_rank_season_categories`, `_parse_standings`, `_resolve_team_key`, and `_parse_my_leagues` (flattens the `users/games/leagues` response into the account's `{league_id, league_key, name, season, game_code}` list). Scoring is **not** hard-coded: `build_scoring_config()` derives a `ScoringConfig` (labels, scored stat_ids in display order, lower-is-better set, **and an `is_points_league` flag**) from the league's own `settings` response, and the labeling/ranking parsers take that config as an argument — so the server adapts to any league's categories **and to its scoring model**. `is_points_league` is detected from a `stat_modifiers` block (points leagues price each stat; a categories league has none) or `scoring_type == "point"`; when true, `_parse_matchup_node` delegates to `_points_matchup_node` (winner from the matchup-level `winner_team_key`/`is_tied`, each team's fantasy total as `points`, stat lines as informational `stat_lines`) instead of the per-category win/loss framing, and standings skip category ranking (`_parse_standings` surfaces `points_for`/`points_against` instead). This is the unit-test target (the repo's main source of bugs); `server.py` imports from it.
- `schemas.py` — the Pydantic input models (`GetRosterInput`, `SearchFreeAgentsInput`, `GetMatchupInput`, the `TransactionType` enum, etc.): the MCP tools' input contract. They share a `LeagueScopedInput` base carrying the optional `league_id` override, so every league-scoped tool advertises it identically (the otherwise-argument-less tools — standings, settings, list-teams — get `GetStandingsInput`/`GetLeagueSettingsInput`/`ListTeamsInput`, which add nothing but that field). FastMCP turns these into the JSON schema advertised to clients, so class/field names are part of the public contract — renaming is a breaking change. `server.py` imports the models it annotates handlers with.
- **Yahoo OAuth credentials** load from `oauth2.json` in the repo root (override the path with the `YAHOO_OAUTH_FILE` env var). The file holds the `consumer_key`/`consumer_secret` plus the access + refresh tokens; `yahoo_oauth.OAuth2` refreshes the access token automatically when expired (`_get_oauth_session`). It is **gitignored and must never be committed.** Other config comes from env vars via `config.py`: `YAHOO_LEAGUE_ID` is required, `YAHOO_SPORT`/`YAHOO_SEASON`/`YAHOO_OAUTH_FILE` are optional.

## Running locally

```
# from the repo root, with oauth2.json present
YAHOO_LEAGUE_ID=<your-league-id> python server.py
```

This launches the streamable-HTTP server (uvicorn) on `127.0.0.1:8000`
(loopback only), serving MCP at `/mcp`. **`YAHOO_LEAGUE_ID` is required** — the
server exits at startup without it (no baked-in default). Optionally set
`YAHOO_SPORT` (a single game code or a comma-separated list like `mlb,nfl` to
serve multiple sports), `YAHOO_SEASON`, or `YAHOO_OAUTH_FILE`. The server does
no authentication of its own; for non-local use, front it with a TLS-terminating
reverse proxy or tunnel that enforces access control.

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
CI runs `ruff check`, then pytest, then a `pip-audit` dependency scan.

The audit step exports the locked **production** tree (`uv export --no-dev
--no-emit-project`) and runs `pip-audit` on it, so a published advisory against
a pinned dependency **fails the build** rather than waiting on a Dependabot
bump. It complements Dependabot (which bumps versions) with active CVE scanning
at PR/push time. Reproduce it locally with:

```
uv export --frozen --no-dev --no-emit-project --no-hashes --format requirements-txt -o requirements-audit.txt
uvx pip-audit -r requirements-audit.txt
```

`tests/fixtures.py` holds minimal Yahoo responses reproducing the positional
quirks (including `SETTINGS_RAW` for `build_scoring_config`, `MY_LEAGUES_RAW`,
a multi-game/multi-league `users/games/leagues` response for `_parse_my_leagues`,
`PLAYER_ENTRY_OWNED`, a taken-player entry carrying an `ownership` sub-resource,
and a **points-league (fantasy football)** set — `SETTINGS_RAW_POINTS` (a
`stat_modifiers` block, real NFL stat_ids), `MATCHUP_NODE_POINTS` /
`MATCHUP_RAW_POINTS` / `SCOREBOARD_RAW_POINTS`, and `STANDINGS_LIST_POINTS`;
these are **assembled on-spec** ahead of a live NFL league and flagged for
re-verification once one drafts);
`tests/test_parsers.py` covers `build_scoring_config` (label/scored/lower-is-better
derivation, empty fallback, **and `is_points_league` detection** via both
`stat_modifiers` and `scoring_type`), `_to_int`/`_to_number`,
`_extract_team_summary`, `_parse_matchup_node` (both framings, **categories and
points**), `_parse_matchup`, `_parse_scoreboard`, `_parse_team_season_stats`,
`_rank_season_categories` (ranking direction + ties), `_parse_standings`
(categories merge **and points_for/points_against surfacing**),
`_flatten_raw_yahoo_player` (incl. owner-team extraction for taken players),
`_resolve_team_key`, and `_parse_my_leagues` (multi-league walk, empty fallback,
dict-vs-list league node) — all imported from `yahoo_parsers` (the test imports
that module directly, not `server`). `tests/test_server_helpers.py` covers the
`server.py` helpers: `_resolve_sort` (incl. the sport-neutral league-label
fallback), `_get_league` resolution (discovery-preferred + degraded fallback,
mocked), and `_format_player`'s `pro_team` output. **Add a case here when you
touch a parser** — especially new stat_ids, response shapes, or scoring models.

Automated tests don't hit Yahoo, so there's still no substitute for exercising
the actual tools against the live league for anything API-facing. After a
change:

1. Start the server locally.
2. Call the affected tool(s) and confirm the JSON shape is intact and values are correct.
3. For anything touching rosters or matchups, verify against the Yahoo web UI for your league.

## Deployment

Deploys are manual and intentional — CI (`.github/workflows/test.yml`) **only
lints, tests, and audits dependencies; it never deploys.** The server is a
long-running streamable-HTTP process (`python server.py`); run it under a
process manager (e.g. systemd) behind a TLS-terminating proxy/tunnel. The
maintainer's environment-specific runbook is in `CLAUDE.local.md` (gitignored).

**Multi-league / multi-sport:** `YAHOO_LEAGUE_ID` sets only the *default*
league. Targeting another league the authenticated account already belongs to —
**including one in a different sport** listed in `YAHOO_SPORT` (e.g. an NFL
league from an `mlb,nfl` deploy) — needs **no redeploy or config change**:
clients pass a per-call `league_id` (see "Tools exposed"), validated against the
account's own leagues. `_get_league` resolves it from the discovered
`league_key`, which already encodes the right game + season, so the same path
serves any sport. Only changing the *default* league (or adding a *new* sport to
`YAHOO_SPORT`) requires updating the env var (and a restart).

## Tools exposed

Keep tool names and input schemas stable — they are the server's public
contract with MCP clients. Renaming a tool or changing a parameter is a
breaking change.

**Multi-league:** every league-scoped tool takes an optional `league_id` (the
bare numeric id) to target a league other than the configured default
(`cfg.league_id`). `_get_league(sc, league_id)` resolves it and validates any
explicit override against the account's own leagues (`_get_my_leagues` →
`_parse_my_leagues`, cached per process), raising a clear error for a league the
token can't see; if discovery is unavailable it degrades permissive rather than
blocking (constructing the key from `cfg.default_sport`, single-sport). Response
payloads echo the *resolved* league via `_resolved_league_id(lg)`, and the
scoring-config cache (`_scoring_configs`) is keyed by `league_key` so leagues
don't clobber each other's labels **or scoring model** — a categories and a
points league can coexist without cross-contamination.

**Scoring model:** every league-scoped tool's framing follows the resolved
league's `ScoringConfig.is_points_league` (see `yahoo_parsers.py`), so a
head-to-head **categories** league (e.g. baseball) and a **points** league
(typical football) each get correct output from the same tools. Matchup and
scoreboard payloads carry a `scoring` field (`"categories"` or `"points"`)
telling the client which framing to expect.

**Player fields:** every tool that emits a player surfaces the pro-sports team
abbreviation under the sport-neutral key **`pro_team`** (from Yahoo's
`editorial_team_abbr`), set at the single output chokepoint `_format_player`
(plus the transactions/ownership/player-notes handlers that build their own
player dicts). This replaced the baseball-specific `mlb_team` and the raw
`editorial_team_abbr` output key in **v2.0** (a breaking change). The parser
boundary (`_flatten_raw_yahoo_player`) still reads Yahoo's actual
`editorial_team_abbr` field — the neutralization happens at output only.

- `yahoo_list_my_leagues` — the leagues the authenticated account belongs to (`league_id`, `name`, `season`, `is_default`), plus `default_league_id`. Use it to discover the ids accepted by the other tools' `league_id` parameter.
- `yahoo_list_teams` — list all teams (numbers, keys, managers)
- `yahoo_get_standings` — league standings. Returns `standings` as a normalized list (via `_parse_standings`): each team has numeric `rank`, `playoff_seed`, a structured `record` (`wins`/`losses`/`ties`/`pct`), and `games_back` (`null` for the leader). In a **categories** league each team also gets a `categories` list of season totals with the team's league `rank` per category (rate stats like ERA/WHIP ranked low-first), from a separate `league/{key}/teams/stats` call that degrades gracefully (standings still return without `categories` if it fails). In a **points** league that call is skipped (category ranking is meaningless); each team instead carries `points_for`/`points_against` (from Yahoo's `team_standings`).
- `yahoo_get_scoreboard` — all matchups for a week. Returns `matchups` as a list of parsed breakdowns (same core parser as `yahoo_get_matchup`, neutral framing), each carrying a `scoring` field. **Categories:** matchup meta, a `teams` list (`name`, `team_key`, `category_points`), and a `categories` list where each entry has per-stat `values` keyed by `team_key`, the winning `team_key` (or `"tie"`/`null` for informational stats), and a `scored` flag. **Points:** `teams` carry `points`/`projected_points`, a top-level `winner` (team_key/`"tie"`/`null`), and informational `stat_lines` (per-stat `values`, no per-stat winner). `week` is resolved to the numeric current week when omitted.
- `yahoo_get_matchup` — one team's H2H matchup detail (a structured `matchup` object, not just the opponent key), with a `scoring` field. Common meta: `week`, `week_start`/`week_end`, `status`, `is_playoffs`. **Categories:** both teams (`team`/`opponent` with `name`, `team_key`, `category_points`) and a `categories` list giving each side's value per stat plus `result` (`win`/`loss`/`tie`, from Yahoo's `stat_winner`) and a `scored` flag (informational stats like H/AB and IP are `scored: false`). **Points** (via `_points_matchup_node`): `team`/`opponent` carry `points`/`projected_points`, a single overall `result` (`win`/`loss`/`tie`/`null`, from the matchup-level `winner_team_key`/`is_tied`), and informational `stat_lines` (no per-stat win/loss). Parsed by `_parse_matchup` from the raw response, since yfa's `Team.matchup()` only yields the opponent's key.
- `yahoo_get_roster` — a team's roster (supports `day` / `week`; `include_stats=true` enriches each player with season category totals via one batched `players;player_keys=…;out=stats` call, parsed by `_flatten_raw_yahoo_player` and merged by player_id)
- `yahoo_get_league_settings` — config, rules, scoring
- `yahoo_get_transactions` — recent transaction history
- `yahoo_search_free_agents` — available free agents. Each player includes a `stats` map of season totals per scoring category (labeled via the league-derived `ScoringConfig`, numbers coerced; ratio stats like baseball's H/AB kept as strings), fetched inline via `;out=percent_owned,stats` and parsed in `_flatten_raw_yahoo_player`. The `sort` key resolves via `_resolve_sort`: named sorts (AR/OR/…), a numeric stat id, the baseball `_STAT_NAME_TO_ID` table, then a sport-neutral fallback that matches the league's own category labels (so football labels like `"Pass Yds"` sort with no per-sport table). `time_period` (`season`/`lastweek`/`lastmonth`/`biweekly`) sets the Yahoo `sort_type` for stat-based sorts — used to surface recent-form pickups; only `season` sends `sort_season`. The displayed `stats` remain season totals; the window only controls ranking order.
- `yahoo_get_waivers` — players currently on the waiver wire (Yahoo `/players` collection with `status=W`, via the same `_fetch_free_agents_raw` path as free agents). Distinct from free agents: these are dropped players serving a waiver period before clearing. Same per-player shape (positions, `pro_team`, percent owned, season `stats`). Read-only — surfaces who to claim; the claim itself is placed in the Yahoo UI.
- `yahoo_get_taken_players` — all rostered (taken) players league-wide (`status=T`), each annotated with the owning fantasy team. The only tool that adds `ownership` (`owner_team_key`/`owner_team_name`) to `;out=` so `_flatten_raw_yahoo_player` emits an `ownership` block — a league-wide owner map for trade-target/category-scarcity analysis without iterating every team's roster. Defaults `count=300` (covers a full league); paginates past Yahoo's 25/page cap.
- `yahoo_get_player_stats` — single player lookup
- `yahoo_get_players_batch` — multiple players in one call
- `yahoo_get_player_notes` — news / injury notes
- `yahoo_get_player_ownership` — who owns a player

## Prompts

`@mcp.prompt` templates (in `server.py`, after the tools) that orchestrate the
tools for common multi-step questions: `analyze_matchup`, `waiver_help`,
`weekly_recap`. **Design rule:** the orchestration/strategy lives in the prompt
text (it tells Claude which tools to chain and how to reason), keeping the tools
themselves a thin read-only data layer. Put new "do X for me" workflows here as
prompts rather than baking strategy into tool handlers. `waiver_help` leans on
`yahoo_search_free_agents`' `time_period=lastweek` for recent form. All three
prompts **branch on the matchup's `scoring` field**, so they give correct
guidance for a points (football) league (points margin, weakest starting spots)
as well as a categories league (weak categories) — keep that branching when
editing them.

## Yahoo API gotchas

The Yahoo Fantasy API response format is the main source of bugs in this repo.
Be defensive:

- Responses are **deeply nested and positional** — arrays interleave data objects with empty `[]` placeholders, and collections use **numeric string keys** (`"0"`, `"1"`, ...) plus a `count`. Never assume a fixed index; locate data by key/shape, not by position.
- Stats arrive as `{stat: {stat_id, value}}` lists — map by `stat_id` (labels/scoring come from the league-derived `ScoringConfig`), never by order.
- Some endpoints require parameters that look optional. **Default them explicitly in the handler** rather than relying on the upstream call to fill them in.

## Known issues / fragile areas

- Historically regression-prone handlers (verify these still work after changes): transactions handler, free-agent search **sort** parameter, player-notes endpoint, and **roster team-number resolution**.
- **`yahoo_search_free_agents` with `sort=PTS` returns zero results** in a head-to-head **categories** league — Yahoo computes no fantasy-points ranking there. Expected behavior, not a bug; the tool description documents it and steers callers to AR or a stat/category sort. Don't "fix" it. (In a **points** league — typical football — `PTS` *is* the natural sort and returns results; the caveat is categories-specific.)
- **Points-league (football) parsing is on-spec, not yet live-verified.** The points-league matchup/settings fixtures (`*_POINTS`) were assembled from the documented Yahoo shape + real NFL stat_ids ahead of a drafted NFL league — the exact node layout (`winner_team_key`/`is_tied`, `team_projected_points`) hasn't been confirmed against a real NFL matchup response. **Re-verify against a live NFL league once one drafts** (call the tools, compare to the Yahoo web UI) and correct the fixtures/parsers if the shape differs.

## Conventions & etiquette

- **Make surgical, reviewable diffs.** Do not rewrite `server.py` wholesale; edit the specific functions that need changing.
- **Atomic commits** with clear messages — one logical change per commit.
- **Preserve tool contracts.** Don't rename tools or change parameter names/types without explicit instruction; downstream MCP clients depend on them.
- **No secrets in the repo.** Yahoo tokens, client IDs/secrets, and any edge/proxy credentials stay out of version control.
- **Match existing style.** Follow the patterns already in the file (naming, error handling, response parsing helpers) rather than introducing new ones.
- **Wrap prose at ~80 columns.** Hard-wrap narrative paragraphs (and blockquotes) near 80 chars so edits produce line-granular diffs; keep Markdown **list items** and **fenced code blocks** on a single line each — never wrap them (a wrapped bullet needs fragile continuation-indentation, and wrapped commands change meaning). Count *characters*, not bytes: `—`/`→`/`…` are multibyte in UTF-8, so a visually-fine line can read >80 bytes. No formatter enforces this (no Prettier/markdownlint/`.editorconfig`) — maintain it by hand. Applies to this file and `CLAUDE.local.md`.
