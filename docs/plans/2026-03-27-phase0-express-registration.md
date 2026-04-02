# Phase 0: Express Registration in MCP Server — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Express Registration API tools (createRegistration, sendVerificationCode, verifyCode) to the Bandwidth MCP server so agents can autonomously create Bandwidth accounts when Express launches March 30.

**Architecture:** The MCP server already generates tools from OpenAPI specs via `FastMCP.from_openapi()`. We add the Express spec URL to `api_server_info` in `servers.py`, add a test fixture, and it works. The Express API requires no authentication (`security: []`), so we need to handle the case where the auth header should NOT be sent.

**Tech Stack:** Python 3.10+, FastMCP, httpx, pytest, pytest-asyncio, pytest-httpx

**Repo:** `Bandwidth/mcp-server` (clone to work in it)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/servers.py` | Modify | Add Express spec to `api_server_info`, handle no-auth APIs |
| `src/server_utils.py` | Modify | Support optional auth (Express has `security: []`) |
| `src/config.py` | Modify | Make BW_USERNAME/BW_PASSWORD optional (Express works without credentials) |
| `test/fixtures/express.yml` | Create | Express OpenAPI spec fixture for testing |
| `test/test_servers.py` | Modify | Add Express server creation tests, update tool count |
| `test/test_express.py` | Create | Express-specific integration tests |
| `test/test_config.py` | Create | Test that config loads without credentials |

---

### Task 1: Clone Repo and Verify Existing Tests Pass

- [ ] **Step 1: Clone the repo**

```bash
gh repo clone Bandwidth/mcp-server /tmp/bw-mcp-impl
cd /tmp/bw-mcp-impl
```

- [ ] **Step 2: Install dependencies**

```bash
pip install -e ".[dev]" 2>/dev/null || pip install -e .
pip install -r dev-requirements.txt
```

- [ ] **Step 3: Run existing tests to establish baseline**

```bash
cd /tmp/bw-mcp-impl
python -m pytest test/ -v
```

Expected: All existing tests pass. Note the total tool count (currently 47 across 5 APIs).

- [ ] **Step 4: Commit baseline (no changes, just verify)**

No commit needed — this is verification only.

---

### Task 2: Add Express Spec Test Fixture

**Files:**
- Create: `test/fixtures/express.yml`

- [ ] **Step 1: Write the Express OpenAPI spec fixture**

Create the test fixture from the actual Express spec. This is a minimal version with the 3 endpoints:

```yaml
openapi: 3.0.3
info:
  title: Bandwidth Express Registration
  version: 1.0.0
  description: Express Registration API for new customer onboarding
servers:
  - url: https://api.bandwidth.com/v1/express
    description: Production
