# CLAUDE.md

Guidance for Claude Code when working in this repository. Keep this file current — when behavior, structure, or conventions change, update this file in the same commit.

## Project overview

This is a **Model Context Protocol (MCP) server** that exposes Yahoo Fantasy Baseball data and roster operations as tools. It is written in **Python** and serves a **remote MCP endpoint over streamable HTTP** at the `/mcp` path.

- **Runtime host:** the deploy host (Debian VM, part of the a hypervisor stack on the hypervisor)
- **Public endpoint:** `https://fantasy.example.com/mcp`
- **Edge security:** the endpoint sits behind a Cloudflare Tunnel, with Cloudflare Access + Managed OAuth in front. The MCP server itself does **not** implement OAuth — Access handles authentication and only forwards authenticated requests. Do not add an OAuth/auth layer to the server without coordinating; it would conflict with the edge config.
- **Upstream API:** Yahoo Fantasy Sports API (OAuth2). The server holds a Yahoo refresh token and mints access tokens to call Yahoo.

<!-- TODO: confirm/adjust the Python version and dependency manager below -->
- **Python version:** 3.14.5
- **Dependency management:** `uv`

## Key files

<!-- TODO: fill in the real layout. As of now the bulk of logic lives in server.py. -->
- `server.py` — main server: MCP tool definitions and Yahoo API client logic.
- TODO: list any other modules (auth/token handling, Yahoo response parsing helpers, config).
- TODO: where the Yahoo OAuth token/refresh token is stored and how it's loaded (env var, file, etc.). **Never commit tokens or secrets.**

## Running locally (dev machine, not the deploy host)

Develop on a workstation, never directly against the live deployed copy. The live runtime updates via `git pull` on the deploy host (see Deployment).

```
# TODO: confirm the exact run command, e.g.
python server.py
# or however the streamable-HTTP server is launched, plus any required env vars
```

## Testing / verifying changes

There is no substitute for exercising the actual tools against the live league. After any change:

1. Start the server locally.
2. Call the affected tool(s) and confirm the JSON shape is intact and values are correct.
3. For anything touching rosters or matchups, verify against the Yahoo web UI for league 12345.

<!-- TODO: if/when a test suite exists, document the command here, e.g. `pytest`. -->
TODO: add automated tests and document the command. High-value targets: the Yahoo response parsers (see "Yahoo API gotchas") and team-number resolution.

## Deployment (pull-on-the deploy host)

Deploys are manual and intentional — no GitHub Actions, no CI bot.

```
# on your dev machine
git add -p && git commit && git push

# on the deploy host
git pull
# TODO: restart the service, e.g.
# sudo systemctl restart <service-name>
```

Before starting an editing session, make sure the deploy host and the repo are in sync (`git fetch && git status` on the deploy host) so you're editing from the same baseline that's actually running. Hand-edits made on the deploy host without committing will show as uncommitted changes — resolve those first.

## League facts (league 12345)

- **Format:** 10-team, head-to-head **categories** league (`scoring_type: head`), Yahoo game code `mlb`, season 2026.
- **Scoring categories (10):**
  - Hitting: R, HR, RBI, SB, AVG
  - Pitching: W, SV, K, ERA, WHIP
- **Yahoo `stat_id` mapping** (the API returns stats keyed by numeric `stat_id`; map them explicitly rather than by position):
  - `7` = R, `12` = HR, `13` = RBI, `16` = SB, `3` = AVG
  - `28` = W, `32` = SV, `42` = K, `26` = ERA, `27` = WHIP
  - Informational (not scored): `50` = IP, `60` = H/AB
- **Owner team:** team **#5**, "[team]."
- **Team-number map** (1–10): 1=[team], 2=[team], 3=[team], 4=[team], 5=[team], 6=[team], 7=[team], 8=[team], 9=[team], 10=[team].
  - **Convention:** treat this map as a hint, not a source of truth. Resolve team identity at runtime via `yahoo_list_teams` rather than hardcoding numbers — Yahoo team numbering is league-specific and the server should not assume it.

## Tools exposed

Keep tool names and input schemas stable — they are the server's public contract with MCP clients. Renaming a tool or changing a parameter is a breaking change.

- `yahoo_list_teams` — list all teams (numbers, keys, managers)
- `yahoo_get_standings` — league standings
- `yahoo_get_scoreboard` — all matchups for a week
- `yahoo_get_matchup` — one team's H2H matchup detail
- `yahoo_get_roster` — a team's roster (supports `day` / `week`)
- `yahoo_get_league_settings` — config, rules, scoring
- `yahoo_get_transactions` — recent transaction history
- `yahoo_search_free_agents` — available free agents
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

## Conventions & etiquette

- **Make surgical, reviewable diffs.** Do not rewrite `server.py` wholesale; edit the specific functions that need changing.
- **Atomic commits** with clear messages — one logical change per commit.
- **Preserve tool contracts.** Don't rename tools or change parameter names/types without explicit instruction; downstream MCP clients depend on them.
- **No secrets in the repo.** Yahoo tokens, client IDs/secrets, and any Cloudflare credentials stay out of version control.
- **Match existing style.** Follow the patterns already in the file (naming, error handling, response parsing helpers) rather than introducing new ones.
