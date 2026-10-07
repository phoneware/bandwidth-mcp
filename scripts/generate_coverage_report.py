"""Generate the API Coverage Report for Bandwidth MCP."""

from __future__ import annotations

import os
from pathlib import Path
import yaml

from registry import get_registry, EXCLUDED_OPERATIONS, SPEC_FILES
from profiles import PROFILES

os.makedirs("docs", exist_ok=True)

reg = get_registry()
all_reg_ops = reg.all_operations

curated_names = set()
for p in PROFILES.values():
    curated_names.update(p)

curated_mapping = {
    "listSites": "numbers.ListSites",
    "listSipPeers": "numbers.GetSipPeers",
    "listPortInOrders": "numbers.ListPortins",
    "getPortInOrder": "numbers.GetPortinOrder",
    "getPortInNotes": "numbers.ListPortinNotes",
    "listPortInLoas": "numbers.ListPortinLoas",
    "listPortOutOrders": "numbers.ListPortouts",
    "getPortOutOrder": "numbers.GetPortoutOrder",
    "searchAvailableNumbers": "numbers.GetAvailableTns",
    "listNumberOrders": "numbers.ListOrders",
    "getNumberOrder": "numbers.GetOrder",
    "getPhoneNumberDetail": "numbers.GetTnDetails",
    "checkPortability": "numbers.CheckLnpPortability",
    "listLidbOrders": "numbers.ListLidbOrders",
    "getLidbOrder": "numbers.GetLidbOrder",
    "getCallForwarding": "numbers.GetTnOptionOrder",
    "listTnOptionOrders": "numbers.ListTnOptionOrders",
    "getTnOptionOrder": "numbers.GetTnOptionOrder",
    "orderPhoneNumbers": "numbers.CreateOrder",
    "disconnectPhoneNumbers": "numbers.CreateDisconnectOrder",
    "createPortInOrder": "numbers.CreatePortin",
    "uploadPortInLoa": "numbers.UploadPortinLoaFile",
    "supplementPortInOrder": "numbers.UpdatePortin",
    "cancelPortInOrder": "numbers.CancelPortin",
    "createLidbOrder": "numbers.CreateLidbOrder",
    "setCallForwarding": "numbers.CreateTnOptionOrder",
    "listReports": "numbers.GetBillingReports",
    "getReport": "numbers.GetBillingReportByType",
    "listReportInstances": "numbers.GetBillingReportInstances",
    "createReportInstance": "numbers.CreateBillingReport",
    "getReportInstance": "numbers.GetBillingReportStatus",
    "downloadReportFile": "numbers.DownloadBillingReport",
    "getCallDetailRecords": "insights.createReport",
    "searchVoiceCalls": "insights.listCalls",
    "getVoiceCall": "insights.listCall",
}

curated_op_ids = set(curated_mapping.values())
specs_dir = Path("src/specs")
operations = []

for spec_key, fname in SPEC_FILES.items():
    data = yaml.safe_load((specs_dir / fname).read_text(encoding="utf-8"))
    paths = data.get("paths", {})
    for path, pdata in paths.items():
        if not isinstance(pdata, dict):
            continue
        for m, op in pdata.items():
            if m.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            bare_id = op.get("operationId", "")
            namespaced = f"{spec_key}.{bare_id}"

            status = "call_api"
            status_label = "Registry (call_api)"
            badge_class = "badge-registry"

            if namespaced in EXCLUDED_OPERATIONS:
                status = "stripped"
                status_label = "Stripped (Security)"
                badge_class = "badge-stripped"
            elif bare_id in curated_names or namespaced in curated_op_ids:
                status = "curated"
                status_label = "Curated Tool"
                badge_class = "badge-curated"

            operations.append(
                {
                    "name": namespaced,
                    "bare_name": bare_id,
                    "spec": spec_key,
                    "method": m.upper(),
                    "path": path,
                    "summary": (op.get("summary") or "").strip() or bare_id,
                    "status": status,
                    "status_label": status_label,
                    "badge_class": badge_class,
                }
            )

operations.sort(key=lambda x: (x["spec"], x["path"], x["method"]))

total_count = len(operations)
curated_count = sum(1 for o in operations if o["status"] == "curated")
call_api_count = sum(1 for o in operations if o["status"] == "call_api")
stripped_count = sum(1 for o in operations if o["status"] == "stripped")

spec_counts = {}
for o in operations:
    s = o["spec"]
    spec_counts[s] = spec_counts.get(s, 0) + 1

rows_html = []
for op in operations:
    name = op["name"]
    spec = op["spec"]
    method = op["method"]
    path = op["path"]
    status_label = op["status_label"]
    badge_class = op["badge_class"]
    summary = op["summary"].replace("<", "&lt;").replace(">", "&gt;")
    rows_html.append(
        f'      <tr data-spec="{spec}" data-status="{op["status"]}">'
        f'<td class="mono"><strong>{name}</strong></td>'
        f"<td>{spec}</td>"
        f'<td><span class="method-tag method-{method}">{method}</span></td>'
        f'<td class="mono">{path}</td>'
        f'<td><span class="badge {badge_class}">{status_label}</span></td>'
        f"<td>{summary}</td></tr>"
    )

