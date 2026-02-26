"""
Event bus for real-time WebSocket broadcasting.
When dashboard server is running, events are broadcast to all connected clients.
When running CLI-only, events are no-ops.
"""

import json
from typing import Any, Callable, Optional


class _EventBus:
    """Simple event bus for broadcasting state changes."""

    def __init__(self):
        self._listeners: list[Callable] = []

    def subscribe(self, callback: Callable):
        """Add a listener for events."""
        self._listeners.append(callback)

    def unsubscribe(self, callback: Callable):
        """Remove a listener."""
        self._listeners = [l for l in self._listeners if l != callback]

    def emit(self, event_type: str, data: Any = None):
        """Broadcast an event to all listeners."""
        event = {"type": event_type, "data": data}
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass


# Singleton instance
EventBus = _EventBus()
