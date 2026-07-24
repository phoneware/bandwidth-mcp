# `src/tools/`: hand-written tools

Tools that the OpenAPI path can't produce. Each module exposes a
`register_*_tools(mcp, config)` (or `(mcp, event_store, config)`) function called
from `app.py`'s lifespan. Unlike the OpenAPI-derived tools, these register
unconditionally, so `app.py` prunes them afterward to honor the env filter.

## Why these exist
Most of Phoneware's value is here. Bandwidth's **Numbers / Dashboard API is
XML**, and `FastMCP.from_openapi` sends JSON, so the carrier-reseller surface
(porting, inventory, sites, reports) has to be hand-written: authenticated XML
requests against `{api_base}/api/v2/accounts/{accountId}/…`, parsed back to JSON.

## Modules
- **`credentials.py`**: `setCredentials` (stdio only; takes client id/secret and
  mints a token) and `clearCredentials`. Under the hosted transport, auth is the
  OAuth `/token` mint in `serve.py`, so `setCredentials` is not registered.
- **`discovery.py`**: `listAccounts`, `listApplications`, `listPhoneNumbers`,
  `createApplication`. Also the shared XML helpers: `_dashboard_get`,
  `_xml_text`, and **`_resolve_account`** (below). `listPhoneNumbers` tries
  `/tns` (Numbers role) then falls back to `/inserviceNumbers` (inservice role),
  because different creds hold different roles.
- **`numbers.py`**: the reseller surface. Read: port-in/out orders + notes,
  available-number search, number orders, sites, SIP peers, per-number detail,
  `checkPortability`. Write (`numbers-write` profile): `orderPhoneNumbers`,
  `disconnectPhoneNumbers`, `createPortInOrder`, `supplementPortInOrder`,
  `cancelPortInOrder`. Also the generic `_xml_to_data`, `_dashboard_json`, and
  `_dashboard_send` helpers reused by `reports.py`.
- **`reports.py`**: usage/billing over the async `/reports` engine: list report
  definitions, create an instance, poll until `Ready`, download the file (zip
  archives unpacked in memory, text truncated at 200k chars).
- **`voice.py`**: `generateBXML` (dict verbs to BXML, optional auto-Gather for
  barge-in) and `respondToCallback` (first-write-wins BXML queue; pre-creates
  call state so BXML can be queued before the answer callback lands).
- **`callbacks.py`**: `getInboundMessages`, `getCallbackEvents` (read the event
  store), and `configureCallbacks` (point an app's webhooks at this server).

## Patterns to follow
- **Read/write annotations.** Every tool passes `ToolAnnotations`
  (`_READ` / `_WRITE` / `_DESTRUCTIVE`) so MCP clients group it correctly.
  Reads set `readOnlyHint=True`; deletes/disconnects/cancels set
  `destructiveHint=True`.
- **Account targeting.** Every account-scoped tool takes `account_id: str = ""`
  and resolves it through `_resolve_account(config, account_id)`, which defaults
  to `BW_ACCOUNT_ID` and rejects any id not in the token's `accounts` claim
  (`BW_ACCOUNTS`). A typo can't silently query the wrong account.
- **XML safety.** Build request bodies with `ElementTree`
  (`Element`/`SubElement`), never f-strings, so subscriber names, numbers, and
  addresses can't inject XML. `_dashboard_send` does this and surfaces the
  `Location` header's trailing id on order creates.
- **Encode the API quirks in the tool, not the caller.** `page`+`size` are always
  sent on `/portins` `/portouts` `/orders`; `lnpchecker` gets E.164 while
  everything else gets bare 10-digit; empty bodies return `{"empty": true}`;
  report done-status is `Ready`. These were all found live against prod; keep
  them.
- **Confirm before carrier writes.** `orderPhoneNumbers`, `disconnect...`, and
  the port-in writes are real, billable, sometimes irreversible carrier actions.
  The tool docstrings tell the agent to confirm exact numbers with the user
  first; keep that guidance when adding more.