rows_joined = "\n".join(rows_html)

template = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Bandwidth API Coverage Report</title>
  <style>
    :root {
      --bg: #0D1117;
      --card-bg: #161B22;
      --border: #30363D;
      --text: #C9D1D9;
      --text-muted: #8B949E;
      --heading: #F0F6FC;
      --brand: #58A6FF;
      --green: #238636;
      --amber: #D29922;
      --red: #DA3633;
      --font-mono: ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, monospace;
      --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: var(--font-sans);
      line-height: 1.5;
      padding: 2.5rem;
    }
    .container {
      max-width: 1300px;
      margin: 0 auto;
    }
    header {
      margin-bottom: 2.5rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 1.5rem;
    }
    h1 {
      color: var(--heading);
      font-size: 2rem;
      font-weight: 600;
      margin-bottom: 0.5rem;
    }
    p.lead {
      color: var(--text-muted);
      font-size: 1.05rem;
    }
    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1.25rem;
      margin-bottom: 2.5rem;
    }
    .metric-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.25rem;
    }
    .metric-value {
      font-size: 2.2rem;
      font-weight: 700;
      color: var(--heading);
      margin-bottom: 0.25rem;
    }
    .metric-label {
      color: var(--text-muted);
      font-size: 0.875rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .notes-box {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-left: 4px solid var(--brand);
      border-radius: 6px;
      padding: 1.25rem;
      margin-bottom: 2.5rem;
    }
    .notes-box h2 {
      color: var(--heading);
      font-size: 1.15rem;
      margin-bottom: 0.75rem;
    }
    .notes-box ul {
      padding-left: 1.25rem;
      color: var(--text);
    }
    .notes-box li {
      margin-bottom: 0.4rem;
    }
    .filter-bar {
      display: flex;
      gap: 1rem;
      margin-bottom: 1.5rem;
      flex-wrap: wrap;
    }
    input.search-input {
      flex: 1;
      min-width: 280px;
      background: var(--card-bg);
      border: 1px solid var(--border);
      color: var(--heading);
      padding: 0.6rem 1rem;
      border-radius: 6px;
      font-size: 0.95rem;
    }
    select.filter-select {
      background: var(--card-bg);
      border: 1px solid var(--border);
      color: var(--heading);
      padding: 0.6rem 1rem;
      border-radius: 6px;
      font-size: 0.95rem;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
    }
    th {
      text-align: left;
      padding: 0.75rem 1rem;
      background: #21262D;
      color: var(--heading);
      font-size: 0.85rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      border-bottom: 1px solid var(--border);
    }
    td {
      padding: 0.75rem 1rem;
      border-bottom: 1px solid var(--border);
      font-size: 0.9rem;
    }
    tr:last-child td { border-bottom: none; }
    tr:hover { background: rgba(255, 255, 255, 0.02); }
    .method-tag {
      display: inline-block;
      padding: 0.15rem 0.4rem;
      border-radius: 4px;
      font-family: var(--font-mono);
      font-size: 0.75rem;
      font-weight: 700;
    }
    .method-GET { background: rgba(56, 139, 253, 0.15); color: #58A6FF; }
    .method-POST { background: rgba(46, 160, 67, 0.15); color: #3FB950; }
    .method-PUT { background: rgba(210, 153, 34, 0.15); color: #D29922; }
    .method-PATCH { background: rgba(187, 128, 9, 0.15); color: #E3B341; }
    .method-DELETE { background: rgba(248, 81, 73, 0.15); color: #F85149; }
    .badge {
      display: inline-block;
      padding: 0.2rem 0.5rem;
      border-radius: 12px;
      font-size: 0.75rem;
      font-weight: 600;
    }
    .badge-curated { background: rgba(46, 160, 67, 0.2); color: #3FB950; border: 1px solid rgba(46, 160, 67, 0.4); }
    .badge-registry { background: rgba(56, 139, 253, 0.2); color: #58A6FF; border: 1px solid rgba(56, 139, 253, 0.4); }
    .badge-stripped { background: rgba(248, 81, 73, 0.2); color: #F85149; border: 1px solid rgba(248, 81, 73, 0.4); }
    .mono { font-family: var(--font-mono); font-size: 0.85rem; }
  </style>
</head>
<body>
<div class="container">
  <header>
    <h1>Bandwidth MCP API Coverage</h1>
    <p class="lead">Full spec-to-tool reconciliation across all eight published Bandwidth OpenAPI specifications.</p>
  </header>

  <div class="metrics-grid">
    <div class="metric-card">
      <div class="metric-value">__TOTAL_COUNT__</div>
      <div class="metric-label">Total Spec Operations</div>
    </div>
    <div class="metric-card">
      <div class="metric-value">__CURATED_COUNT__</div>
      <div class="metric-label">Curated Task Tools</div>
    </div>
    <div class="metric-card">
      <div class="metric-value">__CALL_API_COUNT__</div>
      <div class="metric-label">Reachable via call_api</div>
    </div>
    <div class="metric-card">
      <div class="metric-value">__STRIPPED_COUNT__</div>
      <div class="metric-label">Stripped at Generation</div>
    </div>
  </div>

  <div class="notes-box">
    <h2>Access Channels and Known Refusals</h2>
    <ul>
      <li><strong>Curated tools:</strong> Task-shaped composite tools for porting, number ordering, CNAM, call forwarding, and call history.</li>
      <li><strong>Registry escape hatch (search_api / call_api):</strong> Every uncurated operation is callable by name. All writes require <code>confirm='CONFIRM'</code>. Destructive actions trigger in-band MCP confirmation prompts.</li>
      <li><strong>Security stripping:</strong> Ten raw SIP trunk credential management operations are stripped at generation time.</li>
      <li><strong>Insights Voice Calls (/v1/voice/calls):</strong> Requires the <code>voice_insights</code> API role. Timestamps require comparison operators (gte:, lte:).</li>
      <li><strong>Insights CDR Reports (/v1/reports):</strong> Asynchronous report requests require ISO 8601 timestamps with millisecond precision (.000Z) and explicit <code>region: "US"</code>.</li>
      <li><strong>Numbers XML API (/portins):</strong> Requires explicit <code>page=1&size=...</code> query parameters and E.164 phone numbers.</li>
      <li><strong>Synchronous Reports (/v2/report-definitions):</strong> Returns 403 on standard API credentials because Bandwidth gates synchronous reports behind an enterprise add-on.</li>
    </ul>
  </div>

  <div class="filter-bar">
    <input type="text" id="searchInput" class="search-input" placeholder="Filter by operation, path, or description..." oninput="filterTable()">
    <select id="specFilter" class="filter-select" onchange="filterTable()">
      <option value="">All Specs</option>
      <option value="numbers">Numbers (__NUMBERS_COUNT__)</option>
      <option value="voice">Voice (__VOICE_COUNT__)</option>
      <option value="insights">Insights (__INSIGHTS_COUNT__)</option>
      <option value="end-user-management">End User Management (__EUM_COUNT__)</option>
      <option value="messaging">Messaging (__MESSAGING_COUNT__)</option>
      <option value="toll-free-verification">Toll-Free Verification (__TFV_COUNT__)</option>
      <option value="lookup">Lookup (__LOOKUP_COUNT__)</option>
      <option value="multi-factor-auth">Multi-Factor Auth (__MFA_COUNT__)</option>
    </select>
    <select id="statusFilter" class="filter-select" onchange="filterTable()">
      <option value="">All Reachability</option>
      <option value="curated">Curated Tool</option>
      <option value="call_api">call_api (Registry)</option>
      <option value="stripped">Stripped (Security)</option>
    </select>
  </div>

  <table id="opsTable">
    <thead>
      <tr>
        <th>Operation</th>
        <th>Spec</th>
        <th>Method</th>
        <th>Path</th>
        <th>Reachability</th>
        <th>Summary</th>
      </tr>
    </thead>
    <tbody>
__ROWS__
    </tbody>
  </table>
</div>

<script>
function filterTable() {
  const q = document.getElementById("searchInput").value.toLowerCase();
  const spec = document.getElementById("specFilter").value;
  const status = document.getElementById("statusFilter").value;
  const rows = document.querySelectorAll("#opsTable tbody tr");

  rows.forEach(r => {
    const text = r.innerText.toLowerCase();
    const rowSpec = r.getAttribute("data-spec");
    const rowStatus = r.getAttribute("data-status");

    const matchesQuery = !q || text.includes(q);
    const matchesSpec = !spec || rowSpec === spec;
    const matchesStatus = !status || rowStatus === status;

    r.style.display = (matchesQuery && matchesSpec && matchesStatus) ? "" : "none";
  });
}
</script>
</body>
</html>
"""

final_html = (
    template.replace("__TOTAL_COUNT__", str(total_count))
    .replace("__CURATED_COUNT__", str(curated_count))
    .replace("__CALL_API_COUNT__", str(call_api_count))
    .replace("__STRIPPED_COUNT__", str(stripped_count))
    .replace("__NUMBERS_COUNT__", str(spec_counts.get("numbers", 0)))
    .replace("__VOICE_COUNT__", str(spec_counts.get("voice", 0)))
    .replace("__INSIGHTS_COUNT__", str(spec_counts.get("insights", 0)))
    .replace("__EUM_COUNT__", str(spec_counts.get("end-user-management", 0)))
    .replace("__MESSAGING_COUNT__", str(spec_counts.get("messaging", 0)))
    .replace("__TFV_COUNT__", str(spec_counts.get("toll-free-verification", 0)))
    .replace("__LOOKUP_COUNT__", str(spec_counts.get("lookup", 0)))
    .replace("__MFA_COUNT__", str(spec_counts.get("multi-factor-auth", 0)))
    .replace("__ROWS__", rows_joined)
)

Path("docs/api-coverage.html").write_text(final_html, encoding="utf-8")
print(f"Generated docs/api-coverage.html with {total_count} operations.")
