# `test/`: the suite

`pytest` with `pytest-asyncio` and `pytest-httpx`. It is the gate that runs in PR
CI (`.github/workflows/test-pr.yml`, py3.10-3.14 on ubuntu + windows) and again
as the first Cloud Build step before any deploy. Keep it green.

## Run
```
pip install ".[dev]"
python -m pytest -q            # from the repo root
```
`conftest.py` puts `src/` on `sys.path` and `pyproject.toml` sets
`pythonpath = ["."]`, so imports resolve without installing the package (which
you should not do; see the root `CLAUDE.md`).

## No live network
Spec fetches are mocked. `test/utils.py:create_mock` serves the trimmed OpenAPI
specs in `test/fixtures/*.yml` in place of the real `dev.bandwidth.com/spec/*`
URLs, so the suite runs offline and deterministically. When you add or change a
spec source in `servers.py`, add the matching fixture and mock.

## Helpers (`test/utils.py`)
- `tool_map(mcp)`: `{name: tool}` from `list_tools()` (fastmcp 3.x dropped the
  dict-returning `get_tools()`).
- `server_client(mcp)`: the shared httpx client behind a `from_openapi` server's
  tools (per-tool `tool._client` in 3.x).
- `create_mock(httpx_mock, spec_name)`: mock a spec URL from a fixture.

## What is covered
Config/profile resolution (`test_config`, `test_profiles`), server assembly and
tool counts (`test_servers`, `test_openapi`, `test_integration`), spec caching
(`test_spec_cache`), the event store and callback tools (`test_event_store`,
`test_callbacks`, `test_callback_tools`), BXML generation (`test_bxml`),
credentials/auth (`test_credentials`), Build registration (`test_build`),
instructions (`test_instructions`), host resolution (`test_urls`), the
hand-written Numbers/Dashboard tools (`test_numbers`), and the hosted-mode
safety gate (`test_hosted_safety`).

When adding a hand-written tool, add a test that asserts it registers under its
profile and that its request shape matches the Bandwidth quirk it encodes
(paging params, E.164 vs 10-digit, XML body structure).
