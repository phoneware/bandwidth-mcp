# `src/tools/`: hand-written tools

Curated tools add workflow logic, validation, pagination, or shaped responses
on top of the vendor API. Each module exposes a `register_*_tools(mcp, config)`
function called from `app.py`'s lifespan. Registrations are pruned afterward
to honor the selected profile and exclusions.

## Why these exist
The vendored specifications feed `src/registry.py`; `search_api` and `call_api`
reach operations outside the curated catalog. `src/xml_adapter.py` handles the
Numbers/Dashboard API's XML, JSON, and binary media types from those schemas.
Add a hand-written tool when it contributes workflow behavior, rather than
because an endpoint speaks XML.

## Modules
- **`credentials.py`**: `setCredentials` (stdio only; takes client id/secret and
  mints a token) and `clearCredentials`. Under the hosted transport, auth is the
  OAuth `/token` mint in `serve.py`, so `setCredentials` is not registered.
- **`discovery.py`**: `listAccounts`, `listApplications`, `listPhoneNumbers`,
  `createApplication`. Also the shared XML helpers: `_dashboard_get`,
  `_xml_text`, and **`_resolve_account`** (below). `listPhoneNumbers` tries
  `/tns` (Numbers role) then falls back to `/inserviceNumbers` (inservice role),
  because different creds hold different roles.
- **`numbers.py`**: the reseller surface. Read: port-in/out orders + notes +
  uploaded documents (`listPortInLoas`), available-number search, number orders,
  sites, SIP peers, per-number detail, `checkPortability`. Write
  (`numbers-write` profile): `orderPhoneNumbers`, `disconnectPhoneNumbers`,
  `createPortInOrder`, `uploadPortInLoa`, `supplementPortInOrder`,
  `cancelPortInOrder`. Also the generic `_xml_to_data`, `_dashboard_json`,
  `_dashboard_send`, and `_dashboard_upload` helpers (`reports.py` reuses the
  first three).
  **Port-in validation lives in `_port_in_problems`**, not in the tool body:
  Bandwidth requires the subscriber name and full service address, so the tool
  collects every problem and raises once rather than spending a live carrier
  write to discover them. Keep new required-field rules there so they stay
  unit-testable without a mocked HTTP round trip. Activation scheduling
  (requested_foc_time in Eastern time, localized and setting Triggered=true),
  site resolution by name via `_resolve_site`, order ID defaults via
  `_sanitize_customer_order_id`, and single-call LOA attachment via
  `_upload_port_in_document` live in `numbers.py`.
- **`reports.py`**: usage/billing over the async `/reports` engine: list report
  definitions, create an instance, poll until `Ready`, download the file (zip
  archives unpacked in memory, text truncated at 200k chars).
- **`tnoptions.py`**: carrier-level call forwarding over the asynchronous TN Options
  work-order engine (`/tnoptions`). Read (`numbers` profile): `getCallForwarding`,
  `listTnOptionOrders`, `getTnOptionOrder`. Write (`numbers-write` profile):
  `setCallForwarding`. Current forwarding is not stored on the TN itself but on the
  per-TN SIP-peer record, so `getCallForwarding` resolves site and peer from
  `tns/<tn>/tndetails` first, then reads `sites/<siteId>/sippeers/<peerId>/tns/<tn>`.
- **`voice.py`**: `generateBXML` (dict verbs to BXML, optional auto-Gather for
  barge-in) and `respondToCallback` (first-write-wins BXML queue; pre-creates
  call state so BXML can be queued before the answer callback lands).
- **`callbacks.py`**: `getInboundMessages`, `getCallbackEvents` (read the event
  store), and `configureCallbacks` (point an app's webhooks at this server).
- **`call_history.py`**: curated call history tools over Bandwidth Insights:
  `getCallDetailRecords` (asynchronous Insights CDR reporting, polling, zip
  extraction, and number/window filtering), `searchVoiceCalls` (real-time voice
  call search with latency, jitter, packet loss, and MOS scores), and
  `getVoiceCall`.
- **`meta.py`**: API discovery and escape-hatch execution across the 430+ operations
  in the unified registry: `search_api` (ranked keyword search) and `call_api`
  (invoke any operation by name). Writes require the per-operation token from
  `src/safety.py`, such as `CREATESITE` for `numbers.CreateSite`. Destructive
  calls also require MCP elicitation, with the requesting context forwarded.
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
  Use `check_confirmation` with the tool name; the refusal returns the required
  token. Disconnects, deletes, and cancellations also call
  `elicit_destructive_confirmation`, which refuses unsupported clients by
  default. Keep the same gates on curated, registry, and promoted tools.
