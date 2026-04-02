# Phase 1: MCP Server Expansion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the Bandwidth MCP server to cover the full agent voice flow: account infrastructure, number management, voice calls, BXML generation, composite quickstart tool, and callback listening via the relay service.

**Architecture:** New API specs are added to `api_server_info` (same pattern as Phase 0). Custom tools (quickstart, BXML, listen) are added via `@mcp.tool()` decorator. The listen tools use FastMCP's lifespan context to hold WebSocket state across tool calls. A new `src/tools/` package organizes custom tools by domain.

**Tech Stack:** Python 3.10+, FastMCP ~2.13.0, httpx, websockets, pytest, pytest-asyncio

**Repo:** `Bandwidth/mcp-server`

**Depends on:** Phase 0 (Express Registration) completed. Relay service (Phase 1b) deployed for listen tools.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/servers.py` | Modify | Add 4 new API specs to `api_server_info` |
| `src/app.py` | Modify | Add lifespan context for WebSocket state, register custom tools |
| `src/tools/__init__.py` | Create | Package init |
| `src/tools/quickstart.py` | Create | Composite `quickstartSetup` tool |
| `src/tools/bxml.py` | Create | `generateBxml` and `validateBxml` tools |
| `src/tools/listen.py` | Create | `startListening`, `stopListening`, `getListenerStatus` tools |
| `test/fixtures/accounts.yml` | Create | Accounts API spec fixture (trimmed) |
| `test/fixtures/applications.yml` | Create | Applications API spec fixture (trimmed) |
| `test/fixtures/number-management.yml` | Create | Number management spec fixture (trimmed) |
| `test/fixtures/number-acquisition.yml` | Create | Number acquisition spec fixture (trimmed) |
| `test/fixtures/voice.yml` | Create | Voice API spec fixture (trimmed) |
| `test/test_bxml.py` | Create | BXML generation tests |
| `test/test_quickstart.py` | Create | Quickstart composite tool tests |
| `test/test_listen.py` | Create | Listen tool tests |
| `test/test_servers.py` | Modify | Update total tool counts |
| `pyproject.toml` | Modify | Add `websockets` dependency |

---

### Task 1: Add New API Specs to Server Config

**Files:**
- Modify: `src/servers.py`
- Create: `test/fixtures/accounts.yml`, `test/fixtures/applications.yml`, `test/fixtures/number-management.yml`, `test/fixtures/number-acquisition.yml`, `test/fixtures/voice.yml`
- Modify: `test/test_servers.py`

- [ ] **Step 1: Write minimal test fixtures for the 4 new API specs**

Each fixture needs a `servers` array, at least 2-3 paths with `operationId`, and valid OpenAPI 3.x structure. These are trimmed versions of the real specs — enough to test tool generation without being 200KB each.

Create `test/fixtures/accounts.yml`:
```yaml
openapi: 3.0.1
info:
  title: Bandwidth Account Management
  version: 1.0.0
servers:
  - url: https://api.bandwidth.com/api/v2
paths:
  /accounts/{accountId}/sites:
    get:
      operationId: listSites
      summary: List all sites for an account
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Success
    post:
      operationId: createSite
      summary: Create a new site
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                name:
                  type: string
                description:
                  type: string
      responses:
        '201':
          description: Created
  /accounts/{accountId}/sites/{siteId}:
    get:
      operationId: getSite
      summary: Get site details
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
        - name: siteId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Success
  /accounts/{accountId}/sites/{siteId}/sippeers:
    post:
      operationId: createSipPeer
      summary: Create a SIP peer (location)
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
        - name: siteId
          in: path
          required: true
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                peerName:
                  type: string
                isDefaultPeer:
                  type: boolean
      responses:
        '201':
          description: Created
```

Create `test/fixtures/applications.yml`:
```yaml
openapi: 3.0.1
info:
  title: Bandwidth Applications
  version: 1.0.0
servers:
  - url: https://api.bandwidth.com/api/v2
paths:
  /accounts/{accountId}/applications:
    get:
      operationId: listApplications
      summary: List applications
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Success
    post:
      operationId: createApplication
      summary: Create an application
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                name:
                  type: string
                type:
                  type: string
                  enum: [voice, messaging]
                callInitiatedCallbackUrl:
                  type: string
                callStatusCallbackUrl:
                  type: string
      responses:
        '201':
          description: Created
  /accounts/{accountId}/applications/{applicationId}:
    get:
      operationId: getApplication
      summary: Get application details
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
        - name: applicationId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Success
    delete:
      operationId: deleteApplication
      summary: Delete an application
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
        - name: applicationId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Deleted
```

Create `test/fixtures/number-management.yml`:
```yaml
openapi: 3.1.0
info:
  title: Bandwidth Number Management
  version: 1.0.0
servers:
  - url: https://api.bandwidth.com/api/v2
paths:
  /accounts/{accountId}/phoneNumbers:
    get:
      operationId: listPhoneNumbers
      summary: List phone numbers
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Success
  /accounts/{accountId}/phoneNumbers/{phoneNumber}:
    get:
      operationId: getPhoneNumber
      summary: Get phone number details
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
        - name: phoneNumber
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Success
```

Create `test/fixtures/number-acquisition.yml`:
```yaml
openapi: 3.0.1
info:
  title: Bandwidth Number Acquisition
  version: 1.0.0
