# Deploying the Phoneware-hosted Bandwidth MCP

Vendored fork of the official [`Bandwidth/mcp-server`](https://github.com/Bandwidth/mcp-server)
(Bandwidth ships it as a self-run Beta package; there is no Bandwidth-hosted
version). We run it single-tenant on Cloud Run in `streamable-http` mode behind
an OAuth 2.1 gate; the Bandwidth API creds live in the client's connector
config, not on the server.

## Architecture / security
- `serve.py` is an **OAuth 2.1 authorization server** in front of the
  streamable-http transport. Bandwidth's API creds are server-to-server with no
  user auth of their own, so they are NOT preloaded here; they live in the
  client's connector config (claude.ai custom connector Client ID/Secret
  fields) and only transit `/token`, where the server validates them by
  minting an upstream Bandwidth token with them. Success mints the upstream
  token into process memory and issues our own short-lived signed bearer
  (+ refresh token). A leaked URL grants nothing: `/authorize` auto-approves
  but its codes are worthless without the creds.
- Tools attach the upstream token per-request from the live config
  (`servers.py` `_LiveConfigTokenAuth`), so mint/refresh needs no restart.
  Refresh cycles (~50 min) re-present the creds and re-mint upstream.
- `BW_GATEWAY_TOKEN` (Secret Manager) is the HMAC signing key for codes and
  bearers. It never leaves the server. Bandwidth callback routes + health
  stay open (they deliver async events, not account control).
- Single-tenant: whoever holds Phoneware's Bandwidth creds gets Phoneware's
  account; there is no per-user identity at Bandwidth.
- Stateful (in-memory event store + webhook callbacks + minted token):
  `--min-instances=1`, `--max-instances=1` (do not scale to zero or fan out).

## Coverage note
This server does Voice, Messaging, Lookup, and Recordings. It does **not** do
phone-number search/order/activation/release or e911 (those live in Bandwidth's
`band` CLI + Dashboard/Numbers API). Number provisioning would be a separate
companion MCP over `api.bandwidth.com/api/v2`.

## One-time setup (needs an operator / the owner)
1. **Bandwidth API creds.** Create/obtain the Bandwidth API `client_id` +
   `client_secret` (Bandwidth Dashboard). They go in the **client's connector
   config** (claude.ai Client ID/Secret fields), NOT in Secret Manager and
   never on the server.
2. **Signing key** (HMAC key for OAuth codes/bearers; server-side only):
   ```
   openssl rand -hex 32 | tr -d '\n' | gcloud secrets create bandwidth-gateway-token --data-file=- --project=phoneware-edge
   ```
3. **Artifact Registry repo** `bandwidth-mcp` (us-central1), if not present.
4. **Cloud Build GitHub trigger** on this repo's `main` -> `cloudbuild.yaml`
   (authorize the Cloud Build GitHub App on `phoneware/bandwidth-mcp` first).
5. Grant the Cloud Run runtime SA `roles/secretmanager.secretAccessor` on the
   signing-key secret.

## Deploy
Push to `main` -> the Cloud Build trigger builds + deploys. After the first
deploy, grab the service URL and (for voice/messaging callbacks) set it:
```
gcloud run services update bandwidth-mcp --region=us-central1 \
  --update-env-vars=BW_MCP_BASE_URL=https://<service-url>
```
Optionally map DNS `mcp.bandwidth.phoneware.cloud` (add a CNAME in the monorepo
`godaddy.tf`, mirroring `mcp.peplink`).

## Connect (the owner handles the client side)
claude.ai → Settings → Connectors → Add custom connector:
- **URL**: `https://mcp.bandwidth.phoneware.cloud/mcp`
- **Client ID / Client Secret** (advanced settings): the **Bandwidth API
  creds** (`CLI-...` id + secret from the Bandwidth Dashboard).
claude.ai runs the OAuth flow (instant redirect, no login page) and the
server validates the creds against Bandwidth on every token exchange.
Header-capable clients (Claude Code) use the same URL and OAuth flow.

## Verify
- `POST /mcp` without a bearer -> `401` with `WWW-Authenticate: Bearer
  resource_metadata=...`.
- `POST /token` with garbage creds -> `401 {"error":"invalid_client"}`
  (proves upstream validation is wired).
- Connect claude.ai with the real creds -> a read-only tool (e.g. lookup)
  returns data.

## Local smoke test
Run from `src/` (do NOT `pip install .` — the upstream pyproject omits some
modules like `urls`; upstream runs from src/ and so do we):
```
python3 -m venv .venv && . .venv/bin/activate
pip install "fastmcp~=3.2" "mcp~=1.24" "httpx~=0.28.0" "pyyaml~=6.0.0" "werkzeug>=3.1.4" uvicorn
BW_GATEWAY_TOKEN=$(openssl rand -hex 32) BW_MCP_TRANSPORT=streamable-http PYTHONPATH=src python serve.py
# In another shell: POST /mcp without the bearer must 401; with it, non-401.
```