security: []
paths:
  /registration:
    post:
      operationId: createRegistration
      summary: Initialize a new customer registration
      description: Validates phone number and email are not already in use, creates registration record, begins user creation process
      security: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/registerRequest'
      responses:
        '200':
          description: Registration created successfully
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/registerResponse'
  /registration/code:
    post:
      operationId: sendVerificationCode
      summary: Send or resend SMS verification code
      description: Sends 6-digit SMS code to phone number on registration record
      security: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/sendRequest'
      responses:
        '200':
          description: Verification code sent
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/sendResponse'
  /registration/code/verify:
    post:
      operationId: verifyCode
      summary: Validate SMS verification code
      description: Validates 6-digit code against Bandwidth MFA API
      security: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/verifyRequest'
      responses:
        '200':
          description: Phone number verified
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/verifyResponse'
components:
  schemas:
    registerRequest:
      type: object
      required:
        - phoneNumber
        - email
        - firstName
        - lastName
      additionalProperties: false
      properties:
        phoneNumber:
          type: string
          description: US phone number in E.164 format
          example: "+19195551234"
        email:
          type: string
          description: Email address (must be @bandwidth.com domain)
          example: "user@bandwidth.com"
        firstName:
          type: string
          description: Customer's first name
          example: "Jane"
        lastName:
          type: string
          description: Customer's last name
          example: "Doe"
    registerResponse:
      type: object
      properties:
        links:
          type: array
          items: {}
        data:
          type: object
          properties:
            message:
              type: string
              example: "Onboarding request received successfully"
            status:
              type: string
              example: "USER_CREATION_PENDING"
            registrationId:
              type: string
              format: uuid
              example: "5fce253e-fdbc-433f-829b-6839d1edbebe"
        errors:
          type: array
          items: {}
    sendRequest:
      type: object
      required:
        - phoneNumber
        - email
      additionalProperties: false
      properties:
        phoneNumber:
          type: string
          description: US phone number in E.164 format
          example: "+19195551234"
        email:
          type: string
          description: Email address used during registration
          example: "user@bandwidth.com"
    sendResponse:
      type: object
      properties:
        links:
          type: array
          items: {}
        data:
          type: object
          properties:
            message:
              type: string
              example: "Code successfully sent to +19195551234"
            status:
              type: string
              example: "VERIFICATION_CODE_SENT"
        errors:
          type: array
          items: {}
    verifyRequest:
      type: object
      required:
        - phoneNumber
        - code
        - email
      additionalProperties: false
      properties:
        phoneNumber:
          type: string
          description: US phone number in E.164 format
          example: "+19195551234"
        code:
          type: string
          description: 6-digit SMS verification code
          example: "123456"
        email:
          type: string
          description: Email address used during registration
          example: "user@bandwidth.com"
    verifyResponse:
      type: object
      properties:
        links:
          type: array
          items: {}
        data:
          type: object
          properties:
            message:
              type: string
              example: "+19195551234 successfully verified"
            status:
              type: string
              example: "PHONE_VERIFIED"
            registrationId:
              type: string
              format: uuid
              example: "5fce253e-fdbc-433f-829b-6839d1edbebe"
        errors:
          type: array
          items: {}
```

- [ ] **Step 2: Verify the fixture is valid YAML**

```bash
cd /tmp/bw-mcp-impl
python -c "import yaml; yaml.safe_load(open('test/fixtures/express.yml')); print('Valid YAML')"
```

Expected: `Valid YAML`

- [ ] **Step 3: Commit**

```bash
git add test/fixtures/express.yml
git commit -m "test: add Express Registration API spec fixture"
```

---

### Task 3: Zero-to-One Auth Flow — Optional Startup Credentials + setCredentials Tool

**Files:**
- Modify: `src/config.py`
- Modify: `src/servers.py`
- Modify: `src/app.py`
- Create: `src/tools/credentials.py`
- Create: `test/test_config.py`
- Create: `test/test_credentials.py`

Express is the 0→1 step. An agent with no Bandwidth account starts the MCP server with no credentials, calls Express to register, gets credentials back, then needs to use those credentials for everything after — without restarting the server.

This requires three changes:
1. `load_config()` doesn't crash when credentials are missing
2. Authenticated API servers skip loading at startup if no credentials (with a warning)
3. A `setCredentials` tool lets the agent inject credentials mid-session and triggers loading of the authenticated API servers

- [ ] **Step 1: Write failing tests**

Create `test/test_config.py`:

```python
import os
import pytest
from unittest.mock import patch


def test_load_config_without_credentials():
    """MCP server should start without BW_USERNAME/BW_PASSWORD."""
    with patch.dict(os.environ, {}, clear=True):
        for key in list(os.environ.keys()):
            if key.startswith("BW_"):
                del os.environ[key]

        from src.config import load_config
        config = load_config()
        assert config.get("BW_USERNAME") is None


def test_load_config_with_credentials():
    """When credentials are provided, they should be in the config."""
    with patch.dict(os.environ, {"BW_USERNAME": "user", "BW_PASSWORD": "pass"}):
        from src.config import load_config
        config = load_config()
        assert config["BW_USERNAME"] == "user"
        assert config["BW_PASSWORD"] == "pass"
```

Create `test/test_credentials.py`:

```python
import pytest
from fastmcp import FastMCP


