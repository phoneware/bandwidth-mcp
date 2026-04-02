"""Starlette callback routes for Bandwidth webhooks.

Messaging callbacks are fire-and-forget (store event, return 200).
Voice callbacks are stateful (store event, return BXML or redirect).
"""

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from src.event_store import EventStore


def _bxml_response(bxml: str) -> Response:
    return Response(content=bxml, media_type="application/xml")


def _redirect_bxml(call_id: str) -> str:
    return f'<Response><Redirect redirectUrl="/callbacks/voice/continue/{call_id}" /></Response>'


def create_callback_app(event_store: EventStore) -> Starlette:
    async def messaging_inbound(request: Request) -> JSONResponse:
        payload = await request.json()
        for event in payload:
            key = event.get("message", {}).get("from", "unknown")
            event_store.push("messaging.inbound", key, event)
        return JSONResponse({"status": "ok"})

    async def messaging_status(request: Request) -> JSONResponse:
        payload = await request.json()
        for event in payload:
            key = event.get("message", {}).get("id", "unknown")
            event_store.push("messaging.status", key, event)
        return JSONResponse({"status": "ok"})

    async def voice_answer(request: Request) -> Response:
        payload = await request.json()
        call_id = payload.get("callId", "unknown")
        event_store.push("voice.answer", call_id, payload)
        event_store.create_call(
            call_id=call_id,
            from_number=payload.get("from", ""),
            to_number=payload.get("to", ""),
            application_id=payload.get("applicationId", ""),
        )
        return _bxml_response(_redirect_bxml(call_id))

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

    async def voice_disconnect(request: Request) -> JSONResponse:
        payload = await request.json()
        call_id = payload.get("callId", "unknown")
        event_store.push("voice.disconnect", call_id, payload)
        event_store.remove_call(call_id)
        return JSONResponse({"status": "ok"})

    async def voice_continue(request: Request) -> Response:
        call_id = request.path_params["call_id"]
        call = event_store.get_call(call_id)
        if call:
            bxml = call.consume_pending_bxml()
            if bxml:
                return _bxml_response(bxml)
        return _bxml_response(_redirect_bxml(call_id))

    routes = [
        Route("/callbacks/messaging/inbound", messaging_inbound, methods=["POST"]),
        Route("/callbacks/messaging/status", messaging_status, methods=["POST"]),
        Route("/callbacks/voice/answer", voice_answer, methods=["POST"]),
        Route("/callbacks/voice/gather", voice_gather, methods=["POST"]),
        Route("/callbacks/voice/disconnect", voice_disconnect, methods=["POST"]),
        Route("/callbacks/voice/continue/{call_id}", voice_continue, methods=["POST"]),
    ]

    return Starlette(routes=routes)
