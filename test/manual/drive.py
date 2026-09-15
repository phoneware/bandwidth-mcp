"""Drive the real Bandwidth MCP server over stdio JSON-RPC against the fake
Dashboard, then assert on the HTTP requests it actually emitted.

Everything here is real except the upstream HTTP boundary: a real server
process, real MCP handshake, real tools/call dispatch, real XML bodies.
"""

import json
import os
import subprocess
import sys
import threading
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_bw  # noqa: E402

# The checkout to exercise (defaults to cwd), and the interpreter that has the
# repo's pins. Point BW_PROOF_PYTHON at a venv built from cloudbuild.yaml's
# install list; `pip install .` does not work here, the package omits modules.
WORKTREE = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
PY = os.environ.get("BW_PROOF_PYTHON") or sys.executable

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = ""):
    global checks
    checks += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        failures.append(f"{label}{(': ' + detail) if detail else ''}")
        if detail:
            print(f"        {detail}")


class Server:
    def __init__(self, port: int):
        env = dict(os.environ)
        env.update(
            {
                "PYTHONPATH": "src",
                "PYTHONUNBUFFERED": "1",
                "BW_API_URL": f"http://127.0.0.1:{port}",
                "BW_CLIENT_ID": "fake-client-id",
                "BW_CLIENT_SECRET": "fake-client-secret",
                "BW_MCP_PROFILE": "numbers,numbers-write",
                "BW_MCP_TRANSPORT": "stdio",
            }
        )
        self.proc = subprocess.Popen(
            [PY, "src/app.py"],
            cwd=WORKTREE,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.stderr: list[str] = []
        threading.Thread(target=self._drain, daemon=True).start()
        self._id = 0

    def _drain(self):
        for line in self.proc.stderr:
            self.stderr.append(line.rstrip())

    def call(self, method: str, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._id += 1
            msg["id"] = self._id
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        if notify:
            return None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(
                    "server closed stdout. stderr tail:\n" + "\n".join(self.stderr[-40:])
                )
            try:
                got = json.loads(line)
            except json.JSONDecodeError:
                continue
            if got.get("id") == self._id:
                return got

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def tool_text(resp: dict) -> str:
    result = resp.get("result") or {}
    if resp.get("error"):
        return json.dumps(resp["error"])
    parts = [c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)]
    if result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"]))
    return "\n".join(parts)


def is_error(resp: dict) -> bool:
    """A tool failure arrives either as a JSON-RPC error or as a result with
    isError set. Checking only the first makes "unknown tool" look like
    success, which is exactly how a vacuous check gets written."""
    if resp.get("error"):
        return True
    return bool((resp.get("result") or {}).get("isError"))


def main():
    httpd = fake_bw.serve()
    port = httpd.server_address[1]
    print(f"fake bandwidth on 127.0.0.1:{port}")
    print(f"worktree: {WORKTREE}\n")

    srv = Server(port)
    try:
        init = srv.call(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "callforward-proof", "version": "1"},
            },
        )
        if init.get("error"):
            raise RuntimeError(
                f"initialize failed: {init['error']}\nstderr:\n"
                + "\n".join(srv.stderr[-40:])
            )
        srv.call("notifications/initialized", {}, notify=True)

        listed = srv.call("tools/list", {})
        names = [t["name"] for t in listed["result"]["tools"]]
        print(f"tools on the live surface ({len(names)}): {', '.join(sorted(names))}\n")

        print("[1] the four tools are actually registered and reachable")
        for name in (
            "setCallForwarding",
            "getCallForwarding",
            "listTnOptionOrders",
            "getTnOptionOrder",
        ):
            check(f"{name} in tools/list", name in names)

        annotations = {t["name"]: t.get("annotations") or {} for t in listed["result"]["tools"]}
        check(
            "setCallForwarding is annotated as a write",
            annotations.get("setCallForwarding", {}).get("readOnlyHint") is False,
            json.dumps(annotations.get("setCallForwarding")),
        )
        check(
            "getCallForwarding is annotated read-only",
            annotations.get("getCallForwarding", {}).get("readOnlyHint") is True,
            json.dumps(annotations.get("getCallForwarding")),
        )

        print("\n[2] set a forward: the XML that actually goes on the wire")
        r = srv.call(
            "tools/call",
            {
                "name": "setCallForwarding",
                "arguments": {
                    "numbers": ["+1 (919) 555-1234", "9195550000"],
                    "forward_to": "704-266-1720",
                    "customer_order_id": "rick-cf-1",
                },
            },
        )
        print("   response:", tool_text(r)[:400])
        check("setCallForwarding returned without error", not is_error(r), tool_text(r)[:300])

        print("\n[3] clear a forward")
        r_clear = srv.call(
            "tools/call",
            {"name": "setCallForwarding", "arguments": {"numbers": ["9195551234"]}},
        )
        check("clearing returned without error", not is_error(r_clear), tool_text(r_clear)[:300])

        print("\n[4] read the forward back on a number that has one")
        r_get = srv.call(
            "tools/call", {"name": "getCallForwarding", "arguments": {"number": "+19195551234"}}
        )
        got = tool_text(r_get)
        print("   response:", got[:400])
        check("getCallForwarding reports the destination", "7042661720" in got, got[:300])

        print("\n[5] read a number with no forward set")
        r_bare = srv.call(
            "tools/call", {"name": "getCallForwarding", "arguments": {"number": "9195550000"}}
        )
        bare = tool_text(r_bare)
        print("   response:", bare[:400])
        check("no forward is an answer, not an error", not is_error(r_bare), bare[:300])
        check(
            "reports not forwarding",
            '"forwarding":false' in bare.replace(" ", "").lower()
            or '"forwarding": false' in bare.lower(),
            bare[:300],
        )

        print("\n[6] order history and order status")
        r_list = srv.call(
            "tools/call", {"name": "listTnOptionOrders", "arguments": {"number": "9195551234"}}
        )
        check("listTnOptionOrders returned without error", not is_error(r_list), tool_text(r_list)[:300])
        r_ord = srv.call(
            "tools/call", {"name": "getTnOptionOrder", "arguments": {"order_id": "tnopt-abc123"}}
        )
        ordtext = tool_text(r_ord)
        check("getTnOptionOrder surfaces ProcessingStatus", "COMPLETE" in ordtext, ordtext[:300])

        print("\n[7] a bad destination is refused before it reaches the carrier")
        before = len(fetch_requests(port))
        r_bad = srv.call(
            "tools/call",
            {"name": "setCallForwarding", "arguments": {"numbers": ["9195551234"], "forward_to": "12345"}},
        )
        after_reqs = fetch_requests(port)
        check("a 5-digit destination is rejected", is_error(r_bad), tool_text(r_bad)[:300])
        check(
            "and no HTTP request was sent for it",
            len(after_reqs) == before,
            f"{len(after_reqs) - before} extra request(s)",
        )

    finally:
        reqs = fetch_requests(port)
        srv.close()

    print("\n[8] assertions on the recorded HTTP traffic")
    posts = [r for r in reqs if r["method"] == "POST" and r["path"].endswith("/tnoptions")]
    gets = [r for r in reqs if r["method"] == "GET"]

    check("exactly two tnoptions POSTs were sent", len(posts) == 2, f"got {len(posts)}")

    if posts:
        body = posts[0]["body"]
        print("\n   POST", posts[0]["path"], "\n   body:", body)
        check("posted to the account-scoped tnoptions path", posts[0]["path"] == "/api/v2/accounts/5555555/tnoptions")
        check("sent as application/xml", "xml" in posts[0]["contentType"])
        check("carried the bearer token", posts[0]["authorization"].startswith("Bearer "))
        check("root element is TnOptionOrder", body.startswith("<TnOptionOrder>"))
        check("destination normalised to bare 10-digit", "<CallForward>7042661720</CallForward>" in body)
        check("both numbers normalised to bare 10-digit",
              "<TelephoneNumber>9195551234</TelephoneNumber>" in body
              and "<TelephoneNumber>9195550000</TelephoneNumber>" in body)
        check("customer order id carried", "<CustomerOrderId>rick-cf-1</CustomerOrderId>" in body)
        if "<CallForward>" in body and "<TelephoneNumbers>" in body:
            check("CallForward precedes TelephoneNumbers, per Bandwidth's schema",
                  body.index("<CallForward>") < body.index("<TelephoneNumbers>"))

    if len(posts) > 1:
        clear_body = posts[1]["body"]
        print("\n   POST (clear) body:", clear_body)
        check("clearing emits systemDefault", "<CallForward>systemDefault</CallForward>" in clear_body)
        check("clearing sends no CustomerOrderId", "CustomerOrderId" not in clear_body)

    paths = [r["path"] + (("?" + r["query"]) if r["query"] else "") for r in gets]
    print("\n   GETs:")
    for p in paths:
        print("    ", p)
    check("read resolved the TN's site and peer first",
          "/api/v2/tns/9195551234/tndetails" in paths)
    check("then read the per-TN SIP peer record",
          "/api/v2/accounts/5555555/sites/479/sippeers/500014/tns/9195551234" in paths)
    check("order history filtered by tn",
          any(p.startswith("/api/v2/accounts/5555555/tnoptions?") and "tn=9195551234" in p for p in paths),
          str([p for p in paths if "tnoptions" in p]))
    check("order status read by id",
          "/api/v2/accounts/5555555/tnoptions/tnopt-abc123" in paths)

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        sys.exit(1)
    print("ALL CHECKS PASSED")


def fetch_requests(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/__requests", timeout=10) as r:
        return json.loads(r.read())


if __name__ == "__main__":
    main()
