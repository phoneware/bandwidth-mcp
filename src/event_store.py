"""In-memory event store for callback events and call state.

Ring buffer per event type+key, with per-session read cursors for multi-session
safety and first-write-wins on voice BXML responses.
"""

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CallState:
    call_id: str
    from_number: str
    to_number: str
    application_id: str
    started_at: float = field(default_factory=time.time)
    turns: list[dict] = field(default_factory=list)
    pending_bxml: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def add_turn(self, role: str, text: str) -> None:
        self.turns.append({"role": role, "text": text, "timestamp": time.time()})

    def try_set_bxml(self, bxml: str) -> bool:
        if self.pending_bxml is not None:
            return False
        self.pending_bxml = bxml
        return True

    def consume_pending_bxml(self) -> Optional[str]:
        bxml = self.pending_bxml
        self.pending_bxml = None
        return bxml


class EventStore:
    def __init__(self, max_events: int = 1000, ttl_seconds: int = 3600):
        self._max_events = max_events
        self._ttl = ttl_seconds
        self._events: dict[str, deque[dict]] = defaultdict(
            lambda: deque(maxlen=max_events)
        )
        self._global_counter: int = 0
        self._session_cursors: dict[str, int] = {}
        self._calls: dict[str, CallState] = {}

    def push(self, event_type: str, key: str, event: dict) -> None:
        self._global_counter += 1
        # Use nanosecond precision to avoid timestamp collisions on fast hardware
        received_at = time.time_ns() / 1e9
        event = {**event, "_received_at": received_at, "_seq": self._global_counter}
        self._events[f"{event_type}:{key}"].append(event)
        self._events[event_type].append(event)

    def get_events(
        self, event_type: str, key: Optional[str] = None, since: Optional[float] = None
    ) -> list[dict]:
        bucket = f"{event_type}:{key}" if key else event_type
        events = list(self._events.get(bucket, []))
        now = time.time()
        events = [e for e in events if now - e["_received_at"] < self._ttl]
        if since is not None:
            events = [e for e in events if e["_received_at"] > since]
        return events

    def get_unread(self, event_type: str, session_id: str) -> list[dict]:
        cursor_key = f"{session_id}:{event_type}"
        last_seq = self._session_cursors.get(cursor_key, 0)
        events = list(self._events.get(event_type, []))
        unread = [e for e in events if e["_seq"] > last_seq]
        if unread:
            self._session_cursors[cursor_key] = unread[-1]["_seq"]
        return unread

    def create_call(
        self, call_id: str, from_number: str, to_number: str, application_id: str
    ) -> CallState:
        call = CallState(
            call_id=call_id,
            from_number=from_number,
            to_number=to_number,
            application_id=application_id,
        )
        self._calls[call_id] = call
        return call

    def get_call(self, call_id: str) -> Optional[CallState]:
        return self._calls.get(call_id)

    def remove_call(self, call_id: str) -> None:
        self._calls.pop(call_id, None)