servers:
  - url: https://api.bandwidth.com/api/v2
paths:
  /accounts/{accountId}/availableNumbers:
    get:
      operationId: searchAvailableNumbers
      summary: Search for available phone numbers
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
        - name: areaCode
          in: query
          schema:
            type: string
        - name: quantity
          in: query
          schema:
            type: integer
      responses:
        '200':
          description: Success
  /accounts/{accountId}/orders:
    post:
      operationId: createOrder
      summary: Order phone numbers
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                name:
                  type: string
                siteId:
                  type: string
                existingTelephoneNumberOrderType:
                  type: object
      responses:
        '201':
          description: Created
    get:
      operationId: listOrders
      summary: List number orders
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Success
  /accounts/{accountId}/orders/{orderId}:
    get:
      operationId: getOrder
      summary: Get order status
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
        - name: orderId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Success
```

Create `test/fixtures/voice.yml` (trimmed — full spec is 392KB, fixture has key operations):
```yaml
openapi: 3.0.3
info:
  title: Bandwidth Voice
  version: 4.1.0
servers:
  - url: https://voice.bandwidth.com/api/v2
paths:
  /accounts/{accountId}/calls:
    post:
      operationId: createCall
      summary: Create an outbound call
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required:
                - from
                - to
                - applicationId
                - answerUrl
              properties:
                from:
                  type: string
                to:
                  type: string
                applicationId:
                  type: string
                answerUrl:
                  type: string
      responses:
        '201':
          description: Call created
    get:
      operationId: listCalls
      summary: List calls
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Success
  /accounts/{accountId}/calls/{callId}:
    get:
      operationId: getCall
      summary: Get call state
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
        - name: callId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Success
    post:
      operationId: updateCall
      summary: Update call (redirect or hangup)
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
        - name: callId
          in: path
          required: true
          schema:
            type: string
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                state:
                  type: string
                  enum: [active, completed]
                redirectUrl:
                  type: string
      responses:
        '200':
          description: Updated
  /accounts/{accountId}/calls/{callId}/recordings:
    get:
      operationId: listCallRecordings
      summary: List recordings for a call
      parameters:
        - name: accountId
          in: path
          required: true
          schema:
            type: string
        - name: callId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Success
```

- [ ] **Step 2: Write failing test for new spec tool counts**

Add to `test/test_servers.py` or create a new test:

```python
@pytest.mark.asyncio
async def test_accounts_server_tools(httpx_mock):
    create_mock(httpx_mock, "accounts")
    from src.servers import _create_server
    server = await _create_server(
        url="https://dev.bandwidth.com/spec/accounts.yml",
        config={"BW_USERNAME": "u", "BW_PASSWORD": "p"},
    )
    tools = await server.get_tools()
    assert len(tools) == 4  # listSites, createSite, getSite, createSipPeer


@pytest.mark.asyncio
async def test_voice_server_tools(httpx_mock):
    create_mock(httpx_mock, "voice")
    from src.servers import _create_server
    server = await _create_server(
        url="https://dev.bandwidth.com/spec/voice.yml",
        config={"BW_USERNAME": "u", "BW_PASSWORD": "p"},
    )
    tools = await server.get_tools()
    assert len(tools) == 5  # createCall, listCalls, getCall, updateCall, listCallRecordings
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /tmp/bw-mcp-server
python -m pytest test/test_servers.py -v -k "accounts or voice"
```

Expected: FAIL — fixtures exist but specs aren't in `api_server_info` yet.

- [ ] **Step 4: Add new specs to `api_server_info` in `src/servers.py`**

```python
api_server_info: Dict[str, Dict[str, Any]] = {
    "messaging": {"url": "https://dev.bandwidth.com/spec/messaging.yml"},
    "multi-factor-auth": {"url": "https://dev.bandwidth.com/spec/multi-factor-auth.yml"},
    "phone-number-lookup": {"url": "https://dev.bandwidth.com/spec/phone-number-lookup-v2.yml"},
    "insights": {"url": "https://dev.bandwidth.com/spec/insights.yml"},
    "end-user-management": {"url": "https://dev.bandwidth.com/spec/end-user-management.yml"},
    "express-registration": {
        "url": "https://dev.bandwidth.com/spec/express.yml",
        "requires_auth": False,
    },
    "accounts": {"url": "https://dev.bandwidth.com/spec/accounts.yml"},
    "applications": {"url": "https://dev.bandwidth.com/spec/applications.yml"},
    "number-management": {"url": "https://dev.bandwidth.com/spec/number-management.yml"},
    "number-acquisition": {"url": "https://dev.bandwidth.com/spec/number-acquisition.yml"},
    "voice": {"url": "https://dev.bandwidth.com/spec/voice.yml"},
}
```

- [ ] **Step 5: Add mocks for new fixtures to full server test in `test/test_servers.py`**

Find the test that creates the full MCP server and add:

```python
create_mock(httpx_mock, "accounts")
create_mock(httpx_mock, "applications")
create_mock(httpx_mock, "number-management")
create_mock(httpx_mock, "number-acquisition")
create_mock(httpx_mock, "voice")
```

Update `total_tools` in `calculate_expected_tools` to reflect new count. The fixtures have: accounts(4) + applications(4) + number-management(2) + number-acquisition(4) + voice(5) = 19 new tools. Added to previous 50 (47 original + 3 Express) = 69 total.

- [ ] **Step 6: Run all tests**

```bash
cd /tmp/bw-mcp-server
python -m pytest test/ -v
```

Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/servers.py test/fixtures/ test/test_servers.py
git commit -m "feat: add accounts, applications, numbers, and voice API specs"
```

