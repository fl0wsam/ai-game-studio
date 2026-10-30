"""
tests/tier4_applications/test_real_world_games.py - Tier 4 Real-World Application Scenarios.

Covers full end-to-end multi-agent autonomous game studio workflows:
  - S1:  Neon Cyber Runner (2D Endless Runner / Platformer, Godot 4)
  - S2:  Dungeon Crawler RPG (Top-Down RPG, Godot 4, dialogue & quests)
  - S3:  Retro Cosmic Defender (Space Shooter, 60-frame simulator execution)
  - S4:  Ancient Glyph Puzzle (Grid Match Puzzle, rune sprites & chime audio)
  - S5:  Unity Project Export (2D Arcade in Unity, C# MonoBehaviour validation)
  - S6:  Mock Offline Studio Generation (100% deterministic SHA-256 reproducibility)
  - S7:  Live GUI Directed Workflow (FastAPI REST, WebSocket telemetry, media gallery)
  - S8:  Multi-Room Metroidvania (Interconnected scenes, room transitions)
  - S9:  Branching Dialogue Quest (Narrative RPG, branching tree, GDD & LORE docs)
  - S10: Asset-Heavy Dynamic Loader (>15 distinct assets dynamically linked)
  - S11: Resilient Rate-Limit Recovery (Concurrency stress, 429 retry, mock fallback)
  - S12: Cross-Engine Multi-Export Pipeline (Simultaneous Godot 4, Pygame, Web HTML5)
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import os
import pathlib
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi.testclient import TestClient
from PIL import Image

from studio.core.blackboard import Blackboard
from studio.core.event_bus import EventBus
from studio.core.models import (
    AgentRole,
    AssetItem,
    AssetManifest,
    AssetRequirement,
    AssetType,
    DialogueChoice,
    DialogueNode,
    DiagnosticError,
    EngineTarget,
    GameDesignDocument,
    GameGenre,
    ProjectStatus,
    QuestObjective,
    QuestSpec,
    ScenePlan,
    ScriptSpec,
    VerificationReport,
)
from studio.core.orchestrator import OrchestrationPhase, StudioOrchestrator
from studio.core.security import (
    CircuitBreaker,
    CircuitState,
    ExponentialBackoff,
    TokenBucketRateLimiter,
)
from studio.engine.adapters.godot_adapter import (
    GodotAdapter,
    GodotProjectConfig,
    generate_project_godot,
    parse_tscn,
    serialize_tscn,
)
from studio.engine.adapters.pygame_adapter import PygameAdapter
from studio.engine.adapters.unity_adapter import UnityAdapter
from studio.engine.adapters.web_adapter import WebAdapter
from studio.engine.ast_models import ExtResourceAST, NodeAST, SceneAST
from studio.engine.dynamic_asset_linker import DynamicAssetLinker
from studio.engine.hierarchy import CleanHierarchyPackager
from studio.engine.hub import EngineHub, get_engine_hub
from studio.gui.server import create_app
from studio.providers.mock_provider import (
    MockAudioGenerator,
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
    MultiEngineSyntaxValidator,
)
from studio.verification.tscn_validator import TSCNValidator
from tests.conftest_base import StudioTestCase


class TestRealWorldGames(StudioTestCase):
    """
    Tier 4 End-to-End Real-World Application Scenarios Suite.
    Validates autonomous multi-agent production runs across diverse game archetypes,
    export targets, offline determinism, dynamic asset linking, and verification.
    """

    def setUp(self) -> None:
        super().setUp()
        self.mock_provider = MockProviderClient()
        self.event_bus = EventBus()
        self.engine_hub = EngineHub(register_defaults=True)

    def tearDown(self) -> None:
        self.run_async(self.event_bus.close())
        super().tearDown()

    def _create_icon_asset(self) -> AssetItem:
        """Returns standard icon.svg asset required for Godot 4 projects."""
        return AssetItem(
            asset_id="icon",
            asset_type=AssetType.SPRITE,
            rel_path="assets/sprites/icon.svg",
            engine_path="res://assets/sprites/icon.svg",
            file_bytes=b"<svg xmlns='http://www.w3.org/2000/svg' width='32' height='32'></svg>",
            is_mock=True,
        )

    # ------------------------------------------------------------------------
    # S1: Neon Cyber Runner (2D Endless Runner / Platformer, Godot 4)
    # ------------------------------------------------------------------------
    def test_s01_neon_cyber_runner_godot4(self) -> None:
        """
        Scenario S1: Neon Cyber Runner.
        Full autonomous pipeline producing Godot 4 2D platformer with player,
        parallax background, jump SFX, coin pickup, and 100% verifier pass.
        """
        out_dir = self.workspace_dir / "neon_cyber_runner"
        blackboard = Blackboard(
            project_name="NeonCyberRunner",
            target_engine=EngineTarget.GODOT4.value,
            user_prompt="Neon Cyber Runner: High-speed synthwave platformer navigating rooftop hazards.",
        )
        verif = VerificationPipeline(event_bus=self.event_bus, blackboard=blackboard)
        orchestrator = StudioOrchestrator(
            blackboard=blackboard,
            event_bus=self.event_bus,
            openai_client=self.mock_provider,
            gemini_client=self.mock_provider,
            claude_client=self.mock_provider,
            verification_pipeline=verif,
            max_iterations=3,
        )

        async def _run() -> Any:
            state = await orchestrator.run_pipeline(
                prompt="Neon Cyber Runner: High-speed synthwave platformer navigating rooftop hazards.",
                target_engine=EngineTarget.GODOT4.value,
                project_name="NeonCyberRunner",
            )
            return state

        state = self.run_async(_run())
        self.assertEqual(state.status, ProjectStatus.COMPLETED)
        self.assertIsNotNone(state.gdd)
        self.assertIsNotNone(state.asset_manifest)

        # Export and package to disk
        self.engine_hub.export(
            target=EngineTarget.GODOT4,
            gdd=state.gdd,
            manifest=state.asset_manifest,
            scenes=state.scenes,
            scripts=state.scripts,
            output_dir=out_dir,
        )
        packager = CleanHierarchyPackager(target_engine="godot4")
        packager.create_hierarchy(out_dir)
        packager.package_docs(out_dir, state.gdd)
        packager.package_assets(out_dir, state.asset_manifest)
        packager.package_scripts(out_dir, state.scripts)
        packager.package_scenes(out_dir, state.scenes)
        manifest = packager.generate_manifest(out_dir, project_name="NeonCyberRunner")

        # Verify disk artifacts
        self.assertTrue((out_dir / "project.godot").exists())
        self.assertTrue((out_dir / "project_manifest.json").exists())
        self.assertTrue((out_dir / "docs" / "GDD.md").exists())
        self.assertTrue((out_dir / "assets" / "sprites" / "sprite_player.png").exists())
        self.assertTrue((out_dir / "audio" / "sfx" / "sfx_jump.wav").exists())
        self.assertGreater(manifest.total_files, 5)

        # Asset integrity checks
        self.assert_valid_png(out_dir / "assets" / "sprites" / "sprite_player.png")
        self.assert_valid_wav(out_dir / "audio" / "sfx" / "sfx_jump.wav")

        # Verify final pipeline report
        report = verif.verify_project(state)
        self.assertTrue(report.passed)
        self.assertEqual(len(report.errors), 0)

    # ------------------------------------------------------------------------
    # S2: Dungeon Crawler RPG (Top-Down RPG, Godot 4)
    # ------------------------------------------------------------------------
    def test_s02_dungeon_crawler_rpg(self) -> None:
        """
        Scenario S2: Dungeon Crawler RPG.
        Top-down RPG with dungeon tilemap, player actor, enemy actor, sword SFX,
        and narrative quest lore documented in docs/LORE.md.
        """
        out_dir = self.workspace_dir / "dungeon_crawler_rpg"
        blackboard = Blackboard(
            project_name="DungeonCrawlerRPG",
            target_engine=EngineTarget.GODOT4.value,
            user_prompt="Dungeon Crawler RPG: Dark subterranean exploration with sword combat, goblin enemies, and quest lore.",
        )
        verif = VerificationPipeline(event_bus=self.event_bus, blackboard=blackboard)
        orchestrator = StudioOrchestrator(
            blackboard=blackboard,
            event_bus=self.event_bus,
            openai_client=self.mock_provider,
            gemini_client=self.mock_provider,
            claude_client=self.mock_provider,
            verification_pipeline=verif,
            max_iterations=3,
        )

        async def _run() -> Any:
            return await orchestrator.run_pipeline(
                prompt="Dungeon Crawler RPG: Dark subterranean exploration with sword combat, goblin enemies, and quest lore.",
                target_engine=EngineTarget.GODOT4.value,
                project_name="DungeonCrawlerRPG",
            )

        state = self.run_async(_run())
        self.assertEqual(state.status, ProjectStatus.COMPLETED)
        self.assertEqual(state.gdd.genre, "top_down_rpg")

        # Export to disk
        packager = CleanHierarchyPackager(target_engine="godot4")
        packager.create_hierarchy(out_dir)
        packager.package_docs(out_dir, state.gdd)
        packager.package_assets(out_dir, state.asset_manifest)
        packager.package_scripts(out_dir, state.scripts)
        packager.package_scenes(out_dir, state.scenes)
        packager.generate_manifest(out_dir, project_name="DungeonCrawlerRPG")

        # Verify docs/LORE.md generated and contains dialogue/quest
        lore_file = out_dir / "docs" / "LORE.md"
        self.assertTrue(lore_file.exists())
        lore_content = lore_file.read_text(encoding="utf-8")
        self.assertIn("Narrative Overview", lore_content)

        # Validate GDScript syntax
        syntax_validator = GDScriptSyntaxValidator()
        for script_spec in state.scripts.values():
            errors = syntax_validator.validate(script_spec.code, file_path=script_spec.rel_path)
            self.assertEqual(len(errors), 0, f"Syntax errors in {script_spec.rel_path}: {errors}")

        report = verif.verify_project(state)
        self.assertTrue(report.passed)

    # ------------------------------------------------------------------------
    # S3: Retro Cosmic Defender (Space Shooter, 60-Frame Simulator Execution)
    # ------------------------------------------------------------------------
    def test_s03_retro_cosmic_defender(self) -> None:
        """
        Scenario S3: Retro Cosmic Defender.
        Vertical scrolling space shooter with lasers, hazards, explosion audio,
        and verified 60-frame simulated runtime execution.
        """
        out_dir = self.workspace_dir / "cosmic_defender"
        blackboard = Blackboard(
            project_name="CosmicDefender",
            target_engine=EngineTarget.GODOT4.value,
            user_prompt="Retro Cosmic Defender: Vertical scrolling starship shooter with laser cannons and asteroid hazards.",
        )
        fixed_shooter_code = (
            "extends Area2D\n\n"
            "@export var speed: float = 380.0\n"
            "@onready var shoot_sfx: AudioStreamPlayer2D = $ShootAudio\n\n"
            "func _process(delta: float) -> void:\n"
            "    var motion := Vector2.ZERO\n"
            "    if Input.is_action_pressed(\"ui_left\"):\n"
            "        motion.x -= 1.0\n"
            "    if Input.is_action_pressed(\"ui_right\"):\n"
            "        motion.x += 1.0\n"
            "    if Input.is_action_pressed(\"ui_up\"):\n"
            "        motion.y -= 1.0\n"
            "    if Input.is_action_pressed(\"ui_down\"):\n"
            "        motion.y += 1.0\n"
            "    position += motion.normalized() * speed * delta\n"
            "    position.x = clamp(position.x, 24.0, 1256.0)\n"
            "    position.y = clamp(position.y, 24.0, 696.0)\n\n"
            "    if Input.is_action_just_pressed(\"ui_accept\"):\n"
            "        if shoot_sfx:\n"
            "            shoot_sfx.play()\n"
        )

        class ShooterRepairClaude(MockProviderClient):
            async def repair_code(self, errors, current_scripts, current_scenes):
                repaired = [
                    ScriptSpec(
                        rel_path=s.rel_path,
                        language=s.language,
                        code=fixed_shooter_code,
                        extends_class=s.extends_class,
                    )
                    for s in current_scripts
                ]
                return {"scripts": repaired, "scenes": current_scenes}

        verif = VerificationPipeline(event_bus=self.event_bus, blackboard=blackboard)
        orchestrator = StudioOrchestrator(
            blackboard=blackboard,
            event_bus=self.event_bus,
            openai_client=self.mock_provider,
            gemini_client=self.mock_provider,
            claude_client=ShooterRepairClaude(),
            verification_pipeline=verif,
            max_iterations=3,
        )

        async def _run() -> Any:
            return await orchestrator.run_pipeline(
                prompt="Retro Cosmic Defender: Vertical scrolling starship shooter with laser cannons and asteroid hazards.",
                target_engine=EngineTarget.GODOT4.value,
                project_name="CosmicDefender",
            )

        state = self.run_async(_run())
        self.assertEqual(state.status, ProjectStatus.COMPLETED)
        self.assertEqual(state.gdd.genre, "space_shooter")

        # Export to disk
        self.engine_hub.export(
            target=EngineTarget.GODOT4,
            gdd=state.gdd,
            manifest=state.asset_manifest,
            scenes=state.scenes,
            scripts=state.scripts,
            output_dir=out_dir,
        )
        packager = CleanHierarchyPackager(target_engine="godot4")
        packager.create_hierarchy(out_dir)
        packager.package_docs(out_dir, state.gdd)
        packager.package_assets(out_dir, state.asset_manifest)
        packager.package_scripts(out_dir, state.scripts)
        packager.package_scenes(out_dir, state.scenes)
        packager.generate_manifest(out_dir, project_name="CosmicDefender")

        # Run 60-frame headless simulator
        simulator = DualModeEngineSimulator(project_dir=out_dir, force_mode="mock_engine")
        sim_res = simulator.run_simulation(frames=60)
        self.assertTrue(sim_res.passed, f"Simulation failed: {sim_res.errors}")
        self.assertEqual(sim_res.frames_run, 60)
        self.assertEqual(len(sim_res.errors), 0)

        # Verify laser shoot and explosion audio
        self.assertTrue((out_dir / "audio" / "sfx" / "sfx_shoot.wav").exists())
        self.assertTrue((out_dir / "audio" / "sfx" / "sfx_explosion.wav").exists())

    # ------------------------------------------------------------------------
    # S4: Ancient Glyph Puzzle (Grid Match Puzzle)
    # ------------------------------------------------------------------------
    def test_s04_ancient_glyph_puzzle(self) -> None:
        """
        Scenario S4: Ancient Glyph Puzzle.
        Match-3 jewel grid puzzle with jewel textures, match sound effects,
        and clean AST linkage with 0 verification errors.
        """
        out_dir = self.workspace_dir / "glyph_puzzle"
        blackboard = Blackboard(
            project_name="GlyphPuzzle",
            target_engine=EngineTarget.GODOT4.value,
            user_prompt="Ancient Glyph Puzzle: 8x8 match-3 rune puzzle with glowing jewels and chime audio.",
        )
        verif = VerificationPipeline(event_bus=self.event_bus, blackboard=blackboard)
        orchestrator = StudioOrchestrator(
            blackboard=blackboard,
            event_bus=self.event_bus,
            openai_client=self.mock_provider,
            gemini_client=self.mock_provider,
            claude_client=self.mock_provider,
            verification_pipeline=verif,
            max_iterations=3,
        )

        async def _run() -> Any:
            return await orchestrator.run_pipeline(
                prompt="Ancient Glyph Puzzle: 8x8 match-3 rune puzzle with glowing jewels and chime audio.",
                target_engine=EngineTarget.GODOT4.value,
                project_name="GlyphPuzzle",
            )

        state = self.run_async(_run())
        self.assertEqual(state.status, ProjectStatus.COMPLETED)
        self.assertEqual(state.gdd.genre, "grid_puzzle")

        # Verify item sprite and pickup SFX
        item_found = any("sprite_item" in it.asset_id for it in state.asset_manifest.items)
        sfx_found = any("sfx_pickup" in it.asset_id for it in state.asset_manifest.items)
        self.assertTrue(item_found)
        self.assertTrue(sfx_found)

        # Export to disk and verify report
        packager = CleanHierarchyPackager(target_engine="godot4")
        packager.create_hierarchy(out_dir)
        packager.package_docs(out_dir, state.gdd)
        packager.package_assets(out_dir, state.asset_manifest)
        packager.package_scripts(out_dir, state.scripts)
        packager.package_scenes(out_dir, state.scenes)
        packager.generate_manifest(out_dir, project_name="GlyphPuzzle")

        report = verif.verify_project(state)
        self.assertTrue(report.passed)
        self.assertEqual(len(report.errors), 0)

    # ------------------------------------------------------------------------
    # S5: Unity Project Export (2D Arcade in Unity)
    # ------------------------------------------------------------------------
    def test_s05_unity_project_export(self) -> None:
        """
        Scenario S5: Unity Project Export.
        Generates structured Unity 2D project with C# MonoBehaviour scripts,
        EditorSceneBuilder.cs, package manifest, and verified C# syntax.
        """
        out_dir = self.workspace_dir / "unity_arcade"
        gdd = self.get_mock_gdd("UnityArcade", genre="platformer_2d", target_engine="unity")
        manifest = self.get_mock_manifest("UnityArcade")

        adapter = UnityAdapter()
        project_manifest = adapter.export_project(
            gdd=gdd,
            manifest=manifest,
            scenes=[],
            scripts=[],
            output_dir=out_dir,
        )

        # Verify Unity directory layout
        self.assertTrue((out_dir / "Assets" / "Scripts" / "PlayerController.cs").exists())
        self.assertTrue((out_dir / "Assets" / "Scripts" / "GameManager.cs").exists())
        self.assertTrue((out_dir / "Assets" / "Scripts" / "AudioManager.cs").exists())
        self.assertTrue((out_dir / "Assets" / "Editor" / "EditorSceneBuilder.cs").exists())
        self.assertTrue((out_dir / "Packages" / "manifest.json").exists())

        # Verify C# syntax
        cs_validator = CSharpSyntaxValidator()
        for cs_path in (out_dir / "Assets" / "Scripts").glob("*.cs"):
            code = cs_path.read_text(encoding="utf-8")
            errors = cs_validator.validate(code, file_path=str(cs_path))
            self.assertEqual(len(errors), 0, f"C# syntax error in {cs_path}: {errors}")

        # Validate package.json contains Unity 2D packages
        pkg_json = json.loads((out_dir / "Packages" / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("com.unity.2d.sprite", pkg_json["dependencies"])

        # Verify asset files copied to Unity Assets directory
        self.assertTrue((out_dir / "Assets" / "Sprites" / "player.png").exists())
        self.assertTrue((out_dir / "Assets" / "Audio" / "jump.wav").exists())
        self.assert_valid_png(out_dir / "Assets" / "Sprites" / "player.png")
        self.assert_valid_wav(out_dir / "Assets" / "Audio" / "jump.wav")

    # ------------------------------------------------------------------------
    # S6: Mock Offline Studio Generation (100% Deterministic Reproducibility)
    # ------------------------------------------------------------------------
    def test_s06_mock_offline_studio_generation(self) -> None:
        """
        Scenario S6: Mock Offline Studio Generation.
        Completely offline synthesis using Pillow & Wave, zero API tokens,
        and 100% reproducible byte-identical SHA-256 asset hashes across runs.
        """
        prompt = "Offline Pixel Odyssey: Deterministic synth run."

        async def _generate_run() -> AssetManifest:
            mock = MockProviderClient()
            gdd = await mock.generate_gdd(prompt=prompt, seed=42)
            manifest = await mock.generate_assets(gdd)
            return manifest

        manifest_1 = self.run_async(_generate_run())
        manifest_2 = self.run_async(_generate_run())

        self.assertEqual(len(manifest_1.items), len(manifest_2.items))
        for item1, item2 in zip(manifest_1.items, manifest_2.items):
            self.assertEqual(item1.asset_id, item2.asset_id)
            hash1 = hashlib.sha256(item1.file_bytes).hexdigest()
            hash2 = hashlib.sha256(item2.file_bytes).hexdigest()
            self.assertEqual(hash1, hash2, f"Deterministic mismatch on {item1.asset_id}")

            # Verify media integrity
            if item1.asset_type in (AssetType.SPRITE.value, AssetType.TEXTURE.value):
                self.assert_valid_png(item1.file_bytes)
            elif item1.asset_type in (AssetType.AUDIO_SFX.value, AssetType.AUDIO_MUSIC.value):
                self.assert_valid_wav(item1.file_bytes)

    # ------------------------------------------------------------------------
    # S7: Live GUI Directed Workflow (FastAPI Server + REST + WebSocket)
    # ------------------------------------------------------------------------
    def test_s07_live_gui_directed_workflow(self) -> None:
        """
        Scenario S7: Live GUI Directed Workflow.
        Triggering generation via REST API /api/direct, retrieving state,
        fetching media previews from /api/media, and exporting via /api/export.
        """
        bb = Blackboard(project_name="DirectGuiProject")
        bus = EventBus()
        verif = VerificationPipeline(event_bus=bus, blackboard=bb)

        class GuiGemini(MockProviderClient):
            async def repair_assets(self, errors: Any, current_manifest: Any) -> Any:
                manifest = await super().repair_assets(errors, current_manifest)
                if not manifest.get_item("icon"):
                    manifest.add_item(AssetItem(
                        asset_id="icon",
                        asset_type=AssetType.SPRITE,
                        rel_path="assets/sprites/icon.svg",
                        engine_path="res://assets/sprites/icon.svg",
                        file_bytes=b"<svg xmlns='http://www.w3.org/2000/svg' width='32' height='32'></svg>",
                        is_mock=True,
                    ))
                return manifest

        orch = StudioOrchestrator(
            blackboard=bb,
            event_bus=bus,
            openai_client=self.mock_provider,
            gemini_client=GuiGemini(),
            claude_client=self.mock_provider,
            verification_pipeline=verif,
            max_iterations=3,
        )
        app = create_app(blackboard=bb, event_bus=bus, orchestrator=orch, verification_pipeline=verif)
        client = TestClient(app)

        # Initial status check
        status_resp = client.get("/api/project/status")
        self.assertEqual(status_resp.status_code, 200)

        # Trigger generation via direct endpoint
        direct_payload = {
            "prompt": "Cyber Directing Test Run",
            "target_engine": "godot4",
            "project_name": "DirectGuiProject",
            "mock": True,
        }
        with client.websocket_connect("/api/ws/telemetry") as ws:
            direct_resp = client.post("/api/direct", json=direct_payload)
            self.assertEqual(direct_resp.status_code, 202)

            def _recv_event(timeout: float = 2.0) -> Optional[Dict[str, Any]]:
                try:
                    if hasattr(ws, "portal") and hasattr(ws, "_send_rx"):
                        async def _recv() -> Dict[str, Any]:
                            raw = await asyncio.wait_for(ws._send_rx.receive(), timeout=timeout)
                            if "text" in raw:
                                return json.loads(raw["text"])
                            elif "bytes" in raw:
                                return json.loads(raw["bytes"].decode("utf-8"))
                            return raw
                        return ws.portal.call(_recv)
                    else:
                        return ws.receive_json()
                except Exception:
                    return None

            # Drain events until pipeline completes
            for _ in range(30):
                ev = _recv_event(timeout=2.0)
                if not ev:
                    break
                event_type = ev.get("event_type") or ev.get("event")
                state = ev.get("state")
                agent = ev.get("agent")
                if (
                    event_type in ("pipeline_completed", "pipeline_failed", "job_completed")
                    or (agent == "validator" and state == "complete")
                    or state in ("completed", "failed")
                ):
                    break

        # Check project state
        state_resp = client.get("/api/project/state")
        self.assertEqual(state_resp.status_code, 200)
        state_data = state_resp.json()
        manifest_data = state_data.get("asset_manifest", {}) or {}
        items = manifest_data.get("items", [])
        self.assertGreater(len(items), 0)

        # Fetch first sprite asset binary via /api/project/assets/{asset_id}
        first_sprite = next((it for it in items if "sprite" in it.get("asset_id", "")), items[0])
        asset_id = first_sprite["asset_id"]
        bin_resp = client.get(f"/api/project/assets/{asset_id}")
        self.assertEqual(bin_resp.status_code, 200)
        self.assertGreater(len(bin_resp.content), 0)

        # Export project
        export_out = self.workspace_dir / "gui_export_out"
        export_resp = client.post("/api/project/export", json={"output_dir": str(export_out)})
        self.assertEqual(export_resp.status_code, 200)
        self.assertTrue((export_out / "project.godot").exists())

    # ------------------------------------------------------------------------
    # S8: Multi-Room Metroidvania (Interconnected Scenes & Room Transitions)
    # ------------------------------------------------------------------------
    def test_s08_multi_room_metroidvania(self) -> None:
        """
        Scenario S8: Multi-Room Metroidvania.
        Multiple interconnected .tscn scenes (Hub, Forest Room, Cavern Boss Room)
        with transition door references verified by TSCNValidator and pipeline.
        """
        out_dir = self.workspace_dir / "metroidvania_world"
        validator = TSCNValidator()

        # Scene 1: Hub
        scene_hub = SceneAST(
            name="Hub",
            path="scenes/hub.tscn",
            ext_resources=[
                ExtResourceAST(id="1_tex", type="Texture2D", path="res://assets/sprites/sprite_player.png"),
                ExtResourceAST(id="2_door_tex", type="Texture2D", path="res://assets/sprites/sprite_tile.png"),
            ],
            nodes=[
                NodeAST(name="Hub", type="Node2D", parent=None),
                NodeAST(name="Player", type="CharacterBody2D", parent="."),
                NodeAST(name="DoorToForest", type="Area2D", parent=".", properties={"target_scene": "res://scenes/forest_room.tscn"}),
                NodeAST(name="DoorToCavern", type="Area2D", parent=".", properties={"target_scene": "res://scenes/cavern_boss.tscn"}),
            ],
        )

        # Scene 2: Forest Room
        scene_forest = SceneAST(
            name="ForestRoom",
            path="scenes/forest_room.tscn",
            ext_resources=[
                ExtResourceAST(id="1_tex", type="Texture2D", path="res://assets/sprites/sprite_player.png"),
            ],
            nodes=[
                NodeAST(name="ForestRoom", type="Node2D", parent=None),
                NodeAST(name="DoorToHub", type="Area2D", parent=".", properties={"target_scene": "res://scenes/hub.tscn"}),
            ],
        )

        # Scene 3: Cavern Boss Room
        scene_cavern = SceneAST(
            name="CavernBoss",
            path="scenes/cavern_boss.tscn",
            ext_resources=[
                ExtResourceAST(id="1_tex", type="Texture2D", path="res://assets/sprites/sprite_player.png"),
                ExtResourceAST(id="2_boss_tex", type="Texture2D", path="res://assets/sprites/sprite_enemy.png"),
            ],
            nodes=[
                NodeAST(name="CavernBoss", type="Node2D", parent=None),
                NodeAST(name="BossGolem", type="CharacterBody2D", parent="."),
                NodeAST(name="DoorToHub", type="Area2D", parent=".", properties={"target_scene": "res://scenes/hub.tscn"}),
            ],
        )

        # Validate syntax of all 3 scenes
        for scene in (scene_hub, scene_forest, scene_cavern):
            tscn_text = serialize_tscn(scene)
            errors = validator.validate_tscn_text(tscn_text, file_path=scene.path)
            self.assertEqual(len(errors), 0, f"TSCN errors in {scene.path}: {errors}")

        # Assemble full project and verify
        blackboard = Blackboard(
            project_name="MetroidvaniaWorld",
            target_engine=EngineTarget.GODOT4.value,
            user_prompt="Multi-Room Metroidvania with Hub, Forest, and Cavern Boss rooms.",
        )
        verif = VerificationPipeline(event_bus=self.event_bus, blackboard=blackboard)

        async def _setup() -> VerificationReport:
            gdd = await self.mock_provider.generate_gdd("MetroidvaniaWorld")
            manifest = await self.mock_provider.generate_assets(gdd)
            manifest.items.append(self._create_icon_asset())
            scenes, scripts = await self.mock_provider.generate_code(gdd, manifest=manifest)

            await blackboard.set_gdd(gdd)
            await blackboard.set_asset_manifest(manifest)
            await blackboard.add_scene(scene_hub)
            await blackboard.add_scene(scene_forest)
            await blackboard.add_scene(scene_cavern)
            for sc in scenes:
                await blackboard.add_scene(sc)
            for sp in scripts:
                await blackboard.add_script(sp)
            state = await blackboard.get_state()
            return verif.verify_project(state)

        report = self.run_async(_setup())
        self.assertTrue(report.passed)
        self.assertEqual(len(report.errors), 0)

    # ------------------------------------------------------------------------
    # S9: Branching Dialogue Quest (Narrative RPG)
    # ------------------------------------------------------------------------
    def test_s09_branching_dialogue_quest_rpg(self) -> None:
        """
        Scenario S9: Branching Dialogue Quest.
        Multi-node dialogue tree with player choices and quest state tracking,
        verified through docs/LORE.md rendering and GDD inspection.
        """
        out_dir = self.workspace_dir / "branching_dialogue_rpg"

        gdd = GameDesignDocument(
            title="Chronicles of Elyria",
            genre=GameGenre.TOP_DOWN_RPG.value,
            target_engine=EngineTarget.GODOT4.value,
            high_concept="Narrative RPG with moral choices and quest branches.",
            gameplay_mechanics=["Interactive dialogue", "Quest inventory", "Turn-based combat"],
            narrative_lore="Ancient spirits speak in riddles to test mortal intent.",
            dialogue_trees=[
                DialogueNode(
                    node_id="root_elder",
                    speaker="Elder Oakhaven",
                    text="The ancient shrine whispers of impending doom. Will you aid our sanctuary?",
                    choices=[
                        DialogueChoice(text="I accept the quest.", next_node_id="quest_accept"),
                        DialogueChoice(text="Tell me of this doom first.", next_node_id="lore_inquire"),
                        DialogueChoice(text="I have other matters to attend to.", next_node_id="decline"),
                    ],
                ),
                DialogueNode(
                    node_id="quest_accept",
                    speaker="Elder Oakhaven",
                    text="Take this relic key to unlock the sunken ruins.",
                    choices=[
                        DialogueChoice(text="I will not fail you.", next_node_id=None),
                    ],
                ),
                DialogueNode(
                    node_id="lore_inquire",
                    speaker="Elder Oakhaven",
                    text="Five centuries ago, the shadow monolith cracked open...",
                    choices=[
                        DialogueChoice(text="I understand now. I accept.", next_node_id="quest_accept"),
                    ],
                ),
            ],
            quests=[
                QuestSpec(
                    quest_id="quest_sunken_ruins",
                    title="The Sunken Sanctuary",
                    description="Enter the submerged ruins and purify the corrupted heart.",
                    objectives=[
                        QuestObjective(objective_id="obj_key", description="Obtain the Relic Key", target_count=1),
                        QuestObjective(objective_id="obj_reach", description="Reach the Sunken Ruins", target_count=1),
                        QuestObjective(objective_id="obj_purify", description="Purify corrupted heart", target_count=1),
                    ],
                    rewards=["Sunken Blade", "2000 Gold", "Hero Title"],
                )
            ],
            required_assets=[
                AssetRequirement(asset_id="sprite_player", asset_type=AssetType.SPRITE, rel_path="assets/sprites/player.png"),
                AssetRequirement(asset_id="sfx_jump", asset_type=AssetType.AUDIO_SFX, rel_path="audio/sfx/jump.wav"),
                AssetRequirement(asset_id="icon", asset_type=AssetType.SPRITE, rel_path="assets/sprites/icon.svg"),
            ],
        )

        manifest = self.get_mock_manifest("Chronicles of Elyria")
        manifest.items.append(self._create_icon_asset())

        # Render documentation
        packager = CleanHierarchyPackager(target_engine="godot4")
        packager.create_hierarchy(out_dir)
        packager.package_docs(out_dir, gdd)
        packager.package_assets(out_dir, manifest)
        packager.generate_manifest(out_dir, project_name="Chronicles of Elyria")

        # Verify docs/LORE.md contents
        lore_file = out_dir / "docs" / "LORE.md"
        self.assertTrue(lore_file.exists())
        lore_text = lore_file.read_text(encoding="utf-8")
        self.assertIn("Elder Oakhaven", lore_text)
        self.assertIn("The Sunken Sanctuary", lore_text)
        self.assertIn("purify the corrupted heart", lore_text)

        # Verify dialogue tree structure
        self.assertEqual(len(gdd.dialogue_trees), 3)
        self.assertEqual(len(gdd.dialogue_trees[0].choices), 3)
        self.assertEqual(gdd.quests[0].objectives[0].target_count, 1)

    # ------------------------------------------------------------------------
    # S10: Asset-Heavy Dynamic Loader (>15 Distinct Assets Dynamically Linked)
    # ------------------------------------------------------------------------
    def test_s10_asset_heavy_dynamic_loader(self) -> None:
        """
        Scenario S10: Asset-Heavy Dynamic Loader.
        Over 15 distinct sprites and audio streams dynamically synthesized,
        bound into SceneAST via DynamicAssetLinker, and verified with 0 errors.
        """
        out_dir = self.workspace_dir / "asset_heavy_loader"

        # Define 16 asset requirements: 8 sprites + 8 audio clips
        reqs = []
        for i in range(8):
            reqs.append(
                AssetRequirement(
                    asset_id=f"sprite_{i:02d}",
                    asset_type=AssetType.SPRITE,
                    target_rel_path=f"assets/sprites/sprite_{i:02d}.png",
                    width=32,
                    height=32,
                )
            )
        for i in range(8):
            reqs.append(
                AssetRequirement(
                    asset_id=f"sfx_{i:02d}",
                    asset_type=AssetType.AUDIO_SFX,
                    target_rel_path=f"audio/sfx/sfx_{i:02d}.wav",
                    duration_sec=0.2,
                )
            )

        gdd = GameDesignDocument(
            title="HeavyAssetSpectacle",
            genre=GameGenre.PLATFORMER_2D.value,
            target_engine=EngineTarget.GODOT4.value,
            high_concept="Extravagant visual landscape with numerous sprites and audio streams.",
            gameplay_mechanics=["Heavy asset streaming", "Dynamic audio mixer"],
            required_assets=reqs,
        )

        async def _gen() -> AssetManifest:
            return await self.mock_provider.generate_assets(gdd)

        manifest = self.run_async(_gen())
        manifest.items.append(self._create_icon_asset())
        self.assertEqual(len(manifest.items), 17)

        # Dynamic asset linking into scene
        base_scene = SceneAST(
            name="HeavyScene",
            path="scenes/heavy.tscn",
            nodes=[
                NodeAST(name="HeavyScene", type="Node2D", parent=None),
                NodeAST(name="Player", type="CharacterBody2D", parent="."),
                NodeAST(name="Sprite2D", type="Sprite2D", parent="Player", properties={"texture": "sprite_00"}),
                NodeAST(name="AudioStreamPlayer2D", type="AudioStreamPlayer2D", parent="Player", properties={"stream": "sfx_00"}),
            ],
        )

        linker = DynamicAssetLinker(manifest=manifest, gdd=gdd)
        linked_scene = linker.link_scene(base_scene)
        self.assertGreater(len(linked_scene.ext_resources), 0)

        # Package to disk and run asset verifier
        packager = CleanHierarchyPackager(target_engine="godot4")
        packager.create_hierarchy(out_dir)
        packager.package_assets(out_dir, manifest)
        packager.package_scenes(out_dir, {linked_scene.path: linked_scene})
        packager.package_scripts(out_dir, {"scripts/player.gd": self.get_mock_script("scripts/player.gd")})
        packager.package_docs(out_dir, gdd)
        packager.generate_manifest(out_dir, project_name="HeavyAssetSpectacle")

        verifier = AssetVerifier(project_dir=out_dir, manifest=manifest, gdd=gdd)
        verif_res = verifier.verify_all(fail_on_orphan=False)
        self.assertTrue(verif_res.passed, f"Asset verification failed: {verif_res.errors}")
        self.assertEqual(len(verif_res.missing_files), 0)
        self.assertEqual(len(verif_res.corrupted_files), 0)

    # ------------------------------------------------------------------------
    # S11: Resilient Rate-Limit Recovery (Concurrency Stress & Backoff)
    # ------------------------------------------------------------------------
    def test_s11_resilient_rate_limit_recovery(self) -> None:
        """
        Scenario S11: Resilient Rate-Limit Recovery.
        Simulates provider 429 rate limit errors, verifies token bucket rate
        limiting, exponential backoff retries, and circuit breaker trip/reset.
        """
        rate_limiter = TokenBucketRateLimiter(requests_per_minute=60, tokens_per_minute=1000)
        backoff = ExponentialBackoff(max_retries=5, base_delay=0.01, max_backoff=0.1)
        circuit = CircuitBreaker(failure_threshold=3, recovery_timeout=0.1)

        call_count = 0

        async def flaky_provider_call() -> str:
            nonlocal call_count
            await rate_limiter.acquire(tokens=10)
            if not circuit.can_execute():
                # Failover to mock provider when circuit is open
                return "fallback_mock_response"

            call_count += 1
            if call_count < 3:
                circuit.record_failure()
                raise RuntimeError("429 Too Many Requests")

            circuit.record_success()
            return "live_provider_response"

        result = self.run_async(backoff.execute(flaky_provider_call))
        self.assertEqual(result, "live_provider_response")
        self.assertEqual(circuit.state, CircuitState.CLOSED)

        # Trigger repeated failures to force circuit breaker open
        for _ in range(3):
            circuit.record_failure()
        self.assertEqual(circuit.state, CircuitState.OPEN)
        self.assertFalse(circuit.can_execute())

        # Verify fallback logic executes when circuit is open
        fallback_res = self.run_async(flaky_provider_call())
        self.assertEqual(fallback_res, "fallback_mock_response")

    # ------------------------------------------------------------------------
    # S12: Cross-Engine Multi-Export Pipeline (Godot 4, Pygame, Web HTML5)
    # ------------------------------------------------------------------------
    def test_s12_cross_engine_multi_export_pipeline(self) -> None:
        """
        Scenario S12: Cross-Engine Multi-Export Pipeline.
        Single GDD and asset manifest exported simultaneously to Godot 4,
        Pygame, and Web HTML5, with cross-engine syntax validation.
        """
        export_root = self.workspace_dir / "multi_engine_export"
        gdd = self.get_mock_gdd("MultiEngineGame")
        manifest = self.get_mock_manifest("MultiEngineGame")
        scenes = [self.get_mock_scene()]
        scripts = [self.get_mock_script()]

        manifests = self.engine_hub.export_multi(
            targets=[EngineTarget.GODOT4, EngineTarget.PYGAME, EngineTarget.WEB_HTML5],
            gdd=gdd,
            manifest=manifest,
            scenes=scenes,
            scripts=scripts,
            base_output_dir=export_root,
        )

        self.assertEqual(len(manifests), 3)

        # 1. Godot 4 verification
        godot_dir = export_root / "godot4"
        self.assertTrue((godot_dir / "project.godot").exists())
        self.assertTrue((godot_dir / "scenes" / "main_level.tscn").exists())
        self.assertTrue((godot_dir / "scripts" / "player.gd").exists())

        # 2. Pygame verification
        pygame_dir = export_root / "pygame"
        self.assertTrue((pygame_dir / "main.py").exists())
        self.assertTrue((pygame_dir / "game" / "config.py").exists())

        # 3. Web HTML5 verification
        web_dir = export_root / "web_html5"
        self.assertTrue((web_dir / "index.html").exists())
        self.assertTrue((web_dir / "game.js").exists())
        self.assertTrue((web_dir / "style.css").exists())

        # 4. Multi-engine syntax validator checks all targets
        multi_validator = MultiEngineSyntaxValidator()

        # GDScript
        gd_errors = multi_validator.validate_file(godot_dir / "scripts" / "player.gd")
        self.assertEqual(len(gd_errors), 0, f"GDScript syntax error: {gd_errors}")

        # Python
        py_errors = multi_validator.validate_file(pygame_dir / "main.py")
        self.assertEqual(len(py_errors), 0, f"Pygame Python syntax error: {py_errors}")

        # JavaScript
        js_errors = multi_validator.validate_file(web_dir / "game.js")
        self.assertEqual(len(js_errors), 0, f"Web JavaScript syntax error: {js_errors}")
