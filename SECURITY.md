# Security Policy

This is a read-only MCP server that proxies the Yahoo Fantasy Sports API. It
holds live Yahoo OAuth credentials and, in a hosted deployment, listens on a
network socket — so security reports are welcome.

## Supported versions

Only the latest release receives fixes. Releases are SemVer-tagged
(`vMAJOR.MINOR.PATCH`) with a matching
[GitHub Release](https://github.com/krger/yahoo-fantasy-mcp/releases), so
"upgrade to the latest release" is a concrete instruction, not a hand-wave.

| Version | Supported |
|---------|-----------|
| Latest `2.x` release | ✅ |
| Any earlier tag | ❌ — historical; upgrade instead |

## Reporting a vulnerability

**Use GitHub's private vulnerability reporting:**
[Report a vulnerability](https://github.com/krger/yahoo-fantasy-mcp/security/advisories/new)
(also reachable from the repo's **Security** tab). It is enabled on this repo,
and it is the only channel — please **do not** open a public issue or pull
request for a suspected vulnerability, and do not post details in a discussion.

A useful report includes:

- The affected file, tool, or endpoint (e.g. `server.py`, `yahoo_get_roster`, `POST /mcp`).
- Steps to reproduce — ideally the request or tool arguments that trigger it.
- The impact: what an attacker gains, and what access they need to start with.
- The version or commit you tested (`git describe`, or the release tag).

Report it even if you are not sure it falls in scope. A wrong guess costs a
short reply; an unreported issue costs more.

## What to expect

This is a solo-maintained hobby project, so the commitment is **best-effort,
with no service-level guarantee**:

- Acknowledgment: usually within about a week.
- Assessment and fix: as time allows — no promised timeline.
- Valid reports get a GitHub Security Advisory and a fix in a tagged release.
- Credit in the advisory unless you ask to stay anonymous.

Please practice coordinated disclosure: hold public details until a fix ships
or roughly 90 days have passed, whichever comes first.

## Scope

In scope:

- The server and its helpers: `server.py`, `config.py`, `schemas.py`, `yahoo_parsers.py`.
- The MCP tool surface and its input validation (the Pydantic models in `schemas.py`), including anything that reaches a Yahoo API URL — e.g. parameter injection through a tool argument.
- The streamable-HTTP transport wiring in `build_app()`, including `Host` header validation / DNS-rebinding protection.
- OAuth token handling: how credentials are loaded, refreshed, logged, or leaked into responses or logs.
- A known advisory in a pinned dependency that CI's `pip-audit` step does not already catch.

## Out of scope (by design)

These are deliberate design decisions, documented in `README.md` and
`CLAUDE.md`. Reports about them will be closed with a pointer here:

- **The server performs no authentication or authorization of its own.** It trusts whatever reaches `/mcp`, and every tool is read-only against the single configured Yahoo account. It binds to `127.0.0.1` by default; exposing it publicly without a TLS-terminating proxy or tunnel that enforces access control is a deployment mistake, not a server vulnerability. (The reference deployment sits behind a Cloudflare Tunnel with Cloudflare Access in front.)
- **`oauth2.json` holds live Yahoo credentials in plaintext on the host.** It is gitignored and must never be committed, but "the credentials file is readable by the user who runs the server" is the intended design, not a finding.
- **`MCP_ALLOWED_HOSTS` can be configured to accept a bare `localhost`.** That is deliberate: the transport's built-in loopback allowlist only matches `host:port` forms, and a tunnel that forwards `Host: localhost` is rejected without the explicit entry. Which hosts to allow is a local deployment choice.
- **Yahoo's `403 "This application is not authorized to perform this action"`.** That is Yahoo's per-app Fantasy Sports API approval gate, not a flaw in this code.
- **Vulnerabilities in the Yahoo Fantasy Sports API itself** — report those to Yahoo, not here.
- **Automated scanner output with no demonstrated impact** on this codebase.

## Security practices in this repo

- Private vulnerability reporting, Dependabot security updates, and secret scanning are enabled.
- Dependabot opens grouped weekly dependency PRs for the `uv` lockfile and the pinned GitHub Actions (`.github/dependabot.yml`).
- CI (`.github/workflows/test.yml`) runs `ruff`, the test suite, and a `pip-audit` scan of the exported locked **production** tree on every push and pull request — a published advisory against a pinned dependency fails the build rather than waiting on a bump.
- No secrets in version control: Yahoo tokens, client IDs/secrets, and edge credentials stay out of the repo.