---

### Task 2: Create BXML Generation Tool

**Files:**
- Create: `src/tools/__init__.py`
- Create: `src/tools/bxml.py`
- Create: `test/test_bxml.py`

- [ ] **Step 1: Create the tools package**

```bash
mkdir -p src/tools
touch src/tools/__init__.py
```

- [ ] **Step 2: Write failing tests for BXML generation**

Create `test/test_bxml.py`:

```python
import pytest
from src.tools.bxml import generate_bxml_string, validate_bxml_string


class TestGenerateBxml:
    def test_speak_sentence(self):
        result = generate_bxml_string([
            {"verb": "SpeakSentence", "text": "Hello world"}
        ])
        assert "<SpeakSentence>Hello world</SpeakSentence>" in result
        assert result.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        assert "<Response>" in result
        assert "</Response>" in result

    def test_speak_sentence_with_voice(self):
        result = generate_bxml_string([
            {"verb": "SpeakSentence", "text": "Hello", "voice": "julie"}
        ])
        assert 'voice="julie"' in result
        assert ">Hello</SpeakSentence>" in result

    def test_gather_with_nested_speak(self):
        result = generate_bxml_string([{
            "verb": "Gather",
            "gatherUrl": "https://example.com/gather",
            "maxDigits": "1",
            "children": [
                {"verb": "SpeakSentence", "text": "Press 1 for sales"}
            ],
        }])
        assert "<Gather" in result
        assert 'gatherUrl="https://example.com/gather"' in result
        assert "<SpeakSentence>Press 1 for sales</SpeakSentence>" in result
        assert "</Gather>" in result

    def test_hangup(self):
        result = generate_bxml_string([{"verb": "Hangup"}])
        assert "<Hangup/>" in result

    def test_transfer_with_phone_number(self):
        result = generate_bxml_string([{
            "verb": "Transfer",
            "transferCallerId": "+15551234567",
            "children": [
                {"verb": "PhoneNumber", "text": "+15559876543"}
            ],
        }])
        assert "<Transfer" in result
        assert 'transferCallerId="+15551234567"' in result
        assert "<PhoneNumber>+15559876543</PhoneNumber>" in result

    def test_multiple_verbs(self):
        result = generate_bxml_string([
            {"verb": "SpeakSentence", "text": "Recording now"},
            {"verb": "Record", "recordCompleteUrl": "https://example.com/record"},
            {"verb": "Hangup"},
        ])
        assert "<SpeakSentence>" in result
        assert "<Record" in result
        assert "<Hangup/>" in result

    def test_empty_verbs(self):
        result = generate_bxml_string([])
        assert "<Response>" in result
        assert "</Response>" in result

    def test_forward(self):
        result = generate_bxml_string([{
            "verb": "Forward",
            "to": "+15559876543",
            "from": "+15551234567",
        }])
        assert "<Forward" in result
        assert 'to="+15559876543"' in result

    def test_start_stream(self):
        result = generate_bxml_string([{
            "verb": "StartStream",
            "destination": "wss://example.com/stream",
            "name": "my-stream",
        }])
        assert "<StartStream" in result
        assert 'destination="wss://example.com/stream"' in result


class TestValidateBxml:
    def test_valid_bxml(self):
        bxml = '<?xml version="1.0" encoding="UTF-8"?><Response><SpeakSentence>Hi</SpeakSentence></Response>'
        result = validate_bxml_string(bxml)
        assert result["valid"] is True

    def test_invalid_xml(self):
        result = validate_bxml_string("<Response><Unclosed>")
        assert result["valid"] is False
        assert "error" in result

    def test_missing_response_wrapper(self):
        result = validate_bxml_string('<?xml version="1.0"?><SpeakSentence>Hi</SpeakSentence>')
        assert result["valid"] is False

    def test_unknown_verb(self):
        bxml = '<?xml version="1.0" encoding="UTF-8"?><Response><FakeVerb>Hi</FakeVerb></Response>'
        result = validate_bxml_string(bxml)
        assert result["valid"] is False
        assert "FakeVerb" in result.get("error", "")
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /tmp/bw-mcp-server
python -m pytest test/test_bxml.py -v
```

Expected: FAIL — `src/tools/bxml.py` doesn't exist yet.

- [ ] **Step 4: Implement BXML generation**

Create `src/tools/bxml.py`:

