import time
import pytest
from src.event_store import EventStore, CallState


class TestEventStore:
    def setup_method(self):
        self.store = EventStore(max_events=100, ttl_seconds=3600)

    def test_push_and_get_events(self):
        self.store.push("messaging.inbound", "+19195551234", {"text": "hello"})
        self.store.push("messaging.inbound", "+19195551234", {"text": "world"})
        events = self.store.get_events("messaging.inbound", key="+19195551234")
        assert len(events) == 2
        assert events[0]["text"] == "hello"
        assert events[1]["text"] == "world"

    def test_get_events_empty(self):
        events = self.store.get_events("messaging.inbound", key="+10000000000")
        assert events == []

    def test_get_events_filtered_by_since(self):
        self.store.push("messaging.inbound", "+19195551234", {"text": "old"})
        time.sleep(0.01)  # ensure timestamp separation
        cutoff = time.time()
        time.sleep(0.01)
        self.store.push("messaging.inbound", "+19195551234", {"text": "new"})
        events = self.store.get_events(
            "messaging.inbound", key="+19195551234", since=cutoff
        )
        assert len(events) == 1
        assert events[0]["text"] == "new"

    def test_max_events_ring_buffer(self):
        store = EventStore(max_events=3, ttl_seconds=3600)
        for i in range(5):
            store.push("test", "key", {"n": i})
        events = store.get_events("test", key="key")
        assert len(events) == 3
        assert events[0]["n"] == 2

    def test_get_all_events_by_type(self):
        self.store.push("messaging.inbound", "+11111111111", {"text": "a"})
        self.store.push("messaging.inbound", "+12222222222", {"text": "b"})
        events = self.store.get_events("messaging.inbound")
        assert len(events) == 2

    def test_session_cursor_isolation(self):
        self.store.push("messaging.inbound", "+19195551234", {"text": "msg1"})
        self.store.push("messaging.inbound", "+19195551234", {"text": "msg2"})
        events_a = self.store.get_unread("messaging.inbound", session_id="session-a")
        assert len(events_a) == 2
        events_b = self.store.get_unread("messaging.inbound", session_id="session-b")
        assert len(events_b) == 2
        self.store.push("messaging.inbound", "+19195551234", {"text": "msg3"})
        events_a2 = self.store.get_unread("messaging.inbound", session_id="session-a")
        assert len(events_a2) == 1
        assert events_a2[0]["text"] == "msg3"
        events_b2 = self.store.get_unread("messaging.inbound", session_id="session-b")
        assert len(events_b2) == 1


class TestCallState:
    def setup_method(self):
        self.store = EventStore(max_events=100, ttl_seconds=3600)

    def test_create_and_get_call(self):
        self.store.create_call(
            "call-123",
            from_number="+11111111111",
            to_number="+12222222222",
            application_id="app-1",
        )
        call = self.store.get_call("call-123")
        assert call is not None
        assert call.call_id == "call-123"
        assert call.from_number == "+11111111111"
        assert call.turns == []

    def test_get_missing_call(self):
        assert self.store.get_call("nonexistent") is None

    def test_add_turn(self):
        self.store.create_call("call-123", "+11111111111", "+12222222222", "app-1")
        call = self.store.get_call("call-123")
        call.add_turn("caller", "Hello?")
        call.add_turn("agent", "Hi, how can I help?")
        assert len(call.turns) == 2
        assert call.turns[0]["role"] == "caller"
        assert call.turns[1]["text"] == "Hi, how can I help?"

    def test_pending_bxml(self):
        self.store.create_call("call-123", "+11111111111", "+12222222222", "app-1")
        call = self.store.get_call("call-123")
        call.pending_bxml = "<Response><Hangup/></Response>"
        assert call.pending_bxml == "<Response><Hangup/></Response>"
        bxml = call.consume_pending_bxml()
        assert bxml == "<Response><Hangup/></Response>"
        assert call.pending_bxml is None

    def test_consume_pending_bxml_returns_none_when_empty(self):
        self.store.create_call("call-123", "+11111111111", "+12222222222", "app-1")
        call = self.store.get_call("call-123")
        assert call.consume_pending_bxml() is None

    def test_first_write_wins_for_bxml(self):
        self.store.create_call("call-123", "+11111111111", "+12222222222", "app-1")
        call = self.store.get_call("call-123")
        assert call.try_set_bxml("<Response><Hangup/></Response>") is True
        assert (
            call.try_set_bxml(
                "<Response><SpeakSentence>Too late</SpeakSentence></Response>"
            )
            is False
        )
        assert "Hangup" in call.pending_bxml

    def test_remove_call(self):
        self.store.create_call("call-123", "+11111111111", "+12222222222", "app-1")
        self.store.remove_call("call-123")
        assert self.store.get_call("call-123") is None
