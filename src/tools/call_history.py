"""Curated Call History tools over the Bandwidth Insights API.

Provides:
  - getCallDetailRecords: Asynchronous CDR report generation over the Insights
    reporting engine (Call Detail Records (CDRs)). Polls until complete, unpacks
    the CSV zip archive in memory, and filters by number and time window.
  - searchVoiceCalls: Real-time call search over Insights /v1/voice/calls,
    with time range operators (gte:, lte:) and quality metrics (latency, jitter,
    packet loss, MOS).
  - getVoiceCall: Single call detail by callId from Insights /v1/voice/calls/{callId}.
"""

from __future__ import annotations

import csv
import io
import time
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from tools.discovery import _resolve_account
from urls import insights_base

_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def _format_iso_millis(dt_str: str) -> str:
    """Ensure an ISO 8601 timestamp string has millisecond precision and ends with Z."""
    s = dt_str.strip()
    try:
        clean = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_utc = dt.astimezone(timezone.utc)
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    except Exception:
        # Heuristic fallback: if missing milliseconds, inject .000
        if s.endswith("Z"):
            if "." in s:
                return s
            return s[:-1] + ".000Z"
        if "." not in s:
            return s + ".000Z"
        return s + "Z"


def _clean_number(num: str) -> str:
    """Normalize phone number to digits only for flexible matching."""
    return "".join(c for c in num if c.isdigit())


