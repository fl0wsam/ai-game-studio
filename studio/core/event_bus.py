"""
studio/core/event_bus.py - Async In-Process Pub/Sub Event Bus with Telemetry Buffer
Provides decoupled message routing, hierarchical topic matching (* and **),
subscriber exception shielding, and ring buffers for WebSocket GUI streaming.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Tuple, Union

from studio.core.models import AgentMessage, GuiTelemetryEvent

logger = logging.getLogger("studio.core.event_bus")

EventHandler = Callable[[Any], Union[Awaitable[None], None]]


def topic_matches(pattern: str, topic: str) -> bool:
    """
    Evaluates whether a topic matches a subscription pattern.
    - '*' matches exactly one segment
    - '**' or '#' matches zero or more segments
    """
    if pattern in ("*", "**", "#") or pattern == topic:
        return True

    p_parts = [p for p in pattern.split(".") if p]
    t_parts = [t for t in topic.split(".") if t]

    def _match(p_idx: int, t_idx: int) -> bool:
        while p_idx < len(p_parts) and t_idx < len(t_parts):
            p = p_parts[p_idx]
            if p == "**" or p == "#":
                if p_idx == len(p_parts) - 1:
                    return True
                for next_t in range(t_idx, len(t_parts) + 1):
                    if _match(p_idx + 1, next_t):
                        return True
                return False
            elif p == "*" or p == t_parts[t_idx]:
                p_idx += 1
                t_idx += 1
            else:
                return False
        if p_idx < len(p_parts) and (p_parts[p_idx] == "**" or p_parts[p_idx] == "#"):
            p_idx += 1
        return p_idx == len(p_parts) and t_idx == len(t_parts)

    return _match(0, 0)


class Subscription:
    """Represents an active subscription to a topic pattern."""

    def __init__(self, sub_id: str, pattern: str, handler: EventHandler, is_async: bool) -> None:
        self.sub_id = sub_id
        self.pattern = pattern
        self.handler = handler
        self.is_async = is_async


class EventBus:
    """
    In-process asynchronous event bus supporting hierarchical wildcard subscriptions
    and a bounded telemetry buffer for GUI replay (F-ORCH-02).
    """

    def __init__(self, telemetry_buffer_size: int = 1000, history_capacity: Optional[int] = None) -> None:
        buffer_size = history_capacity if history_capacity is not None else telemetry_buffer_size
        self._subscriptions: Dict[str, Subscription] = {}
        self._lock = asyncio.Lock()
        self._thread_lock = threading.RLock()
        self._telemetry_buffer: Deque[GuiTelemetryEvent] = deque(maxlen=buffer_size)
        self._event_history: Deque[Tuple[float, str, Any]] = deque(maxlen=buffer_size)
        self._closed = False

    # ------------------------------------------------------------------------
    # Subscription Management
    # ------------------------------------------------------------------------

    def subscribe(self, pattern: str, handler: EventHandler) -> str:
        """
        Subscribes a handler to a topic pattern.
        Returns a unique subscription ID.
        """
        sub_id = str(uuid.uuid4())
        is_async = asyncio.iscoroutinefunction(handler)
        with self._thread_lock:
            self._subscriptions[sub_id] = Subscription(sub_id, pattern, handler, is_async)
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """Removes a subscription by ID."""
        with self._thread_lock:
            return self._subscriptions.pop(sub_id, None) is not None

    def clear_subscribers(self, pattern: Optional[str] = None) -> int:
        """Removes all subscribers, or subscribers matching a given pattern."""
        with self._thread_lock:
            if pattern is None:
                count = len(self._subscriptions)
                self._subscriptions.clear()
                return count
            removed = [sid for sid, sub in self._subscriptions.items() if sub.pattern == pattern]
            for sid in removed:
                del self._subscriptions[sid]
            return len(removed)

    # ------------------------------------------------------------------------
    # Publishing & Dispatch
    # ------------------------------------------------------------------------

    async def publish(self, topic: str, event: Any) -> None:
        """
        Publishes an event to all matching subscribers asynchronously.
        Guarantees subscriber error isolation.
        """
        if self._closed:
            logger.warning(f"Attempted to publish to closed EventBus on topic '{topic}'")
            return

        with self._thread_lock:
            self._event_history.append((time.time(), topic, event))
            if isinstance(event, GuiTelemetryEvent):
                self._telemetry_buffer.append(event)
            elif isinstance(event, AgentMessage) and topic.startswith("telemetry."):
                telem = GuiTelemetryEvent(
                    event_type=event.message_type,
                    agent=event.sender.value if hasattr(event.sender, "value") else str(event.sender),
                    data=event.payload,
                    timestamp=event.timestamp,
                )
                self._telemetry_buffer.append(telem)

            matching_subs = [
                sub for sub in self._subscriptions.values()
                if topic_matches(sub.pattern, topic)
            ]

        if not matching_subs:
            return

        tasks: List[Awaitable[Any]] = []
        for sub in matching_subs:
            tasks.append(self._invoke_handler(sub, topic, event))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _invoke_handler(self, sub: Subscription, topic: str, event: Any) -> None:
        """Executes a single subscriber handler with exception shielding."""
        try:
            if sub.is_async:
                await sub.handler(event)  # type: ignore
            else:
                sub.handler(event)
        except Exception as e:
            logger.error(
                f"Exception in EventBus subscriber '{sub.sub_id}' (pattern='{sub.pattern}') "
                f"while handling topic '{topic}': {e}",
                exc_info=True,
            )

    def publish_sync(self, topic: str, event: Any) -> None:
        """
        Fire-and-forget sync helper that dispatches via existing event loop or thread.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(topic, event))
        except RuntimeError:
            asyncio.run(self.publish(topic, event))

    # ------------------------------------------------------------------------
    # Telemetry Ring Buffer Access
    # ------------------------------------------------------------------------

    async def emit_telemetry(self, telem_event: GuiTelemetryEvent) -> None:
        """Convenience method to emit a GUI telemetry event and publish to telemetry.* topic."""
        topic = f"telemetry.{telem_event.event_type}"
        await self.publish(topic, telem_event)

    def get_telemetry_history(
        self,
        since_timestamp: Optional[float] = None,
        event_type: Optional[str] = None,
        agent: Optional[str] = None,
        limit: int = 100,
    ) -> List[GuiTelemetryEvent]:
        """
        Retrieves telemetry history for WebSocket client sync and catch-up.
        """
        with self._thread_lock:
            items = list(self._telemetry_buffer)

        results: List[GuiTelemetryEvent] = []
        for it in reversed(items):
            if since_timestamp and it.timestamp <= since_timestamp:
                continue
            if event_type and it.event_type != event_type:
                continue
            if agent and it.agent != agent:
                continue
            results.append(it)
            if len(results) >= limit:
                break

        return list(reversed(results))

    def get_history(
        self,
        topic_filter: Optional[str] = None,
        limit: int = 100,
    ) -> List[Any]:
        """
        Retrieves recent published events matching an optional topic filter.
        """
        with self._thread_lock:
            items = list(self._event_history)

        results: List[Any] = []
        for _, topic, event in reversed(items):
            if topic_filter is None or topic_matches(topic_filter, topic):
                results.append(event)
                if len(results) >= limit:
                    break
        return list(reversed(results))

    # ------------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------------

    async def close(self) -> None:
        """Closes the event bus and clears subscribers."""
        self._closed = True
        with self._thread_lock:
            self._subscriptions.clear()
