import pytest
from starlette.testclient import TestClient
from src.callbacks import create_callback_app
from src.event_store import EventStore


@pytest.fixture
def event_store():
    return EventStore(max_events=100, ttl_seconds=3600)


@pytest.fixture
def client(event_store):
    app = create_callback_app(event_store)
    return TestClient(app)


class TestMessagingCallbacks:
    def test_inbound_message(self, client, event_store):
        payload = [
            {
                "type": "message-received",
                "message": {
                    "from": "+19195551234",
                    "to": ["+19195554321"],
                    "text": "Hello from tests",
                    "id": "msg-abc123",
                },
            }
        ]
        response = client.post("/callbacks/messaging/inbound", json=payload)
        assert response.status_code == 200
        events = event_store.get_events("messaging.inbound")
        assert len(events) == 1
        assert events[0]["message"]["text"] == "Hello from tests"

    def test_message_status(self, client, event_store):
        payload = [
            {
                "type": "message-delivered",
                "message": {"id": "msg-abc123"},
            }
        ]
        response = client.post("/callbacks/messaging/status", json=payload)
        assert response.status_code == 200
        events = event_store.get_events("messaging.status")
        assert len(events) == 1


class TestVoiceCallbacks:
    def test_answer_callback_returns_redirect(self, client, event_store):
        payload = {
            "eventType": "answer",
            "callId": "call-123",
            "from": "+19195551234",
            "to": "+19195554321",
            "applicationId": "app-1",
        }
        response = client.post("/callbacks/voice/answer", json=payload)
        assert response.status_code == 200
        assert "application/xml" in response.headers["content-type"]
        assert "<Redirect" in response.text
        assert "call-123" in response.text
        call = event_store.get_call("call-123")
        assert call is not None
        assert call.from_number == "+19195551234"

    def test_gather_callback_stores_transcription(self, client, event_store):
        event_store.create_call("call-456", "+19195551234", "+19195554321", "app-1")
        payload = {
            "eventType": "gather",
            "callId": "call-456",
            "digits": "",
            "terminatingDigit": "",
            "speech": {"transcript": "I want to check my order", "confidence": 0.95},
        }
        response = client.post("/callbacks/voice/gather", json=payload)
        assert response.status_code == 200
        assert "<Redirect" in response.text
        call = event_store.get_call("call-456")
        assert len(call.turns) == 1
        assert call.turns[0]["role"] == "caller"
        assert "check my order" in call.turns[0]["text"]

    def test_disconnect_callback(self, client, event_store):
        event_store.create_call("call-789", "+19195551234", "+19195554321", "app-1")
        payload = {"eventType": "disconnect", "callId": "call-789", "cause": "hangup"}
        response = client.post("/callbacks/voice/disconnect", json=payload)
        assert response.status_code == 200
        assert event_store.get_call("call-789") is None

    def test_continue_returns_pending_bxml(self, client, event_store):
        event_store.create_call("call-100", "+11111111111", "+12222222222", "app-1")
        call = event_store.get_call("call-100")
        call.pending_bxml = "<Response><SpeakSentence>Hello</SpeakSentence></Response>"
        response = client.post("/callbacks/voice/continue/call-100")
        assert response.status_code == 200
        assert "Hello" in response.text
        assert call.pending_bxml is None

    def test_continue_redirects_when_no_bxml(self, client, event_store):
        event_store.create_call("call-200", "+11111111111", "+12222222222", "app-1")
        response = client.post("/callbacks/voice/continue/call-200")
        assert response.status_code == 200
        assert "<Redirect" in response.text
