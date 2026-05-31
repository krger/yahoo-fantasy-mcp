# Yahoo Fantasy Baseball MCP Server

[![tests](https://github.com/krger/yahoo-fantasy-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/krger/yahoo-fantasy-mcp/actions/workflows/test.yml)

A read-only MCP server that lets Claude Desktop access your Yahoo Fantasy Baseball league data.

## Tools

| Tool | Description |
|------|-------------|
| `yahoo_get_roster` | View any team's roster (yours or opponent's) |
| `yahoo_get_standings` | Current league standings |
| `yahoo_get_scoreboard` | All matchups for a given week |
| `yahoo_search_free_agents` | Search available players by position/stat |
| `yahoo_get_player_stats` | Look up a specific player's stats |
| `yahoo_get_league_settings` | League rules, scoring categories, deadlines |
| `yahoo_get_matchup` | Detailed H2H matchup breakdown |
| `yahoo_list_teams` | List all teams (useful for finding team numbers) |

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

### 3. Authenticate with Yahoo (one-time)

The first time you run the server, `yahoo_oauth` will open a browser window
asking you to authorize the app. Grant access, and it will save your tokens
back into `oauth2.json`. After this initial auth, tokens refresh automatically.

To test authentication before hooking up Claude Desktop:

```bash
uv run python -c "
from yahoo_oauth import OAuth2
sc = OAuth2(None, None, from_file='oauth2.json')
print('Token valid:', sc.token_is_valid())
print('Auth OK')
"
```

### 4. Configure Claude Desktop

Edit your Claude Desktop config file:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Add this to the `mcpServers` section (adjust paths to match your system):

```json
{
  "mcpServers": {
    "yahoo-fantasy": {
      "command": "uv",
      "args": [
        "--directory", "/FULL/PATH/TO/yahoo-fantasy-mcp",
        "run", "python", "server.py"
      ],
      "env": {
        "YAHOO_LEAGUE_ID": "YOUR_LEAGUE_ID"
      }
    }
  }
}
```

**Windows example paths:**
```json
{
  "mcpServers": {
    "yahoo-fantasy": {
      "command": "uv",
      "args": [
        "--directory", "C:\\Users\\YourUser\\yahoo-fantasy-mcp",
        "run", "python", "server.py"
      ],
      "env": {
        "YAHOO_LEAGUE_ID": "YOUR_LEAGUE_ID"
      }
    }
  }
}
```

### 5. Restart Claude Desktop

After saving the config, restart Claude Desktop. You should see the Yahoo
Fantasy tools available in the tools menu (hammer icon).

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

**"OAuth credentials file not found"**
Make sure `oauth2.json` is in the same directory as `server.py`.

**"Authentication failed"**
Delete the token fields from `oauth2.json` (keep only `consumer_key` and
`consumer_secret`) and re-run to redo the browser auth flow.

**"Resource not found"**
The league may not be active yet, or the game ID for the current MLB
season may not be available. Check that your league is visible at
baseball.fantasysports.yahoo.com.

**Tools not showing in Claude Desktop**
Check the Claude Desktop logs for MCP errors. On macOS:
`~/Library/Logs/Claude/mcp*.log`
