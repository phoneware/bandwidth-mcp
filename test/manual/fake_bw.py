"""Stand-in for api.bandwidth.com.

Answers the OAuth token endpoint and the Dashboard XML routes the call
forwarding tools touch, and records every request it receives so the driver
can assert on the bytes the MCP server actually put on the wire.

Only the upstream HTTP boundary is faked. The MCP server, its JSON-RPC
transport, its handlers, and its XML serialization are all real.
"""

import base64
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ACCOUNT = "5555555"

REQUESTS: list[dict] = []
_LOCK = threading.Lock()


def _jwt(payload: dict) -> str:
    def seg(d: dict) -> str:
        raw = json.dumps(d, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{seg({'alg': 'none', 'typ': 'JWT'})}.{seg(payload)}.sig"


# Two numbers: one forwarded, one with no CallForward element at all.
TN_FORWARDED = "9195551234"
TN_BARE = "9195550000"
SITE_ID = "479"
PEER_ID = "500014"

TNDETAILS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<TelephoneNumberResponse>
  <TelephoneNumberDetails>
    <City>RALEIGH</City>
    <State>NC</State>
    <FullNumber>{tn}</FullNumber>
    <Status>Inservice</Status>
    <AccountId>{acct}</AccountId>
    <Site>
      <Id>{site}</Id>
      <Name>phoneware-main</Name>
    </Site>
    <SipPeer>
      <PeerId>{peer}</PeerId>
      <PeerName>edge</PeerName>
    </SipPeer>
  </TelephoneNumberDetails>
</TelephoneNumberResponse>"""

SIPPEER_TN_FORWARDED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<SipPeerTelephoneNumberResponse>
  <SipPeerTelephoneNumber>
    <FullNumber>{tn}</FullNumber>
    <CallForward>7042661720</CallForward>
    <CallingNameDisplay>true</CallingNameDisplay>
  </SipPeerTelephoneNumber>
</SipPeerTelephoneNumberResponse>"""

SIPPEER_TN_BARE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<SipPeerTelephoneNumberResponse>
  <SipPeerTelephoneNumber>
    <FullNumber>{tn}</FullNumber>
    <CallingNameDisplay>false</CallingNameDisplay>
  </SipPeerTelephoneNumber>
</SipPeerTelephoneNumberResponse>"""

ORDER_CREATED = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<TnOptionOrderResponse>
  <TnOptionOrder>
    <OrderCreateDate>2026-09-15T12:01:14.324Z</OrderCreateDate>
    <AccountId>{acct}</AccountId>
    <OrderId>tnopt-abc123</OrderId>
    <ProcessingStatus>RECEIVED</ProcessingStatus>
  </TnOptionOrder>
</TnOptionOrderResponse>"""

ORDER_STATUS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<TnOptionOrder>
  <OrderId>tnopt-abc123</OrderId>
  <ProcessingStatus>COMPLETE</ProcessingStatus>
  <TnOptionGroups>
    <TnOptionGroup>
      <CallForward>7042661720</CallForward>
      <TelephoneNumbers>
        <TelephoneNumber>{tn}</TelephoneNumber>
      </TelephoneNumbers>
    </TnOptionGroup>
  </TnOptionGroups>
  <ErrorList/>
</TnOptionOrder>"""

ORDER_LIST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<TnOptionOrders>
  <TotalCount>1</TotalCount>
  <TnOptionOrderSummary>
    <OrderId>tnopt-abc123</OrderId>
    <ProcessingStatus>COMPLETE</ProcessingStatus>
    <OrderType>tn_option</OrderType>
  </TnOptionOrderSummary>
</TnOptionOrders>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep stdout clean for the driver
        pass

    def _record(self, method: str, body: bytes):
        parsed = urlparse(self.path)
        with _LOCK:
            REQUESTS.append(
                {
                    "method": method,
                    "path": parsed.path,
                    "query": parsed.query,
                    "body": body.decode("utf-8", "replace") if body else "",
                    "authorization": self.headers.get("Authorization", ""),
                    "contentType": self.headers.get("Content-Type", ""),
                }
            )

    def _send(self, status: int, body: str, ctype: str, extra: dict | None = None):
        raw = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/__requests":
            with _LOCK:
                self._send(200, json.dumps(REQUESTS), "application/json")
            return

        self._record("GET", b"")
        acct = f"/api/v2/accounts/{ACCOUNT}"

        if path.startswith("/api/v2/tns/") and path.endswith("/tndetails"):
            tn = path.split("/")[4]
            self._send(
                200,
                TNDETAILS.format(tn=tn, acct=ACCOUNT, site=SITE_ID, peer=PEER_ID),
                "application/xml",
            )
            return

        if path.startswith(f"{acct}/sites/") and "/tns/" in path:
            tn = path.rsplit("/", 1)[-1]
            tpl = SIPPEER_TN_FORWARDED if tn == TN_FORWARDED else SIPPEER_TN_BARE
            self._send(200, tpl.format(tn=tn), "application/xml")
            return

        if path == f"{acct}/tnoptions":
            self._send(200, ORDER_LIST, "application/xml")
            return

        if path.startswith(f"{acct}/tnoptions/"):
            self._send(200, ORDER_STATUS.format(tn=TN_FORWARDED), "application/xml")
            return

        self._send(404, f"<Error>no fake route for {path}</Error>", "application/xml")

    def do_POST(self):
        body = self._read_body()
        parsed = urlparse(self.path)
        path = parsed.path
        self._record("POST", body)

        if path == "/api/v1/oauth2/token":
            token = _jwt({"accounts": [ACCOUNT], "sub": "fake"})
            self._send(
                200,
                json.dumps({"access_token": token, "token_type": "Bearer", "expires_in": 3600}),
                "application/json",
            )
            return

        if path == f"/api/v2/accounts/{ACCOUNT}/tnoptions":
            self._send(
                201,
                ORDER_CREATED.format(acct=ACCOUNT),
                "application/xml",
                {"Location": f"https://api.bandwidth.com/api/v2/accounts/{ACCOUNT}/tnoptions/tnopt-abc123"},
            )
            return

        self._send(404, f"<Error>no fake route for {path}</Error>", "application/xml")


def serve(port: int = 0) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


if __name__ == "__main__":
    srv = serve(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    print(srv.server_address[1], flush=True)
    threading.Event().wait()
