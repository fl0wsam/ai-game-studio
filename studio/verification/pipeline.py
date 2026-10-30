"""
studio/verification/pipeline.py - Master Automated Verification & Simulation Pipeline.
Coordinates all 4 verification stages:
1. Syntax Validation (MultiEngineSyntaxValidator)
2. TSCN Schema & Reference Verification (TSCNValidator)
3. Asset Integrity & Header Check (AssetVerifier)
4. Dual-Mode Engine Simulation (DualModeEngineSimulator)
Conforms to PROJECT.md contracts, supports fail_fast and complete modes,
and integrates with Blackboard and EventBus.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from studio.core.blackboard import Blackboard
from studio.core.event_bus import EventBus
from studio.core.models import (
    DiagnosticError,
    GuiTelemetryEvent,
    ProjectStatus,
    StudioProjectState,
    VerificationReport,
)
from studio.engine.adapters.godot_adapter import GodotProjectConfig, generate_project_godot, serialize_tscn
from studio.verification.asset_verifier import AssetVerifier
from studio.verification.engine_simulator import DualModeEngineSimulator, SimulationResult
from studio.verification.syntax_validators import MultiEngineSyntaxValidator
from studio.verification.tscn_validator import TSCNValidator


# ============================================================================
# Review Feedback Error Routing Helper
# ============================================================================

def route_errors_for_review(report: VerificationReport) -> Dict[str, List[DiagnosticError]]:
    """
    Partitions diagnosed errors into target roles for the orchestrator review loop:
    - 'engineer' (Claude): syntax_check, schema_check, simulation
    - 'artist' (Gemini): asset_integrity
    """
    code_errors = [e for e in report.errors if e.stage in ("syntax_check", "schema_check", "simulation")]
    asset_errors = [e for e in report.errors if e.stage == "asset_integrity"]
    return {
        "engineer": code_errors,
        "artist": asset_errors,
    }


# ============================================================================
# Verification Pipeline Coordinator
# ============================================================================

class VerificationPipeline:
    """
    End-to-End automated verification and simulation pipeline.
    Validates syntax, scene graph integrity, asset headers, and headless runtime execution.
    """

    ALL_STAGES: List[str] = [
        "syntax_check",
        "schema_check",
        "asset_integrity",
        "simulation",
    ]

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        blackboard: Optional[Blackboard] = None,
    ) -> None:
        self.event_bus = event_bus
        self.blackboard = blackboard
        self.syntax_validator = MultiEngineSyntaxValidator()
        self.tscn_validator = TSCNValidator()

    # ------------------------------------------------------------------------
    # Public Execution Entrypoints
    # ------------------------------------------------------------------------

    def verify_project(
        self,
        target: Union[StudioProjectState, Blackboard, Path, str],
        stages: Optional[List[str]] = None,
        fail_fast: bool = False,
        simulation_frames: int = 60,
    ) -> VerificationReport:
        """
        Synchronous / Universal verification runner.
        Accepts StudioProjectState, Blackboard, or on-disk directory Path/str.
        """
        start_time = time.perf_counter()
        target_stages = stages if stages is not None else list(self.ALL_STAGES)

        # Broadcast start event
        self._publish_event("verification.started", {
            "stages": target_stages,
            "fail_fast": fail_fast,
            "simulation_frames": simulation_frames,
        })

        # Resolve state or directory
        bb_instance: Optional[Blackboard] = None
        state_instance: Optional[StudioProjectState] = None
        project_dir: Optional[Path] = None

        if isinstance(target, Blackboard):
            bb_instance = target
            with target._thread_lock:
                state_instance = target._state.model_copy(deep=True)
        elif isinstance(target, StudioProjectState):
            state_instance = target
        elif isinstance(target, (Path, str)):
            p = Path(target).resolve()
            if not p.exists() or not p.is_dir():
                err = DiagnosticError(
                    stage="system",
                    severity="ERROR",
                    file_path=str(p),
                    line=1,
                    message=f"Project directory does not exist: {p}",
                )
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                report = VerificationReport(
                    passed=False,
                    stages_executed=[],
                    errors=[err],
                    warnings=[],
                    execution_time_ms=elapsed_ms,
                    summary="Verification failed: Project directory does not exist.",
                )
                self._record_and_publish_completion(report, bb_instance)
                return report
            project_dir = p
        else:
            raise TypeError(f"Unsupported target type for verification: {type(target)}")

        # Execute stages
        stages_executed: List[str] = []
        errors: List[DiagnosticError] = []
        warnings: List[DiagnosticError] = []
        sim_frames_run = 0

        # Create temporary working directory if validating in-memory state
        temp_dir_obj = None
        if state_instance and not project_dir:
            temp_dir_obj = tempfile.TemporaryDirectory(prefix="studio_verif_")
            project_dir = Path(temp_dir_obj.name)
            self._materialize_state_to_disk(state_instance, project_dir)

        try:
            assert project_dir is not None

            for stage_name in target_stages:
                stages_executed.append(stage_name)
                self._publish_event("verification.stage_started", {"stage": stage_name})

                stage_errors: List[DiagnosticError] = []
                stage_warnings: List[DiagnosticError] = []

                if stage_name == "syntax_check":
                    if state_instance and state_instance.scripts:
                        for s in state_instance.scripts.values():
                            stage_errors.extend(self.syntax_validator.validate_script(s))
                    else:
                        stage_errors.extend(self.syntax_validator.validate_project_scripts(project_dir))

                elif stage_name == "schema_check":
                    if state_instance and state_instance.scenes:
                        for scn in state_instance.scenes.values():
                            stage_errors.extend(
                                self.tscn_validator.validate_scene_ast(scn, project_root=project_dir)
                            )
                    else:
                        stage_errors.extend(self.tscn_validator.validate_project_scenes(project_dir))

                elif stage_name == "asset_integrity":
                    manifest = state_instance.asset_manifest if state_instance else None
                    gdd = state_instance.gdd if state_instance else None
                    asset_verifier = AssetVerifier(project_dir, manifest=manifest, gdd=gdd)
                    asset_res = asset_verifier.verify_all()
                    stage_errors.extend(asset_res.errors)
                    stage_warnings.extend(asset_res.warnings)

                elif stage_name == "simulation":
                    sim = DualModeEngineSimulator(project_dir)
                    sim_res: SimulationResult = sim.run_simulation(frames=simulation_frames)
                    stage_errors.extend(sim_res.errors)
                    stage_warnings.extend(sim_res.warnings)
                    sim_frames_run = sim_res.frames_run

                # Separate errors vs warnings for stages that returned unified lists
                real_errors = [e for e in stage_errors if e.severity == "ERROR"]
                real_warnings = [e for e in stage_errors if e.severity in ("WARNING", "INFO")] + stage_warnings

                errors.extend(real_errors)
                warnings.extend(real_warnings)

                self._publish_event("verification.stage_completed", {
                    "stage": stage_name,
                    "errors_count": len(real_errors),
                    "warnings_count": len(real_warnings),
                })

                # Fail-Fast early return
                if fail_fast and len(real_errors) > 0:
                    break

        finally:
            if temp_dir_obj:
                try:
                    temp_dir_obj.cleanup()
                except Exception:
                    pass

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        passed = (len(errors) == 0)

        status_str = "PASSED" if passed else "FAILED"
        summary = (
            f"Verification {status_str}: {len(errors)} errors, {len(warnings)} warnings "
            f"across {len(stages_executed)} stages in {elapsed_ms:.2f}ms."
        )

        report = VerificationReport(
            passed=passed,
            stages_executed=stages_executed,
            errors=errors,
            warnings=warnings,
            execution_time_ms=elapsed_ms,
            simulation_frames_run=sim_frames_run,
            summary=summary,
        )

        # Update blackboard & publish completion
        self._record_and_publish_completion(report, bb_instance or self.blackboard)

        return report

    async def verify_project_async(
        self,
        target: Union[StudioProjectState, Blackboard, Path, str],
        stages: Optional[List[str]] = None,
        fail_fast: bool = False,
        simulation_frames: int = 60,
    ) -> VerificationReport:
        """Asynchronous execution entrypoint."""
        report = self.verify_project(
            target=target,
            stages=stages,
            fail_fast=fail_fast,
            simulation_frames=simulation_frames,
        )

        # If blackboard provided, ensure async notification dispatched
        bb = target if isinstance(target, Blackboard) else self.blackboard
        if bb:
            await bb.set_verification_report(report)

        return report

    # ------------------------------------------------------------------------
    # State Materialization to Temp Directory
    # ------------------------------------------------------------------------

    def _materialize_state_to_disk(
        self, state: StudioProjectState, target_dir: Path
    ) -> None:
        """Writes in-memory StudioProjectState to disk structure for simulation."""
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "scenes").mkdir(parents=True, exist_ok=True)
        (target_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (target_dir / "assets").mkdir(parents=True, exist_ok=True)
        (target_dir / "audio").mkdir(parents=True, exist_ok=True)

        # 1. Scripts
        for rel_p, script in state.scripts.items():
            out_file = target_dir / rel_p
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(script.code, encoding="utf-8")

        # 2. Scenes
        main_scene_path: Optional[str] = None
        for rel_p, scene in state.scenes.items():
            out_file = target_dir / rel_p
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(serialize_tscn(scene), encoding="utf-8")
            if not main_scene_path:
                main_scene_path = f"res://{rel_p}"

        # 3. Assets from Manifest
        if state.asset_manifest:
            for item in state.asset_manifest.items:
                r_path = item.rel_path or item.engine_path.replace("res://", "").lstrip("/")
                out_asset = target_dir / r_path
                out_asset.parent.mkdir(parents=True, exist_ok=True)
                if item.file_bytes:
                    out_asset.write_bytes(item.file_bytes)
                else:
                    # Create dummy valid header if bytes not populated in mock
                    ext = out_asset.suffix.lower()
                    if ext == ".png":
                        # 1x1 valid PNG
                        png_1x1 = (
                            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                            b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00"
                            b"\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
                        )
                        out_asset.write_bytes(png_1x1)
                    elif ext == ".wav":
                        # Valid minimal RIFF/WAVE header
                        wav_min = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
                        out_asset.write_bytes(wav_min)
                    else:
                        out_asset.write_bytes(b"DATA")

        # 4. project.godot
        if not (target_dir / "project.godot").exists():
            cfg = GodotProjectConfig(
                project_name=state.project_name or "GeneratedGame",
                main_scene_path=main_scene_path or "res://scenes/main_level.tscn",
            )
            (target_dir / "project.godot").write_text(generate_project_godot(cfg), encoding="utf-8")

    # ------------------------------------------------------------------------
    # Event & Blackboard Helpers
    # ------------------------------------------------------------------------

    def _publish_event(self, topic: str, payload: Any) -> None:
        if self.event_bus:
            try:
                self.event_bus.publish_sync(topic, payload)
            except Exception:
                pass

    def _record_and_publish_completion(
        self, report: VerificationReport, blackboard: Optional[Blackboard]
    ) -> None:
        # Update Blackboard state if available
        if blackboard:
            try:
                with blackboard._thread_lock:
                    blackboard._state.verification_report = report.model_copy(deep=True)
            except Exception:
                pass

        # Publish completion event
        completion_topic = "verification.complete" if report.passed else "verification.error"
        self._publish_event(completion_topic, report.model_dump())

        # Stream GUI telemetry event
        if self.event_bus:
            telem = GuiTelemetryEvent(
                event_type="verification_done",
                agent="validator",
                state="complete" if report.passed else "failed",
                data={
                    "passed": report.passed,
                    "errors": len(report.errors),
                    "warnings": len(report.warnings),
                    "stages_executed": report.stages_executed,
                    "execution_time_ms": report.execution_time_ms,
                },
            )
            try:
                self.event_bus.publish_sync(f"telemetry.{telem.event_type}", telem)
            except Exception:
                pass
