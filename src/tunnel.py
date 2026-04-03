"""Auto-tunnel for dev mode using cloudflared.

Starts a Cloudflare quick tunnel so the MCP server's callback routes
are reachable from the internet without manual setup. For development
and testing only — production deployments should use a real host and
set BW_MCP_BASE_URL.
"""

import subprocess
import time
import re
import atexit
import warnings
from typing import Optional

_tunnel_process: Optional[subprocess.Popen] = None


def start_tunnel(port: int) -> Optional[str]:
    """Start a cloudflared tunnel and return the public URL.

    Returns None if cloudflared isn't installed or tunnel fails to start.
    """
    global _tunnel_process

    try:
        # Check if cloudflared is available
        subprocess.run(
            ["cloudflared", "--version"],
            capture_output=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        warnings.warn(
            "cloudflared not installed. Callback URLs won't work without a public URL. "
            "Install with: brew install cloudflared (macOS) or see https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/",
            UserWarning,
        )
        return None

    # Start the tunnel
    _tunnel_process = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Register cleanup
    atexit.register(stop_tunnel)

    # Wait for the URL to appear in stderr
    url = _wait_for_url(_tunnel_process, timeout=15)
    if url:
        print(f"Tunnel active: {url}")
    else:
        warnings.warn("Failed to start cloudflared tunnel — timed out waiting for URL.", UserWarning)
        stop_tunnel()

    return url


def _wait_for_url(process: subprocess.Popen, timeout: int = 15) -> Optional[str]:
    """Read cloudflared stderr until we find the tunnel URL."""
    start = time.time()
    while time.time() - start < timeout:
        if process.poll() is not None:
            return None
        line = process.stderr.readline().decode("utf-8", errors="replace")
        # cloudflared prints the URL like: https://xxx-xxx.trycloudflare.com
        match = re.search(r"(https://[a-zA-Z0-9-]+\.trycloudflare\.com)", line)
        if match:
            return match.group(1)
    return None


def stop_tunnel() -> None:
    """Stop the cloudflared tunnel if running."""
    global _tunnel_process
    if _tunnel_process and _tunnel_process.poll() is None:
        _tunnel_process.terminate()
        _tunnel_process.wait(timeout=5)
    _tunnel_process = None