@pytest.mark.asyncio
async def test_set_credentials_tool_registered():
    """setCredentials should be a tool on the MCP server."""
    from src.tools.credentials import register_credentials_tools

    mcp = FastMCP(name="Test")
    config = {}
    register_credentials_tools(mcp, config, reload_callback=None)
    tools = await mcp.get_tools()
    assert "setCredentials" in tools


@pytest.mark.asyncio
async def test_set_credentials_updates_config():
    """setCredentials should update the shared config dict."""
    from src.tools.credentials import register_credentials_tools

    config = {}
    reload_called = []

    async def mock_reload():
        reload_called.append(True)

    mcp = FastMCP(name="Test")
    register_credentials_tools(mcp, config, reload_callback=mock_reload)

    # Simulate calling the tool
    from src.tools.credentials import set_credentials_flow
    result = await set_credentials_flow(
        config=config,
        username="new_user",
        password="new_pass",
        account_id="acct-123",
        reload_callback=mock_reload,
    )

    assert config["BW_USERNAME"] == "new_user"
    assert config["BW_PASSWORD"] == "new_pass"
    assert config["BW_ACCOUNT_ID"] == "acct-123"
    assert result["status"] == "credentials_set"
    assert len(reload_called) == 1  # reload was triggered
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd /tmp/bw-mcp-impl
python -m pytest test/test_config.py test/test_credentials.py -v
```

Expected: FAIL.

- [ ] **Step 3: Make credentials optional in `src/config.py`**

```python
def load_config() -> Dict[str, str]:
    config = {}

    # All vars are optional at startup — Express works without auth.
    # Authenticated APIs need BW_USERNAME + BW_PASSWORD, which can be
    # set later via the setCredentials tool after Express registration.
    all_vars = [
        "BW_USERNAME", "BW_PASSWORD", "BW_ACCOUNT_ID",
        "BW_NUMBER", "BW_MESSAGING_APPLICATION_ID", "BW_VOICE_APPLICATION_ID",
    ]
    for var in all_vars:
        value = os.environ.get(var)
        if value:
            config[var] = value

    if "BW_USERNAME" not in config or "BW_PASSWORD" not in config:
        import warnings
        warnings.warn(
            "BW_USERNAME/BW_PASSWORD not set. Only Express Registration tools will be available. "
            "Use the setCredentials tool after registration to enable authenticated APIs."
        )

    return config
```

- [ ] **Step 4: Update `_create_server` to check credentials before building auth header**

In `src/servers.py`, modify the `requires_auth=True` path:

```python
if requires_auth:
    if "BW_USERNAME" not in config or "BW_PASSWORD" not in config:
        raise ValueError("BW_USERNAME and BW_PASSWORD required for authenticated APIs")
    auth_header = create_auth_header(config["BW_USERNAME"], config["BW_PASSWORD"])
    headers["Authorization"] = f"Basic {auth_header}"
```

The existing try/except in `create_bandwidth_mcp` already catches this ValueError per-server and logs a warning, so authenticated servers gracefully skip when credentials are missing.

- [ ] **Step 5: Create the setCredentials tool**

Create `src/tools/__init__.py`:

```python
```

Create `src/tools/credentials.py`:

```python
"""Credentials tool — allows agents to set Bandwidth API credentials mid-session.

This enables the zero-to-one flow: an agent starts with no credentials,
uses Express Registration to create an account, receives credentials,
then calls setCredentials to unlock authenticated API tools.
"""

from typing import Callable, Optional


async def set_credentials_flow(
    config: dict,
    username: str,
    password: str,
    account_id: str,
    reload_callback: Optional[Callable] = None,
) -> dict:
    """Update the shared config with new credentials and reload authenticated servers."""
    config["BW_USERNAME"] = username
    config["BW_PASSWORD"] = password
    config["BW_ACCOUNT_ID"] = account_id

    if reload_callback:
        await reload_callback()

    return {
        "status": "credentials_set",
        "username": username,
        "account_id": account_id,
        "message": "Credentials set. Authenticated API tools are now available.",
    }


