# Bandwidth MCP (Phoneware fork): agent guide

Phoneware's **vendored fork** of the official
[`Bandwidth/mcp-server`](https://github.com/Bandwidth/mcp-server). Upstream ships
it as a self-run beta Python package with no hosted version. We run it
**single-tenant on Cloud Run behind an OAuth 2.1 gateway**, serving `claude.ai`
(and header-capable clients like Claude Code) at
`https://mcp.bandwidth.phoneware.cloud/mcp`.

This file is the operational guide for working in the repo. `README.md` is the
overview, `DEPLOY.md` is the deploy/security detail, and `src/specs/AGENTS.md` is
the per-tool reference an agent reads at runtime.

## What is ours vs upstream

Upstream gives us a FastMCP server that turns Bandwidth's public OpenAPI specs
into MCP tools (voice, messaging, lookup, recordings) plus a Build-registration
onboarding flow. Everything under "the changes" that matters to Phoneware is our
delta on top:

- **Hosted OAuth 2.1 gateway** (`serve.py`). Bandwidth API creds are
  server-to-server with no user identity, so we never bake them into the server.
  They live in the **client's connector config** (claude.ai Client ID/Secret
  fields) and only transit `/token`, where we validate them by minting an
  upstream Bandwidth token. See [OAuth model](#oauth-model-servepy).
- **Numbers / porting / carrier tools** (`src/tools/numbers.py`) and
  **usage/billing reports** (`src/tools/reports.py`). Hand-written against
  Bandwidth's XML Dashboard API, which `from_openapi` cannot drive. This is the
  surface a carrier reseller actually lives in and the reason the fork exists.
- **Multi-account.** One client ID can hold several Bandwidth accounts; tools
  take an optional `account_id` and validate it against the token's claims
  (`listAccounts`, `_resolve_account`).
- **Deployment-managed tool surface.** The live tool set is chosen entirely by
  env in `cloudbuild.yaml`, enforced uniformly across OpenAPI-derived AND
  hand-written tools.
- **Read/write tool annotations** so MCP clients group tools instead of dumping
  them under "Other".
- **CI/CD deploy** via GitHub Actions + Workload Identity Federation. Never from
  a workstation.

## Protocol version: 2026-07-28 (stateless)

The server speaks the **2026-07-28** MCP revision and the handshake era at the
same endpoint, negotiated per request. That revision deleted protocol sessions:
no `initialize` handshake, no `Mcp-Session-Id`, every request self-describing
through `_meta` (`io.modelcontextprotocol/protocolVersion`, `clientInfo`,
`clientCapabilities`), plus a mandatory `server/discover` RPC that returns
supported versions, capabilities, and identity in one call.

This suits us: our tools are request/response with no server-initiated traffic,
so nothing needed a live connection in the first place. `serve.py` passes
`stateless_http=True` so handshake-era clients are sessionless too, and a
container restart can no longer strand a client holding a session id.

It rides on **fastmcp 4.0.0b1 + MCP Python SDK 2.0.0**, the first releases that
implement the revision. 4.0.0b1 is a **beta**, pinned exactly and deliberately;
`test/test_gateway.py` is the safety net that proves both eras still work.
`src/app.py` also sets `cache_ttl`/`cache_scope`, which fill the revision's
`ttlMs`/`cacheScope` hints so clients can hold the (startup-fixed) tool list
instead of re-listing.

## Layout
```
serve.py                 hosted OAuth 2.1 gateway in front of streamable-http (Phoneware)
cloudbuild.yaml          Cloud Build: pytest gate -> image -> Cloud Run (deployment-managed env)
Dockerfile               runs `python serve.py` from src/
DEPLOY.md                deploy + security model
common_use_cases.md      upstream tool-picking guide
src/
  app.py                 FastMCP instance + lifespan: builds every tool source, then prunes to the configured set
  servers.py             OpenAPI-derived tools (from_openapi over Bandwidth specs); live-config token auth
  config.py              env/CLI parsing, startup OAuth, tool-filter precedence
  profiles.py            curated tool presets (voice, messaging, lookup, numbers, numbers-write, billing, ...)
  urls.py                host resolution (prod/test, per-host overrides, swap_host, dashboard_api_base)
  oauth.py               Bandwidth client-credentials token exchange
  event_store.py         in-memory callback/call-state store (single instance)
  callbacks.py           Starlette routes Bandwidth webhooks POST into
  instructions.py        dynamic MCP instructions built from the live tool set
  resources.py           MCP resources (config, AGENTS.md)
  tools/                 hand-written tools (XML Dashboard API + custom voice): see src/tools/CLAUDE.md
  specs/                 bundled specs + AGENTS.md agent reference
test/                    pytest suite + OpenAPI fixtures: see test/CLAUDE.md
```

## Two tool sources (important)

Every tool comes from one of two places, and they behave differently:

1. **OpenAPI-derived** (`servers.py`). `FastMCP.from_openapi` over Bandwidth's
   JSON/REST specs (voice, messaging, lookup, insights, TFV, end-user-mgmt,
   build-registration). Filtered at build time by a route map.
2. **Hand-written** (`src/tools/*.py`). The XML Dashboard API (numbers, porting,
   sites, reports) plus custom voice (BXML, callbacks) and session tools. These
   register unconditionally, so `app.py` prunes them after the fact to honor the
   same env config. See [Uniform tool gating](#uniform-tool-gating).

## Build / run
Python, no pnpm. Toolchain is per-repo; if `python`/`pytest` are missing, run
through `mise`.
```
pip install ".[dev]"          # deps + black/pytest/pytest-asyncio/pytest-httpx
python -m pytest -q           # full suite (what CI gates on)
```
Dependency pins live in **three** places that must move together:
`pyproject.toml`, the `Dockerfile`'s `pip install`, and `cloudbuild.yaml`'s test
step. The last two install by hand rather than from `pyproject.toml`, so a pin
bumped in one place and not the others means CI tests one stack and Cloud Run
ships another. `uv pip install` needs `--prerelease=allow` for the fastmcp beta;
plain `pip` takes it from the exact `==4.0.0b1` pin.
Run locally **from `src/`**, never `pip install .` the package: the upstream
`pyproject.toml` omits some modules (e.g. `urls`), so an installed package
can't import them. Upstream runs from `src/` and so do we.
```
# stdio (default): a local MCP client spawns this
PYTHONPATH=src python src/app.py

# hosted gateway (what Cloud Run runs)
BW_GATEWAY_TOKEN=$(openssl rand -hex 32) BW_MCP_TRANSPORT=streamable-http \
  PYTHONPATH=src python serve.py
```

## Phoneware MCP servers in this project (`.mcp.json`)
Project-scoped MCP servers for the four Phoneware MCPs, so a session here can
drive the whole stack. Claude Code asks for approval the first time it loads
them.

| Server | How it runs | What it needs |
|---|---|---|
| `bandwidth` | hosted, `https://mcp.bandwidth.phoneware.cloud/mcp` | OAuth; the client id/secret ARE the Bandwidth API creds |
| `peplink` | hosted, `https://mcp.peplink.phoneware.cloud/mcp` | OAuth with DCR (`/register`), so the client registers itself |
| `netsapiens` | local stdio, `../netsapiens-mcp/build/index.js` | `NETSAPIENS_API_TOKEN`, or `NETSAPIENS_OAUTH_CLIENT_ID`/`_SECRET`/`_USERNAME`/`_PASSWORD`. `NETSAPIENS_API_URL` defaults to `https://edge.phoneware.cloud` |
| `autotask` | local stdio, `../autotask-mcp/dist/index.js` | `AUTOTASK_USERNAME`, `AUTOTASK_SECRET`, `AUTOTASK_INTEGRATION_CODE` (starts read-only; set `AUTOTASK_READ_ONLY=false` to allow writes) |

- **It is tracked on purpose**, via a `!.mcp.json` negation at the end of
  `.gitignore`. Upstream ignores `.mcp.json` alongside `.DS_Store` as a personal
  scratch file; ours is shared Phoneware config. Without the negation `git add`
  skips it silently and only this doc section ships, which is exactly what
  happened on the first attempt (PR #5).
- **No secrets in this file.** Every credential is `${VAR}` expansion from the
  environment, with empty defaults so one missing var can't stop the other
  servers loading. The two stdio servers exit at startup without their creds
  (verified: both refuse with a clear message), so they show as failed until
  the vars are exported.
- **Sibling checkouts.** The stdio entries resolve through
  `${PHONEWARE_SRC:-..}`, which assumes the go-style layout
  (`~/dev/src/github.com/phoneware/*`). Set `PHONEWARE_SRC` if yours differs.
  `autotask-mcp` needs `npm install && npm run build` once; `dist/` is
  gitignored.
- **The `bandwidth` entry needs one extra command per machine.** Claude Code's
  automatic OAuth requires Dynamic Client Registration, and this gateway
  deliberately has none (`client_id` IS the Bandwidth client id, see the OAuth
  model below). Connecting without creds fails with *"Incompatible auth server:
  does not support dynamic client registration"*. Hand it the creds instead:
  ```
  claude mcp add --transport http --scope local \
    --client-id "$BW_CLIENT_ID" --client-secret \
    bandwidth https://mcp.bandwidth.phoneware.cloud/mcp
  ```
  `--client-secret` prompts for it (or reads `MCP_CLIENT_SECRET`) and stores it
  in Claude Code's local config, never in the repo. A local entry shadows the
  project entry of the same name (verified), so `.mcp.json` keeps documenting
  the endpoint for everyone while creds stay per-operator. The flow itself is
  verified against production: `/authorize` returns a code carrying `iss`, and
  `/token` validates the pair against Bandwidth, so a fake pair gets
  `invalid_client`.
  peplink's server advertises `/register` and self-registers, so it needs none
  of this; claude.ai's connector UI has its own Client ID/Secret fields and
  needs none of it either.
- **Supporting Claude Code's automatic flow would mean redesigning the auth
  model**, not adding an endpoint. DCR hands the client an id/secret that WE
  mint, but this server holds no Bandwidth creds of its own to act on: the
  client's creds ARE the authorization. Supporting it means collecting them at
  `/authorize` behind a real login page and carrying them in an encrypted
  refresh token (roughly what `netsapiens-mcp` does). That is a deliberate
  security change, not a config tweak.

## Deploy (CI only, never from a workstation)
Push to `main` triggers `.github/workflows/deploy.yml`, which authenticates to
`phoneware-edge` via **Workload Identity Federation** (SA
`edge-tf-deployer`, no static keys) and runs `gcloud builds submit
--config=cloudbuild.yaml`. Cloud Build then: runs `pytest` (gate), builds the
image, pushes to Artifact Registry, and `gcloud run deploy`s `bandwidth-mcp` in
`us-central1`. WIF is allowlisted for this repo in the monorepo's
`infra/terraform/github-actions.tf`.

The **live tool surface and config are set in `cloudbuild.yaml`** (`--set-env-vars`),
not in code. Current deployment:
- `BW_MCP_PROFILE=numbers,numbers-write,billing`: the carrier/reseller surface.
- `BW_MCP_EXCLUDE_TOOLS=clearCredentials,createRegistration,uploadMedia,deleteMedia,createApplication`.
- `BW_ACCOUNT_ID=5011369` pins the primary account (the OAuth token lists
  `5011296` first, which is NOT the account the numbers live on).
- **Voice, messaging, and lookup are deliberately off**: those creds 403 (Voice
  runs on NetSapiens, texting goes through Clerk/NS), and TN Lookup is not
  enabled on the account. Re-add a profile here if Bandwidth enables the product.

Only `BW_GATEWAY_TOKEN` is mounted as a secret (the HMAC signing key). Bandwidth
API creds are NOT mounted anywhere server-side; they live in the client's
connector config.

## OAuth model (`serve.py`)
`serve.py` is a small OAuth 2.1 authorization server wrapping the streamable-http
transport:
1. Client hits `/authorize`; we auto-approve (no login page) and return a
   short-lived signed code. The code alone grants nothing.
2. Client calls `/token` with the code, PKCE verifier, and its client
   id/secret: **the Bandwidth API creds**. We validate them the only way that
   means anything: a client-credentials exchange against Bandwidth. Success mints
   the upstream token into in-process config and issues our own signed bearer +
   refresh token.
3. `/mcp` requires our bearer. Tools attach the live upstream token per-request
   (`servers.py` `_LiveConfigTokenAuth`), so mint/refresh needs no restart.

No Bandwidth secret is stored at rest. On container restart the first `/mcp`
call 401s, the client refreshes, and the mint re-runs. Bandwidth webhook
callback routes stay open (Bandwidth can't present our bearer, and they deliver
async events, not account control).

Per the 2026-07-28 authorization spec:
- **RFC 9207**: every authorization response carries `iss`, success or error,
  and the AS metadata advertises
  `authorization_response_iss_parameter_supported`.
- **RFC 8707 resource indicators**: `resource` is validated at `/authorize` and
  `/token` and bound into the issued token as `aud`. A foreign origin gets
  `invalid_target` instead of a token. The check compares origin only, on
  purpose: clients disagree about the `/mcp` path and trailing slashes, and
  rejecting on that would break a working connector for no security gain.
- **Dynamic Client Registration is not implemented and should stay that way.**
  The revision deprecates DCR in favor of Client ID Metadata Documents anyway,
  and neither fits this server: `client_id` here IS the Bandwidth API client id,
  which is the whole authorization model.

## Uniform tool gating
`app.py` builds all tool sources, then walks `list_tools()` and removes any tool
that the env config (`BW_MCP_TOOLS` / `BW_MCP_PROFILE` / `BW_MCP_EXCLUDE_TOOLS`)
blocks. OpenAPI tools are also pre-filtered by a route map, but hand-written
registrations ignore that, so this post-prune is what makes the deployment env
the single source of truth for the whole surface. Filter precedence lives in
`config.py:get_enabled_tools` (`--tools` > `BW_MCP_TOOLS` > `--profile` >
`BW_MCP_PROFILE` > default set; `full` loads everything).

## Gotchas worth knowing
- **Run from `src/`, not an installed package** (see above).
- **A modern request needs BOTH the `MCP-Protocol-Version` header and the full
  `_meta` envelope.** Send the `_meta` version alone and the server quietly
  answers in handshake-era shape (no `resultType`, no `ttlMs`, and
  `server/discover` comes back "Method not found"); send the header without
  `io.modelcontextprotocol/clientCapabilities` in `_meta` and you get a 400
  naming the missing key. Both were mistaken for "FastMCP 4 has not implemented
  discover yet" while writing `test/test_gateway.py`. It has.
- **`Tools (0): None` at startup is not a failure.** That banner summarizes the
  OpenAPI-derived surface, which is empty on the numbers/billing profile, and it
  prints before the hand-written tools register. The live surface is 31 tools.
- **Dashboard XML API paging**: `/portins`, `/portouts`, `/orders` 404 without
  explicit `page` + `size`. The hand-written tools always send them.
- **`lnpchecker` wants E.164** (`+1NXXNXXXXXX`); every other Dashboard endpoint
  wants bare 10-digit. `checkPortability` handles the conversion.
- **Port-ins need a subscriber name AND full service address** (as they appear
  on the losing carrier's bill), so `createPortInOrder` validates up front via
  `_port_in_problems` rather than spending a live carrier write on a 400.
- **LOA upload is raw bytes, not multipart**: POST the document with its own
  `Content-Type` to `portins/{id}/loas`, then PUT `<FileMetaData>` to
  `.../loas/{filename}/metadata` to mark it as the LOA. `uploadPortInLoa` does
  both, and reports a metadata failure instead of raising (the file is already
  stored by then).
- **Report instances finish as `Status: Ready`**, not the documented
  `COMPLETED`. Poll for `Ready`.
- **Empty response bodies mean "nothing here"** on several Dashboard endpoints
  (e.g. a port-in with no notes); `_dashboard_json` returns `{"empty": true}`.
- **Multi-account**: `token.accounts[0]` is not the main account. `account_id`
  params are validated against the token's `accounts` claim so a typo can't
  silently hit the wrong account.
- **Single instance, stateful**: the event store and the minted upstream token
  are in memory. Cloud Run runs `--min-instances=1 --max-instances=1`; do not
  scale to zero or fan out, or callback state and the token split.
- **`setCredentials` is stdio-only** (it takes secret material as tool args). The
  hosted transport never registers it; auth there is the OAuth `/token` mint.

## Conventions / rules
- **Adding a hand-written tool**: put it in a `src/tools/*.py` module with a
  `register_*_tools(mcp, config)` function, wire the call in `app.py`'s lifespan,
  and add its name to the right profile in `profiles.py`. Give it a
  `ToolAnnotations` read/write hint. Build XML bodies with `ElementTree`
  (`SubElement`), never string interpolation, so user values can't inject XML.
- **Changing the live surface**: edit `cloudbuild.yaml`'s `--set-env-vars`
  (profiles + excludes). `--set-env-vars` REPLACES the whole env, so keep the
  list complete (`BW_MCP_BASE_URL` must ride along or a deploy wipes it).
- **Never commit secrets.** Bandwidth creds live in the claude.ai connector;
  `BW_GATEWAY_TOKEN` lives in Secret Manager.
- **Deploys run in CI, never locally.** No `gcloud run deploy` /
  `gcloud builds submit` from a workstation.
- **Tests gate everything.** `pytest` runs in PR CI (`test-pr.yml`, py3.10-3.14
  on ubuntu + windows) and again as the first Cloud Build step before deploy.
  Keep it green.
- No time estimates in plans (phase/priority order instead).

## Keeping upstream in sync
This is a fork, so upstream fixes land by merging `Bandwidth/mcp-server`. Our
delta is additive (new `src/tools/` modules, `serve.py`, `cloudbuild.yaml`,
`.github/`, profile entries) and mostly avoids editing upstream files, which
keeps merges clean. `src/specs/AGENTS.md` is the one upstream doc that has
drifted from our deployment (it still describes the voice/messaging surface);
treat this guide and `README.md` as authoritative for what actually ships.
