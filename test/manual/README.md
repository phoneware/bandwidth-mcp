# `test/manual/`: proving a tool against a stand-in Bandwidth

A unit test tells you a function built the string you expected. It does not
tell you the server registered the tool, that the deployment's profile filter
kept it, that JSON-RPC dispatch reaches it, or what bytes actually left the
process. That gap is where a hand-written Dashboard tool goes wrong.

This harness closes it without spending a live carrier call. Everything is
real except the upstream HTTP boundary: a real server process, a real MCP
handshake, real `tools/call` dispatch, real XML serialization. Only
`api.bandwidth.com` is replaced, by pointing `BW_API_URL` at a local server
that records what it receives.

It is not part of `pytest` and CI does not run it. Run it by hand when you add
or change a tool that puts something on the wire.

## Running it

You need an interpreter with the repo's pins. `pip install .` does NOT work
here (the upstream `pyproject.toml` omits modules such as `urls`, so the
installed package cannot import itself). Build a venv from the same list
`cloudbuild.yaml` installs:

```
uv venv /tmp/bwmcp-venv
uv pip install --python /tmp/bwmcp-venv/bin/python --prerelease=allow \
  "fastmcp==4.0.0b1" "mcp>=2.0.0,<3" "httpx~=0.28.0" "httpx2~=2.9" \
  "pyyaml~=6.0.0" "werkzeug>=3.1.4" "google-cloud-firestore==2.21.0" \
  tzdata uvicorn pytest pytest-asyncio pytest-httpx
```

Then, from anywhere:

```
/tmp/bwmcp-venv/bin/python test/manual/drive.py /path/to/the/checkout
```

`BW_PROOF_PYTHON` overrides the interpreter used to spawn the server if you
want to drive it with a different one.

## What it currently covers

The call forwarding tools (`setCallForwarding`, `getCallForwarding`,
`listTnOptionOrders`, `getTnOptionOrder`). 30 checks: registration,
annotations, the exact `TnOptionOrder` XML including child order and number
normalisation, `systemDefault` on a clear, the two-step site and peer
resolution behind the read, and that a rejected input sends no HTTP request
at all.

## The rule that makes it worth anything

**Run it against the commit BEFORE your change and watch it fail.** A check
that passes on both trees is measuring nothing. When the call forwarding tools
were added it scored 30/30 on the branch and 2/20 on the merge-base, and the
two that still passed did so vacuously, which is the kind of thing you only
find by looking.

Note that a tool failure arrives either as a JSON-RPC `error` or as a result
with `isError` set. Checking only the first makes "unknown tool" read as
success, which is exactly how the first draft of this harness reported six
passing checks against a tree that had none of the tools.

## Adding coverage for a new tool

Add routes to `fake_bw.py` for the endpoints your tool touches, keyed on path.
Always keep `POST /api/v1/oauth2/token` answering, since the server mints its
token at startup and nothing else runs until it does. Then add a section to
`drive.py` that calls the tool and asserts on entries in the recorded request
list, not on the tool's return value: the return value is a summary, the
recorded request is what Bandwidth would have seen.