```python
"""BXML generation and validation tools for Bandwidth voice call control."""

import xml.etree.ElementTree as ET

VALID_VERBS = {
    "SpeakSentence", "PlayAudio", "Pause", "Ring",
    "Gather", "StartGather", "StopGather", "SendDtmf",
    "Transfer", "Bridge", "Forward", "Redirect", "Hangup", "Conference",
    "Record", "StartRecording", "PauseRecording", "ResumeRecording", "StopRecording",
    "StartStream", "StopStream", "StartTranscription", "StopTranscription",
    "Tag", "PhoneNumber", "SipUri",
}

SELF_CLOSING_VERBS = {"Hangup", "Pause", "PauseRecording", "ResumeRecording", "StopRecording", "StopGather"}

TEXT_CONTENT_KEYS = {"text"}
CHILDREN_KEY = "children"


def _build_verb_xml(verb_dict: dict, indent: int = 1) -> str:
    """Build XML string for a single BXML verb."""
    verb_name = verb_dict["verb"]
    text = verb_dict.get("text", "")
    children = verb_dict.get(CHILDREN_KEY, [])
    attrs = {
        k: str(v)
        for k, v in verb_dict.items()
        if k not in ("verb", "text", CHILDREN_KEY)
    }
    attr_str = " ".join(f'{k}="{v}"' for k, v in attrs.items())
    prefix = "  " * indent

    if verb_name in SELF_CLOSING_VERBS and not text and not children:
        tag = f"<{verb_name} {attr_str}/>" if attr_str else f"<{verb_name}/>"
        return f"{prefix}{tag}"

    tag_open = f"<{verb_name} {attr_str}>" if attr_str else f"<{verb_name}>"

    if children:
        lines = [f"{prefix}{tag_open}"]
        for child in children:
            lines.append(_build_verb_xml(child, indent + 1))
        lines.append(f"{prefix}</{verb_name}>")
        return "\n".join(lines)

    return f"{prefix}{tag_open}{text}</{verb_name}>"


def generate_bxml_string(verbs: list[dict]) -> str:
    """Construct a BXML document from a list of verb objects.

    Each verb is a dict with:
      - "verb": str — the BXML verb name (e.g. "SpeakSentence", "Gather")
      - "text": str (optional) — text content for the verb
      - "children": list[dict] (optional) — nested verbs (e.g. SpeakSentence inside Gather)
      - Any other keys become XML attributes
    """
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<Response>"]
    for v in verbs:
        lines.append(_build_verb_xml(v))
    lines.append("</Response>")
    return "\n".join(lines)


def validate_bxml_string(bxml: str) -> dict:
    """Validate a BXML string for correctness.

    Returns {"valid": True} or {"valid": False, "error": "description"}.
    """
    try:
        root = ET.fromstring(bxml)
    except ET.ParseError as e:
        return {"valid": False, "error": f"XML parse error: {e}"}

    if root.tag != "Response":
        return {"valid": False, "error": f"Root element must be <Response>, got <{root.tag}>"}

    for child in root:
        if child.tag not in VALID_VERBS:
            return {
                "valid": False,
                "error": f"Unknown BXML verb: <{child.tag}>. Valid verbs: {sorted(VALID_VERBS)}",
            }

    return {"valid": True}
```

- [ ] **Step 5: Run tests**

```bash
cd /tmp/bw-mcp-server
python -m pytest test/test_bxml.py -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/tools/__init__.py src/tools/bxml.py test/test_bxml.py
git commit -m "feat: add BXML generation and validation tools"
```

---

### Task 3: Register BXML Tools on the MCP Server

**Files:**
- Modify: `src/app.py`
- Create: `test/test_bxml_mcp.py`

- [ ] **Step 1: Write failing test that BXML tools appear on the MCP server**

Create `test/test_bxml_mcp.py`:

```python
import pytest
from fastmcp import FastMCP


@pytest.mark.asyncio
async def test_bxml_tools_registered():
    from src.tools.bxml import register_bxml_tools

    mcp = FastMCP(name="Test")
    register_bxml_tools(mcp)
    tools = await mcp.get_tools()
    assert "generateBxml" in tools
    assert "validateBxml" in tools


@pytest.mark.asyncio
async def test_generate_bxml_tool_callable():
    from src.tools.bxml import register_bxml_tools

    mcp = FastMCP(name="Test")
    register_bxml_tools(mcp)
    tools = await mcp.get_tools()
    assert tools["generateBxml"].description is not None
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /tmp/bw-mcp-server
python -m pytest test/test_bxml_mcp.py -v
```

Expected: FAIL — `register_bxml_tools` doesn't exist.

- [ ] **Step 3: Add registration function to `src/tools/bxml.py`**

Append to `src/tools/bxml.py`:

```python
def register_bxml_tools(mcp: "FastMCP"):
    """Register BXML generation tools on the MCP server."""

    @mcp.tool(name="generateBxml")
    def generate_bxml(verbs: list[dict]) -> str:
        """Construct a valid BXML document from structured verb objects.

        Each verb is a dict with:
        - "verb": the BXML verb name (SpeakSentence, Gather, Transfer, Hangup, etc.)
        - "text": text content (optional, for verbs like SpeakSentence)
        - "children": nested verbs (optional, for verbs like Gather, Transfer)
        - Any other keys become XML attributes (e.g. "voice", "gatherUrl", "maxDigits")

        Example: [{"verb": "SpeakSentence", "text": "Hello!", "voice": "julie"}, {"verb": "Hangup"}]
        """
        return generate_bxml_string(verbs)

    @mcp.tool(name="validateBxml")
    def validate_bxml(bxml: str) -> dict:
        """Validate a BXML XML string for correctness. Returns {"valid": true} or {"valid": false, "error": "..."}."""
        return validate_bxml_string(bxml)
```