def register_credentials_tools(
    mcp: "FastMCP",
    config: dict,
    reload_callback: Optional[Callable] = None,
):
    """Register the setCredentials tool on the MCP server."""

    @mcp.tool(name="setCredentials")
    async def set_credentials(
        username: str,
        password: str,
        account_id: str,
    ) -> dict:
        """Set Bandwidth API credentials after Express Registration.

        Call this after creating an account via createRegistration + verifyCode.
        This enables all authenticated API tools (voice, numbers, messaging, etc.).

        Args:
            username: Bandwidth API username
            password: Bandwidth API password
            account_id: Bandwidth account ID
        """
        return await set_credentials_flow(
            config=config,
            username=username,
            password=password,
            account_id=account_id,
            reload_callback=reload_callback,
        )
```

- [ ] **Step 6: Wire into `src/app.py` with a reload callback**

Update `src/app.py` to register the credentials tool and provide a callback that loads authenticated API servers when credentials are set:

```python
import asyncio
import os
import warnings

from fastmcp import FastMCP
from src.servers import create_bandwidth_mcp, api_server_info, _create_server
from src.config import load_config, get_enabled_tools, get_excluded_tools
from src.server_utils import create_route_map_fn
from src.tools.credentials import register_credentials_tools

os.environ["FASTMCP_EXPERIMENTAL_ENABLE_NEW_OPENAPI_PARSER"] = "true"

mcp = FastMCP(name="Bandwidth MCP")
_config = {}


async def _reload_authenticated_servers():
    """Load authenticated API servers after credentials are set mid-session."""
    enabled_tools = get_enabled_tools()
    excluded_tools = get_excluded_tools()
    route_map_fn = create_route_map_fn(enabled_tools, excluded_tools)

    for api_name, api_info in api_server_info.items():
        requires_auth = api_info.get("requires_auth", True)
        if not requires_auth:
            continue  # Skip Express — already loaded
        try:
            server = await _create_server(
                url=api_info["url"],
                route_map_fn=route_map_fn,
                config=_config,
                requires_auth=True,
            )
            await mcp.import_server(server)
        except Exception as e:
            warnings.warn(f"Failed to load {api_name} after credential update: {e}")


async def setup():
    global _config
    enabled_tools = get_enabled_tools()
    excluded_tools = get_excluded_tools()
    _config = load_config()

    await create_bandwidth_mcp(mcp, enabled_tools, excluded_tools, _config)

    # Always register setCredentials — needed for zero-to-one flow
    register_credentials_tools(mcp, _config, reload_callback=_reload_authenticated_servers)


def main():
    asyncio.run(setup())
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run all tests**

```bash
cd /tmp/bw-mcp-impl
python -m pytest test/ -v
```

Expected: All pass — existing tests provide credentials so authenticated servers load normally. New tests verify the no-credentials startup and setCredentials flow.

- [ ] **Step 8: Commit**

```bash
git add src/config.py src/servers.py src/app.py src/tools/__init__.py src/tools/credentials.py test/test_config.py test/test_credentials.py
git commit -m "feat: zero-to-one auth flow — optional startup credentials + setCredentials tool"
```

---

### Task 4: Write Failing Tests for Express Server Creation

**Files:**
- Create: `test/test_express.py`
- Reference: `test/test_servers.py` (for patterns)
- Reference: `test/utils.py` (for create_mock helper)

- [ ] **Step 1: Write test for Express server creation without auth**

The Express API has `security: []` — no authentication required. The current `_create_server` always attaches a Basic Auth header. We need to test that Express works without auth.

