"""
studio/gui/websocket_manager.py - Real-Time WebSocket Telemetry Manager
Manages client connection lifecycles, broadcasts live GuiTelemetryEvents from EventBus,
and streams historical telemetry replay buffers for client state synchronization.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, List, Optional, Set, Union
from fastapi import WebSocket, WebSocketDisconnect

from studio.core.event_bus import EventBus
from studio.core.models import GuiTelemetryEvent

logger = logging.getLogger("studio.gui.websocket_manager")


class WebSocketTelemetryManager:
    """
    Manages active WebSocket connections for live telemetry streaming (PROJECT.md § 6).
    Subscribes to EventBus telemetry topics and broadcasts events to all connected clients.
    """

    def __init__(self, event_bus: Optional[EventBus] = None, history_limit: int = 100) -> None:
        self.event_bus = event_bus
        self.history_limit = history_limit
        self.active_connections: Set[WebSocket] = set()
        self._lock = threading.RLock()
        self._bus_sub_id: Optional[str] = None

        if self.event_bus:
            self.attach_event_bus(self.event_bus)

    def attach_event_bus(self, event_bus: EventBus) -> None:
        """Subscribes this manager to the EventBus telemetry wildcard topic."""
        self.event_bus = event_bus
        self._bus_sub_id = self.event_bus.subscribe("telemetry.*", self._handle_bus_event)
        logger.info(f"WebSocketTelemetryManager subscribed to EventBus (sub_id={self._bus_sub_id})")

    def detach_event_bus(self) -> None:
        """Unsubscribes from the EventBus."""
        if self.event_bus and self._bus_sub_id:
            self.event_bus.unsubscribe(self._bus_sub_id)
            self._bus_sub_id = None

    async def connect(self, websocket: WebSocket) -> None:
        """
        Accepts a new client connection, adds it to the active set,
        and immediately transmits historical telemetry events for state catch-up.
        """
        await websocket.accept()
        with self._lock:
            self.active_connections.add(websocket)
        logger.info(f"WebSocket client connected. Active connections: {len(self.active_connections)}")

        # Send catch-up history
        if self.event_bus:
            try:
                history = self.event_bus.get_telemetry_history(limit=self.history_limit)
                for event in history:
                    try:
                        payload = event.model_dump(mode="json") if hasattr(event, "model_dump") else event
                        await websocket.send_json(payload)
                    except Exception as e:
                        logger.warning(f"Failed to send history event to client: {e}")
                        break
            except Exception as e:
                logger.warning(f"Failed to retrieve telemetry history: {e}")

    async def disconnect(self, websocket: WebSocket) -> None:
        """Removes a disconnected client from the active set."""
        with self._lock:
            self.active_connections.discard(websocket)
        logger.info(f"WebSocket client disconnected. Active connections: {len(self.active_connections)}")

    async def broadcast(self, event: Union[GuiTelemetryEvent, Dict[str, Any]]) -> int:
        """
        Broadcasts an event payload to all active clients.
        Dead connections are automatically pruned.
        Returns the number of successfully delivered messages.
        """
        with self._lock:
            if not self.active_connections:
                return 0
            clients = list(self.active_connections)

        payload: Dict[str, Any]
        if hasattr(event, "model_dump"):
            payload = event.model_dump(mode="json")
        elif isinstance(event, dict):
            payload = event
        else:
            payload = {"data": str(event)}

        dead_connections: List[WebSocket] = []
        sent_count = 0

        for ws in clients:
            try:
                await ws.send_json(payload)
                sent_count += 1
            except (WebSocketDisconnect, RuntimeError, Exception) as e:
                logger.debug(f"Failed to send to client ({e}); marking for removal")
                dead_connections.append(ws)

        if dead_connections:
            with self._lock:
                for dead in dead_connections:
                    self.active_connections.discard(dead)

        return sent_count

    async def _handle_bus_event(self, event: Any) -> None:
        """EventBus subscriber callback: forwards GuiTelemetryEvent to WebSocket clients."""
        try:
            if isinstance(event, GuiTelemetryEvent):
                await self.broadcast(event)
            elif hasattr(event, "model_dump"):
                await self.broadcast(event.model_dump(mode="json"))
            elif isinstance(event, dict):
                await self.broadcast(event)
        except Exception as e:
            logger.warning(f"Error handling event bus callback: {e}")

    async def close_all(self) -> None:
        """Gracefully closes all active WebSocket connections on shutdown."""
        with self._lock:
            clients = list(self.active_connections)
            self.active_connections.clear()

        for ws in clients:
            try:
                await ws.close(code=1001, reason="Server shutting down")
            except Exception:
                pass
        self.detach_event_bus()