- [ ] **Step 4: Wire into `src/app.py`**

In `src/app.py`, after the `create_bandwidth_mcp` call in `setup()`, add:

```python
from src.tools.bxml import register_bxml_tools
register_bxml_tools(mcp)
```

- [ ] **Step 5: Run tests**

```bash
cd /tmp/bw-mcp-server
python -m pytest test/test_bxml_mcp.py test/test_bxml.py -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/tools/bxml.py src/app.py test/test_bxml_mcp.py
git commit -m "feat: register BXML tools on MCP server"
```

---

### Task 4: Build Composite quickstartSetup Tool

**Files:**
- Create: `src/tools/quickstart.py`
- Create: `test/test_quickstart.py`

- [ ] **Step 1: Write failing tests**

Create `test/test_quickstart.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_quickstart_calls_all_steps():
    """quickstart_setup should call create site, create location, create app, search numbers, and order."""
    from src.tools.quickstart import quickstart_flow

    mock_client = AsyncMock()

    # Mock responses for each step
    mock_client.post.side_effect = [
        # 1. Create site
        MagicMock(status_code=201, json=lambda: {"id": "site-123", "name": "Agent Site"}),
        # 2. Create SIP peer
        MagicMock(status_code=201, json=lambda: {"id": "peer-456", "name": "Default"}),
        # 3. Create application
        MagicMock(status_code=201, json=lambda: {"id": "app-789", "name": "Voice App"}),
        # 4. Order number
        MagicMock(status_code=201, json=lambda: {"id": "order-abc"}),
    ]
    mock_client.get.side_effect = [
        # 5. Search available numbers
        MagicMock(status_code=200, json=lambda: {"telephoneNumbers": ["+19195551234"]}),
        # 6. Get order status
        MagicMock(status_code=200, json=lambda: {"status": "COMPLETE"}),
    ]

    result = await quickstart_flow(
        client=mock_client,
        account_id="acct-1",
        callback_url="https://example.com/callback",
        area_code="919",
    )

    assert result["siteId"] == "site-123"
    assert result["locationId"] == "peer-456"
    assert result["applicationId"] == "app-789"
    assert result["phoneNumber"] == "+19195551234"
    assert result["status"] == "complete"
    assert mock_client.post.call_count == 4
    assert mock_client.get.call_count >= 1


@pytest.mark.asyncio
async def test_quickstart_returns_error_on_site_failure():
    """If site creation fails, quickstart should return an error."""
    from src.tools.quickstart import quickstart_flow

    mock_client = AsyncMock()
    mock_client.post.side_effect = [
        MagicMock(status_code=400, json=lambda: {"error": "bad request"}, raise_for_status=MagicMock(side_effect=Exception("400"))),
    ]

    with pytest.raises(Exception):
        await quickstart_flow(
            client=mock_client,
            account_id="acct-1",
            callback_url="https://example.com/callback",
        )
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /tmp/bw-mcp-server
python -m pytest test/test_quickstart.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement quickstart flow**

Create `src/tools/quickstart.py`:

```python
"""Composite quickstart tool — provisions a complete Bandwidth voice setup in one call."""

import httpx
from src.server_utils import create_auth_header


async def quickstart_flow(
    client: httpx.AsyncClient,
    account_id: str,
    callback_url: str,
    area_code: str = "919",
    site_name: str = "Agent Site",
) -> dict:
    """Execute the full quickstart provisioning flow.

    Creates site → SIP peer → voice application → searches and orders a phone number.
    Returns a dict with all created resource IDs.
    """
    # Step 1: Create site
    resp = await client.post(
        f"/accounts/{account_id}/sites",
        json={"name": site_name, "description": "Created by Bandwidth MCP quickstart"},
    )
    resp.raise_for_status()
    site = resp.json()
    site_id = site["id"]

    # Step 2: Create SIP peer (location)
    resp = await client.post(
        f"/accounts/{account_id}/sites/{site_id}/sippeers",
        json={"peerName": "Default Location", "isDefaultPeer": True},
    )
    resp.raise_for_status()
    peer = resp.json()
    peer_id = peer["id"]

    # Step 3: Create voice application
    resp = await client.post(
        f"/accounts/{account_id}/applications",
        json={
            "name": f"{site_name} Voice App",
            "type": "voice",
            "callInitiatedCallbackUrl": callback_url,
            "callStatusCallbackUrl": callback_url,
        },
    )
    resp.raise_for_status()
    app = resp.json()
    app_id = app["id"]

    # Step 4: Search for available number
    resp = await client.get(
        f"/accounts/{account_id}/availableNumbers",
        params={"areaCode": area_code, "quantity": 1},
    )
    resp.raise_for_status()
    numbers = resp.json().get("telephoneNumbers", [])
    if not numbers:
        return {
            "siteId": site_id,
            "locationId": peer_id,
            "applicationId": app_id,
            "phoneNumber": None,
            "status": "complete_no_number",
            "message": f"Infrastructure created but no numbers available in area code {area_code}",
        }

    phone_number = numbers[0]

    # Step 5: Order the number
    resp = await client.post(
        f"/accounts/{account_id}/orders",
        json={
            "name": "Quickstart Order",
            "siteId": site_id,
            "existingTelephoneNumberOrderType": {
                "telephoneNumberList": [{"telephoneNumber": phone_number}],
            },
        },
    )
    resp.raise_for_status()
    order = resp.json()

    # Step 6: Poll order status (simple — check once)
    order_id = order["id"]
    resp = await client.get(f"/accounts/{account_id}/orders/{order_id}")
    resp.raise_for_status()

    return {
        "siteId": site_id,
        "locationId": peer_id,
        "applicationId": app_id,
        "phoneNumber": phone_number,
        "orderId": order_id,
        "status": "complete",
    }


