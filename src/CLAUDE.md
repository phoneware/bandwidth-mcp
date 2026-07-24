# `src/`: server internals

The MCP server itself. Read the root `CLAUDE.md` first for the fork story and
deploy model; this file is how the runtime is wired.

## Startup flow (`app.py`)
`mcp = FastMCP(name=..., lifespan=lifespan)`. Everything happens inside the
lifespan so it runs on FastMCP's event loop:

1. `get_enabled_tools()` / `get_excluded_tools()` read the env/CLI filter
   (`config.py`).
2. `load_config()` + `authenticate_config()` read env and, if `BW_CLIENT_ID`/
   `BW_CLIENT_SECRET` are present, do the startup OAuth token exchange. In the
   hosted gateway these are absent at boot; the token is minted later by
   `serve.py` at `/token`.
3. Optional dev tunnel (`BW_MCP_DEV_TUNNEL`) opens a `cloudflared` tunnel and
   sets `BW_MCP_BASE_URL`.
4. `create_bandwidth_mcp()` mounts the OpenAPI-derived tools (`servers.py`).
5. `register_*_tools()` adds the hand-written tools (credentials, callbacks,
   voice, discovery, numbers, reports).
6. **Uniform gating**: walk `list_tools()` and `remove_tool()` anything the
   filter blocks. This is what makes the env config authoritative for the
   hand-written tools too (they register unconditionally).
7. Auto-configure voice callbacks if a base URL + voice app + token exist.
8. `build_instructions()` sets the MCP instructions from the final tool list.

`register_callback_routes(mcp, event_store)` runs at **module level**, not in the
lifespan, so Starlette has the Bandwidth webhook routes in its table before
`mcp.run()`.

## Tool sources

### OpenAPI-derived (`servers.py`)
`api_server_info` lists Bandwidth's public specs. `_create_server()` fetches a
spec, rewrites its server host via `swap_host()` (so `BW_ENVIRONMENT` and
per-host overrides apply), and builds `FastMCP.from_openapi`. Two details matter:

- **`_LiveConfigTokenAuth`** attaches the *current* `BW_ACCESS_TOKEN` from the
  shared config on every request instead of baking it into headers at startup.
  That is what lets `serve.py` mint/refresh the upstream token after boot with no
  restart.
- **`_ensure_content_type`** sniffs the body and sets `Content-Type` when
  `from_openapi` omits it (e.g. `application/xml` for `updateCallBxml`), which
  Bandwidth otherwise 415s.
- **`_annotate_component`** stamps read/write `ToolAnnotations` from the HTTP
  method so clients can group tools.

The Numbers spec is intentionally NOT loaded here (it is XML; `from_openapi`
sends JSON). Numbers live as hand-written tools instead. `insights` excludes
`listCalls`/`listCall` because they collide with the voice spec.

### Hand-written (`tools/`)
See `src/tools/CLAUDE.md`.

## Config + profiles (`config.py`, `profiles.py`)
`profiles.py` cherrypicks operationIds into named presets rather than loading
whole specs (430+ tools). `DEFAULT_TOOLS` = voice + messaging + lookup +
`_ALWAYS_TOOLS` (`setCredentials`, `clearCredentials`, `listAccounts`). The live
deployment uses `numbers,numbers-write,billing` instead (set in `cloudbuild.yaml`).

`get_enabled_tools()` precedence: `--tools` > `BW_MCP_TOOLS` > `--profile` >
`BW_MCP_PROFILE` > `DEFAULT_TOOLS`. `full` (checked on the raw string) means "no
filter, load everything". `None` return = no filter.

## Host resolution (`urls.py`)
Prod is default. `BW_ENVIRONMENT=test|uat` flips API + Voice hosts in one shot;
a per-host override (`BW_API_URL`, `BW_VOICE_URL`, ...) wins over it. The
Dashboard XML API is served from the API gateway at `{api_base}/api/v2`
(`dashboard_api_base()`), so there is no separate dashboard URL. `swap_host()`
rewrites OpenAPI spec server URLs through the same resolver so generated tools
respect the env too.

## Events + callbacks (`event_store.py`, `callbacks.py`)
`EventStore` is an in-memory ring buffer per `event_type:key` with a TTL (~1h),
per-session read cursors, and `CallState` for live calls. `CallState.try_set_bxml`
is first-write-wins, which is how `respondToCallback` stays safe across sessions.
`callbacks.py` registers the Starlette routes Bandwidth POSTs webhooks into; they
push onto the store, and `getCallbackEvents` / `getInboundMessages` read it back.
Because it is in-memory, the service runs as a single warm instance (see root
`CLAUDE.md`).

## OAuth (`oauth.py`)
`get_oauth_token()` does the Bandwidth client-credentials exchange and returns
the access token plus the `accounts` claim. Used both at startup
(`authenticate_config`) and by the hosted gateway's `/token` mint (`serve.py`).
