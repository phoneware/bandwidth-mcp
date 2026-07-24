# Bandwidth MCP (Phoneware fork)

Phoneware's **vendored fork** of the official
[`Bandwidth/mcp-server`](https://github.com/Bandwidth/mcp-server), an MCP server
that exposes Bandwidth's APIs to an AI agent. Upstream ships it as a self-run
beta Python package with no hosted version. This fork runs it **single-tenant on
Google Cloud Run behind an OAuth 2.1 gateway**, serving `claude.ai` (and
header-capable clients like Claude Code) at:

```
https://mcp.bandwidth.phoneware.cloud/mcp
```

Phoneware is a carrier reseller on Bandwidth, so the fork's reason for being is
the **numbers / porting / carrier / billing** surface, which upstream does not
ship. Voice and messaging remain available for local use but are off in the live
deployment (Phoneware's voice runs on NetSapiens and texting goes through
Clerk/NS).

- **Agent guide** (working in this repo): [`CLAUDE.md`](CLAUDE.md)
- **Deploy + security model**: [`DEPLOY.md`](DEPLOY.md)
- **Per-tool agent reference**: [`src/specs/AGENTS.md`](src/specs/AGENTS.md)
  (still written around the upstream voice/messaging surface; the deployed
  surface is numbers/porting/billing, see below)

## What this fork changes

Everything below is Phoneware's delta on top of upstream. This is "the changes".

| Area | Upstream | This fork |
|---|---|---|
| Hosting | self-run stdio package | hosted OAuth 2.1 gateway on Cloud Run (`serve.py`) |
| Auth | env creds or stdio `setCredentials` | Bandwidth creds live in the **client's connector config**; validated at `/token` by minting an upstream token; nothing credential-shaped at rest |
| Numbers / porting | none (Numbers API is XML; `from_openapi` can't drive it) | hand-written XML tools: port-in/out, inventory search, orders, sites, SIP peers, per-number detail, portability, carrier writes (`src/tools/numbers.py`) |
| Billing | none | async usage/billing reports engine (`src/tools/reports.py`) |
| Accounts | first account only | multi-account: one client ID, `account_id` per tool, validated against the token claims |
| Client UX | tools ungrouped ("Other") | read/write `ToolAnnotations` so clients group tools |
| Deploy | n/a | GitHub Actions + Workload Identity Federation, pytest-gated Cloud Build; never from a workstation |

## The deployed surface

The live tool set is chosen entirely by environment in
[`cloudbuild.yaml`](cloudbuild.yaml), not in code:

- `BW_MCP_PROFILE=numbers,numbers-write,billing`: the carrier/reseller surface.
- Excludes `clearCredentials, createRegistration, uploadMedia, deleteMedia, createApplication`.
- `BW_ACCOUNT_ID=5011369` pins the primary account (the OAuth token lists another
  account first).
- Voice, messaging, and lookup profiles are deliberately dropped (403 /
  "account not authorized").

The profiles themselves live in [`src/profiles.py`](src/profiles.py):

| Profile | Tools (high level) |
|---|---|
| `numbers` | port-in/out orders + notes, `searchAvailableNumbers`, number orders, sites, SIP peers, `getPhoneNumberDetail`, `checkPortability`, CNAM reads (`listLidbOrders`, `getLidbOrder`), plus `listPhoneNumbers` / `listApplications` |
| `numbers-write` | `orderPhoneNumbers`, `disconnectPhoneNumbers`, `createPortInOrder`, `supplementPortInOrder`, `cancelPortInOrder`, `createLidbOrder` (set CNAM) — real, billable carrier actions |
| `billing` | `listReports`, `getReport`, report instances (create/get/list), `downloadReportFile` |
| `voice` / `messaging` / `lookup` / `recordings` / `onboarding` | upstream surfaces, available locally, off in the deployment |

`listAccounts` is always available. See
[`src/specs/AGENTS.md`](src/specs/AGENTS.md) for argument-level tool docs (note
its framing predates the numbers surface).

## Connecting

**claude.ai** → Settings → Connectors → Add custom connector:
- **URL**: `https://mcp.bandwidth.phoneware.cloud/mcp`
- **Client ID / Client Secret** (advanced settings): the **Bandwidth API creds**
  (`CLI-…` id + secret from the Bandwidth Dashboard).

claude.ai runs the OAuth flow (instant redirect, no login page) and the server
validates the creds against Bandwidth on every token exchange. Header-capable
clients (Claude Code) use the same URL and flow. Full detail and the security
rationale are in [`DEPLOY.md`](DEPLOY.md).

## Deploy

CI/CD only. Push to `main` triggers
[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml), which
authenticates to `phoneware-edge` via Workload Identity Federation and runs
`gcloud builds submit --config=cloudbuild.yaml`. Cloud Build gates on `pytest`,
builds the image, pushes to Artifact Registry, and deploys `bandwidth-mcp` to
Cloud Run (single warm instance; the event store and minted token live in
memory). Never deploy from a workstation. See [`DEPLOY.md`](DEPLOY.md).

## Local development

Python, no pnpm. Run **from `src/`**; do not `pip install .` the package (the
upstream `pyproject.toml` omits modules like `urls`, so an installed package
can't import them).

```sh
pip install ".[dev]"
python -m pytest -q                        # the suite CI gates on

# stdio (default): a local MCP client spawns this
BW_CLIENT_ID=... BW_CLIENT_SECRET=... PYTHONPATH=src python src/app.py

# hosted gateway (what Cloud Run runs)
BW_GATEWAY_TOKEN=$(openssl rand -hex 32) BW_MCP_TRANSPORT=streamable-http \
  PYTHONPATH=src python serve.py
```

### Choosing tools locally

Precedence: `--tools` > `BW_MCP_TOOLS` > `--profile` > `BW_MCP_PROFILE` > default
set. `full` loads everything.

```sh
BW_MCP_PROFILE=numbers,billing         # a couple of profiles
BW_MCP_TOOLS=listPortInOrders,getPortInOrder   # explicit allowlist
BW_MCP_EXCLUDE_TOOLS=disconnectPhoneNumbers    # denylist (wins over the above)
```

Profiles keep the agent's context small by cherrypicking operationIds instead of
loading whole specs. See [`common_use_cases.md`](common_use_cases.md) for the
upstream tool-picking guide.

### Local callback tunnel (dev only)

Voice/messaging callbacks need Bandwidth to reach the server over a public URL.
For local work set `BW_MCP_DEV_TUNNEL=1` (with a non-stdio transport and no
`BW_MCP_BASE_URL`) and the server opens an ephemeral `cloudflared` tunnel and
wires callbacks to it. Requires `cloudflared` on `PATH`. Development only.

## Repo map

```
serve.py            hosted OAuth 2.1 gateway (Phoneware)
cloudbuild.yaml     pytest gate -> image -> Cloud Run (deployment-managed env)
CLAUDE.md           agent guide for working in this repo
DEPLOY.md           deploy + security model
src/                the server: see src/CLAUDE.md
src/tools/          hand-written XML Dashboard + voice tools: see src/tools/CLAUDE.md
test/               pytest suite: see test/CLAUDE.md
```

## Credit

The upstream server, its OpenAPI-derived tools, the profile system, and the
Build-registration flow are Bandwidth's work
([`Bandwidth/mcp-server`](https://github.com/Bandwidth/mcp-server)). Upstream
fixes land here by merging that repo; our delta is additive to keep merges clean.