def register_quickstart_tools(mcp: "FastMCP", config: dict):
    """Register the quickstartSetup composite tool on the MCP server."""

    @mcp.tool(name="quickstartSetup")
    async def quickstart_setup(
        callback_url: str,
        area_code: str = "919",
        site_name: str = "Agent Site",
    ) -> dict:
        """Provision a complete Bandwidth voice setup in one step.

        Creates a site, SIP peer (location), voice application, and orders a phone number.
        Returns all resource IDs needed to make and receive calls.

        Args:
            callback_url: URL where Bandwidth will send voice callbacks (answer, status, etc.)
            area_code: Area code to search for phone numbers (default: 919)
            site_name: Name for the site/sub-account (default: "Agent Site")
        """
        account_id = config.get("BW_ACCOUNT_ID", "")
        if not account_id:
            return {"error": "BW_ACCOUNT_ID is required for quickstart setup"}

        auth = create_auth_header(config["BW_USERNAME"], config["BW_PASSWORD"])
        async with httpx.AsyncClient(
            base_url="https://api.bandwidth.com/api/v2",
            headers={
                "Authorization": f"Basic {auth}",
                "User-Agent": "Bandwidth-MCP-Server/0.1.0",
            },
        ) as client:
            return await quickstart_flow(
                client=client,
                account_id=account_id,
                callback_url=callback_url,
                area_code=area_code,
                site_name=site_name,
            )
```

- [ ] **Step 4: Run tests**

```bash
cd /tmp/bw-mcp-server
python -m pytest test/test_quickstart.py -v
```

Expected: All pass.

- [ ] **Step 5: Wire into `src/app.py`**

In `src/app.py` `setup()`, after BXML registration:

```python
from src.tools.quickstart import register_quickstart_tools
register_quickstart_tools(mcp, config)
```

- [ ] **Step 6: Commit**

```bash
git add src/tools/quickstart.py test/test_quickstart.py src/app.py
git commit -m "feat: add quickstartSetup composite tool"
```

---

### Task 5: Build Listen Tools (Relay Integration)

**Files:**
- Create: `src/tools/listen.py`
- Create: `test/test_listen.py`
- Modify: `src/app.py`
- Modify: `pyproject.toml`

**Note:** These tools depend on the relay service (Phase 1b) being deployed. Tests mock the WebSocket connection.

- [ ] **Step 1: Add websockets dependency**

In `pyproject.toml`, add to dependencies:

```toml
websockets = ">=13.0"
```

Install:

```bash
cd /tmp/bw-mcp-server
pip install websockets
```

- [ ] **Step 2: Write failing tests**

Create `test/test_listen.py`:

```python
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_start_listening_connects_websocket():
    """startListening should open a WebSocket to the relay service."""
    from src.tools.listen import start_listening_flow

    mock_ws = AsyncMock()
    mock_ws.closed = False

    with patch("src.tools.listen.websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
        with patch("src.tools.listen.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            # Mock: get current app config
            mock_client.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"callInitiatedCallbackUrl": "https://original.com/callback"},
            )
            # Mock: update app callback URL
            mock_client.put.return_value = MagicMock(status_code=200)
            mock_client_cls.return_value = mock_client

            state = {"listeners": {}, "lock": asyncio.Lock()}
            result = await start_listening_flow(
                state=state,
                app_id="app-123",
                forward_to="http://localhost:3000",
                relay_url="wss://relay.bandwidth.com",
                config={"BW_USERNAME": "u", "BW_PASSWORD": "p", "BW_ACCOUNT_ID": "acct-1"},
            )

            assert result["status"] == "listening"
            assert "app-123" in state["listeners"]
            assert state["listeners"]["app-123"]["original_url"] == "https://original.com/callback"


