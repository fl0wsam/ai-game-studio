"""
studio/gui/routes.py - Studio REST API Route Controllers
Implements endpoints for direct prompt generation, project state inspection,
agent status tracking, asset binary streaming, verification, engine export, and health telemetry.
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from studio.core.blackboard import Blackboard
from studio.core.event_bus import EventBus
from studio.core.models import (
    AssetItem,
    EngineTarget,
    GuiTelemetryEvent,
    ProjectStatus,
    StudioProjectState,
    VerificationReport,
)
from studio.core.orchestrator import StudioOrchestrator
from studio.engine.hierarchy import CleanHierarchyPackager
from studio.engine.hub import EngineHub
from studio.gui.desktop import normalize_cross_platform_path
from studio.verification.pipeline import VerificationPipeline

logger = logging.getLogger("studio.gui.routes")

router = APIRouter()
_background_tasks: Set[asyncio.Task[Any]] = set()


# ----------------------------------------------------------------------------
# Request & Response Models
# ----------------------------------------------------------------------------

class DirectRequest(BaseModel):
    """Payload for POST /api/direct initiating autonomous game creation."""
    prompt: str = Field(..., description="High concept premise for the game")
    target_engine: Optional[str] = Field(default="godot4", description="Target game engine (godot4, pygame, web_html5, unity)")
    engine: Optional[str] = Field(default=None, description="Alias for target_engine")
    genre: Optional[str] = Field(default=None, description="Optional genre hint (platformer_2d, top_down_rpg, etc.)")
    project_name: Optional[str] = Field(default=None, description="Project title (auto-generated if omitted)")
    mock: bool = Field(default=True, description="Force deterministic offline mock provider")
    max_iterations: int = Field(default=3, ge=1, le=10, description="Maximum review iterations")
    constraints: Dict[str, Any] = Field(default_factory=dict, description="Custom constraints & style overrides")


class VerifyRequest(BaseModel):
    """Payload for POST /api/project/verify."""
    stages: Optional[List[str]] = Field(default=None, description="Subset of stages: syntax_check, schema_check, asset_integrity, simulation")
    fail_fast: bool = Field(default=False, description="Halt immediately on first error")
    simulation_frames: int = Field(default=60, ge=0, le=1000, description="Number of simulation frames to execute")


class RefineRequest(BaseModel):
    """Payload for POST /api/project/refine applying iterative prompts."""
    prompt: str = Field(..., description="Modification / refinement instruction")


class RefineResponse(BaseModel):
    """Response payload for POST /api/project/refine."""
    success: bool
    status: str
    message: str
    version: int


class ExportRequest(BaseModel):
    """Payload for POST /api/project/export."""
    output_dir: Optional[str] = Field(default="./output", description="Destination export directory path")
    target_engine: Optional[str] = Field(default=None, description="Override engine target")
    overwrite: bool = Field(default=True, description="Overwrite existing files")


class ExportResponse(BaseModel):
    """Response payload for POST /api/project/export."""
    success: bool
    status: str
    output_dir: str
    engine: str
    total_files: int
    total_bytes: int
    manifest: Optional[Dict[str, Any]] = None
    validation: Optional[Dict[str, Any]] = None


# ----------------------------------------------------------------------------
# Dependency Resolvers (Accessing Application State)
# ----------------------------------------------------------------------------

def get_blackboard(request: Request) -> Blackboard:
    return request.app.state.blackboard


def get_event_bus(request: Request) -> EventBus:
    return request.app.state.event_bus


def get_orchestrator(request: Request) -> StudioOrchestrator:
    return request.app.state.orchestrator


def get_engine_hub(request: Request) -> EngineHub:
    return request.app.state.engine_hub


def get_verification_pipeline(request: Request) -> VerificationPipeline:
    return request.app.state.verification_pipeline


# ----------------------------------------------------------------------------
# Route Endpoints
# ----------------------------------------------------------------------------

@router.post("/direct", status_code=status.HTTP_202_ACCEPTED)
async def direct_generation(
    req: DirectRequest,
    blackboard: Blackboard = Depends(get_blackboard),
    orchestrator: StudioOrchestrator = Depends(get_orchestrator),
    event_bus: EventBus = Depends(get_event_bus),
    hub: EngineHub = Depends(get_engine_hub),
) -> Dict[str, Any]:
    """
    Triggers autonomous multi-agent generation with prompt and constraints.
    Runs pipeline in the background while broadcasting telemetry to WebSockets.
    """
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Prompt cannot be empty or whitespace.",
        )

    state = await blackboard.get_state()
    if state.status == ProjectStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Autonomous generation is already running. Please wait for completion.",
        )

    project_name = req.project_name or f"Game_{int(time.time())}"
    target_engine = req.target_engine or req.engine or "godot4"

    # Auto-detect archetype if genre is missing, auto, or empty
    genre = req.genre
    if not genre or str(genre).lower() in ("auto", "none", "", "null"):
        from studio.providers.mock_provider import detect_archetype
        genre = detect_archetype(prompt)
        logger.info(f"Auto-detected game genre archetype from prompt: '{genre}'")

    # Clear previous game build files from output directory (clean workspace)
    try:
        import shutil
        out_dir = Path("./output").resolve()
        if out_dir.exists():
            for item in out_dir.iterdir():
                try:
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
                except Exception as del_err:
                    logger.warning(f"Failed to clear output file {item}: {del_err}")
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as clean_err:
        logger.warning(f"Could not purge ./output directory: {clean_err}")

    # Reset blackboard state atomically so no stale assets or scripts leak
    await blackboard.reset(
        project_name=project_name,
        target_engine=target_engine,
        user_prompt=prompt,
    )

    # Asynchronous background generation worker
    async def _run_job() -> None:
        try:
            logger.info(f"Starting background generation pipeline for '{project_name}' [Genre: {genre}]")
            if req.mock:
                from studio.providers.mock_provider import MockProviderClient
                mock_client = MockProviderClient()
                orchestrator.openai_client = mock_client
                orchestrator.gemini_client = mock_client
                orchestrator.claude_client = mock_client
                is_testing = "test" in sys.argv[0] or bool(os.environ.get("PYTEST_CURRENT_TEST"))
                orchestrator.pacing_delay = 0.0 if is_testing else 0.55
            state = await orchestrator.run_pipeline(
                prompt=prompt,
                target_engine=target_engine,
                project_name=project_name,
                genre=genre,
            )

            # Auto-export game output so it is immediately playable on disk
            try:
                out_dir = Path("./output").resolve()
                out_dir.mkdir(parents=True, exist_ok=True)
                if state.gdd and state.asset_manifest:
                    hub.export(
                        target=target_engine,
                        gdd=state.gdd,
                        manifest=state.asset_manifest,
                        scenes=state.scenes,
                        scripts=state.scripts,
                        output_dir=out_dir,
                    )
                    # Always ensure web browser playable edition exists in ./output
                    if target_engine != "web_html5":
                        try:
                            from studio.engine.adapters.web_adapter import WebPhaserAdapter
                            web_adapter = WebPhaserAdapter()
                            web_adapter.export_project(
                                gdd=state.gdd,
                                manifest=state.asset_manifest,
                                scenes=state.scenes,
                                scripts=state.scripts,
                                output_dir=out_dir,
                            )
                        except Exception as web_err:
                            logger.warning(f"Web browser export fallback warning: {web_err}")
                    logger.info(f"Auto-exported game to {out_dir}")
            except Exception as exp_err:
                logger.warning(f"Auto-export exception: {exp_err}")
        except Exception as e:
            logger.error(f"Error in background generation pipeline: {e}", exc_info=True)
            await event_bus.emit_telemetry(
                GuiTelemetryEvent(
                    event_type="pipeline_failed",
                    state="failed",
                    data={"error": str(e)},
                )
            )

    task = asyncio.create_task(_run_job())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {
        "status": "started",
        "project_id": state.project_id,
        "project_name": project_name,
        "target_engine": target_engine,
        "genre": genre,
        "message": f"Autonomous generation launched for genre '{genre}'. Streaming telemetry to /api/ws/telemetry.",
    }


@router.post("/project/refine", response_model=RefineResponse)
async def refine_project(
    req: RefineRequest,
    blackboard: Blackboard = Depends(get_blackboard),
    hub: EngineHub = Depends(get_engine_hub),
    event_bus: EventBus = Depends(get_event_bus),
) -> RefineResponse:
    """
    Applies iterative user refinement prompts to an existing game project.
    Patches game design mechanics, lore, and engine parameters in real time.
    """
    instruction = req.prompt.strip()
    if not instruction:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Refinement prompt cannot be empty.",
        )

    state = await blackboard.get_state()
    if not state.gdd:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No game project exists yet. Please generate a game first before refining.",
        )

    # Broadcast agent telemetry for refinement
    await event_bus.emit_telemetry(
        GuiTelemetryEvent(
            event_type="agent_state",
            agent="claude_engineer",
            state="active",
            data={"action": f"Applying refinement: '{instruction}'"},
        )
    )

    # Update GDD mechanics, lore, and metadata
    gdd = state.gdd
    gdd.gameplay_mechanics.append(f"User Refinement: {instruction}")
    gdd.narrative_lore += f"\n[Refinement]: {instruction}"
    gdd.version += 1

    # Extract common tweaks into custom_metadata
    lower_inst = instruction.lower()
    if not isinstance(gdd.custom_metadata, dict):
        gdd.custom_metadata = {}

    if any(k in lower_inst for k in ("speed", "fast", "سرعت", "سریع")):
        curr_spd = gdd.custom_metadata.get("speed_multiplier", 1.0)
        gdd.custom_metadata["speed_multiplier"] = round(curr_spd * 1.4, 2)
    if any(k in lower_inst for k in ("jump", "leap", "پرش", "بپرد")):
        curr_jmp = gdd.custom_metadata.get("jump_multiplier", 1.0)
        gdd.custom_metadata["jump_multiplier"] = round(curr_jmp * 1.3, 2)
    if any(k in lower_inst for k in ("hp", "health", "life", "lives", "جون", "جان", "سلامت")):
        gdd.custom_metadata["hp_override"] = 5
    if any(k in lower_inst for k in ("laser", "weapon", "shoot", "اسلحه", "تیر", "شلیک")):
        gdd.custom_metadata["rapid_fire"] = True

    await blackboard.set_gdd(gdd)

    # Re-export updated game to ./output
    target_engine = state.target_engine or "godot4"
    out_dir = Path("./output").resolve()
    if state.asset_manifest:
        try:
            hub.export(
                target=target_engine,
                gdd=gdd,
                manifest=state.asset_manifest,
                scenes=state.scenes,
                scripts=state.scripts,
                output_dir=out_dir,
            )
            logger.info(f"Re-exported refined game to {out_dir}")
        except Exception as e:
            logger.warning(f"Refine export warning: {e}")

    await event_bus.emit_telemetry(
        GuiTelemetryEvent(
            event_type="agent_state",
            agent="claude_engineer",
            state="complete",
            data={"action": f"Refined successfully: '{instruction}'"},
        )
    )
    await event_bus.emit_telemetry(
        GuiTelemetryEvent(
            event_type="log_chunk",
            agent="CLAUDE",
            data={"level": "SUCCESS", "message": f"Applied iterative refinement: '{instruction}' (v{gdd.version})"},
        )
    )
    await event_bus.emit_telemetry(
        GuiTelemetryEvent(
            event_type="pipeline_completed",
            state="complete",
            data={"summary": f"Refinement '{instruction}' applied successfully."},
        )
    )

    return RefineResponse(
        success=True,
        status="refined",
        message=f"Game refined successfully: '{instruction}'",
        version=gdd.version,
    )


@router.get("/project/state")
async def get_project_state(
    include_logs: bool = Query(default=True),
    log_limit: int = Query(default=100, ge=1, le=1000),
    blackboard: Blackboard = Depends(get_blackboard),
) -> Dict[str, Any]:
    """Returns the canonical snapshot of current GDD, manifest, scenes, scripts, and status."""
    state: StudioProjectState = await blackboard.get_state()
    dump = state.model_dump(mode="json")
    if not include_logs:
        dump["logs"] = []
    elif len(dump.get("logs", [])) > log_limit:
        dump["logs"] = dump["logs"][-log_limit:]
    return dump


@router.get("/project/status")
async def get_project_status(
    blackboard: Blackboard = Depends(get_blackboard),
) -> Dict[str, Any]:
    """Returns active operational status of each agent and current studio phase."""
    state = await blackboard.get_state()
    is_active = state.status == ProjectStatus.IN_PROGRESS

    # Determine individual agent states based on active phase
    phase = state.active_phase.upper()
    agent_states = {
        "orchestrator": {
            "name": "Studio Orchestrator",
            "state": "active" if is_active else ("complete" if state.status == ProjectStatus.COMPLETED else "idle"),
            "phase": state.active_phase,
        },
        "openai_director": {
            "name": "OpenAI Director",
            "state": "active" if is_active and "NARRATIVE" in phase else ("complete" if state.gdd else "idle"),
            "task": "Generating GDD & Narrative" if is_active and "NARRATIVE" in phase else ("GDD Ready" if state.gdd else "Standing by"),
        },
        "gemini_artist": {
            "name": "Gemini Artist",
            "state": "active" if is_active and ("ASSET" in phase or "MEDIA" in phase) else ("complete" if state.asset_manifest else "idle"),
            "task": "Synthesizing Multimodal Assets" if is_active and ("ASSET" in phase or "MEDIA" in phase) else ("Assets Generated" if state.asset_manifest else "Standing by"),
        },
        "claude_engineer": {
            "name": "Claude Engineer",
            "state": "active" if is_active and ("CODE" in phase or "ENGINE" in phase or "SCENE" in phase) else ("complete" if state.scenes or state.scripts else "idle"),
            "task": "Compiling Engine Architecture & Scripts" if is_active and ("CODE" in phase or "ENGINE" in phase or "SCENE" in phase) else ("Code Ready" if state.scripts else "Standing by"),
        },
        "validator": {
            "name": "Automated Validator",
            "state": "active" if is_active and "VERIF" in phase else ("complete" if state.verification_report and state.verification_report.passed else ("failed" if state.verification_report and not state.verification_report.passed else "idle")),
            "task": "Running Syntax & Simulation Tests" if is_active and "VERIF" in phase else ("Validation Passed" if state.verification_report and state.verification_report.passed else "Ready"),
        },
    }

    return {
        "project_name": state.project_name,
        "project_status": state.status.value,
        "active_phase": state.active_phase,
        "revision": state.revision,
        "agents": agent_states,
    }


@router.get("/project/assets/{asset_id}")
async def get_project_asset(
    asset_id: str,
    request: Request,
    metadata: bool = Query(default=False),
    blackboard: Blackboard = Depends(get_blackboard),
) -> Response:
    """
    Retrieves asset binary bytes (PNG, WAV, MP3, etc.) or metadata.
    Guarded against path traversal and missing asset IDs.
    """
    clean_id = asset_id.strip()
    # Check for path traversal attempts in asset_id
    if ".." in clean_id or "/" in clean_id or "\\" in clean_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invalid asset id '{asset_id}'.",
        )

    item: Optional[AssetItem] = await blackboard.get_asset_item(clean_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset with id '{clean_id}' not found.",
        )

    accept_header = request.headers.get("accept", "")
    if metadata or "application/json" in accept_header:
        return Response(
            content=item.model_dump_json(),
            media_type="application/json",
            headers={"X-Asset-Type": str(item.asset_type)},
        )

    # Determine media MIME type
    ext = Path(item.rel_path).suffix.lower()
    mime_mapping = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".mp4": "video/mp4",
        ".svg": "image/svg+xml",
        ".json": "application/json",
    }
    content_type = mime_mapping.get(ext) or mimetypes.guess_type(item.rel_path)[0] or "application/octet-stream"

    file_bytes = item.file_bytes or b""
    filename = Path(item.rel_path).name or f"{clean_id}{ext or '.bin'}"

    return Response(
        content=file_bytes,
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Asset-Type": str(item.asset_type),
            "X-Asset-Checksum": str(item.checksum),
        },
    )


@router.post("/project/verify")
async def verify_project(
    req: VerifyRequest,
    blackboard: Blackboard = Depends(get_blackboard),
    verifier: VerificationPipeline = Depends(get_verification_pipeline),
    event_bus: EventBus = Depends(get_event_bus),
) -> Dict[str, Any]:
    """Triggers the automated verification pipeline across current state."""
    state = await blackboard.get_state()

    # Validate stages if provided
    if req.stages:
        for stg in req.stages:
            if stg not in verifier.ALL_STAGES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid verification stage '{stg}'. Valid stages: {verifier.ALL_STAGES}",
                )

    report: VerificationReport = verifier.verify_project(
        target=state,
        stages=req.stages,
        fail_fast=req.fail_fast,
        simulation_frames=req.simulation_frames,
    )

    await blackboard.set_verification_report(report)
    await event_bus.emit_telemetry(
        GuiTelemetryEvent(
            event_type="verification_done",
            state="complete" if report.passed else "failed",
            data=report.model_dump(mode="json"),
        )
    )

    return report.model_dump(mode="json")


@router.post("/project/export", response_model=ExportResponse)
async def export_project(
    req: ExportRequest,
    blackboard: Blackboard = Depends(get_blackboard),
    hub: EngineHub = Depends(get_engine_hub),
) -> ExportResponse:
    """Exports generated game files into target directory on disk."""
    out_dir_str = req.output_dir or "./output"

    # Validate output_dir against path traversal attacks
    try:
        # Check raw string parts for traversal escapes
        parts = Path(out_dir_str.replace("\\", "/")).parts
        if ".." in parts:
            raise ValueError(f"Path traversal detected in output_dir: '{out_dir_str}'")
        out_dir = Path(out_dir_str).resolve()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid output directory path: {e}",
        )

    state = await blackboard.get_state()
    target_engine = req.target_engine or state.target_engine or "godot4"

    # Create canonical hierarchy
    packager = CleanHierarchyPackager(target_engine=target_engine)
    out_dir.mkdir(parents=True, exist_ok=True)
    packager.create_hierarchy(out_dir)

    total_files = 0
    total_bytes = 0
    manifest_dict = None
    validation_dict = None

    # If project has generated assets, scenes, or scripts, export them
    if state.gdd and state.asset_manifest:
        try:
            hub_manifest = hub.export(
                target=target_engine,
                gdd=state.gdd,
                manifest=state.asset_manifest,
                scenes=state.scenes,
                scripts=state.scripts,
                output_dir=out_dir,
            )
            total_files = len(hub_manifest.files_created)
            manifest_dict = hub_manifest.model_dump(mode="json")
        except Exception as e:
            logger.warning(f"EngineHub export exception: {e}; falling back to CleanHierarchyPackager")

    # Package documentation and canonical manifest
    if state.gdd:
        packager.package_docs(out_dir, state.gdd)
    if state.asset_manifest:
        packager.package_assets(out_dir, state.asset_manifest, overwrite=req.overwrite)
    if state.scripts:
        packager.package_scripts(out_dir, state.scripts, overwrite=req.overwrite)
    if state.scenes:
        packager.package_scenes(out_dir, state.scenes, overwrite=req.overwrite)

    hierarchy_manifest = packager.generate_manifest(out_dir, project_name=state.project_name)
    validation_report = packager.verify_hierarchy(out_dir)

    total_files = hierarchy_manifest.total_files
    total_bytes = hierarchy_manifest.total_bytes
    manifest_dict = hierarchy_manifest.model_dump(mode="json")
    validation_dict = validation_report.model_dump(mode="json")

    return ExportResponse(
        success=True,
        status="exported",
        output_dir=str(out_dir),
        engine=target_engine,
        total_files=total_files,
        total_bytes=total_bytes,
        manifest=manifest_dict,
        validation=validation_dict,
    )


@router.get("/health")
async def health_check(
    request: Request,
    blackboard: Blackboard = Depends(get_blackboard),
) -> Dict[str, Any]:
    """Service health and telemetry status endpoint."""
    state = await blackboard.get_state()
    uptime = time.time() - getattr(request.app.state, "start_time", time.time())
    ws_mgr = getattr(request.app.state, "websocket_manager", None)
    active_ws = len(ws_mgr.active_connections) if ws_mgr else 0

    return {
        "status": "healthy",
        "version": "0.1.0",
        "uptime_seconds": round(uptime, 2),
        "active_project": state.project_name,
        "project_status": state.status.value,
        "active_phase": state.active_phase,
        "active_websocket_clients": active_ws,
    }


@router.get("/project/logs")
async def get_project_logs(
    limit: int = Query(default=100, ge=1, le=1000),
    blackboard: Blackboard = Depends(get_blackboard),
) -> List[Dict[str, Any]]:
    """Returns historical execution logs."""
    return await blackboard.get_logs(limit=limit)
