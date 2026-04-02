import pytest
from xml.etree.ElementTree import fromstring
from src.tools.voice import generate_bxml_flow


@pytest.mark.asyncio
async def test_speak_sentence():
    result = await generate_bxml_flow(
        [{"type": "SpeakSentence", "text": "Hello world"}]
    )
    assert "<SpeakSentence" in result
    assert "Hello world" in result
    fromstring(result)


@pytest.mark.asyncio
async def test_speak_with_voice():
    result = await generate_bxml_flow(
        [{"type": "SpeakSentence", "text": "Hi", "voice": "julie"}]
    )
    assert 'voice="julie"' in result


@pytest.mark.asyncio
async def test_gather_wrapping_speak():
    result = await generate_bxml_flow(
        [
            {
                "type": "Gather",
                "input_type": "speech dtmf",
                "max_wait_time": 8,
                "speech_timeout": 2,
                "verbs": [{"type": "SpeakSentence", "text": "How can I help?"}],
            }
        ]
    )
    root = fromstring(result)
    gather = root.find("Gather")
    assert gather is not None
    assert gather.get("inputType") == "speech dtmf"
    speak = gather.find("SpeakSentence")
    assert speak is not None
    assert speak.text == "How can I help?"


@pytest.mark.asyncio
async def test_transfer():
    result = await generate_bxml_flow(
        [{"type": "Transfer", "transfer_to": "+19195551234"}]
    )
    root = fromstring(result)
    transfer = root.find("Transfer")
    assert transfer is not None
    phone = transfer.find("PhoneNumber")
    assert phone is not None
    assert phone.text == "+19195551234"


@pytest.mark.asyncio
async def test_hangup():
    result = await generate_bxml_flow([{"type": "Hangup"}])
    assert "<Hangup" in result
    fromstring(result)


@pytest.mark.asyncio
async def test_pause():
    result = await generate_bxml_flow([{"type": "Pause", "duration": 3}])
    root = fromstring(result)
    pause = root.find("Pause")
    assert pause is not None
    assert pause.get("duration") == "3"


@pytest.mark.asyncio
async def test_redirect():
    result = await generate_bxml_flow(
        [{"type": "Redirect", "redirect_url": "/callbacks/voice/continue/call-1"}]
    )
    root = fromstring(result)
    redirect = root.find("Redirect")
    assert redirect is not None
    assert redirect.get("redirectUrl") == "/callbacks/voice/continue/call-1"


@pytest.mark.asyncio
async def test_record():
    result = await generate_bxml_flow(
        [{"type": "Record", "max_duration": 60, "silence_timeout": 5}]
    )
    root = fromstring(result)
    record = root.find("Record")
    assert record is not None
    assert record.get("maxDuration") == "60"


@pytest.mark.asyncio
async def test_play_audio():
    result = await generate_bxml_flow(
        [{"type": "PlayAudio", "url": "https://example.com/audio.mp3"}]
    )
    root = fromstring(result)
    play = root.find("PlayAudio")
    assert play is not None
    assert play.text == "https://example.com/audio.mp3"


@pytest.mark.asyncio
async def test_send_dtmf():
    result = await generate_bxml_flow([{"type": "SendDtmf", "digits": "1234#"}])
    root = fromstring(result)
    dtmf = root.find("SendDtmf")
    assert dtmf is not None
    assert dtmf.text == "1234#"


@pytest.mark.asyncio
async def test_multiple_verbs():
    result = await generate_bxml_flow(
        [
            {"type": "SpeakSentence", "text": "Goodbye"},
            {"type": "Hangup"},
        ]
    )
    root = fromstring(result)
    children = list(root)
    assert len(children) == 2
    assert children[0].tag == "SpeakSentence"
    assert children[1].tag == "Hangup"


@pytest.mark.asyncio
async def test_unknown_verb_raises():
    with pytest.raises(ValueError, match="Unknown BXML verb"):
        await generate_bxml_flow([{"type": "FlyToMoon"}])


@pytest.mark.asyncio
async def test_auto_gather_wrap():
    result = await generate_bxml_flow(
        [{"type": "SpeakSentence", "text": "Hello"}],
        auto_gather=True,
    )
    root = fromstring(result)
    gather = root.find("Gather")
    assert gather is not None
    assert gather.find("SpeakSentence") is not None


@pytest.mark.asyncio
async def test_xml_escaping():
    result = await generate_bxml_flow(
        [{"type": "SpeakSentence", "text": 'Use <b> & "quotes"'}]
    )
    fromstring(result)
