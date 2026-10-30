"""
studio/gui/server.py - Studio FastAPI + Uvicorn ASGI Server Application
Configures REST routing, WebSocket telemetry streaming, static asset serving,
CORS policies, font MIME types, and application lifespan state.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from studio.core.blackboard import Blackboard
from studio.core.event_bus import EventBus
from studio.core.orchestrator import StudioOrchestrator
from studio.core.security import CredentialManager
from studio.engine.hub import EngineHub, get_engine_hub
from studio.gui.routes import router as api_router
from studio.gui.websocket_manager import WebSocketTelemetryManager
from studio.providers.claude_client import ClaudeClient
from studio.providers.gemini_client import GeminiClient
from studio.providers.openai_client import OpenAIClient
from studio.verification.pipeline import VerificationPipeline

logger = logging.getLogger("studio.gui.server")

# Register font and media MIME types
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("font/otf", ".otf")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")


def create_app(
    blackboard: Optional[Blackboard] = None,
    event_bus: Optional[EventBus] = None,
    orchestrator: Optional[StudioOrchestrator] = None,
    engine_hub: Optional[EngineHub] = None,
    verification_pipeline: Optional[VerificationPipeline] = None,
    static_dir: Optional[Path] = None,
) -> FastAPI:
    """
    Factory creating a configured FastAPI application instance.
    Enables testing with in-memory test doubles without modifying global singletons.
    """
    bb = blackboard or Blackboard(project_name="AIGameStudioProject")
    bus = event_bus or EventBus()
    hub = engine_hub or get_engine_hub()
    v_pipe = verification_pipeline or VerificationPipeline(event_bus=bus, blackboard=bb)

    if orchestrator is None:
        cred_mgr = CredentialManager()
        openai_c = OpenAIClient(api_key=cred_mgr.get_key("openai"))
        gemini_c = GeminiClient(api_key=cred_mgr.get_key("gemini"))
        claude_c = ClaudeClient(api_key=cred_mgr.get_key("anthropic"))
        orch = StudioOrchestrator(
            blackboard=bb,
            event_bus=bus,
            openai_client=openai_c,
            gemini_client=gemini_c,
            claude_client=claude_c,
            verification_pipeline=v_pipe,
        )
    else:
        orch = orchestrator

    ws_manager = WebSocketTelemetryManager(event_bus=bus)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.start_time = time.time()
        logger.info("Studio GUI backend starting up")
        yield
        logger.info("Studio GUI backend shutting down")
        await ws_manager.close_all()
        await bus.close()

    app = FastAPI(
        title="AI Game Studio",
        description="Autonomous Multi-Agent AI Game Studio Dashboard",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Attach state references
    app.state.blackboard = bb
    app.state.event_bus = bus
    app.state.orchestrator = orch
    app.state.engine_hub = hub
    app.state.verification_pipeline = v_pipe
    app.state.websocket_manager = ws_manager
    app.state.start_time = time.time()

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API routes
    app.include_router(api_router, prefix="/api")

    # WebSocket Telemetry endpoint
    @app.websocket("/api/ws/telemetry")
    async def websocket_telemetry_endpoint(websocket: WebSocket) -> None:
        await ws_manager.connect(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                # Respond to ping messages for keep-alive testing
                if data == "ping":
                    await websocket.send_text("pong")
                else:
                    try:
                        parsed = json.loads(data)
                        if isinstance(parsed, dict) and parsed.get("type") == "ping":
                            await websocket.send_json({"type": "pong"})
                    except Exception:
                        pass
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            await ws_manager.disconnect(websocket)

    # Static asset mounting & Root Route
    resolved_static = (static_dir or (Path(__file__).parent / "static")).resolve()
    resolved_static.mkdir(parents=True, exist_ok=True)

    app.mount("/static", StaticFiles(directory=str(resolved_static)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index() -> Response:
        index_file = resolved_static / "index.html"
        if index_file.is_file():
            return FileResponse(str(index_file), media_type="text/html; charset=utf-8")
        return Response("<!DOCTYPE html><html><head><title>AI Game Studio</title></head><body><h1>AI Game Studio Visual Dashboard</h1></body></html>", media_type="text/html; charset=utf-8")

    no_cache_headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    @app.get("/game", include_in_schema=False)
    async def serve_game() -> Response:
        output_index = Path("./output/index.html").resolve()
        if output_index.is_file():
            return FileResponse(str(output_index), media_type="text/html; charset=utf-8", headers=no_cache_headers)
        return Response("<h1>Game not yet exported</h1><p>Please click 'Build Game' and 'Export Game' first.</p>", media_type="text/html; charset=utf-8", headers=no_cache_headers)

    @app.get("/style.css", include_in_schema=False)
    async def serve_output_css() -> Response:
        css = Path("./output/style.css").resolve()
        if css.is_file():
            return FileResponse(str(css), media_type="text/css", headers=no_cache_headers)
        return Response("", media_type="text/css", headers=no_cache_headers)

    @app.get("/game.js", include_in_schema=False)
    async def serve_output_js() -> Response:
        js = Path("./output/game.js").resolve()
        if js.is_file():
            return FileResponse(str(js), media_type="application/javascript", headers=no_cache_headers)
        return Response("", media_type="application/javascript", headers=no_cache_headers)

    # Static mount for output directory
    output_dir_path = Path("./output").resolve()
    output_dir_path.mkdir(parents=True, exist_ok=True)
    app.mount("/output", StaticFiles(directory=str(output_dir_path), html=True), name="output_direct")

    return app


# Default module-level ASGI app for Uvicorn
app = create_app()
