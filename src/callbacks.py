"""Callback routes for Bandwidth webhooks.

Messaging callbacks are fire-and-forget (store event, return 200).
Voice callbacks are stateful (store event, return BXML or redirect).

Routes are registered directly on the FastMCP instance via custom_route
so they're served on the same HTTP transport as MCP tools.
"""

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from event_store import EventStore


def _bxml_response(bxml: str) -> Response:
    return Response(content=bxml, media_type="application/xml")


def _redirect_bxml(call_id: str) -> str:
    return f'<Response><Redirect redirectUrl="/callbacks/voice/continue/{call_id}" /></Response>'


def register_callback_routes(mcp, event_store: EventStore) -> None:
    """Register callback HTTP routes on the FastMCP server."""

    @mcp.custom_route("/callbacks/messaging/inbound", methods=["POST"])
    async def messaging_inbound(request: Request) -> JSONResponse:
        payload = await request.json()
        for event in payload:
            key = event.get("message", {}).get("from", "unknown")
            event_store.push("messaging.inbound", key, event)
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/callbacks/messaging/status", methods=["POST"])
    async def messaging_status(request: Request) -> JSONResponse:
        payload = await request.json()
        for event in payload:
            key = event.get("message", {}).get("id", "unknown")
            event_store.push("messaging.status", key, event)
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/callbacks/voice/answer", methods=["POST"])
    async def voice_answer(request: Request) -> Response:
        payload = await request.json()
        call_id = payload.get("callId", "unknown")
        event_store.push("voice.answer", call_id, payload)

        # Check if BXML was pre-queued (agent called respondToCallback before the call was answered)
        existing_call = event_store.get_call(call_id)
        if existing_call and existing_call.pending_bxml:
            # Update with real call info
            existing_call.from_number = payload.get("from", "")
            existing_call.to_number = payload.get("to", "")
            existing_call.application_id = payload.get("applicationId", "")
            bxml = existing_call.consume_pending_bxml()
            return _bxml_response(bxml)

        # No pre-queued BXML — create call state and redirect to wait for agent
        event_store.create_call(
            call_id=call_id,
            from_number=payload.get("from", ""),
            to_number=payload.get("to", ""),
            application_id=payload.get("applicationId", ""),
        )
        return _bxml_response(_redirect_bxml(call_id))

    @mcp.custom_route("/callbacks/voice/gather", methods=["POST"])
    async def voice_gather(request: Request) -> Response:
        payload = await request.json()
        call_id = payload.get("callId", "unknown")
        event_store.push("voice.gather", call_id, payload)
        call = event_store.get_call(call_id)
        if call:
            speech = payload.get("speech", {})
            transcript = speech.get("transcript", "")
            digits = payload.get("digits", "")
            text = transcript or digits or "(no input)"
            call.add_turn("caller", text)
        return _bxml_response(_redirect_bxml(call_id))

    @mcp.custom_route("/callbacks/voice/disconnect", methods=["POST"])
    async def voice_disconnect(request: Request) -> JSONResponse:
        payload = await request.json()
        call_id = payload.get("callId", "unknown")
        event_store.push("voice.disconnect", call_id, payload)
        event_store.remove_call(call_id)
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/callbacks/voice/continue/{call_id}", methods=["POST"])
    async def voice_continue(request: Request) -> Response:
        call_id = request.path_params["call_id"]
        call = event_store.get_call(call_id)
        if call:
            bxml = call.consume_pending_bxml()
            if bxml:
                return _bxml_response(bxml)
        return _bxml_response(_redirect_bxml(call_id))
