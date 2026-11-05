#!/usr/bin/env python3
"""Upload an existing local port-in document through Phoneware's hosted MCP.

Run from a Python environment with this project's dependencies installed:
    python scripts/upload_portin_loa.py --order-id ORDER_ID --file /path/to/document.pdf --confirm UPLOADPORTINLOA

The OAuth authorization URL is printed for the operator to open. Tokens remain
in memory. This helper does not create or modify an authorization document.
"""

import argparse
import asyncio
import base64
import hashlib
import os
from pathlib import Path
import stat
import sys
from typing import Any

from fastmcp import Client
from fastmcp.client.auth.oauth import OAuth


MCP_URL = "https://mcp.bandwidth.phoneware.cloud/mcp"
MAX_DOCUMENT_BYTES = 3_000_000
CONFIRMATION = "UPLOADPORTINLOA"

class UploadError(Exception):
    """A deliberately safe message for the operator."""




class ManualOAuth(OAuth):
    """Present the authorization URL without opening the operator's browser."""

    async def redirect_handler(self, authorization_url: str) -> None:
        print(
            "Open this authorization URL in your browser and sign in:\n"
            f"{authorization_url}",
            file=sys.stderr,
            flush=True,
        )


def _read_document(file_path: Path) -> bytes:
    # O_NONBLOCK prevents opening a FIFO from hanging before fstat can reject it.
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(file_path, flags)
        with os.fdopen(fd, "rb") as document:
            info = os.fstat(document.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise UploadError("The selected document must be a regular file.")
            if info.st_size == 0 or info.st_size > MAX_DOCUMENT_BYTES:
                raise UploadError("The document must contain 1 to 3,000,000 bytes.")
            content = document.read(MAX_DOCUMENT_BYTES + 1)
    except OSError as exc:
        raise UploadError("Cannot read the selected document.") from exc
    if not content or len(content) > MAX_DOCUMENT_BYTES or len(content) != info.st_size:
        raise UploadError("The document is empty, too large, or changed while being read.")
    return content


async def upload_port_in_document(
    *,
    client: Client,
    order_id: str,
    file_path: Path,
    account_id: str = "",
    document_type: str = "LOA",
    confirm: str = "",
) -> dict[str, Any]:
    """Send selected local bytes and their digest to the authenticated MCP client.

    Any exception during dispatch leaves the upload outcome unknown. Check the
    order's documents before retrying. No exception text from the remote call is
    exposed, since a transport or tool error can include request contents.
    """
    if confirm != CONFIRMATION:
        raise UploadError("Pass --confirm UPLOADPORTINLOA to authorize the upload.")
    if not order_id.strip():
        raise UploadError("An order ID is required.")
    file_path = Path(file_path)
    content = _read_document(file_path)
    arguments = {
        "order_id": order_id,
        "file_base64": base64.b64encode(content).decode("ascii"),
        "file_sha256": hashlib.sha256(content).hexdigest(),
        "filename": file_path.name,
        "document_type": document_type,
        "account_id": account_id,
        "confirm": confirm,
    }
    try:
        result = await client.call_tool("uploadPortInLoa", arguments)
    except Exception:
        raise UploadError(
            "Upload outcome unknown. Inspect the order's document list before retrying."
        ) from None
    if result.is_error:
        raise UploadError(
            "The upload tool reported an error. Inspect the order's document list before retrying."
        )
    if not isinstance(result.data, dict) or result.data.get("error"):
        raise UploadError(
            "Upload did not return a confirmed success. Inspect the order's document list before retrying."
        )
    return result.data


async def _run(args: argparse.Namespace) -> None:
    auth = ManualOAuth(mcp_url=MCP_URL, client_name="Phoneware local document upload")
    async with Client(MCP_URL, auth=auth) as client:
        await upload_port_in_document(
            client=client,
            order_id=args.order_id,
            file_path=args.file,
            account_id=args.account_id,
            document_type=args.document_type,
            confirm=args.confirm,
        )
    print("Upload request completed. Check the order's document list to verify the attachment.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--order-id", required=True, help="Existing port-in order ID")
    parser.add_argument("--file", required=True, type=Path, help="Existing local document")
    parser.add_argument("--account-id", default="", help="Optional Bandwidth account ID")
    parser.add_argument("--document-type", default="LOA", choices=("LOA", "INVOICE", "CSR", "OTHER"))
    parser.add_argument("--confirm", required=True, help="Type UPLOADPORTINLOA to authorize upload")
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        parser.error("--confirm must be UPLOADPORTINLOA")
    try:
        asyncio.run(_run(args))
    except UploadError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        # Do not print transport exceptions: they can contain tokens or payloads.
        print("Connection or authorization failed. If an upload was dispatched, inspect the order's document list before retrying.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())