@pytest.mark.asyncio
async def test_stop_listening_closes_websocket():
    """stopListening should close the WebSocket and restore the original URL."""
    from src.tools.listen import stop_listening_flow

    mock_ws = AsyncMock()
    mock_ws.closed = False
    mock_task = AsyncMock()

    state = {
        "listeners": {
            "app-123": {
                "ws": mock_ws,
                "task": mock_task,
                "original_url": "https://original.com/callback",
                "forward_to": "http://localhost:3000",
            },
        },
        "lock": asyncio.Lock(),
    }

    with patch("src.tools.listen.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.put.return_value = MagicMock(status_code=200)
        mock_client_cls.return_value = mock_client

        result = await stop_listening_flow(
            state=state,
            app_id="app-123",
            config={"BW_USERNAME": "u", "BW_PASSWORD": "p", "BW_ACCOUNT_ID": "acct-1"},
        )

        assert result["status"] == "stopped"
        assert "app-123" not in state["listeners"]
        mock_ws.close.assert_called_once()
        mock_task.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_stop_listening_nonexistent_app():
    """stopListening for an app that's not being listened to should error."""
    from src.tools.listen import stop_listening_flow

    state = {"listeners": {}, "lock": asyncio.Lock()}

    with pytest.raises(ValueError, match="No active listener"):
        await stop_listening_flow(
            state=state,
            app_id="app-nonexistent",
            config={"BW_USERNAME": "u", "BW_PASSWORD": "p", "BW_ACCOUNT_ID": "acct-1"},
        )
```

- [ ] **Step 3: Run to verify failure**

```bash
cd /tmp/bw-mcp-server
python -m pytest test/test_listen.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 4: Implement listen tools**

Create `src/tools/listen.py`:

```python
"""Listen tools — WebSocket relay integration for receiving voice callbacks locally."""

import asyncio
import json
import websockets
import httpx
from src.server_utils import create_auth_header

RELAY_URL = "wss://relay.bandwidth.com"


async def _forward_loop(ws, forward_to: str):
    """Background task: receive events from relay WebSocket, POST to local server."""
    async with httpx.AsyncClient() as client:
        try:
            async for message in ws:
                event = json.loads(message)
                callback_path = event.get("path", "/callback")
                url = f"{forward_to.rstrip('/')}{callback_path}"
                try:
                    resp = await client.post(
                        url,
                        json=event.get("body", {}),
                        headers={"Content-Type": "application/json"},
                    )
                    # Send BXML response back to relay
                    bxml_response = resp.text
                    await ws.send(json.dumps({
                        "type": "bxml_response",
                        "callbackId": event.get("callbackId"),
                        "bxml": bxml_response,
                    }))
                except httpx.HTTPError:
                    await ws.send(json.dumps({
                        "type": "error",
                        "callbackId": event.get("callbackId"),
                        "error": "Failed to forward to local server",
                    }))
        except websockets.ConnectionClosed:
            pass


async def start_listening_flow(
    state: dict,
    app_id: str,
    forward_to: str,
    config: dict,
    relay_url: str = RELAY_URL,
) -> dict:
    """Start listening for callbacks on an application.

    Connects to the relay service, swaps the app's callback URL, and begins forwarding.
    """
    async with state["lock"]:
        if app_id in state["listeners"]:
            raise ValueError(f"Already listening on app {app_id}")

    account_id = config.get("BW_ACCOUNT_ID", "")
    auth = create_auth_header(config["BW_USERNAME"], config["BW_PASSWORD"])
    headers = {
        "Authorization": f"Basic {auth}",
        "User-Agent": "Bandwidth-MCP-Server/0.1.0",
    }

    # Read current callback URL so we can restore it later
    async with httpx.AsyncClient(
        base_url="https://api.bandwidth.com/api/v2",
        headers=headers,
    ) as client:
        resp = await client.get(f"/accounts/{account_id}/applications/{app_id}")
        resp.raise_for_status()
        app_config = resp.json()
        original_url = app_config.get("callInitiatedCallbackUrl", "")

    # Connect to relay
    ws_url = f"{relay_url}/v1/listen/{app_id}"
    ws = await websockets.connect(ws_url, additional_headers=headers)

    # Start background forwarding task
    task = asyncio.create_task(_forward_loop(ws, forward_to))

    async with state["lock"]:
        state["listeners"][app_id] = {
            "ws": ws,
            "task": task,
            "original_url": original_url,
            "forward_to": forward_to,
        }

    return {
        "status": "listening",
        "app_id": app_id,
        "forward_to": forward_to,
        "original_callback_url": original_url,
    }


async def stop_listening_flow(
    state: dict,
    app_id: str,
    config: dict,
) -> dict:
    """Stop listening and restore the original callback URL."""
    async with state["lock"]:
        listener = state["listeners"].pop(app_id, None)
        if not listener:
            raise ValueError(f"No active listener for app {app_id}")

    # Clean up WebSocket
    listener["task"].cancel()
    await listener["ws"].close()

    # Restore original callback URL
    account_id = config.get("BW_ACCOUNT_ID", "")
    auth = create_auth_header(config["BW_USERNAME"], config["BW_PASSWORD"])
    async with httpx.AsyncClient(
        base_url="https://api.bandwidth.com/api/v2",
        headers={
            "Authorization": f"Basic {auth}",
            "User-Agent": "Bandwidth-MCP-Server/0.1.0",
        },
    ) as client:
        await client.put(
            f"/accounts/{account_id}/applications/{app_id}",
            json={"callInitiatedCallbackUrl": listener["original_url"]},
        )

    return {
        "status": "stopped",
        "app_id": app_id,
        "restored_callback_url": listener["original_url"],
    }


def register_listen_tools(mcp: "FastMCP", config: dict, lifespan_state: dict):
    """Register listen tools on the MCP server."""

    @mcp.tool(name="startListening")
    async def start_listening(app_id: str, forward_to: str = "http://localhost:3000") -> dict:
        """Start listening for voice callbacks on a Bandwidth application.

        Opens a WebSocket to the Bandwidth relay service and forwards incoming
        callbacks to your local server. Your server should respond with BXML.

        Args:
            app_id: The Bandwidth application ID to listen on
            forward_to: Local URL to forward callbacks to (default: http://localhost:3000)
        """
        return await start_listening_flow(
            state=lifespan_state,
            app_id=app_id,
            forward_to=forward_to,
            config=config,
        )

    @mcp.tool(name="stopListening")
    async def stop_listening(app_id: str) -> dict:
        """Stop listening for callbacks and restore the original callback URL.

        Args:
            app_id: The Bandwidth application ID to stop listening on
        """
        return await stop_listening_flow(
            state=lifespan_state,
            app_id=app_id,
            config=config,
        )

    @mcp.tool(name="getListenerStatus")
    async def get_listener_status() -> dict:
        """Get the status of all active callback listeners."""
        return {
            app_id: {
                "forward_to": info["forward_to"],
                "connected": not info["ws"].closed,
                "original_url": info["original_url"],
            }
            for app_id, info in lifespan_state["listeners"].items()
        }
```

- [ ] **Step 5: Run tests**

```bash
cd /tmp/bw-mcp-server
python -m pytest test/test_listen.py -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/tools/listen.py test/test_listen.py pyproject.toml
git commit -m "feat: add listen tools for relay WebSocket integration"
```

---

### Task 6: Wire Listen Tools with Lifespan Context into app.py

**Files:**
- Modify: `src/app.py`

- [ ] **Step 1: Add lifespan context and register listen tools**

Update `src/app.py` to use FastMCP's lifespan pattern for WebSocket state:

```python
import asyncio
import os

from fastmcp import FastMCP
from src.servers import create_bandwidth_mcp
from src.config import load_config, get_enabled_tools, get_excluded_tools
from src.tools.bxml import register_bxml_tools
from src.tools.quickstart import register_quickstart_tools
from src.tools.listen import register_listen_tools

os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

# Shared state for WebSocket listeners
_listen_state = {
    "listeners": {},
    "lock": asyncio.Lock(),
}

mcp = FastMCP(name="Bandwidth MCP")


async def setup():
    enabled_tools = get_enabled_tools()
    excluded_tools = get_excluded_tools()
    config = load_config()

    await create_bandwidth_mcp(mcp, enabled_tools, excluded_tools, config)

    # Register custom tools
    register_bxml_tools(mcp)
    register_quickstart_tools(mcp, config)
    register_listen_tools(mcp, config, _listen_state)


def main():
    asyncio.run(setup())
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run all tests**

```bash
cd /tmp/bw-mcp-server
python -m pytest test/ -v
```

Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/app.py
git commit -m "feat: wire all custom tools into MCP server with listen state"
```

---

### Task 7: Update README and Create PR

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README with new capabilities**

Add sections for:
- New API coverage (accounts, applications, numbers, voice)
- Custom tools (generateBxml, validateBxml, quickstartSetup)
- Listen tools (startListening, stopListening, getListenerStatus)
- Updated tool filtering examples

- [ ] **Step 2: Run full test suite one final time**

```bash
cd /tmp/bw-mcp-server
python -m pytest test/ -v
```

Expected: All tests pass.

- [ ] **Step 3: Commit and create PR**

```bash
git add README.md
git commit -m "docs: update README with Phase 1 capabilities"
git push -u origin feat/phase1-voice-expansion
gh pr create \
  --title "feat: full voice flow — specs, BXML, quickstart, listen" \
  --body "$(cat <<'EOF'
## Summary
- Adds accounts, applications, number-management, number-acquisition, and voice API specs
- Adds generateBxml / validateBxml tools for BXML construction
- Adds quickstartSetup composite tool (site → location → app → number in one call)
- Adds startListening / stopListening / getListenerStatus tools for relay integration
- Depends on relay service (Phase 1b) for listen functionality

## Test plan
- [ ] All existing tests pass
- [ ] New API specs generate correct tool counts
- [ ] BXML generation produces valid XML for all verb types
- [ ] BXML validation catches invalid XML and unknown verbs
- [ ] Quickstart flow calls APIs in correct sequence
- [ ] Listen tools manage WebSocket state correctly
- [ ] Stop listening restores original callback URL

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Summary

| Task | What | Effort |
|------|------|--------|
| 1 | Add 5 API specs + fixtures + tests | 30 min |
| 2 | BXML generation & validation | 30 min |
| 3 | Register BXML tools on MCP | 15 min |
| 4 | Quickstart composite tool | 30 min |
| 5 | Listen tools (relay integration) | 45 min |
| 6 | Wire listen state into app.py | 15 min |
| 7 | README + PR | 15 min |
| **Total** | | **~3 hours** |

**After this ships, an agent can:**
1. Register an account (Phase 0)
2. Run `quickstartSetup` to provision everything in one call
3. `startListening` to receive callbacks locally
4. `createCall` to make a call
5. `generateBxml` to respond to callbacks
6. `stopListening` when done