```python
import pytest
from test.utils import create_mock


@pytest.mark.asyncio
async def test_express_server_has_three_tools(httpx_mock):
    """Express Registration API should expose exactly 3 tools."""
    create_mock(httpx_mock, "express")

    from src.servers import _create_server

    server = await _create_server(
        url="https://dev.bandwidth.com/spec/express.yml",
        config={"BW_USERNAME": "user", "BW_PASSWORD": "pass"},
    )
    tools = await server.get_tools()
    assert len(tools) == 3


@pytest.mark.asyncio
async def test_express_server_tool_names(httpx_mock):
    """Express tools should have correct operation IDs."""
    create_mock(httpx_mock, "express")

    from src.servers import _create_server

    server = await _create_server(
        url="https://dev.bandwidth.com/spec/express.yml",
        config={"BW_USERNAME": "user", "BW_PASSWORD": "pass"},
    )
    tools = await server.get_tools()
    tool_names = sorted(tools.keys())
    assert tool_names == ["createRegistration", "sendVerificationCode", "verifyCode"]


@pytest.mark.asyncio
async def test_express_server_no_auth_header(httpx_mock):
    """Express API requires no auth — server should work without credentials."""
    create_mock(httpx_mock, "express")

    from src.servers import _create_server

    server = await _create_server(
        url="https://dev.bandwidth.com/spec/express.yml",
        config={},
        requires_auth=False,
    )
    tools = await server.get_tools()
    assert len(tools) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /tmp/bw-mcp-impl
python -m pytest test/test_express.py -v
```

Expected: FAIL — `_create_server` doesn't accept `requires_auth` parameter, and the mock for "express" doesn't exist in `test/utils.py` yet.

- [ ] **Step 3: Commit failing tests**

```bash
git add test/test_express.py
git commit -m "test: add failing tests for Express Registration server"
```

---

### Task 4: Add Express Mock to Test Utils

**Files:**
- Modify: `test/utils.py`

- [ ] **Step 1: Read current test/utils.py**

Read the file to see the `create_mock` function signature and pattern.

- [ ] **Step 2: Verify create_mock already handles "express"**

The `create_mock` function loads `test/fixtures/{spec_name}.yml`. Since we created `test/fixtures/express.yml` in Task 2, the mock should work automatically — `create_mock(httpx_mock, "express")` loads `test/fixtures/express.yml`.

Verify by checking the function reads from `test/fixtures/{spec_name}.yml`.

- [ ] **Step 3: Run the Express tests again to confirm the mock works**

```bash
cd /tmp/bw-mcp-impl
python -m pytest test/test_express.py::test_express_server_has_three_tools -v
```

Expected: First two tests may pass (they use default config with auth). Third test (`test_express_server_no_auth_header`) still fails because `_create_server` doesn't accept `requires_auth`.

---

### Task 5: Implement No-Auth Support in _create_server

**Files:**
- Modify: `src/servers.py`
- Modify: `src/server_utils.py`

- [ ] **Step 1: Read current src/servers.py**

Read the `_create_server` function to understand the current implementation.

- [ ] **Step 2: Add `requires_auth` parameter to `_create_server`**

In `src/servers.py`, modify the `_create_server` function to accept an optional `requires_auth` parameter. When `False`, skip the auth header:

```python
async def _create_server(
    url: str,
    route_map_fn=None,
    config: dict = {},
    requires_auth: bool = True,
) -> "FastMCP":
    spec_object = await fetch_openapi_spec(url)

    if "servers" not in spec_object or len(spec_object["servers"]) == 0:
        raise ValueError(f"OpenAPI spec at {url} does not contain any servers")

    base_url = spec_object["servers"][0]["url"]

    headers = {"User-Agent": "Bandwidth-MCP-Server/0.1.0"}
    if requires_auth:
        auth_header = create_auth_header(config["BW_USERNAME"], config["BW_PASSWORD"])
        headers["Authorization"] = f"Basic {auth_header}"

    client = httpx.AsyncClient(base_url=base_url, headers=headers)

    server = FastMCP.from_openapi(
        openapi_spec=spec_object,
        client=client,
        name="Bandwidth",
        route_map_fn=route_map_fn,
    )

    return server
```

- [ ] **Step 3: Run the Express tests**

```bash
cd /tmp/bw-mcp-impl
python -m pytest test/test_express.py -v
```

Expected: All 3 Express tests pass.

- [ ] **Step 4: Run all tests to verify no regressions**