def register_call_history_tools(mcp: FastMCP, config: Dict[str, Any]) -> None:
    """Register curated call history tools."""

    @mcp.tool(
        name="getCallDetailRecords",
        title="Get Call Detail Records (CDRs)",
        description=(
            "Request and download historical Call Detail Records (CDRs) from the Bandwidth "
            "Insights async reporting engine. Polls the report job until ready, unpacks the CSV, "
            "and filters by phone number and time window. Returns matched records and total count. "
            "Dates must be ISO timestamps (e.g. '2026-10-06T00:00:00Z')."
        ),
        annotations=_READ,
    )
    async def get_call_detail_records(
        start_time: str,
        end_time: str,
        phone_number: str = "",
        limit: int = 50,
        account_id: str = "",
    ) -> Dict[str, Any]:
        token = config.get("BW_ACCESS_TOKEN")
        if not token:
            raise RuntimeError("Not authenticated with Bandwidth.")

        account = _resolve_account(config, account_id)
        start_iso = _format_iso_millis(start_time)
        end_iso = _format_iso_millis(end_time)

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        base_url = f"{insights_base().rstrip('/')}/api/v1"

        # 1. Request report generation
        payload = {
            "reportName": "Call Detail Records (CDRs)",
            "category": "USAGE",
            "domain": "VOICE",
            "region": "US",
            "accountIds": [str(account)],
            "filters": {
                "callInviteTime": [start_iso, end_iso],
            },
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base_url}/reports", headers=headers, json=payload
            )
            if resp.status_code == 403:
                return {
                    "error": (
                        "The Bandwidth API credential lacks the Insights voice reporting role. "
                        "Enable Voice Insights in the Bandwidth App."
                    ),
                    "status_code": 403,
                }
            if resp.status_code not in (200, 201, 202):
                return {
                    "error": f"Failed to create CDR report request: {resp.text}",
                    "status_code": resp.status_code,
                }

            create_data = resp.json().get("data", {})
            report_id = create_data.get("reportId") or create_data.get("id")
            if not report_id:
                return {
                    "error": "No reportId returned by Insights API",
                    "response": resp.json(),
                }

            # 2. Poll report status
            poll_headers = {"Authorization": f"Bearer {token}"}
            max_attempts = 20
            poll_interval = 2.0
            completed = False

            for attempt in range(max_attempts):
                await httpx.AsyncClient().aclose()  # allow event loop tick
                time.sleep(poll_interval)
                status_resp = await client.get(
                    f"{base_url}/reports/{report_id}", headers=poll_headers
                )
                if status_resp.status_code == 200:
                    rep_data = status_resp.json().get("data", {})
                    status = rep_data.get("status")
                    if status == "COMPLETED":
                        completed = True
                        break
                    elif status in ("FAILED", "ERROR"):
                        return {
                            "error": f"CDR report generation failed: {rep_data.get('errorMessage', 'Unknown error')}",
                            "status": status,
                            "report_id": report_id,
                        }

            if not completed:
                return {
                    "error": f"CDR report timed out after {int(max_attempts * poll_interval)} seconds",
                    "report_id": report_id,
                    "status": "PENDING",
                }

            # 3. Download report file
            file_resp = await client.get(
                f"{base_url}/reports/{report_id}/file", headers=poll_headers
            )
            if file_resp.status_code != 200:
                return {
                    "error": f"Failed to download report file: {file_resp.text}",
                    "status_code": file_resp.status_code,
                }

            # 4. Parse zip archive and CSV
            calls: List[Dict[str, Any]] = []
            clean_filter_num = _clean_number(phone_number) if phone_number else ""

            try:
                z = zipfile.ZipFile(io.BytesIO(file_resp.content))
                for filename in z.namelist():
                    if not filename.endswith(".csv"):
                        continue
                    csv_text = z.read(filename).decode("utf-8", errors="replace")
                    reader = csv.DictReader(io.StringIO(csv_text))
                    for row in reader:
                        src = row.get("Call Source", "")
                        dst = row.get("Call Destination", "")
                        if clean_filter_num:
                            src_digits = _clean_number(src)
                            dst_digits = _clean_number(dst)
                            if (clean_filter_num not in src_digits) and (
                                clean_filter_num not in dst_digits
                            ):
                                continue

                        calls.append(
                            {
                                "callId": row.get("Call ID"),
                                "startTime": row.get("Call Start Time"),
                                "duration": int(row.get("Duration", 0) or 0),
                                "callingNumber": src,
                                "calledNumber": dst,
                                "direction": row.get("Direction"),
                                "result": row.get("Result"),
                                "responseCode": row.get("Response Code"),
                                "hangupSource": row.get("Hangup Source"),
                                "attestation": row.get("Attestation"),
                                "postDialDelay": row.get("Post Dial Delay"),
                                "sourceCountry": row.get("Source Country"),
                                "destinationCountry": row.get("Destination Country"),
                                "packetsSent": row.get("Packets Sent"),
                                "packetsReceived": row.get("Packets Received"),
                            }
                        )
            except Exception as e:
                return {
                    "error": f"Failed to extract and parse CSV from zip archive: {e}"
                }

            total_records = len(calls)
            return {
                "report_id": report_id,
                "total_records": total_records,
                "matched_records": total_records,
                "returned_records": min(total_records, limit),
                "calls": calls[:limit],
            }

    @mcp.tool(
        name="searchVoiceCalls",
        title="Search Real-Time Voice Calls (Insights)",
        description=(
            "Search real-time voice calls via Bandwidth Voice Insights. Supports filtering by "
            "time window (start_time, end_time), phone number, call direction (inbound, outbound), "
            "and result (completed, busy, failed). Exposes full call metadata and network quality "
            "metrics: latency, jitter, packet loss, and MOS scores."
        ),
        annotations=_READ,
    )
    async def search_voice_calls(
        start_time: str,
        end_time: str,
        phone_number: str = "",
        calling_number: str = "",
        called_number: str = "",
        direction: str = "",
        result: str = "",
        limit: int = 25,
        sort: str = "startTime:desc",
        account_id: str = "",
    ) -> Dict[str, Any]:
        token = config.get("BW_ACCESS_TOKEN")
        if not token:
            raise RuntimeError("Not authenticated with Bandwidth.")

        account = _resolve_account(config, account_id)
        base_url = f"{insights_base().rstrip('/')}/api/v1/voice/calls"

        # Format operator-prefixed timestamps required by Insights
        s_time = start_time.strip()
        if not any(s_time.startswith(op) for op in ("gte:", "gt:", "lte:", "lt:")):
            s_time = f"gte:{s_time}"

        e_time = end_time.strip()
        if not any(e_time.startswith(op) for op in ("gte:", "gt:", "lte:", "lt:")):
            e_time = f"lte:{e_time}"

        params: Dict[str, Any] = {
            "accountId": str(account),
            "startTime": s_time,
            "endTime": e_time,
            "limit": min(limit, 100),
            "sort": sort,
        }

        if calling_number:
            params["callingNumber"] = calling_number
        elif phone_number and not called_number:
            # If a generic phone number is provided, search callingNumber or check
            params["callingNumber"] = phone_number

        if called_number:
            params["calledNumber"] = called_number

        if direction:
            params["callDirection"] = direction.lower()

        if result:
            params["callResult"] = result.lower()

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(base_url, headers=headers, params=params)
            if resp.status_code == 403:
                return {
                    "error": (
                        "The Bandwidth API credential lacks the Insights voice role (voice_insights). "
                        "Enable Voice Insights in the Bandwidth App."
                    ),
                    "status_code": 403,
                }
            if resp.status_code != 200:
                return {
                    "error": f"Failed to search voice calls: {resp.text}",
                    "status_code": resp.status_code,
                }

            data = resp.json().get("data", {})
            total_count = data.get("totalCount", 0)
            raw_calls = data.get("calls", [])

            formatted_calls = []
            for c in raw_calls:
                formatted_calls.append(
                    {
                        "callId": c.get("callId"),
                        "startTime": c.get("startTime"),
                        "endTime": c.get("endTime"),
                        "duration": c.get("duration"),
                        "callingNumber": c.get("callingNumber"),
                        "calledNumber": c.get("calledNumber"),
                        "callDirection": c.get("callDirection"),
                        "callType": c.get("callType"),
                        "callResult": c.get("callResult"),
                        "sipResponseCode": c.get("sipResponseCode"),
                        "sipResponseDescription": c.get("sipResponseDescription"),
                        "hangUpSource": c.get("hangUpSource"),
                        "postDialDelay": c.get("postDialDelay"),
                        "cost": c.get("cost"),
                        "locationName": c.get("locationName"),
                        "subAccountName": c.get("subAccountName"),
                        "attestationIndicator": c.get("attestationIndicator"),
                        "quality": {
                            "carrierLatency": c.get("carrierLatency"),
                            "customerLatency": c.get("customerLatency"),
                            "carrierJitter": c.get("carrierJitter"),
                            "customerJitter": c.get("customerJitter"),
                            "carrierPacketLossPercentage": c.get(
                                "carrierPacketLossPercentage"
                            ),
                            "customerPacketLossPercentage": c.get(
                                "customerPacketLossPercentage"
                            ),
                            "customerSbcCustomerSendMos": c.get(
                                "customerSbcCustomerSendMos"
                            ),
                            "customerSbcCustomerReceiveMos": c.get(
                                "customerSbcCustomerReceiveMos"
                            ),
                            "customerSbcCarrierSendMos": c.get(
                                "customerSbcCarrierSendMos"
                            ),
                            "customerSbcCarrierReceiveMos": c.get(
                                "customerSbcCarrierReceiveMos"
                            ),
                        },
                    }
                )

            return {
                "totalCount": total_count,
                "returnedCount": len(formatted_calls),
                "calls": formatted_calls,
            }

    @mcp.tool(
        name="getVoiceCall",
        title="Get Voice Call Details (Insights)",
        description="Retrieve comprehensive details and network quality metrics for a single call by callId from Bandwidth Voice Insights.",
        annotations=_READ,
    )
    async def get_voice_call(call_id: str, account_id: str = "") -> Dict[str, Any]:
        token = config.get("BW_ACCESS_TOKEN")
        if not token:
            raise RuntimeError("Not authenticated with Bandwidth.")

        account = _resolve_account(config, account_id)
        url = f"{insights_base().rstrip('/')}/api/v1/voice/calls/{call_id}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        params = {"accountId": str(account)}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 403:
                return {
                    "error": (
                        "The Bandwidth API credential lacks the Insights voice role (voice_insights). "
                        "Enable Voice Insights in the Bandwidth App."
                    ),
                    "status_code": 403,
                }
            if resp.status_code == 404:
                return {"error": f"Call '{call_id}' not found", "status_code": 404}
            if resp.status_code != 200:
                return {
                    "error": f"Failed to get call: {resp.text}",
                    "status_code": resp.status_code,
                }

            data = resp.json().get("data", {})
            return data
