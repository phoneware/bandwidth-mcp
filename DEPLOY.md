# Deploying the Phoneware-hosted Bandwidth MCP

Vendored fork of the official [`Bandwidth/mcp-server`](https://github.com/Bandwidth/mcp-server)
(Bandwidth ships it as a self-run Beta package; there is no Bandwidth-hosted
version). We run it single-tenant on Cloud Run in `streamable-http` mode with a
static Bearer gate.

## Architecture / security
- `serve.py` builds the FastMCP streamable-http ASGI app and wraps it so `/mcp`
  requires `Authorization: Bearer $BW_GATEWAY_TOKEN`. Only Phoneware's Claude
  connection (which sends that header) can drive the MCP. Bandwidth callback
  routes + health stay open (Bandwidth can't present our token).
- Single-tenant: one instance uses Phoneware's own Bandwidth
  `BW_CLIENT_ID`/`BW_CLIENT_SECRET`. No per-user OAuth.
- Stateful (in-memory event store + webhook callbacks): `--min-instances=1`,
  `--max-instances=1` (do not scale to zero or fan out, callback state would split).

## Coverage note
This server does Voice, Messaging, Lookup, and Recordings. It does **not** do
phone-number search/order/activation/release or e911 (those live in Bandwidth's
`band` CLI + Dashboard/Numbers API). Number provisioning would be a separate
companion MCP over `api.bandwidth.com/api/v2`.

## One-time setup (needs an operator / the owner)
1. **Bandwidth API creds.** Create/obtain the Bandwidth API `client_id` +
   `client_secret` (Bandwidth Dashboard). Store in Secret Manager (phoneware-edge):
   ```
   printf %s "<client-id>"     | gcloud secrets create bandwidth-client-id     --data-file=- --project=phoneware-edge
   printf %s "<client-secret>" | gcloud secrets create bandwidth-client-secret --data-file=- --project=phoneware-edge
   ```
2. **Gateway token** (the inbound Bearer the Claude client will send):
   ```
   openssl rand -hex 32 | tr -d '\n' | gcloud secrets create bandwidth-gateway-token --data-file=- --project=phoneware-edge
   ```
3. **Artifact Registry repo** `bandwidth-mcp` (us-central1), if not present.
4. **Cloud Build GitHub trigger** on this repo's `main` -> `cloudbuild.yaml`
   (authorize the Cloud Build GitHub App on `phoneware/bandwidth-mcp` first).
5. Grant the Cloud Run runtime SA `roles/secretmanager.secretAccessor` on the
   three secrets.

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
Point the Claude MCP client at `https://<url>/mcp` (streamable-http) with header
`Authorization: Bearer <bandwidth-gateway-token>`.

## Verify
- Unauthenticated `GET/POST /mcp` -> `401 Unauthorized`.
- With the bearer -> MCP handshake succeeds; a read-only tool (e.g. list
  applications / list numbers) returns data.

## Local smoke test
Run from `src/` (do NOT `pip install .` — the upstream pyproject omits some
modules like `urls`; upstream runs from src/ and so do we):
```
python3 -m venv .venv && . .venv/bin/activate
pip install "fastmcp~=3.2" "mcp~=1.24" "httpx~=0.28.0" "pyyaml~=6.0.0" "werkzeug>=3.1.4" uvicorn
BW_GATEWAY_TOKEN=$(openssl rand -hex 32) BW_MCP_TRANSPORT=streamable-http PYTHONPATH=src python serve.py
# In another shell: POST /mcp without the bearer must 401; with it, non-401.
```
