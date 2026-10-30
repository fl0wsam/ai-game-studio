"""
tests/tier3_combinations/test_full_pipeline_combinations.py - Tier 3 Pairwise & Cross-Feature Combination Tests
Tests cross-cutting interactions across Orchestrator, EventBus, Blackboard, Providers,
Engine Hub Adapters, Dynamic Asset Linker, Verification Pipeline, and Studio GUI/CLI.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import io
import json
import os
import pathlib
import tempfile
import time
from typing import Any, Dict, List, Optional

from fastapi.testclient import TestClient
from PIL import Image

from tests.conftest_base import StudioTestCase
from studio.cli import execute_headless_pipeline
from studio.core.blackboard import Blackboard
from studio.core.event_bus import EventBus
from studio.core.models import (
    AgentMessage,
    AgentRole,
    AssetItem,
    AssetManifest,
    AssetRequirement,
    AssetType,
    ConnectionAST,
    DiagnosticError,
    DialogueChoice,
    DialogueNode,
    EngineTarget,
    ExtResourceAST,
    GameDesignDocument,
    GameGenre,
    GuiTelemetryEvent,
    NodeAST,
    ProjectStatus,
    QuestObjective,
    QuestSpec,
    ReviewFeedback,
    SceneAST,
    ScenePlan,
    ScriptSpec,
    StudioProjectState,
    TokenUsageMetrics,
    VerificationReport,
)
from studio.core.orchestrator import OrchestrationPhase, StudioOrchestrator
from studio.core.security import (
    CircuitBreaker,
    CircuitState,
    CredentialManager,
    ExponentialBackoff,
    TokenBucketRateLimiter,
)
from studio.engine.adapters.godot_adapter import (
    GodotAdapter,
    GodotProjectConfig,
    generate_project_godot,
    serialize_tscn,
)
from studio.engine.adapters.pygame_adapter import PygameAdapter
from studio.engine.adapters.unity_adapter import UnityAdapter
from studio.engine.adapters.web_adapter import WebAdapter
from studio.engine.ast_models import (
    ExtResourceRef,
    SubResourceAST,
    SubResourceRef,
    Vector2,
)
from studio.engine.dynamic_asset_linker import DynamicAssetLinker
from studio.engine.hierarchy import CleanHierarchyPackager
from studio.engine.hub import EngineHub, get_engine_hub
from studio.gui.routes import DirectRequest, ExportRequest, VerifyRequest
from studio.gui.server import create_app
from studio.providers.mock_provider import (
    MockAudioGenerator,
    MockCodeGenerator,
    MockGDDGenerator,
    MockProviderClient,
    MockSpriteGenerator,
)
from studio.verification.asset_verifier import AssetVerifier
from studio.verification.engine_simulator import DualModeEngineSimulator
from studio.verification.pipeline import VerificationPipeline
from studio.verification.syntax_validators import (
    CSharpSyntaxValidator,
    GDScriptSyntaxValidator,
    JavaScriptSyntaxValidator,
    MultiEngineSyntaxValidator,
)
from studio.verification.tscn_validator import TSCNValidator


class TestFullPipelineCombinations(StudioTestCase):
    """
    Tier 3 Comprehensive Cross-Feature Combination Test Suite.
    Covers >=25 pairwise and cross-module interaction contracts across the studio.
    """

    def setUp(self) -> None:
        super().setUp()
        self.mock_provider = MockProviderClient()
        self.event_bus = EventBus()
        self.blackboard = Blackboard(
            project_name="ComboTestGame",
            target_engine=EngineTarget.GODOT4.value,
            user_prompt="Comprehensive cross-module integration test game",
        )
        self.verification_pipeline = VerificationPipeline(
            event_bus=self.event_bus,
            blackboard=self.blackboard,
        )
        self.engine_hub = EngineHub(register_defaults=True)

    def tearDown(self) -> None:
        self.run_async(self.event_bus.close())
        super().tearDown()

    # ------------------------------------------------------------------------
    # Combo 1: OpenAI GDD Generation -> Blackboard Storage -> EventBus
    # ------------------------------------------------------------------------
    def test_combo_01_openai_gdd_blackboard_eventbus(self) -> None:
        """OpenAI GDD generation -> Blackboard state storage -> EventBus notification."""
        received_events: List[Any] = []

        async def _subscriber(payload: Any) -> None:
            received_events.append(payload)

        async def _run() -> None:
            self.event_bus.subscribe("agent.openai_director.gdd_ready", _subscriber)
            gdd = await self.mock_provider.generate_gdd(
                prompt="Neon Cyber Runner: high speed platformer",
                archetype=GameGenre.PLATFORMER_2D.value,
            )
            await self.blackboard.set_gdd(gdd)
            await self.event_bus.publish("agent.openai_director.gdd_ready", gdd)

        self.run_async(_run())

        state = self.run_async(self.blackboard.get_state())
        self.assertIsNotNone(state.gdd)
        self.assertEqual(state.gdd.genre, GameGenre.PLATFORMER_2D.value)
        self.assertGreater(len(state.gdd.gameplay_mechanics), 0)
        self.assertEqual(len(received_events), 1)
        self.assertEqual(received_events[0].title, state.gdd.title)
        self.assertGreaterEqual(state.revision, 2)

    # ------------------------------------------------------------------------
    # Combo 2: GDD Requirements -> Gemini Multimodal Assets -> Manifest Binding
    # ------------------------------------------------------------------------
    def test_combo_02_gdd_requirements_gemini_manifest_binding(self) -> None:
        """OpenAI GDD requirements -> Gemini asset synthesis -> Asset manifest binding."""
        async def _run() -> AssetManifest:
            gdd = await self.mock_provider.generate_gdd("Top-down dungeon crawler RPG", archetype="top_down_rpg")
            manifest = await self.mock_provider.generate_assets(gdd)
            await self.blackboard.set_asset_manifest(manifest)
            return manifest

        manifest = self.run_async(_run())
        self.assertGreater(len(manifest.items), 0)

        for item in manifest.items:
            self.assertTrue(item.is_mock)
            self.assertIsNotNone(item.file_bytes)
            self.assertTrue(item.engine_path.startswith("res://"))
            if "sprite" in item.asset_type or "texture" in item.asset_type:
                self.assert_valid_png(item.file_bytes)
            elif "audio" in item.asset_type or "sfx" in item.asset_type or "music" in item.asset_type:
                self.assert_valid_wav(item.file_bytes)

        stored_manifest = self.run_async(self.blackboard.get_asset_manifest())
        self.assertIsNotNone(stored_manifest)
        self.assertEqual(len(stored_manifest.items), len(manifest.items))

    # ------------------------------------------------------------------------
    # Combo 3: GDD Scenes Plan -> Claude Systems Code & Scenes -> AST Models
    # ------------------------------------------------------------------------
    def test_combo_03_gdd_scenes_claude_engine_code_ast(self) -> None:
        """OpenAI GDD scenes_plan -> Claude systems code generation -> SceneAST & ScriptSpec."""
        async def _run() -> Dict[str, Any]:
            gdd = await self.mock_provider.generate_gdd("Neon Platformer Odyssey", archetype="platformer_2d")
            manifest = await self.mock_provider.generate_assets(gdd)
            return await self.mock_provider.generate_code_and_scenes(gdd, manifest)

        code_result = self.run_async(_run())
        scenes: List[SceneAST] = code_result["scenes"]
        scripts: List[ScriptSpec] = code_result["scripts"]

        self.assertGreater(len(scenes), 0)
        self.assertGreater(len(scripts), 0)

        # Verify GDScript syntax validity using MultiEngineSyntaxValidator
        syntax_validator = MultiEngineSyntaxValidator()
        for s in scripts:
            if s.language == "gdscript":
                errors = syntax_validator.validate_script(s)
                self.assertEqual(len(errors), 0, f"Syntax errors in {s.rel_path}: {errors}")

        for sc in scenes:
            self.assertIsNotNone(sc.name)
            self.assertTrue(sc.path.endswith(".tscn"))
            self.assertGreater(len(sc.nodes), 0)

    # ------------------------------------------------------------------------
    # Combo 4: Gemini Assets + Claude SceneAST -> Dynamic Asset Linker -> Godot .tscn
    # ------------------------------------------------------------------------
    def test_combo_04_gemini_assets_claude_ast_linker_godot_serializer(self) -> None:
        """Gemini assets + Claude SceneAST -> Dynamic Asset Linker -> Godot 4 .tscn serialization."""
        async def _run() -> Tuple[SceneAST, GameDesignDocument, AssetManifest]:
            gdd = await self.mock_provider.generate_gdd("Retro Cyber Runner", archetype="platformer_2d")
            manifest = await self.mock_provider.generate_assets(gdd)
            code_res = await self.mock_provider.generate_code_and_scenes(gdd, manifest)
            return code_res["scenes"][0], gdd, manifest

        scene, gdd, manifest = self.run_async(_run())

        from studio.engine.ast_models import (
            ExtResourceAST as EngineExtResourceAST,
            NodeAST as EngineNodeAST,
            SceneAST as EngineSceneAST,
        )
        engine_scene = EngineSceneAST(
            name=scene.name,
            path=scene.path,
            ext_resources=[
                EngineExtResourceAST(id=e.id, type=e.type, path=e.path)
                for e in scene.ext_resources
            ],
            nodes=[
                EngineNodeAST(name=n.name, type=n.type, parent=n.parent, properties=n.properties)
                for n in scene.nodes
            ],
        )

        linker = DynamicAssetLinker(manifest=manifest, gdd=gdd)
        linked_scene = linker.link_scene(engine_scene)

        # Verify textures and audio are bound as ExtResource
        ext_types = {ext.type for ext in linked_scene.ext_resources}
        self.assertTrue("Texture2D" in ext_types or "AudioStream" in ext_types)

        # Serialize to Godot 4 TSCN text
        tscn_text = serialize_tscn(linked_scene)
        self.assertIn("[gd_scene", tscn_text)
        self.assertIn("format=3", tscn_text)
        self.assertIn('[node name="', tscn_text)

        # Generate Godot project configuration
        cfg = GodotProjectConfig(project_name="RetroRunner", main_scene_path=linked_scene.path)
        project_godot = generate_project_godot(cfg)
        self.assertIn("config_version=5", project_godot)
        self.assertIn(linked_scene.path, project_godot)

    # ------------------------------------------------------------------------
    # Combo 5: Claude SceneAST + Engine Hub -> Pygame Export Adapter -> game.py
    # ------------------------------------------------------------------------
    def test_combo_05_claude_ast_engine_hub_pygame_adapter(self) -> None:
        """Claude SceneAST + Engine Hub -> Pygame export adapter -> runnable game.py."""
        gdd = self.get_mock_gdd(title="Pygame Runner", target_engine=EngineTarget.PYGAME.value)
        manifest = self.get_mock_manifest("Pygame Runner")
        scenes = [self.get_mock_scene()]
        scripts = [self.get_mock_script(rel_path="scripts/player.py", language="python")]

        out_dir = self.workspace_dir / "pygame_export"
        manifest_res = self.engine_hub.export(
            target=EngineTarget.PYGAME,
            gdd=gdd,
            manifest=manifest,
            scenes=scenes,
            scripts=scripts,
            output_dir=out_dir,
        )

        entry_py = out_dir / manifest_res.entry_file
        self.assertTrue(entry_py.exists(), f"Entry file {manifest_res.entry_file} was not generated")

        # Verify Python syntax validity
        code = entry_py.read_text(encoding="utf-8")
        parsed = ast.parse(code)
        self.assertIsNotNone(parsed)
        self.assertIn("pygame", code.lower())
        self.assertGreater(len(manifest_res.files_created), 0)

    # ------------------------------------------------------------------------
    # Combo 6: Claude SceneAST + Engine Hub -> HTML5/Canvas Export Adapter
    # ------------------------------------------------------------------------
    def test_combo_06_claude_ast_engine_hub_html5_adapter(self) -> None:
        """Claude SceneAST + Engine Hub -> HTML5/Canvas export adapter -> index.html & game.js."""
        gdd = self.get_mock_gdd(title="Web Canvas Runner", target_engine=EngineTarget.WEB_HTML5.value)
        manifest = self.get_mock_manifest("Web Canvas Runner")
        scenes = [self.get_mock_scene()]
        scripts = [self.get_mock_script(rel_path="scripts/game.js", language="javascript")]

        out_dir = self.workspace_dir / "html5_export"
        self.engine_hub.export(
            target=EngineTarget.WEB_HTML5,
            gdd=gdd,
            manifest=manifest,
            scenes=scenes,
            scripts=scripts,
            output_dir=out_dir,
        )

        index_html = out_dir / "index.html"
        game_js = out_dir / "game.js"
        self.assertTrue(index_html.exists())
        self.assertTrue(game_js.exists())

        html_content = index_html.read_text(encoding="utf-8")
        self.assertIn("<canvas", html_content)
        self.assertIn("game.js", html_content)

        js_validator = JavaScriptSyntaxValidator()
        js_errors = js_validator.validate_file(game_js)
        self.assertEqual(len(js_errors), 0, f"JS errors: {js_errors}")

    # ------------------------------------------------------------------------
    # Combo 7: Claude SceneAST + Engine Hub -> Unity Export Adapter -> C# Scripts
    # ------------------------------------------------------------------------
    def test_combo_07_claude_ast_engine_hub_unity_adapter(self) -> None:
        """Claude SceneAST + Engine Hub -> Unity adapter -> C# scripts + EditorSceneBuilder.cs."""
        gdd = self.get_mock_gdd(title="Unity Arcade", target_engine=EngineTarget.UNITY.value)
        manifest = self.get_mock_manifest("Unity Arcade")
        scenes = [self.get_mock_scene()]
        scripts = [self.get_mock_script(rel_path="scripts/PlayerController.cs", language="csharp")]

        out_dir = self.workspace_dir / "unity_export"
        self.engine_hub.export(
            target=EngineTarget.UNITY,
            gdd=gdd,
            manifest=manifest,
            scenes=scenes,
            scripts=scripts,
            output_dir=out_dir,
        )

        editor_builder = out_dir / "Assets" / "Editor" / "EditorSceneBuilder.cs"
        self.assertTrue(editor_builder.exists(), "EditorSceneBuilder.cs was not generated")

        cs_validator = CSharpSyntaxValidator()
        builder_errors = cs_validator.validate_file(editor_builder)
        self.assertEqual(len(builder_errors), 0, f"C# syntax errors in builder: {builder_errors}")

    # ------------------------------------------------------------------------
    # Combo 8: Exported Project Directory -> VerificationPipeline.verify_project()
    # ------------------------------------------------------------------------
    def test_combo_08_exported_project_verification_pipeline(self) -> None:
        """Exported project directory -> VerificationPipeline (syntax + schema + assets + simulation)."""
        async def _setup_and_verify() -> VerificationReport:
            gdd = await self.mock_provider.generate_gdd("Neon Cyber Platformer", archetype="platformer_2d")
            manifest = await self.mock_provider.generate_assets(gdd)
            code_res = await self.mock_provider.generate_code_and_scenes(gdd, manifest)

            out_dir = self.workspace_dir / "godot_export_combo8"
            self.engine_hub.export(
                target=EngineTarget.GODOT4,
                gdd=gdd,
                manifest=manifest,
                scenes=code_res["scenes"],
                scripts=code_res["scripts"],
                output_dir=out_dir,
            )
            # Ensure standard Godot icon.svg is present
            icon_file = out_dir / "assets" / "sprites" / "icon.svg"
            icon_file.parent.mkdir(parents=True, exist_ok=True)
            icon_file.write_text("<svg xmlns='http://www.w3.org/2000/svg' width='32' height='32'></svg>", encoding="utf-8")

            # Run full verification pipeline on exported directory
            return self.verification_pipeline.verify_project(
                target=out_dir,
                stages=VerificationPipeline.ALL_STAGES,
                simulation_frames=60,
            )

        report = self.run_async(_setup_and_verify())
        self.assertTrue(report.passed, f"Verification failed with errors: {report.errors}")
        self.assertEqual(len(report.errors), 0)
        self.assertGreaterEqual(report.simulation_frames_run, 60)
        self.assertIn("syntax_check", report.stages_executed)
        self.assertIn("schema_check", report.stages_executed)
        self.assertIn("asset_integrity", report.stages_executed)
        self.assertIn("simulation", report.stages_executed)

    # ------------------------------------------------------------------------
    # Combo 9: Studio Generation -> FastAPI /api/direct -> WebSocket Telemetry
    # ------------------------------------------------------------------------
    def test_combo_09_fastapi_direct_background_task_websocket_telemetry(self) -> None:
        """FastAPI /api/direct -> Background task -> EventBus telemetry -> WebSocket broadcast."""
        orchestrator = StudioOrchestrator(
            blackboard=self.blackboard,
            event_bus=self.event_bus,
            openai_client=self.mock_provider,
            gemini_client=self.mock_provider,
            claude_client=self.mock_provider,
            verification_pipeline=self.verification_pipeline,
        )
        app = create_app(
            blackboard=self.blackboard,
            event_bus=self.event_bus,
            orchestrator=orchestrator,
        )
        client = TestClient(app)

        received_events: List[Dict[str, Any]] = []

        with client.websocket_connect("/api/ws/telemetry") as ws:
            resp = client.post(
                "/api/direct",
                json={
                    "prompt": "Cyber Infiltration Action",
                    "target_engine": "godot4",
                    "project_name": "ComboTeleGame",
                    "mock": True,
                },
            )
            self.assertEqual(resp.status_code, 202)

            # Receive initial streaming event
            start_event = ws.receive_json()
            received_events.append(start_event)

            # Wait for background task to complete and stream events
            for _ in range(8):
                try:
                    ev = ws.receive_json()
                    received_events.append(ev)
                    if ev.get("event_type") in ("pipeline_completed", "pipeline_failed"):
                        break
                except Exception:
                    break

        self.assertGreater(len(received_events), 0)
        event_types = [e.get("event_type") for e in received_events]
        self.assertTrue(any(et in ("pipeline_started", "agent_state") for et in event_types))

    # ------------------------------------------------------------------------
    # Combo 10: Provider Rate Limiting + Circuit Breaker -> MockProvider Fallback
    # ------------------------------------------------------------------------
    def test_combo_10_rate_limiting_circuit_breaker_mock_fallback(self) -> None:
        """Token bucket rate limiter + circuit breaker failure -> graceful fallback to MockProvider."""
        limiter = TokenBucketRateLimiter(requests_per_minute=1, tokens_per_minute=100)
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)

        async def _consume():
            acquired_1 = await limiter.acquire(tokens=1)
            acquired_2 = await limiter.acquire(tokens=150, timeout=0.01)
            return acquired_1, acquired_2

        acq1, acq2 = self.run_async(_consume())
        self.assertTrue(acq1)
        self.assertFalse(acq2)

        # Trip circuit breaker
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.OPEN)
        self.assertFalse(breaker.can_execute())

        # Graceful fallback: when breaker is open, use MockProvider
        active_provider = self.mock_provider if not breaker.can_execute() else None
        self.assertIsNotNone(active_provider)

        async def _run() -> GameDesignDocument:
            return await active_provider.generate_gdd("Rate limited fallback game")

        gdd = self.run_async(_run())
        self.assertIsNotNone(gdd)
        self.assertIn("fallback", gdd.title.lower() + gdd.high_concept.lower())

    # ------------------------------------------------------------------------
    # Combo 11: Exported Game Directory -> REST /api/project/export -> Clean Hierarchy
    # ------------------------------------------------------------------------
    def test_combo_11_rest_api_export_clean_hierarchy(self) -> None:
        """Exported game state -> REST /api/project/export -> CleanHierarchyPackager & validation."""
        async def _populate_blackboard() -> None:
            gdd = self.get_mock_gdd("REST Export Game")
            manifest = self.get_mock_manifest("REST Export Game")
            scene = self.get_mock_scene()
            script = self.get_mock_script()

            await self.blackboard.set_gdd(gdd)
            await self.blackboard.set_asset_manifest(manifest)
            await self.blackboard.add_scene(scene)
            await self.blackboard.add_script(script)

        self.run_async(_populate_blackboard())

        app = create_app(
            blackboard=self.blackboard,
            event_bus=self.event_bus,
            engine_hub=self.engine_hub,
        )
        client = TestClient(app)

        export_target = self.workspace_dir / "rest_export_out"
        resp = client.post(
            "/api/project/export",
            json={
                "output_dir": str(export_target),
                "target_engine": "godot4",
                "overwrite": True,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertGreater(data["total_files"], 0)
        self.assertTrue((export_target / "project_manifest.json").exists())
        self.assertTrue((export_target / "docs" / "GDD.md").exists())

    # ------------------------------------------------------------------------
    # Combo 12: Directing Panel Parameter Changes -> Studio Reconfiguration
    # ------------------------------------------------------------------------
    def test_combo_12_directing_panel_parameter_reconfiguration(self) -> None:
        """Directing panel parameter changes (genre, engine) -> Orchestrator reconfiguration."""
        async def _run_custom(engine: str, genre: str, prompt: str) -> StudioProjectState:
            bb = Blackboard(project_name="ReconfigTest", target_engine=engine, user_prompt=prompt)
            orch = StudioOrchestrator(
                blackboard=bb,
                event_bus=self.event_bus,
                openai_client=self.mock_provider,
                gemini_client=self.mock_provider,
                claude_client=self.mock_provider,
                verification_pipeline=self.verification_pipeline,
            )
            return await orch.run_pipeline(prompt=prompt, target_engine=engine, project_name="ReconfigTest")

        # Test 1: Reconfigure with Godot 4 Platformer
        state_godot = self.run_async(_run_custom("godot4", "platformer_2d", "Cyber Ninja Infiltration"))
        self.assertEqual(state_godot.status, ProjectStatus.COMPLETED)
        self.assertEqual(state_godot.gdd.genre, "platformer_2d")

        # Test 2: Reconfigure export targeting Pygame
        out_pygame = self.workspace_dir / "reconfig_pygame"
        manifest_py = self.engine_hub.export(
            target=EngineTarget.PYGAME,
            gdd=state_godot.gdd,
            manifest=state_godot.asset_manifest,
            scenes=state_godot.scenes,
            scripts=state_godot.scripts,
            output_dir=out_pygame,
        )
        self.assertTrue((out_pygame / "main.py").exists())
        self.assertEqual(manifest_py.entry_file, "main.py")

    # ------------------------------------------------------------------------
    # Combo 13: Dynamic Asset Linker Collision Bindings Synthesis
    # ------------------------------------------------------------------------
    def test_combo_13_dynamic_asset_linker_collision_synthesis(self) -> None:
        """SceneAST CharacterBody2D actor without collision -> DynamicAssetLinker synthesizes CollisionShape2D."""
        from studio.engine.ast_models import (
            NodeAST as EngineNodeAST,
            SceneAST as EngineSceneAST,
        )
        raw_scene = EngineSceneAST(
            name="MainLevel",
            path="scenes/main_level.tscn",
            nodes=[
                EngineNodeAST(name="MainLevel", type="Node2D", parent=None),
                EngineNodeAST(name="Player", type="CharacterBody2D", parent="."),
            ],
        )
        linker = DynamicAssetLinker()
        linked = linker.link_scene(raw_scene)

        # Verify a CollisionShape2D was synthesized
        node_types = [n.type for n in linked.nodes]
        self.assertIn("CollisionShape2D", node_types)
        collision_node = next(n for n in linked.nodes if n.type == "CollisionShape2D")
        self.assertEqual(collision_node.parent, "Player")
        self.assertIn("shape", collision_node.properties)
        self.assertIsInstance(collision_node.properties["shape"], SubResourceRef)

    # ------------------------------------------------------------------------
    # Combo 14: Multi-Format Audio Playback Checksum & Header Verification
    # ------------------------------------------------------------------------
    def test_combo_14_multi_format_audio_checksum_and_magic_bytes(self) -> None:
        """AssetItem stores SHA-256 checksums -> AssetVerifier validates WAV magic bytes and checksum."""
        audio_bytes = self.create_test_audio(duration=0.1, freq=440.0)
        audio_path = self.workspace_dir / "audio" / "sfx" / "test_jump.wav"
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(audio_bytes)

        item = AssetItem(
            asset_id="sfx_jump",
            asset_type=AssetType.AUDIO_SFX,
            rel_path="audio/sfx/test_jump.wav",
            engine_path="res://audio/sfx/test_jump.wav",
            file_bytes=audio_bytes,
        )
        self.assertIsNotNone(item.checksum)

        verifier = AssetVerifier(project_dir=self.workspace_dir, manifest=AssetManifest(project_name="AudioTest", items=[item]))
        res = verifier.verify_all()
        self.assertEqual(len(res.errors), 0, f"Asset verifier errors: {res.errors}")
        self.assert_valid_wav(audio_path)

    # ------------------------------------------------------------------------
    # Combo 15: Log Inspector Streaming During Active Build
    # ------------------------------------------------------------------------
    def test_combo_15_log_inspector_concurrent_streaming(self) -> None:
        """Blackboard appends logs concurrently -> GET /api/project/logs returns sliced stream."""
        async def _add_logs() -> None:
            for i in range(10):
                await self.blackboard.add_log("orchestrator", "INFO", f"Build step {i} executed")

        self.run_async(_add_logs())

        app = create_app(blackboard=self.blackboard, event_bus=self.event_bus)
        client = TestClient(app)

        resp = client.get("/api/project/logs?limit=4")
        self.assertEqual(resp.status_code, 200)
        logs = resp.json()
        self.assertEqual(len(logs), 4)
        self.assertIn("step 9", logs[-1]["message"])

    # ------------------------------------------------------------------------
    # Combo 16: Review Feedback Loop: Script Syntax Repair
    # ------------------------------------------------------------------------
    def test_combo_16_review_loop_script_syntax_repair(self) -> None:
        """Syntax error in script -> Orchestrator routes review feedback -> Claude repairs code."""
        async def _run() -> VerificationReport:
            gdd = await self.mock_provider.generate_gdd("SyntaxRepairTest")
            manifest = await self.mock_provider.generate_assets(gdd)
            manifest.items.append(
                AssetItem(
                    asset_id="icon",
                    asset_type=AssetType.SPRITE,
                    rel_path="assets/sprites/icon.svg",
                    engine_path="res://assets/sprites/icon.svg",
                    file_bytes=b"<svg xmlns='http://www.w3.org/2000/svg' width='32' height='32'></svg>",
                )
            )
            scenes, good_scripts = await self.mock_provider.generate_code(gdd, manifest=manifest)
            for sc in scenes:
                await self.blackboard.add_scene(sc)

            class AutoRepairClaude(MockProviderClient):
                async def repair_code(
                    self,
                    errors: List[DiagnosticError],
                    current_scripts: List[ScriptSpec],
                    current_scenes: List[SceneAST],
                ) -> Dict[str, Any]:
                    return {"scripts": good_scripts, "scenes": current_scenes}

            bad_script = ScriptSpec(
                rel_path="scripts/player.gd",
                language="gdscript",
                code="extends CharacterBody2D\n\nfunc _physics_process(delta broken syntax",
            )

            await self.blackboard.set_gdd(gdd)
            await self.blackboard.set_asset_manifest(manifest)
            await self.blackboard.add_script(bad_script)

            orchestrator = StudioOrchestrator(
                blackboard=self.blackboard,
                event_bus=self.event_bus,
                openai_client=self.mock_provider,
                gemini_client=self.mock_provider,
                claude_client=AutoRepairClaude(),
                verification_pipeline=self.verification_pipeline,
                max_iterations=2,
            )
            return await orchestrator.execute_verification_and_review_phase()

        report = self.run_async(_run())
        self.assertTrue(report.passed)
        state = self.run_async(self.blackboard.get_state())
        self.assertGreater(len(state.review_history), 0)
        self.assertEqual(state.review_history[0].target_role, AgentRole.ENGINEER)

    # ------------------------------------------------------------------------
    # Combo 17: Review Feedback Loop: Missing Asset Repair
    # ------------------------------------------------------------------------
    def test_combo_17_review_loop_missing_asset_repair(self) -> None:
        """Missing asset diagnosed -> Review feedback routed to Gemini -> Repaired manifest passes."""
        async def _run() -> VerificationReport:
            gdd = await self.mock_provider.generate_gdd("AssetRepairTest")
            full_manifest = await self.mock_provider.generate_assets(gdd)
            full_manifest.items.append(
                AssetItem(
                    asset_id="icon",
                    asset_type=AssetType.SPRITE,
                    rel_path="assets/sprites/icon.svg",
                    engine_path="res://assets/sprites/icon.svg",
                    file_bytes=b"<svg xmlns='http://www.w3.org/2000/svg' width='32' height='32'></svg>",
                )
            )

            scenes, scripts = await self.mock_provider.generate_code(gdd, manifest=full_manifest)
            for sc in scenes:
                await self.blackboard.add_scene(sc)
            for sp in scripts:
                await self.blackboard.add_script(sp)

            # Incomplete manifest missing the icon item
            incomplete_items = [it for it in full_manifest.items if it.asset_id != "icon"]
            incomplete_manifest = AssetManifest(
                project_name="AssetRepairTest",
                items=incomplete_items,
            )

            class AutoRepairGemini(MockProviderClient):
                async def repair_assets(
                    self,
                    errors: List[DiagnosticError],
                    current_manifest: Optional[AssetManifest],
                ) -> AssetManifest:
                    return full_manifest

            await self.blackboard.set_gdd(gdd)
            await self.blackboard.set_asset_manifest(incomplete_manifest)

            orchestrator = StudioOrchestrator(
                blackboard=self.blackboard,
                event_bus=self.event_bus,
                openai_client=self.mock_provider,
                gemini_client=AutoRepairGemini(),
                claude_client=self.mock_provider,
                verification_pipeline=self.verification_pipeline,
                max_iterations=2,
            )
            return await orchestrator.execute_verification_and_review_phase()

        report = self.run_async(_run())
        self.assertTrue(report.passed)
        state = self.run_async(self.blackboard.get_state())
        self.assertGreater(len(state.review_history), 0)
        self.assertEqual(state.review_history[0].target_role, AgentRole.ARTIST)

    # ------------------------------------------------------------------------
    # Combo 18: Engine Hub Multi-Export (Godot 4 + Pygame Simultaneous Export)
    # ------------------------------------------------------------------------
    def test_combo_18_engine_hub_multi_export_godot_and_pygame(self) -> None:
        """EngineHub.export_multi across Godot 4 and Pygame simultaneously."""
        gdd = self.get_mock_gdd("MultiExportGame")
        manifest = self.get_mock_manifest("MultiExportGame")
        scenes = [self.get_mock_scene()]
        scripts = [self.get_mock_script()]

        base_out = self.workspace_dir / "multi_export_out"
        results = self.engine_hub.export_multi(
            targets=[EngineTarget.GODOT4, EngineTarget.PYGAME],
            gdd=gdd,
            manifest=manifest,
            scenes=scenes,
            scripts=scripts,
            base_output_dir=base_out,
        )

        self.assertIn("godot4", results)
        self.assertIn("pygame", results)
        self.assertTrue((base_out / "godot4" / "project.godot").exists())
        self.assertTrue((base_out / "pygame" / "main.py").exists())

    # ------------------------------------------------------------------------
    # Combo 19: Web HTML5 Export with Embedded Canvas & Asset Preloading
    # ------------------------------------------------------------------------
    def test_combo_19_web_adapter_canvas_resolution_and_manifest(self) -> None:
        """WebAdapter exports with custom resolution and asset preloading bindings."""
        gdd = self.get_mock_gdd("WebRunner", target_engine="web_html5")
        manifest = self.get_mock_manifest("WebRunner")
        scenes = [self.get_mock_scene()]
        scripts = [self.get_mock_script(rel_path="scripts/main.js", language="javascript")]

        out_dir = self.workspace_dir / "web_res_out"
        adapter = WebAdapter()
        manifest_res = adapter.export_project(
            gdd=gdd,
            manifest=manifest,
            scenes=scenes,
            scripts=scripts,
            output_dir=out_dir,
            canvas_width=1280,
            canvas_height=720,
        )

        index_html = (out_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn('width="1280"', index_html)
        self.assertIn('height="720"', index_html)
        self.assertGreater(len(manifest_res.files_created), 0)

    # ------------------------------------------------------------------------
    # Combo 20: Unity Adapter C# Scene Builder AST Mapping
    # ------------------------------------------------------------------------
    def test_combo_20_unity_adapter_csharp_scene_builder_ast_mapping(self) -> None:
        """UnityAdapter maps SceneAST nodes to Unity C# GameObject builders."""
        gdd = self.get_mock_gdd("UnityASTGame", target_engine="unity")
        manifest = self.get_mock_manifest("UnityASTGame")
        scene = SceneAST(
            name="MainScene",
            path="scenes/main.tscn",
            nodes=[
                NodeAST(name="MainScene", type="Node2D", parent=None),
                NodeAST(name="Camera2D", type="Camera2D", parent="."),
                NodeAST(name="Player", type="CharacterBody2D", parent="."),
                NodeAST(name="Sprite2D", type="Sprite2D", parent="Player"),
            ],
        )

        out_dir = self.workspace_dir / "unity_ast_out"
        adapter = UnityAdapter()
        adapter.export_project(gdd=gdd, manifest=manifest, scenes=[scene], scripts=[], output_dir=out_dir)

        builder_cs = (out_dir / "Assets" / "Editor" / "EditorSceneBuilder.cs").read_text(encoding="utf-8")
        self.assertIn("GameObject", builder_cs)
        self.assertIn("Player", builder_cs)
        self.assertIn("Camera", builder_cs)

    # ------------------------------------------------------------------------
    # Combo 21: GDD Dialogue & Quests Docs Packaging
    # ------------------------------------------------------------------------
    def test_combo_21_gdd_dialogue_quest_docs_packaging(self) -> None:
        """CleanHierarchyPackager packages GDD narrative, dialogue trees, and quest specs into docs/."""
        gdd = self.get_mock_gdd("NarrativeRPG")
        packager = CleanHierarchyPackager(target_engine="godot4")
        out_dir = self.workspace_dir / "narrative_docs_out"
        packager.create_hierarchy(out_dir)
        packager.package_docs(out_dir, gdd)

        gdd_md = out_dir / "docs" / "GDD.md"
        lore_md = out_dir / "docs" / "LORE.md"
        controls_md = out_dir / "docs" / "CONTROLS.md"

        self.assertTrue(gdd_md.exists())
        self.assertTrue(lore_md.exists())
        self.assertTrue(controls_md.exists())

        lore_content = lore_md.read_text(encoding="utf-8")
        self.assertIn("Dialogue & Characters", lore_content)
        self.assertIn("Handler Zero", lore_content)
        self.assertIn("Quests & Objectives", lore_content)

        gdd_content = gdd_md.read_text(encoding="utf-8")
        self.assertIn("High Concept", gdd_content)
        self.assertIn("Core Mechanics", gdd_content)

    # ------------------------------------------------------------------------
    # Combo 22: Blackboard Concurrency & Thread-Safety
    # ------------------------------------------------------------------------
    def test_combo_22_blackboard_concurrency_thread_safety(self) -> None:
        """Concurrent coroutines modifying Blackboard state without race conditions."""
        bb = Blackboard(project_name="ConcurrencyTest")

        async def _worker(idx: int) -> None:
            await bb.add_log("worker", "DEBUG", f"Log from worker {idx}")
            await bb.record_token_metrics("claude", prompt_tokens=10, completion_tokens=5, cost_usd=0.0001)

        async def _run_all() -> None:
            tasks = [_worker(i) for i in range(25)]
            await asyncio.gather(*tasks)

        self.run_async(_run_all())

        state = self.run_async(bb.get_state())
        self.assertEqual(len(state.logs), 25)
        metrics = self.run_async(bb.get_token_metrics())
        self.assertEqual(metrics["claude"].prompt_tokens, 250)
        self.assertEqual(metrics["claude"].completion_tokens, 125)

    # ------------------------------------------------------------------------
    # Combo 23: Directing API Invalid Payload Rejection Preserves Blackboard
    # ------------------------------------------------------------------------
    def test_combo_23_directing_api_invalid_payload_preserves_blackboard(self) -> None:
        """Directing API rejects whitespace-only prompt without mutating state."""
        app = create_app(blackboard=self.blackboard, event_bus=self.event_bus)
        client = TestClient(app)

        initial_state = self.run_async(self.blackboard.get_state())
        resp = client.post("/api/direct", json={"prompt": "   ", "target_engine": "godot4"})
        self.assertEqual(resp.status_code, 400)

        current_state = self.run_async(self.blackboard.get_state())
        self.assertEqual(initial_state.revision, current_state.revision)
        self.assertEqual(current_state.status, ProjectStatus.PENDING)

    # ------------------------------------------------------------------------
    # Combo 24: Asset Verifier Corrupt Magic Bytes Detection
    # ------------------------------------------------------------------------
    def test_combo_24_asset_verifier_corrupt_magic_bytes_detection(self) -> None:
        """Asset with corrupt magic header is flagged with DiagnosticError."""
        corrupt_path = self.workspace_dir / "assets" / "sprites" / "corrupt.png"
        corrupt_path.parent.mkdir(parents=True, exist_ok=True)
        corrupt_path.write_bytes(b"CORRUPT_NOT_A_VALID_PNG_HEADER")

        item = AssetItem(
            asset_id="sprite_bad",
            asset_type=AssetType.SPRITE,
            rel_path="assets/sprites/corrupt.png",
            engine_path="res://assets/sprites/corrupt.png",
            file_bytes=b"CORRUPT_NOT_A_VALID_PNG_HEADER",
        )
        verifier = AssetVerifier(
            project_dir=self.workspace_dir,
            manifest=AssetManifest(project_name="CorruptTest", items=[item]),
        )
        res = verifier.verify_all()
        self.assertFalse(res.passed)
        self.assertGreater(len(res.errors), 0)
        self.assertEqual(res.errors[0].stage, "asset_integrity")

    # ------------------------------------------------------------------------
    # Combo 25: CLI Headless Pipeline E2E Execution
    # ------------------------------------------------------------------------
    def test_combo_25_cli_headless_build_full_pipeline(self) -> None:
        """Single-command CLI headless pipeline executes and returns exit code 0."""
        out_dir = self.workspace_dir / "cli_headless_out"
        args = argparse.Namespace(
            headless=True,
            gui=False,
            window=False,
            browser=False,
            no_browser=True,
            prompt="Endless Neon Cyber Run",
            engine="godot4",
            genre="platformer_2d",
            output_dir=str(out_dir),
            project_name="CLIHeadlessTest",
            mock=True,
            iterations=3,
            verify=True,
            host="127.0.0.1",
            port=8080,
            log_level="INFO",
        )

        exit_code = self.run_async(execute_headless_pipeline(args))
        self.assertEqual(exit_code, 0)
        self.assertTrue((out_dir / "project.godot").exists())
        self.assertTrue((out_dir / "project_manifest.json").exists())

    # ------------------------------------------------------------------------
    # Combo 26: Blackboard Token Usage Metrics Aggregation
    # ------------------------------------------------------------------------
    def test_combo_26_blackboard_token_usage_metrics_aggregation(self) -> None:
        """Token metrics recorded across OpenAI, Gemini, Claude accumulate accurately."""
        async def _run() -> Dict[str, TokenUsageMetrics]:
            await self.blackboard.record_token_metrics(
                "openai", prompt_tokens=1500, completion_tokens=800, cost_usd=0.012
            )
            await self.blackboard.record_token_metrics(
                "gemini", prompt_tokens=400, completion_tokens=200, cost_usd=0.003
            )
            await self.blackboard.record_token_metrics(
                "claude", prompt_tokens=2000, completion_tokens=1200, cost_usd=0.025
            )
            return await self.blackboard.get_token_metrics()

        metrics = self.run_async(_run())
        total_prompt = sum(m.prompt_tokens for m in metrics.values())
        total_completion = sum(m.completion_tokens for m in metrics.values())
        total_cost = sum(m.estimated_cost_usd for m in metrics.values())

        self.assertEqual(total_prompt, 3900)
        self.assertEqual(total_completion, 2200)
        self.assertAlmostEqual(total_cost, 0.040, places=4)

    # ------------------------------------------------------------------------
    # Combo 27: Dynamic Asset Linker Handles Unlinked Surplus Assets Gracefully
    # ------------------------------------------------------------------------
    def test_combo_27_dynamic_asset_linker_unlinked_asset_handling(self) -> None:
        """AssetManifest containing surplus assets not in ScenePlan links cleanly without error."""
        manifest = self.get_mock_manifest("SurplusTest")
        # Add extra surplus asset
        extra_item = AssetItem(
            asset_id="extra_secret_gem",
            asset_type=AssetType.SPRITE,
            rel_path="assets/sprites/gem.png",
            engine_path="res://assets/sprites/gem.png",
            file_bytes=self.create_test_image(),
        )
        manifest.items.append(extra_item)

        from studio.engine.ast_models import (
            ExtResourceAST as EngineExtResourceAST,
            NodeAST as EngineNodeAST,
            SceneAST as EngineSceneAST,
        )
        scene = EngineSceneAST(
            name="SurplusLevel",
            path="scenes/surplus.tscn",
            ext_resources=[
                EngineExtResourceAST(id="1_tex", type="Texture2D", path="res://assets/sprites/player.png"),
                EngineExtResourceAST(id="2_sfx", type="AudioStream", path="res://audio/sfx/jump.wav"),
            ],
            nodes=[
                EngineNodeAST(name="SurplusLevel", type="Node2D", parent=None),
                EngineNodeAST(name="Player", type="CharacterBody2D", parent="."),
            ],
        )
        linker = DynamicAssetLinker(manifest=manifest, gdd=self.get_mock_gdd())
        linked = linker.link_scene(scene)

        # Validate bindings does not crash and scene remains valid
        errors = linker.validate_bindings(linked)
        self.assertEqual(len(errors), 0, f"Unexpected errors with surplus assets: {errors}")

    # ------------------------------------------------------------------------
    # Combo 28: EventBus Wildcard & Topic Routing Fidelity
    # ------------------------------------------------------------------------
    def test_combo_28_eventbus_wildcard_topic_routing(self) -> None:
        """EventBus wildcards (agent.*, telemetry.#) correctly dispatch to multiple handlers."""
        received_wildcard: List[str] = []

        async def _wildcard_handler(payload: Any) -> None:
            received_wildcard.append(payload.get("source"))

        async def _run() -> None:
            self.event_bus.subscribe("agent.*", _wildcard_handler)
            await self.event_bus.publish("agent.openai_director", {"source": "openai"})
            await self.event_bus.publish("agent.gemini_artist", {"source": "gemini"})
            await self.event_bus.publish("agent.claude_engineer", {"source": "claude"})
            await self.event_bus.publish("other.unmatched", {"source": "unmatched"})

        self.run_async(_run())
        self.assertEqual(received_wildcard, ["openai", "gemini", "claude"])
