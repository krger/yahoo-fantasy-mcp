# Yahoo Fantasy MCP Server

[![tests](https://github.com/krger/yahoo-fantasy-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/krger/yahoo-fantasy-mcp/actions/workflows/test.yml)

A read-only MCP server that gives Claude access to your Yahoo Fantasy Sports league data (baseball, football, …). It runs as a **remote MCP server over streamable HTTP** (serving the MCP endpoint at `/mcp`) — you host it and connect Claude to it by URL, rather than running it as a local stdio subprocess.

**Works with any Yahoo league.** Point it at your own league with `YAHOO_LEAGUE_ID` (there's no baked-in default) — the server reads your league's settings from Yahoo at runtime, so it adapts to the league's own scoring: standings and matchup breakdowns use the actual categories in a head-to-head **categories** league (e.g. baseball), or fantasy-points totals in a head-to-head **points** league (typical fantasy football). It doesn't assume a particular sport or category set, and the current season is auto-detected. One server can serve **multiple sports** at once (see `YAHOO_SPORT`).

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
| `yahoo_list_my_leagues` | List the leagues your Yahoo account is in (for the `league_id` override) |

**Multiple leagues?** `YAHOO_LEAGUE_ID` sets your default league, but every tool also accepts an optional `league_id` argument to target another league your account belongs to. Call `yahoo_list_my_leagues` to see the available ids; an id outside your account's leagues is rejected.

## Prompts

One-click prompt templates (in Claude's connector menu) that chain the tools for common multi-step questions:

| Prompt | What it does |
|--------|--------------|
| **Analyze my matchup** | Summarizes your current head-to-head matchup — categories won/lost/tied in a categories league, or the fantasy-points margin in a points league — with the margins and where it'll be decided |
| **Waiver wire help** | Finds recent-form free agents who'd improve your team — targeting the categories you're losing, or your weakest starting spots in a points league |
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

From the `yahoo-fantasy-mcp` directory, sync the pinned dependencies from the
lockfile:

```bash
uv sync
```

This creates a `.venv/` and installs the exact versions in `uv.lock`, so you get
a reproducible environment. For a runtime-only install (no test/lint tooling),
add `--no-dev`.

### 3. Get Yahoo API access, credentials, and authenticate (one-time)

> [!IMPORTANT]
> **Yahoo now gates Fantasy Sports API access behind an approval process**
> (changed around 2026-07-24). Creating an app is no longer enough — the old
> `developer.yahoo.com/fantasysports/guide/` now redirects to the
> [Yahoo Sports Developer Portal](https://sports.yahoo.com/developer/), the
> self-serve app form no longer offers a Fantasy Sports permission, and apps
> created under the previous flow lost access at the cutover. Until your app is
> approved, **every** API call returns `403 "This application is not authorized
> to perform this action"` — including for a correctly configured app whose
> settings page still shows Fantasy Sports permission checked.

**Request access first** at <https://sports.yahoo.com/developer/access/>. The
form asks for your contact details, a product description, your use case, and
expected user volume. Read-only access is the default, which is all this server
needs. Yahoo reviews each submission and reaches out; no turnaround time is
published, and there is no support email or forum.

If you **already have a Yahoo app**, put its **App ID** in the form's App ID
field so that app gets approved — this preserves your existing Client ID,
Client Secret, and user authorization. Don't delete and re-create the app: you'd
lose the App ID the request needs, and a replacement can't be given Fantasy
Sports permission through the self-serve form.

Your app (created at <https://developer.yahoo.com/apps/create/>, or as directed
during the approval process) needs:

- **Application Type:** Confidential Client.
- **API Permissions:** **Fantasy Sports → Read**, if offered. Yahoo only grants read-only access for Fantasy Sports — there is no Read/Write option, so roster moves through the API aren't possible. This permission may not appear on the create form until your access request is approved.
- **Redirect URI(s):** an `https://` URL **on a domain you control**. Yahoo rejects `localhost` and no longer supports the out-of-band (`oob`) flow, so use a real domain — it doesn't have to actually serve anything (e.g. your eventual host, `https://fantasy.example.com`). Avoid a URL sitting behind an authenticating proxy: the consent redirect carries the `code` you need in its query string, and an interstitial login page can take it away before you can copy it.

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

**Getting `403 "This application is not authorized to perform this action"` on
every call?** That's the access gating described above, not a credentials
problem. The tell is that token refresh still succeeds while the API refuses:
if a `refresh_token` grant against `api.login.yahoo.com/oauth2/get_token`
returns `200` but `/fantasy/v2/game/mlb` returns `403`, the tokens are fine and
the app simply isn't approved yet. Re-running the authorize flow, re-saving the
app, or creating a new app will not change it — only approval will.

### 4. Run the server

This is a streamable-HTTP server (not stdio). Start it from the repo root:

```bash
YAHOO_LEAGUE_ID=YOUR_LEAGUE_ID uv run python server.py
```

It listens on `127.0.0.1:8000` (loopback only) and serves MCP at **`http://localhost:8000/mcp`**.
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
| `YAHOO_LEAGUE_ID` | _(required)_ | Your **default** Yahoo league ID — the numeric id in your league URL (e.g. `12345`). The server refuses to start without it. Tools accept a per-call `league_id` to target other leagues your account is in. |
| `YAHOO_SPORT` | `mlb` | Yahoo game code. Accepts a comma-separated list (e.g. `mlb,nfl`) to serve multiple sports from one deployment; the first is the default sport. Per-call `league_id` can target any league your account is in across these games. |
| `YAHOO_SEASON` | _(current)_ | Season year (e.g. `2026`). If unset, the current season is auto-detected; set it to pin a past season. |
| `YAHOO_OAUTH_FILE` | `./oauth2.json` | Path to OAuth credentials |

## Example Prompts

Once connected, try asking Claude things like:

- "Show me my current roster"
- "Who are the best available shortstops?" (baseball) / "Who are the best available running backs?" (football)
- "What are the league standings?"
- "Show me the free agent starting pitchers sorted by ERA"
- "Compare my team's roster to team 3's roster"
- "What's my matchup looking like this week?"
- "Which leagues am I in?" / "Show the standings for my other league"

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