```bash
cd /tmp/bw-mcp-impl
python -m pytest test/ -v
```

Expected: All tests pass (existing + new Express tests).

- [ ] **Step 5: Commit**

```bash
git add src/servers.py test/test_express.py
git commit -m "feat: support no-auth APIs in _create_server"
```

---

### Task 6: Add Express to api_server_info

**Files:**
- Modify: `src/servers.py`

- [ ] **Step 1: Add Express entry to api_server_info**

In `src/servers.py`, add the Express Registration API to the `api_server_info` dict. Include a `requires_auth` flag:

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
}
```

- [ ] **Step 2: Update `create_bandwidth_mcp` to pass `requires_auth`**

In the `create_bandwidth_mcp` function, when iterating through `api_server_info`, pass `requires_auth` to `_create_server`:

```python
async def create_bandwidth_mcp(
    mcp: FastMCP,
    enabled_tools=None,
    excluded_tools=None,
    config: dict = {},
) -> FastMCP:
    route_map_fn = create_route_map_fn(enabled_tools, excluded_tools)

    for api_name, api_info in api_server_info.items():
        try:
            requires_auth = api_info.get("requires_auth", True)
            server = await _create_server(
                url=api_info["url"],
                route_map_fn=route_map_fn,
                config=config,
                requires_auth=requires_auth,
            )
            await mcp.import_server(server)
        except Exception as e:
            warnings.warn(f"Failed to create {api_name} server: {e}")

    add_resources(mcp, config)
    await print_server_info(mcp)
    return mcp
```

- [ ] **Step 3: Update test tool count**

In `test/test_servers.py`, the `calculate_expected_tools` function uses a default `total_tools=47`. With 3 new Express tools, update to `total_tools=50`:

```python
def calculate_expected_tools(tools=None, excluded_tools=None, total_tools=50):
```

Also update any parameterized test cases that hardcode tool counts.

- [ ] **Step 4: Add Express fixture mock to the full server test**

In `test/test_servers.py`, find where the other fixtures are mocked (e.g., `create_mock(httpx_mock, "messaging")`). Add:

```python
create_mock(httpx_mock, "express")
```

- [ ] **Step 5: Run all tests**

```bash
cd /tmp/bw-mcp-impl
python -m pytest test/ -v
```

Expected: All tests pass, including the full server creation test now showing 50 tools.

- [ ] **Step 6: Commit**

```bash
git add src/servers.py test/test_servers.py
git commit -m "feat: add Express Registration API to MCP server"
```

---

### Task 7: Verify Express Spec URL Will Work at Launch

**Files:** None (verification only)

- [ ] **Step 1: Confirm the Express spec URL pattern**

The existing specs are fetched from `https://dev.bandwidth.com/spec/{name}.yml`. The Express spec is in `api-specs/internal/express.yml` on the `DREAM-2177-bw-express-registration` branch.

When that branch merges to main (at Express launch), verify the spec will be published to `https://dev.bandwidth.com/spec/express.yml`. If the URL is different (e.g., it stays under `internal/` or has a different path), update the URL in `api_server_info`.

- [ ] **Step 2: Check if internal specs are published to dev.bandwidth.com**

The `internal/` directory in `api-specs` may not be published to the public docs site. If that's the case, the MCP server will need to either:
- Reference the spec from a different URL (e.g., raw GitHub content)
- Have the spec moved to `external/` before launch
- Bundle the spec locally in the repo

Document the decision and update the URL if needed.

- [ ] **Step 3: Test with the real spec URL (after merge)**

Once the Express branch merges:

```bash
curl -s https://dev.bandwidth.com/spec/express.yml | head -5
```

Expected: The OpenAPI spec YAML content. If 404, the URL needs updating.

---

### Task 8: Write Integration Test for Full Express Flow

**Files:**
- Modify: `test/test_express.py`

- [ ] **Step 1: Add test that validates Express tools have correct parameter schemas**

