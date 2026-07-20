"""MCP tools for programmable voice: BXML generation and call response."""

from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from event_store import EventStore


from mcp.types import ToolAnnotations

# Client-facing read/write hints so MCP clients (claude.ai) can group tools
# instead of dumping everything under "Other".
_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


def _snake_to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _build_verb(verb: dict[str, Any], parent: Element) -> None:
    verb_type = verb.get("type")
    if not verb_type:
        raise ValueError("Each verb must have a 'type' field")

    if verb_type == "SpeakSentence":
        el = SubElement(parent, "SpeakSentence")
        el.text = verb.get("text", "")
        if "voice" in verb:
            el.set("voice", verb["voice"])
        if "locale" in verb:
            el.set("locale", verb["locale"])
    elif verb_type == "Gather":
        el = SubElement(parent, "Gather")
        for attr in [
            "input_type",
            "max_wait_time",
            "speech_timeout",
            "max_digits",
            "inter_digit_timeout",
            "terminating_digits",
            "first_digit_timeout",
            "repeat_count",
            "gather_url",
            "gather_method",
        ]:
            if attr in verb:
                el.set(_snake_to_camel(attr), str(verb[attr]))
        for child_verb in verb.get("verbs", []):
            _build_verb(child_verb, el)
    elif verb_type == "Transfer":
        el = SubElement(parent, "Transfer")
        if "transfer_caller_id" in verb:
            el.set("transferCallerId", verb["transfer_caller_id"])
        phone = SubElement(el, "PhoneNumber")
        phone.text = verb.get("transfer_to", "")
    elif verb_type == "Hangup":
        SubElement(parent, "Hangup")
    elif verb_type == "Pause":
        el = SubElement(parent, "Pause")
        if "duration" in verb:
            el.set("duration", str(verb["duration"]))
    elif verb_type == "Redirect":
        el = SubElement(parent, "Redirect")
        if "redirect_url" in verb:
            el.set("redirectUrl", verb["redirect_url"])
    elif verb_type == "Record":
        el = SubElement(parent, "Record")
        for attr in [
            "max_duration",
            "silence_timeout",
            "callback_url",
            "file_format",
            "transcribe",
        ]:
            if attr in verb:
                el.set(_snake_to_camel(attr), str(verb[attr]))
    elif verb_type == "PlayAudio":
        el = SubElement(parent, "PlayAudio")
        el.text = verb.get("url", "")
    elif verb_type == "Ring":
        el = SubElement(parent, "Ring")
        if "duration" in verb:
            el.set("duration", str(verb["duration"]))
    elif verb_type == "SendDtmf":
        el = SubElement(parent, "SendDtmf")
        el.text = verb.get("digits", "")
    elif verb_type == "Bridge":
        el = SubElement(parent, "Bridge")
        el.set("targetCall", verb.get("target_call", ""))
    elif verb_type == "StartRecording":
        el = SubElement(parent, "StartRecording")
        if "callback_url" in verb:
            el.set("recordingAvailableUrl", verb["callback_url"])
    elif verb_type == "StopRecording":
        SubElement(parent, "StopRecording")
    elif verb_type == "StartTranscription":
        el = SubElement(parent, "StartTranscription")
        if "callback_url" in verb:
            el.set("transcriptionAvailableUrl", verb["callback_url"])
        if "tracks" in verb:
            el.set("tracks", verb["tracks"])
    elif verb_type == "StopTranscription":
        SubElement(parent, "StopTranscription")
    else:
        raise ValueError(f"Unknown BXML verb: '{verb_type}'")


async def generate_bxml_flow(
    verbs: list[dict[str, Any]],
    auto_gather: bool = False,
    gather_url: str = "",
) -> str:
    root = Element("Response")
    for verb in verbs:
        if auto_gather and verb.get("type") == "SpeakSentence":
            gather_verb = {
                "type": "Gather",
                "max_wait_time": 8,
                "speech_timeout": 2,
                "input_type": "speech dtmf",
                "verbs": [verb],
            }
            if gather_url:
                gather_verb["gather_url"] = gather_url
            _build_verb(gather_verb, root)
        else:
            _build_verb(verb, root)
    return tostring(root, encoding="unicode", xml_declaration=False)


async def respond_to_callback_flow(
    event_store: EventStore,
    call_id: str,
    bxml: str,
) -> dict:
    """Queue BXML for a call. Creates the call state if it doesn't exist yet
    (allows pre-queuing BXML before the answer callback arrives)."""
    call = event_store.get_call(call_id)
    if not call:
        # Pre-create call state so BXML is ready when the callback arrives
        call = event_store.create_call(call_id, "", "", "")
    if not call.try_set_bxml(bxml):
        return {"error": "already_handled", "call_id": call_id}
    call.add_turn("agent", "(BXML response queued)")
    return {"status": "queued", "call_id": call_id}


def register_voice_tools(mcp, event_store: EventStore, config: dict = None) -> None:
    @mcp.tool(name="generateBXML", annotations=_READ)
    async def generate_bxml(
        verbs: list[dict[str, Any]],
        auto_gather: bool = True,
    ) -> str:
        """Generate valid Bandwidth XML (BXML) from verb descriptions.

        Each verb is a dict with 'type' and type-specific fields. Supported types:
        SpeakSentence, Gather, Transfer, PlayAudio, Record, Pause, Hangup,
        Redirect, Bridge, Ring, SendDtmf, StartRecording, StopRecording,
        StartTranscription, StopTranscription.

        When auto_gather is True (default), top-level SpeakSentence verbs are
        wrapped in Gather for barge-in support (caller can interrupt).

        Args:
            verbs: List of verb descriptions.
            auto_gather: Wrap SpeakSentence in Gather for barge-in. Default True.
        """
        base_url = (config or {}).get("BW_MCP_BASE_URL", "")
        gather_url = f"{base_url}/callbacks/voice/gather" if base_url else ""
        return await generate_bxml_flow(verbs, auto_gather, gather_url)

    @mcp.tool(name="respondToCallback", annotations=_WRITE)
    async def respond_to_callback(call_id: str, bxml: str) -> dict:
        """Queue a BXML response for an active voice call.

        Use after reading a gather result from getCallbackEvents and generating
        BXML via generateBXML. The next redirect for this call will deliver the BXML.

        First-write-wins: if another session already queued BXML for this call,
        this call returns an error instead of overwriting.

        Args:
            call_id: The call ID to respond to.
            bxml: Valid BXML string (use generateBXML to produce this).
        """
        return await respond_to_callback_flow(event_store, call_id, bxml)
