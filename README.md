# Yahoo Fantasy Baseball MCP Server

[![tests](https://github.com/krger/yahoo-fantasy-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/krger/yahoo-fantasy-mcp/actions/workflows/test.yml)

A read-only MCP server that gives Claude access to your Yahoo Fantasy Baseball league data. It runs as a **remote MCP server over streamable HTTP** (serving the MCP endpoint at `/mcp`) — you host it and connect Claude to it by URL, rather than running it as a local stdio subprocess.

**Works with any Yahoo league.** Point it at your own league with `YAHOO_LEAGUE_ID` (there's no baked-in default) — the server reads your league's scoring categories from Yahoo at runtime, so standings ranks and matchup breakdowns adapt to whatever categories your league actually uses (it doesn't assume a particular 10-category setup). The current season is auto-detected.

## Tools

| Tool | Description |
|------|-------------|
| `yahoo_get_roster` | View any team's roster (yours or opponent's) |
| `yahoo_get_standings` | Current league standings |
| `yahoo_get_scoreboard` | All matchups for a given week |
| `yahoo_search_free_agents` | Search available players by position/stat, with recent-form windows (last week / two weeks / month) |
| `yahoo_get_player_stats` | Look up a specific player's stats |
| `yahoo_get_league_settings` | League rules, scoring categories, deadlines |
| `yahoo_get_matchup` | Detailed H2H matchup breakdown |
| `yahoo_get_roster` | A team's roster, optionally enriched with each player's season stats |
| `yahoo_list_teams` | List all teams (useful for finding team numbers) |

## Prompts

One-click prompt templates (in Claude's connector menu) that chain the tools for common multi-step questions:

| Prompt | What it does |
|--------|--------------|
| **Analyze my matchup** | Summarizes your current head-to-head matchup — categories won/lost/tied, margins, and where it'll be decided |
| **Waiver wire help** | Identifies the categories you're losing, then surfaces recent-form free agents who'd improve them |
| **Weekly recap** | Standings + your matchup status + notable league transactions |

## Setup

### 1. Install uv (if you don't have it)

```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart your terminal after installing.

### 2. Install dependencies

From the `yahoo-fantasy-mcp` directory:

```bash
uv venv
uv pip install -e .
```

Or if you prefer to just install the deps directly:

```bash
uv venv
uv pip install "mcp[cli]>=1.2.0" yahoo_fantasy_api yahoo_oauth
```

### 3. Get Yahoo credentials and authenticate (one-time)

Create a Yahoo app at <https://developer.yahoo.com/apps/create/>:

- **Application Type:** Confidential Client.
- **API Permissions:** enable **Fantasy Sports → Read** (or Read/Write if you
  ever want roster moves).
- **Redirect URI(s):** an `https://` URL **on a domain you control**. Yahoo
  rejects `localhost` and no longer supports the out-of-band (`oob`) flow, so
  use a real domain — it doesn't have to actually serve anything (e.g. your
  eventual host, `https://fantasy.example.com`).

Copy the **Client ID** and **Client Secret**, then create `oauth2.json` in the
repo root:

```json
{
  "consumer_key": "<your Client ID>",
  "consumer_secret": "<your Client Secret>",
  "callback_uri": "<the exact Redirect URI you registered>"
}
```

`callback_uri` must match the registered Redirect URI byte-for-byte. Now run the
one-time authorize flow:

```bash
uv run python -c "from yahoo_oauth import OAuth2; OAuth2(None, None, from_file='oauth2.json')"
```

It opens a browser to Yahoo's consent page. After you approve, Yahoo redirects
to `<callback_uri>?code=XXXX`. **The landing page may show a 404 or an error —
that's fine; the value you need is in the browser's address bar.** Copy the
`code` parameter from the URL and paste it at the `Enter verifier :` prompt.
The library writes `access_token`/`refresh_token` into `oauth2.json`, and tokens
refresh automatically from then on (`oauth2.json` is gitignored — never commit it).

### 4. Run the server

This is a streamable-HTTP server (not stdio). Start it from the repo root:

```bash
YAHOO_LEAGUE_ID=YOUR_LEAGUE_ID uv run python server.py
```

It listens on `0.0.0.0:8000` and serves MCP at **`http://localhost:8000/mcp`**.
(See [Environment Variables](#environment-variables) for the other settings.)

The server itself does **no authentication** — it trusts whatever reaches it.
For anything past local use, put it behind a reverse proxy or tunnel that
terminates TLS and enforces access control. (This repo's reference deployment
runs behind a Cloudflare Tunnel with Cloudflare Access in front, exposed at a
public `/mcp` URL.)

### 5. Connect Claude to the server

Add the server to Claude (Desktop or web) as a **custom connector**, pointing at
its `/mcp` URL:

- Local: `http://localhost:8000/mcp`
- Hosted: `https://your-domain/mcp`

In Claude, go to **Settings → Connectors → Add custom connector**, paste the
URL, and save. Once connected, the Yahoo Fantasy tools appear in the connectors
list and are available in chat.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `YAHOO_LEAGUE_ID` | _(required)_ | Your Yahoo league ID — the numeric id in your league URL (e.g. `12345`). The server refuses to start without it. |
| `YAHOO_SPORT` | `mlb` | Yahoo game code |
| `YAHOO_SEASON` | _(current)_ | Season year (e.g. `2026`). If unset, the current season is auto-detected; set it to pin a past season. |
| `YAHOO_OAUTH_FILE` | `./oauth2.json` | Path to OAuth credentials |

## Example Prompts

Once connected, try asking Claude things like:

- "Show me my current roster"
- "Who are the best available shortstops?"
- "What are the league standings?"
- "Show me the free agent starting pitchers sorted by ERA"
- "Compare my team's roster to team 3's roster"
- "What's my matchup looking like this week?"

## Troubleshooting

**Yahoo shows "Not Found" instead of asking for authorization**
Yahoo dropped support for the out-of-band (`oob`) flow. Make sure `oauth2.json`
has a `callback_uri` set to a registered `https://` Redirect URI (not
`localhost`), as in step 3 — then grab the `code` from the browser's address
bar after approving.

**"OAuth credentials file not found"**
Make sure `oauth2.json` is in the same directory as `server.py` (or point
`YAHOO_OAUTH_FILE` at it).

**"Authentication failed"**
Delete the token fields from `oauth2.json` (keep `consumer_key`,
`consumer_secret`, and `callback_uri`) and re-run the authorize flow.

**`YAHOO_LEAGUE_ID is required`**
The server has no default league — set the `YAHOO_LEAGUE_ID` env var before
starting it.

**"Resource not found"**
The league may not be active yet, or the game ID for the current
season may not be available. Check that your league is visible on Yahoo
Fantasy.

**`Not Acceptable: Client must accept text/event-stream`**
That's the MCP endpoint's normal response to a plain browser/`curl` — it's not
an error in the server. Connect with an MCP client (the custom connector
above), not a browser.

**Connector won't connect / tools not showing**
Confirm the server is running and reachable at the `/mcp` URL, that any proxy
in front forwards to it, and check the server logs (when run under systemd:
`journalctl -u yahoo-fantasy-mcp.service -n 50`).

## License

[MIT](LICENSE) © 2026 Kyle Green
