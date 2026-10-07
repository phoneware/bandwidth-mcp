"""Script to refresh vendored Bandwidth OpenAPI specs and regenerate coverage report."""

from __future__ import annotations

import subprocess
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SPECS_DIR = REPO_ROOT / "src" / "specs"

SPEC_SOURCES = [
    ("numbers.yml", "https://dev.bandwidth.com/spec/numbers.yml"),
    ("voice.yml", "https://dev.bandwidth.com/spec/voice.yml"),
    ("messaging.yml", "https://dev.bandwidth.com/spec/messaging.yml"),
    ("insights.yml", "https://dev.bandwidth.com/spec/insights.yml"),
    ("phone-number-lookup-v2.yml", "https://dev.bandwidth.com/spec/phone-number-lookup-v2.yml"),
    ("end-user-management.yml", "https://dev.bandwidth.com/spec/end-user-management.yml"),
    ("toll-free-verification.yml", "https://dev.bandwidth.com/spec/toll-free-verification.yml"),
    ("multi-factor-auth.yml", "https://dev.bandwidth.com/spec/multi-factor-auth.yml"),
]


def refresh_specs() -> None:
    print(f"Refreshing {len(SPEC_SOURCES)} vendored specs under {SPECS_DIR}...")
    SPECS_DIR.mkdir(parents=True, exist_ok=True)

    for filename, url in SPEC_SOURCES:
        target = SPECS_DIR / filename
        print(f"Fetching {url} -> {filename}...")
        req = urllib.request.Request(url, headers={"User-Agent": "BandwidthMCP/1.0"})
        with urllib.request.urlopen(req) as resp:
            content = resp.read()
            target.write_bytes(content)
            print(f"  Saved {filename} ({len(content)} bytes)")

    print("\nRegenerating coverage report...")
    gen_script = REPO_ROOT / "scripts" / "generate_coverage_report.py"
    subprocess.run([sys.executable, str(gen_script)], check=True)
    print("Done. Review and commit changes to lock the new spec baseline.")


if __name__ == "__main__":
    refresh_specs()