```python
@pytest.mark.asyncio
async def test_create_registration_tool_parameters(httpx_mock):
    """createRegistration tool should require phoneNumber, email, firstName, lastName."""
    create_mock(httpx_mock, "express")

    from src.servers import _create_server

    server = await _create_server(
        url="https://dev.bandwidth.com/spec/express.yml",
        config={},
        requires_auth=False,
    )
    tools = await server.get_tools()
    create_reg = tools["createRegistration"]
    schema = create_reg.inputSchema
    assert "phoneNumber" in schema.get("properties", {}) or "requestBody" in str(schema)


@pytest.mark.asyncio
async def test_verify_code_tool_parameters(httpx_mock):
    """verifyCode tool should require phoneNumber, code, email."""
    create_mock(httpx_mock, "express")

    from src.servers import _create_server

    server = await _create_server(
        url="https://dev.bandwidth.com/spec/express.yml",
        config={},
        requires_auth=False,
    )
    tools = await server.get_tools()
    verify = tools["verifyCode"]
    schema = verify.inputSchema
    assert "code" in str(schema) or "verifyRequest" in str(schema)
```

- [ ] **Step 2: Run all tests**

```bash
cd /tmp/bw-mcp-impl
python -m pytest test/ -v
```

Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add test/test_express.py
git commit -m "test: add Express tool parameter validation tests"
```

---

### Task 9: Update README and Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read current README**

Read the README to understand the current documentation structure.

- [ ] **Step 2: Add Express Registration to the API list**

In the section that lists supported APIs, add:

```markdown
### Express Registration
- `createRegistration` - Register a new Bandwidth account
- `sendVerificationCode` - Send SMS verification code
- `verifyCode` - Verify phone number with SMS code

> Note: Express Registration does not require authentication. These tools work without `BW_USERNAME`/`BW_PASSWORD`.
```

- [ ] **Step 3: Add Express to the use cases / tool filtering examples**

Add a recommended configuration for account creation:

```markdown
#### Account Creation Flow
```
BW_MCP_TOOLS=createRegistration,sendVerificationCode,verifyCode
```
```

- [ ] **Step 4: Run tests one final time**

```bash
cd /tmp/bw-mcp-impl
python -m pytest test/ -v
```

Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: add Express Registration API to README"
```

---

### Task 10: Create Pull Request

- [ ] **Step 1: Create a feature branch (if not already on one)**

```bash
cd /tmp/bw-mcp-impl
git checkout -b feat/express-registration
```

- [ ] **Step 2: Push and create PR**

```bash
git push -u origin feat/express-registration
gh pr create \
  --title "feat: add Express Registration API support" \
  --body "$(cat <<'EOF'
## Summary
- Adds Express Registration API (createRegistration, sendVerificationCode, verifyCode) to the MCP server
- Supports no-auth APIs via `requires_auth` flag on `_create_server`
- Ships alongside Express API launch (March 30)

## Changes
- `src/servers.py`: Added Express to `api_server_info`, added `requires_auth` parameter
- `test/fixtures/express.yml`: Express OpenAPI spec fixture
- `test/test_express.py`: Express-specific tests
- `test/test_servers.py`: Updated tool counts
- `README.md`: Documentation for Express tools

## Test plan
- [ ] All existing tests pass (no regressions)
- [ ] Express server creates 3 tools
- [ ] Express tools have correct operation IDs
- [ ] Express server works without auth credentials
- [ ] Tool parameter schemas include required fields
- [ ] Verify Express spec URL resolves after branch merge
EOF
)"
```

---

## Summary

| Task | What | Effort |
|------|------|--------|
| 1 | Clone & verify baseline | 5 min |
| 2 | Create Express spec fixture | 10 min |
| 3 | Write failing Express tests | 10 min |
| 4 | Verify mock setup | 5 min |
| 5 | Implement no-auth support | 15 min |
| 6 | Add Express to server config | 15 min |
| 7 | Verify spec URL at launch | 5 min (blocked until merge) |
| 8 | Integration tests | 10 min |
| 9 | Update README | 10 min |
| 10 | Create PR | 5 min |
| **Total** | | **~90 min** |
