# Deploying the Phoneware-hosted Bandwidth MCP

Vendored fork of the official [`Bandwidth/mcp-server`](https://github.com/Bandwidth/mcp-server)
(Bandwidth ships it as a self-run Beta package; there is no Bandwidth-hosted
version). We run it single-tenant on Cloud Run in `streamable-http` mode behind
an OAuth 2.1 gate, with **Google sign-in** as the identity check. The Bandwidth
API credential lives on the server, in Secret Manager, and nowhere else.

## Architecture / security
- `serve.py` is an **OAuth 2.1 authorization server** in front of the
  streamable-http transport, and an **OAuth 2.0 client to Google** behind it.
- **The Bandwidth credential is the carrier account.** It is a
  `client_credentials` pair with no user auth of its own: an exchange with it
  needs no login and returns a token carrying Porting, Ordering, Number
  Activation, Billing Reports, Configuration and Regulatory across every
  account on it. Whoever holds it can port numbers away or submit disconnects.
  It therefore lives **only** in Secret Manager (`bandwidth-client-id`,
  `bandwidth-client-secret`), mounted into this service. Never in a repo, a
  connector field, or a plugin. It is never sent to a client.
- **Google is the gate, not the credential.** Bandwidth has no per-user
  identity, so Google's only job is to produce a verified email.
  `/authorize` does not approve anything; it hands the browser to Google.
  `/auth/google/callback` verifies the `id_token` and matches the address
  against `BW_OAUTH_ALLOWED_DOMAINS` plus optional `BW_OAUTH_ALLOWED_EMAILS`.
  An address outside the allowlist is refused there and never reaches a tool.
  The service **refuses to boot** on an empty allowlist: unconfigured means
  nobody, never everybody.
- **Our bearer names a person.** The verified email rides on the authorization
  code and into the issued access and refresh tokens, so an `/mcp` call is
  attributable to someone rather than to "whoever has the key". `/token`
  refreshes and `/mcp` bearer requests recheck the current allowlist every time.
- **Dynamic Client Registration is implemented** (`/register`, RFC 7591), which
  is possible only because `client_id` is ours to mint again. A connector needs
  nothing filled in. Registrations are signed blobs rather than stored rows, so
  a redeploy does not invalidate an already-connected client. New registrations
  have no calendar expiry; old signed blobs that include `exp` still honor it.
  `/register` is open by design, and the redirect-URI policy is what keeps that
  safe: without it anyone could register `redirect_uri=https://evil.example` and
  collect a real phoneware.us sign-in.
- Clients are **public** (`token_endpoint_auth_method: none`): PKCE binds the
  exchange, so there is no client secret for a connector to store or leak.
- `BW_GATEWAY_TOKEN` (Secret Manager) is the HMAC signing key for codes,
  bearers, refresh tokens and client ids. It never leaves the server. Rotating
  it invalidates every signed blob, which is the intended blunt revoke.
- Refresh tokens are signed credentials with no normal calendar expiry. The
  only durable OAuth state is a small Firestore cursor document per refresh
  family containing the current and previous `seq`/`jti` values plus the
  previous successor. That is enough to make a lost refresh response
  idempotently recoverable across deploys and to revoke on older replay, without
  storing bearer or refresh-token strings.
- Bandwidth callback routes + health stay open (they deliver async events, not
  account control).
- Authorization follows the 2026-07-28 MCP spec: `iss` on every authorization
  response (RFC 9207) and `resource` indicators validated and bound into the
  token audience (RFC 8707).
- Tools attach the upstream token per-request from the live config
  (`servers.py` `_LiveConfigTokenAuth`), so mint/refresh needs no restart.
  The upstream token is minted from the server-side credential on demand and
  refreshed as it ages. A transient upstream failure with a valid bearer returns
  `temporarily_unavailable` instead of an invalid-token challenge.
- **The MCP protocol is stateless** (2026-07-28: no handshake, no session id),
  and handshake-era clients are served sessionlessly too. That removes session
  affinity as a reason to pin one instance.
  The Cloud Run runtime service account
  `859122914438-compute@developer.gserviceaccount.com` already has
  `roles/datastore.user` in the project, which is enough for the Firestore
  refresh cursor ledger.

## Coverage note
The live deployment runs the **numbers / porting / carrier / billing** surface
(`BW_MCP_PROFILE=numbers,numbers-write,billing`): port-in/out orders,
available-number search, number orders, sites, SIP peers, per-number detail,
portability, carrier writes (order/disconnect/port), and usage/billing reports,
over Bandwidth's XML Dashboard API (`api.bandwidth.com/api/v2`). Voice,
messaging, and lookup are built but **off** in the deployment (those creds 403;
Phoneware's voice is NetSapiens and texting is Clerk/NS). e911 provisioning is
not exposed. The surface is set in `cloudbuild.yaml`, see `CLAUDE.md`.

## One-time setup (needs an operator / the owner)
1. **Google OAuth client.** Google Cloud console (`phoneware-edge`) -> APIs &
   Services -> Credentials -> Create OAuth client ID -> Web application.
   Authorized redirect URI, exactly one, and it must equal `BW_MCP_BASE_URL`
   + `/auth/google/callback`:
   ```
   https://mcp.bandwidth.phoneware.cloud/auth/google/callback
   ```
   Both halves go into Secret Manager. The id is not secret, but keeping the
   pair together means one place to look and one place to rotate; splitting it
   across GCP and a CI variable just creates two ways to half-configure the gate.
   ```
   printf %s '<client-id>'     | gcloud secrets create bandwidth-mcp-google-client-id --data-file=- --project=phoneware-edge
   printf %s '<client-secret>' | gcloud secrets create bandwidth-mcp-google-secret    --data-file=- --project=phoneware-edge
   ```
2. **Bandwidth API creds.** Create/obtain the Bandwidth API `client_id` +
   `client_secret` (Bandwidth Dashboard). They go in **Secret Manager and
   nowhere else**: not in a connector field, not in a plugin `.mcp.json`, not
   in this repo. Anyone holding them holds the carrier account.
   ```
   printf %s '<CLI-...>'  | gcloud secrets create bandwidth-client-id     --data-file=- --project=phoneware-edge
   printf %s '<secret>'   | gcloud secrets create bandwidth-client-secret --data-file=- --project=phoneware-edge
   ```
   To rotate, add a new version (`gcloud secrets versions add`) and redeploy;
   the service pins `:latest`.
3. **Signing key** (HMAC key for OAuth codes/bearers/client ids; server-side only):
   ```
   openssl rand -hex 32 | tr -d '\n' | gcloud secrets create bandwidth-gateway-token --data-file=- --project=phoneware-edge
   ```
4. **Artifact Registry repo** `bandwidth-mcp` (us-central1), if not present.
5. Grant the Cloud Run runtime SA `roles/secretmanager.secretAccessor` on all
   five secrets (`bandwidth-gateway-token`, `bandwidth-client-id`,
   `bandwidth-client-secret`, `bandwidth-mcp-google-client-id`,
   `bandwidth-mcp-google-secret`). Nothing credential-shaped is configured in
   GitHub; a missing secret fails the Cloud Run deploy rather than shipping a
   half-open gate.
6. **Who may sign in** is `BW_OAUTH_ALLOWED_DOMAINS` in `cloudbuild.yaml`
   (`phoneware.us`). Add a named outside collaborator with
   `BW_OAUTH_ALLOWED_EMAILS` rather than widening the domain list.

## Deploy
Push to `main` -> the Cloud Build trigger builds + deploys. After the first
deploy, grab the service URL and (for voice/messaging callbacks) set it:
```
gcloud run services update bandwidth-mcp --region=us-central1 \
  --update-env-vars=BW_MCP_BASE_URL=https://<service-url>
```
Optionally map DNS `mcp.bandwidth.phoneware.cloud` (add a CNAME in the monorepo
`godaddy.tf`, mirroring `mcp.peplink`).

## Connect
claude.ai -> Settings -> Connectors -> Add custom connector:
- **URL**: `https://mcp.bandwidth.phoneware.cloud/mcp`
- **Client ID / Client Secret**: leave both blank. The connector registers
  itself. Filling them in is what the old model required and it is no longer
  how this works.

The browser lands on a Google sign-in. Sign in with a `phoneware.us` account
and the connector finishes. Claude Code uses the same URL and the same flow
with no extra flags; `claude mcp add --transport http bandwidth <url>` is the
whole setup.

Anyone who connected under the old model must reconnect once: their cached
`client_id` was the Bandwidth credential, which is no longer a client id.

## Verify
- `GET /.well-known/oauth-authorization-server` advertises a
  `registration_endpoint` (without it, clients report *"Automatic client
  registration isn't supported"*).
- `POST /register` with a `claude.ai` redirect URI -> `201` and a `client_id`;
  with `https://evil.example` -> `400 invalid_redirect_uri`.
- `GET /authorize` with that client -> `302` to `accounts.google.com`, and no
  `code` in the location. Nothing is approved before a sign-in.
- `GET /authorize` with an unregistered `client_id` -> `401`.
- `POST /mcp` without a bearer -> `401` with `WWW-Authenticate: Bearer
  resource_metadata=...`.
- Sign in with a non-allowlisted Google account -> `403` refusal page, and no
  redirect back to the client.
- Sign in with a `phoneware.us` account -> a read-only tool returns data, and
  the issued bearer carries that address as `sub`.

## Local smoke test
Run from `src/` (do NOT `pip install .` — the upstream pyproject omits some
modules like `urls`; upstream runs from src/ and so do we):
```
python3 -m venv .venv && . .venv/bin/activate
pip install "fastmcp~=3.2" "mcp~=1.24" "httpx~=0.28.0" "pyyaml~=6.0.0" "werkzeug>=3.1.4" uvicorn
BW_GATEWAY_TOKEN=$(openssl rand -hex 32) BW_MCP_TRANSPORT=streamable-http PYTHONPATH=src python serve.py
# In another shell: POST /mcp without the bearer must 401; with it, non-401.
```
