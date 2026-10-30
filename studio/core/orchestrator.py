"""
studio/core/orchestrator.py - Tri-Provider Autonomous Orchestration Coordinator
Coordinates OpenAI, Gemini, and Claude through sequential stages,
enforcing strict role separation, broadcasting live telemetry, and managing review loops.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from studio.core.blackboard import Blackboard
from studio.core.event_bus import EventBus
from studio.core.models import (
    AgentMessage,
    AgentRole,
    AssetItem,
    AssetManifest,
    DiagnosticError,
    EngineTarget,
    GameDesignDocument,
    GuiTelemetryEvent,
    ProjectStatus,
    ReviewFeedback,
    SceneAST,
    ScriptSpec,
    StudioProjectState,
    VerificationReport,
)

logger = logging.getLogger("studio.core.orchestrator")


class OrchestrationPhase(str, Enum):
    INIT = "INIT"
    NARRATIVE = "NARRATIVE"
    ASSET_GEN = "ASSET_GEN"
    ENGINE_CODE = "ENGINE_CODE"
    VERIFICATION = "VERIFICATION"
    REVIEW_LOOP = "REVIEW_LOOP"
    PACKAGING = "PACKAGING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StudioOrchestrator:
    """
    Coordinates OpenAI, Gemini, and Claude through sequential stages,
    enforcing role separation, broadcasting live telemetry, and managing review loops (F-ORCH-01 to F-ORCH-07).
    """

    def __init__(
        self,
        blackboard: Blackboard,
        event_bus: EventBus,
        openai_client: Any,
        gemini_client: Any,
        claude_client: Any,
        verification_pipeline: Optional[Any] = None,
        max_iterations: int = 3,
    ) -> None:
        self.blackboard = blackboard
        self.event_bus = event_bus
        self.openai_client = openai_client
        self.gemini_client = gemini_client
        self.claude_client = claude_client
        self.verification_pipeline = verification_pipeline
        self.max_iterations = max_iterations
        self._current_phase = OrchestrationPhase.INIT
        self._iteration_count = 0
        self.pacing_delay: float = 0.0

    @property
    def current_phase(self) -> OrchestrationPhase:
        return self._current_phase

    @property
    def iteration_count(self) -> int:
        return self._iteration_count

    # ------------------------------------------------------------------------
    # Pipeline Execution Entry Point
    # ------------------------------------------------------------------------

    async def run_pipeline(
        self,
        prompt: str,
        target_engine: str = EngineTarget.GODOT4.value,
        project_name: str = "AIGameStudioProject",
        genre: Optional[str] = None,
    ) -> StudioProjectState:
        """
        Executes the full tri-provider pipeline autonomously from prompt to verified game.
        """
        logger.info(f"Starting Studio Orchestration for '{project_name}' [Engine: {target_engine}, Genre: {genre}]")
        self._current_phase = OrchestrationPhase.INIT
        await self.blackboard.set_status(ProjectStatus.IN_PROGRESS, active_phase=self._current_phase.value)
        await self._broadcast_telemetry("pipeline_started", None, "active", {"prompt": prompt, "engine": target_engine, "genre": genre})

        try:
            if self.pacing_delay > 0:
                await asyncio.sleep(self.pacing_delay)

            # Phase 1: OpenAI Director (GDD & Lore)
            gdd = await self.execute_narrative_phase(prompt, target_engine, project_name, genre=genre)

            if self.pacing_delay > 0:
                await asyncio.sleep(self.pacing_delay)

            # Phase 2: Gemini Artist (Multimodal Assets)
            manifest = await self.execute_asset_phase(gdd)

            if self.pacing_delay > 0:
                await asyncio.sleep(self.pacing_delay)

            # Phase 3: Claude Engineer (Engine Scenes & Code)
            await self.execute_engine_code_phase(gdd, manifest)

            if self.pacing_delay > 0:
                await asyncio.sleep(self.pacing_delay)

            # Phase 4 & 5: Verification & Review Loop
            final_report = await self.execute_verification_and_review_phase()

            if final_report.passed:
                self._current_phase = OrchestrationPhase.COMPLETED
                await self.blackboard.set_status(ProjectStatus.COMPLETED, active_phase=self._current_phase.value)
                await self._broadcast_telemetry(
                    "pipeline_completed",
                    None,
                    "complete",
                    {"summary": "Project generated and verified successfully", "errors": []},
                )
            else:
                self._current_phase = OrchestrationPhase.FAILED
                await self.blackboard.set_status(ProjectStatus.FAILED, active_phase=self._current_phase.value)
                await self._broadcast_telemetry(
                    "pipeline_failed",
                    None,
                    "failed",
                    {"errors": [e.model_dump() for e in final_report.errors]},
                )

        except Exception as e:
            logger.critical(f"Fatal error during studio pipeline: {e}", exc_info=True)
            self._current_phase = OrchestrationPhase.FAILED
            await self.blackboard.set_status(ProjectStatus.FAILED, active_phase=self._current_phase.value)
            await self._broadcast_telemetry("pipeline_failed", None, "failed", {"error": str(e)})
            raise

        return await self.blackboard.get_state()

    # ------------------------------------------------------------------------
    # Phase 1: OpenAI Narrative & GDD Generation
    # ------------------------------------------------------------------------

    async def execute_narrative_phase(
        self,
        prompt: str,
        target_engine: str,
        project_name: str,
        genre: Optional[str] = None,
    ) -> GameDesignDocument:
        self._current_phase = OrchestrationPhase.NARRATIVE
        await self.blackboard.set_status(ProjectStatus.IN_PROGRESS, active_phase=self._current_phase.value)
        await self._broadcast_telemetry(
            "agent_state",
            AgentRole.DIRECTOR.value,
            "active",
            {"action": f"Drafting narrative, GDD and mechanics for '{project_name}'", "task": f"Designing '{project_name}'"}
        )

        logger.info("Phase 1: Dispatching GDD generation to OpenAI Director")
        gdd: GameDesignDocument = await self.openai_client.generate_gdd(
            prompt=prompt,
            context={"target_engine": target_engine, "project_name": project_name, "genre": genre},
            archetype=genre,
        )

        await self.blackboard.set_gdd(gdd)
        await self._broadcast_telemetry(
            "agent_state",
            AgentRole.DIRECTOR.value,
            "complete",
            {"title": gdd.title, "genre": gdd.genre, "task": f"GDD Ready: '{gdd.title}' ({gdd.genre})"}
        )
        await self.event_bus.publish("agent.openai_director.gdd_ready", gdd)
        return gdd

    # ------------------------------------------------------------------------
    # Phase 2: Gemini Multimodal Asset Generation
    # ------------------------------------------------------------------------

    async def execute_asset_phase(self, gdd: GameDesignDocument) -> AssetManifest:
        self._current_phase = OrchestrationPhase.ASSET_GEN
        await self.blackboard.set_status(ProjectStatus.IN_PROGRESS, active_phase=self._current_phase.value)
        await self._broadcast_telemetry(
            "agent_state",
            AgentRole.ARTIST.value,
            "active",
            {"required_assets_count": len(gdd.required_assets), "task": f"Synthesizing {len(gdd.required_assets)} assets for '{gdd.title}'"}
        )

        logger.info(f"Phase 2: Dispatching {len(gdd.required_assets)} asset requests to Gemini Artist")
        manifest: AssetManifest = await self.gemini_client.generate_assets(
            gdd=gdd,
            context={"project_name": gdd.title},
        )

        await self.blackboard.set_asset_manifest(manifest)

        # Emit asset_generated for each newly synthesized asset item
        for item in manifest.items:
            await self.event_bus.emit_telemetry(
                GuiTelemetryEvent(
                    event_type="asset_generated",
                    agent=AgentRole.ARTIST.value,
                    state="complete",
                    data=item.model_dump(mode="json"),
                )
            )

        await self._broadcast_telemetry(
            "agent_state",
            AgentRole.ARTIST.value,
            "complete",
            {"generated_count": len(manifest.items), "task": f"Generated {len(manifest.items)} media assets"}
        )
        await self.event_bus.publish("agent.gemini_artist.assets_ready", manifest)
        return manifest

    # ------------------------------------------------------------------------
    # Phase 3: Claude Engine Code & Scene Generation
    # ------------------------------------------------------------------------

    async def execute_engine_code_phase(
        self,
        gdd: GameDesignDocument,
        manifest: AssetManifest,
    ) -> Dict[str, Any]:
        self._current_phase = OrchestrationPhase.ENGINE_CODE
        await self.blackboard.set_status(ProjectStatus.IN_PROGRESS, active_phase=self._current_phase.value)
        await self._broadcast_telemetry(
            "agent_state",
            AgentRole.ENGINEER.value,
            "active",
            {"action": f"Generating scene trees and scripts for {gdd.target_engine}", "task": f"Compiling {gdd.target_engine} scripts"}
        )

        logger.info("Phase 3: Dispatching code and scene generation to Anthropic Claude")
        if hasattr(self.claude_client, "generate_code_and_scenes"):
            code_result = await self.claude_client.generate_code_and_scenes(gdd=gdd, manifest=manifest)
            scenes: List[SceneAST] = code_result.get("scenes", [])
            scripts: List[ScriptSpec] = code_result.get("scripts", [])
        else:
            scenes, scripts = await self.claude_client.generate_code(gdd=gdd, manifest=manifest)
            code_result = {"scenes": scenes, "scripts": scripts}

        for scene in scenes:
            await self.blackboard.add_scene(scene)

        for script in scripts:
            await self.blackboard.add_script(script)

        await self._broadcast_telemetry(
            "agent_state",
            AgentRole.ENGINEER.value,
            "complete",
            {
                "scenes_count": len(scenes),
                "scripts_count": len(scripts),
                "task": f"Generated {len(scenes)} scenes & {len(scripts)} scripts"
            }
        )
        await self.event_bus.publish("agent.claude_engineer.code_ready", code_result)
        return code_result

    # ------------------------------------------------------------------------
    # Phase 4 & 5: Verification & Review Feedback Loop
    # ------------------------------------------------------------------------

    async def execute_verification_and_review_phase(self) -> VerificationReport:
        self._iteration_count = 0

        while self._iteration_count <= self.max_iterations:
            self._current_phase = OrchestrationPhase.VERIFICATION
            await self.blackboard.set_status(ProjectStatus.IN_PROGRESS, active_phase=self._current_phase.value)
            await self._broadcast_telemetry("agent_state", AgentRole.VALIDATOR.value, "active", {"iteration": self._iteration_count})

            report: VerificationReport = await self._run_verification()
            await self.blackboard.set_verification_report(report)

            if report.passed:
                logger.info("Verification passed with 0 errors!")
                await self._broadcast_telemetry("agent_state", AgentRole.VALIDATOR.value, "complete", {"passed": True})
                return report

            # Verification failed - initiate review feedback loop if within retry limit
            self._iteration_count += 1
            if self._iteration_count > self.max_iterations:
                logger.error(f"Verification failed after {self.max_iterations} iteration cycles.")
                return report

            logger.warning(
                f"Verification reported {len(report.errors)} errors. Entering Review Cycle {self._iteration_count}/{self.max_iterations}"
            )
            self._current_phase = OrchestrationPhase.REVIEW_LOOP
            await self.blackboard.set_status(ProjectStatus.REVIEW_REQUIRED, active_phase=self._current_phase.value)

            # Partition errors between Claude (scripts/scenes) and Gemini (assets)
            code_errors = [e for e in report.errors if e.stage in ("syntax_check", "schema_check", "simulation")]
            asset_errors = [e for e in report.errors if e.stage == "asset_integrity"]

            if code_errors:
                feedback = ReviewFeedback(
                    reviewer=AgentRole.VALIDATOR,
                    target_role=AgentRole.ENGINEER,
                    target_artifact="code_and_scenes",
                    approved=False,
                    issues=[f"[{e.stage}] {e.file_path}:{e.line or '?'}: {e.message}" for e in code_errors],
                    iteration_cycle=self._iteration_count,
                )
                await self.blackboard.add_review_feedback(feedback)
                await self._broadcast_telemetry("review_feedback", AgentRole.ENGINEER.value, "reviewing", feedback.model_dump())

                # Request fix from Claude
                if hasattr(self.claude_client, "repair_code"):
                    repaired = await self.claude_client.repair_code(
                        errors=code_errors,
                        current_scripts=await self.blackboard.get_scripts(),
                        current_scenes=await self.blackboard.get_scenes(),
                    )
                    for s in repaired.get("scripts", []):
                        await self.blackboard.add_script(s)
                    for sc in repaired.get("scenes", []):
                        await self.blackboard.add_scene(sc)

            if asset_errors:
                feedback = ReviewFeedback(
                    reviewer=AgentRole.VALIDATOR,
                    target_role=AgentRole.ARTIST,
                    target_artifact="asset_manifest",
                    approved=False,
                    issues=[f"[{e.stage}] {e.file_path}: {e.message}" for e in asset_errors],
                    iteration_cycle=self._iteration_count,
                )
                await self.blackboard.add_review_feedback(feedback)
                await self._broadcast_telemetry("review_feedback", AgentRole.ARTIST.value, "reviewing", feedback.model_dump())

                # Request missing assets from Gemini
                if hasattr(self.gemini_client, "repair_assets"):
                    repaired_manifest = await self.gemini_client.repair_assets(
                        errors=asset_errors,
                        current_manifest=await self.blackboard.get_asset_manifest(),
                    )
                    await self.blackboard.set_asset_manifest(repaired_manifest)

        return await self.blackboard.get_verification_report() or VerificationReport(
            passed=False,
            errors=[DiagnosticError(stage="system", message="Max iterations exceeded", file_path="project")],
        )

    async def _run_verification(self) -> VerificationReport:
        """Invokes external verification pipeline or default internal sanity checker."""
        if self.verification_pipeline:
            state = await self.blackboard.get_state()
            if hasattr(self.verification_pipeline, "verify_project"):
                res = self.verification_pipeline.verify_project(state)
                if asyncio.iscoroutine(res):
                    return await res
                return res

        # Default fallback verifier (validates presence of GDD, manifest, scenes, scripts)
        errors: List[DiagnosticError] = []
        state = await self.blackboard.get_state()
        if not state.gdd:
            errors.append(DiagnosticError(stage="schema_check", file_path="GDD", message="Missing GameDesignDocument"))
        if not state.asset_manifest or len(state.asset_manifest.items) == 0:
            errors.append(DiagnosticError(stage="asset_integrity", file_path="assets", message="AssetManifest is empty or missing"))
        if not state.scenes:
            errors.append(DiagnosticError(stage="schema_check", file_path="scenes", message="No scenes generated"))
        if not state.scripts:
            errors.append(DiagnosticError(stage="syntax_check", file_path="scripts", message="No engine scripts generated"))

        return VerificationReport(
            passed=len(errors) == 0,
            stages_executed=["schema_check", "asset_integrity", "syntax_check"],
            errors=errors,
            warnings=[],
            execution_time_ms=5.0,
        )

    # ------------------------------------------------------------------------
    # Telemetry Helper
    # ------------------------------------------------------------------------

    async def _broadcast_telemetry(
        self,
        event_type: str,
        agent: Optional[str],
        state: Optional[str],
        data: Dict[str, Any],
    ) -> None:
        telem = GuiTelemetryEvent(
            event_type=event_type,
            agent=agent,
            state=state,
            data=data,
            timestamp=time.time(),
        )
        await self.event_bus.emit_telemetry(telem)
