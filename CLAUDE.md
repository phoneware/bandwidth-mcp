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
- **Dashboard XML API paging**: `/portins`, `/portouts`, `/orders` 404 without
  explicit `page` + `size`. The hand-written tools always send them.
- **`lnpchecker` wants E.164** (`+1NXXNXXXXXX`); every other Dashboard endpoint
  wants bare 10-digit. `checkPortability` handles the conversion.
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
