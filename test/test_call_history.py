"""Tests for curated call history tools."""

import csv
import io
import zipfile
import pytest
from pytest_httpx import HTTPXMock
from fastmcp import FastMCP

from tools.call_history import (
    _format_iso_millis,
    _clean_number,
    register_call_history_tools,
)
from utils import tool_map


def test_format_iso_millis():
    assert _format_iso_millis("2026-10-06T00:00:00Z") == "2026-10-06T00:00:00.000Z"
    assert _format_iso_millis("2026-10-06T00:00:00") == "2026-10-06T00:00:00.000Z"
    assert _format_iso_millis("2026-10-06T12:30:45.123Z") == "2026-10-06T12:30:45.123Z"


def test_clean_number():
    assert _clean_number("+1 (480) 555-1212") == "14805551212"
    assert _clean_number("+19195551234") == "19195551234"
    assert _clean_number("9195551234") == "9195551234"


def make_mock_zip(rows: list[dict]) -> bytes:
    csv_buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(csv_buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    else:
        csv_buf.write(
            "Customer ID,Call ID,Call Start Time,Duration,Call Source,Call Destination\n"
        )

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("test_cdrs.csv", csv_buf.getvalue())
    return zip_buf.getvalue()


@pytest.mark.asyncio
async def test_get_call_detail_records_flow(httpx_mock: HTTPXMock):
    config = {
        "BW_ACCESS_TOKEN": "mock-token",
        "BW_ACCOUNT_ID": "5011369",
        "BW_ACCOUNTS": ["5011369"],
    }
    mcp = FastMCP("test")
    register_call_history_tools(mcp, config)

    report_id = "test-report-uuid-1234"
    # 1. POST /reports
    httpx_mock.add_response(
        method="POST",
        url="https://insights.bandwidth.com/api/v1/reports",
        status_code=202,
        json={"data": {"reportId": report_id, "status": "PENDING"}},
    )

    # 2. GET /reports/{id} -> COMPLETED
    httpx_mock.add_response(
        method="GET",
        url=f"https://insights.bandwidth.com/api/v1/reports/{report_id}",
        status_code=200,
        json={"data": {"reportId": report_id, "status": "COMPLETED"}},
    )

    # 3. GET /reports/{id}/file -> zip content
    sample_rows = [
        {
            "Customer ID": "5011369",
            "Call ID": "call-1",
            "Call Start Time": "2026-10-06T00:10:00.000Z",
            "Duration": "45",
            "Response Code": "200",
            "Result": "completed",
            "Call Source": "+19195551111",
            "Call Destination": "+14805552222",
            "Direction": "inbound",
            "Hangup Source": "CALLED_PARTY",
            "Attestation": "A",
            "Post Dial Delay": "0",
            "Source Country": "US",
            "Destination Country": "US",
            "Packets Sent": "100",
            "Packets Received": "100",
        },
        {
            "Customer ID": "5011369",
            "Call ID": "call-2",
            "Call Start Time": "2026-10-06T00:20:00.000Z",
            "Duration": "120",
            "Response Code": "200",
            "Result": "completed",
            "Call Source": "+19195553333",
            "Call Destination": "+14805554444",
            "Direction": "outbound",
            "Hangup Source": "CALLING_PARTY",
            "Attestation": "B",
            "Post Dial Delay": "1",
            "Source Country": "US",
            "Destination Country": "US",
            "Packets Sent": "250",
            "Packets Received": "240",
        },
    ]

    httpx_mock.add_response(
        method="GET",
        url=f"https://insights.bandwidth.com/api/v1/reports/{report_id}/file",
        status_code=200,
        content=make_mock_zip(sample_rows),
        headers={"Content-Type": "application/zip"},
    )

    res = await mcp.call_tool(
        "getCallDetailRecords",
        {
            "start_time": "2026-10-06T00:00:00Z",
            "end_time": "2026-10-06T01:00:00Z",
            "phone_number": "4805552222",
            "limit": 10,
        },
    )

    sc = res.structured_content
    assert sc.get("report_id") == report_id
    assert sc.get("total_records") == 1
    calls = sc.get("calls", [])
    assert len(calls) == 1
    assert calls[0]["callId"] == "call-1"
    assert calls[0]["callingNumber"] == "+19195551111"
    assert calls[0]["calledNumber"] == "+14805552222"


@pytest.mark.asyncio
async def test_search_voice_calls_with_operators(httpx_mock: HTTPXMock):
    config = {
        "BW_ACCESS_TOKEN": "mock-token",
        "BW_ACCOUNT_ID": "5011369",
        "BW_ACCOUNTS": ["5011369"],
    }
    mcp = FastMCP("test")
    register_call_history_tools(mcp, config)

    mock_resp = {
        "data": {
            "totalCount": 1,
            "calls": [
                {
                    "callId": "call-voice-123",
                    "startTime": "2026-10-06T10:00:00Z",
                    "endTime": "2026-10-06T10:02:00Z",
                    "duration": 120,
                    "callingNumber": "+19195551111",
                    "calledNumber": "+14805552222",
                    "callDirection": "inbound",
                    "callType": "sip",
                    "callResult": "completed",
                    "sipResponseCode": "200",
                    "sipResponseDescription": "OK",
                    "cost": "0.012",
                    "locationName": "Main Trunk",
                    "subAccountName": "Default",
                    "attestationIndicator": "A",
                    "carrierLatency": 15.2,
                    "customerLatency": 18.4,
                    "carrierJitter": 2.1,
                    "customerJitter": 3.4,
                    "carrierPacketLossPercentage": 0.0,
                    "customerPacketLossPercentage": 0.1,
                    "customerSbcCustomerSendMos": 4.35,
                    "customerSbcCustomerReceiveMos": 4.38,
                    "customerSbcCarrierSendMos": 4.32,
                    "customerSbcCarrierReceiveMos": 4.36,
                }
            ],
        }
    }

    httpx_mock.add_response(
        url="https://insights.bandwidth.com/api/v1/voice/calls?accountId=5011369&startTime=gte%3A2026-10-06T00%3A00%3A00Z&endTime=lte%3A2026-10-06T01%3A00%3A00Z&limit=25&sort=startTime%3Adesc",
        status_code=200,
        json=mock_resp,
    )

    res = await mcp.call_tool(
        "searchVoiceCalls",
        {
            "start_time": "2026-10-06T00:00:00Z",
            "end_time": "2026-10-06T01:00:00Z",
        },
    )

    sc = res.structured_content
    assert sc.get("totalCount") == 1
    calls = sc.get("calls", [])
    assert len(calls) == 1
    c = calls[0]
    assert c["callId"] == "call-voice-123"
    assert c["quality"]["carrierLatency"] == 15.2
    assert c["quality"]["customerSbcCustomerSendMos"] == 4.35


@pytest.mark.asyncio
async def test_search_voice_calls_403_explanation(httpx_mock: HTTPXMock):
    config = {
        "BW_ACCESS_TOKEN": "mock-token",
        "BW_ACCOUNT_ID": "5011369",
        "BW_ACCOUNTS": ["5011369"],
    }
    mcp = FastMCP("test")
    register_call_history_tools(mcp, config)

    httpx_mock.add_response(
        url="https://insights.bandwidth.com/api/v1/voice/calls?accountId=5011369&startTime=gte%3A2026-10-06T00%3A00%3A00Z&endTime=lte%3A2026-10-06T01%3A00%3A00Z&limit=25&sort=startTime%3Adesc",
        status_code=403,
        json={"error": "Forbidden"},
    )

    res = await mcp.call_tool(
        "searchVoiceCalls",
        {
            "start_time": "2026-10-06T00:00:00Z",
            "end_time": "2026-10-06T01:00:00Z",
        },
    )

    sc = res.structured_content
    assert sc.get("status_code") == 403
    assert "voice_insights" in sc.get("error", "")


@pytest.mark.asyncio
async def test_get_voice_call(httpx_mock: HTTPXMock):
    config = {
        "BW_ACCESS_TOKEN": "mock-token",
        "BW_ACCOUNT_ID": "5011369",
        "BW_ACCOUNTS": ["5011369"],
    }
    mcp = FastMCP("test")
    register_call_history_tools(mcp, config)

    httpx_mock.add_response(
        url="https://insights.bandwidth.com/api/v1/voice/calls/call-999?accountId=5011369",
        status_code=200,
        json={
            "data": {"callId": "call-999", "duration": 85, "callResult": "completed"}
        },
    )

    res = await mcp.call_tool("getVoiceCall", {"call_id": "call-999"})
    sc = res.structured_content
    assert sc.get("callId") == "call-999"
    assert sc.get("duration") == 85


@pytest.mark.asyncio
async def test_get_call_detail_records_sleeps_asynchronously(httpx_mock: HTTPXMock, monkeypatch):
    def fail_on_blocking_sleep(seconds):
        raise AssertionError("Blocking time.sleep called in async polling loop")
    monkeypatch.setattr("time.sleep", fail_on_blocking_sleep)

    config = {"BW_ACCESS_TOKEN": "mock-token", "BW_ACCOUNT_ID": "5011369", "BW_ACCOUNTS": ["5011369"]}
    mcp = FastMCP("test")
    register_call_history_tools(mcp, config)

    report_id = "test-report-uuid-async"
    httpx_mock.add_response(
        method="POST",
        url="https://insights.bandwidth.com/api/v1/reports",
        status_code=202,
        json={"data": {"reportId": report_id, "status": "PENDING"}},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"https://insights.bandwidth.com/api/v1/reports/{report_id}",
        status_code=200,
        json={"data": {"reportId": report_id, "status": "PENDING"}},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"https://insights.bandwidth.com/api/v1/reports/{report_id}",
        status_code=200,
        json={"data": {"reportId": report_id, "status": "COMPLETED"}},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"https://insights.bandwidth.com/api/v1/reports/{report_id}/file",
        status_code=200,
        content=make_mock_zip([]),
        headers={"Content-Type": "application/zip"},
    )

    res = await mcp.call_tool("getCallDetailRecords", {"start_time": "2026-10-06T00:00:00Z", "end_time": "2026-10-06T01:00:00Z"})
    assert res.structured_content.get("report_id") == report_id
    assert res.structured_content.get("total_records") == 0


@pytest.mark.asyncio
async def test_get_call_detail_records_handles_no_results(httpx_mock: HTTPXMock):
    config = {"BW_ACCESS_TOKEN": "mock-token", "BW_ACCOUNT_ID": "5011369", "BW_ACCOUNTS": ["5011369"]}
    mcp = FastMCP("test")
    register_call_history_tools(mcp, config)

    report_id = "test-report-uuid-no-results"
    httpx_mock.add_response(
        method="POST",
        url="https://insights.bandwidth.com/api/v1/reports",
        status_code=202,
        json={"data": {"reportId": report_id, "status": "PENDING"}},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"https://insights.bandwidth.com/api/v1/reports/{report_id}",
        status_code=200,
        json={"data": {"reportId": report_id, "status": "NO_RESULTS"}},
    )

    res = await mcp.call_tool("getCallDetailRecords", {"start_time": "2026-10-06T00:00:00Z", "end_time": "2026-10-06T01:00:00Z"})
    sc = res.structured_content
    assert sc.get("status") == "NO_RESULTS"
    assert sc.get("total_records") == 0
    assert sc.get("calls") == []


@pytest.mark.asyncio
async def test_search_voice_calls_searches_both_legs_for_phone_number(httpx_mock: HTTPXMock):
    config = {"BW_ACCESS_TOKEN": "mock-token", "BW_ACCOUNT_ID": "5011369", "BW_ACCOUNTS": ["5011369"]}
    mcp = FastMCP("test")
    register_call_history_tools(mcp, config)

    # Leg 1: callingNumber=+14805552222
    httpx_mock.add_response(
        url="https://insights.bandwidth.com/api/v1/voice/calls?accountId=5011369&startTime=gte%3A2026-10-06T00%3A00%3A00Z&endTime=lte%3A2026-10-06T01%3A00%3A00Z&limit=25&sort=startTime%3Adesc&callingNumber=%2B14805552222",
        status_code=200,
        json={"data": {"totalCount": 1, "calls": [{"callId": "call-leg-1", "startTime": "2026-10-06T00:10:00Z", "callingNumber": "+14805552222", "calledNumber": "+19195551111"}]}},
    )
    # Leg 2: calledNumber=+14805552222
    httpx_mock.add_response(
        url="https://insights.bandwidth.com/api/v1/voice/calls?accountId=5011369&startTime=gte%3A2026-10-06T00%3A00%3A00Z&endTime=lte%3A2026-10-06T01%3A00%3A00Z&limit=25&sort=startTime%3Adesc&calledNumber=%2B14805552222",
        status_code=200,
        json={"data": {"totalCount": 1, "calls": [{"callId": "call-leg-2", "startTime": "2026-10-06T00:20:00Z", "callingNumber": "+19195553333", "calledNumber": "+14805552222"}]}},
    )

    res = await mcp.call_tool(
        "searchVoiceCalls",
        {
            "start_time": "2026-10-06T00:00:00Z",
            "end_time": "2026-10-06T01:00:00Z",
            "phone_number": "+14805552222",
        },
    )
    sc = res.structured_content
    calls = sc.get("calls", [])
    # Must return both calls where +14805552222 was calling OR called
    assert len(calls) == 2
    call_ids = {c["callId"] for c in calls}
    assert call_ids == {"call-leg-1", "call-leg-2"}
