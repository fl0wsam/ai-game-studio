"""
tests/tier1_features/test_gui_studio.py - Tier 1 Systematic Feature Tests (F-GUI-01 through F-GUI-05)
Opaque-box, requirement-driven tests covering Visual Studio GUI and CLI in isolation:
- F-GUI-01: Single-Command CLI Launcher & Desktop Entrypoints (>=5 tests)
- F-GUI-02: Interactive Agent Graph (>=5 tests)
- F-GUI-03: Media & Asset Preview Gallery (>=5 tests)
- F-GUI-04: Workspace & Log Viewer (>=5 tests)
- F-GUI-05: Prompt & Directing Panel (>=5 tests)
- Departure Mono Typography, Zero External CDN, and Cross-Platform Desktop Contracts (>=5 tests)
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi.testclient import TestClient

from tests.conftest_base import StudioTestCase
from studio.cli import (
    build_parser,
    execute_headless_pipeline,
    main as cli_main,
    run_gui_server,
    validate_arguments,
)
from studio.core.blackboard import Blackboard
from studio.core.event_bus import EventBus
from studio.core.models import (
    AgentMessage,
    AgentRole,
    AssetItem,
    AssetManifest,
    AssetType,
    EngineTarget,
    GameDesignDocument,
    GameGenre,
    GuiTelemetryEvent,
    NodeAST,
    ProjectStatus,
    QuestObjective,
    QuestSpec,
    SceneAST,
    ScriptSpec,
    StudioProjectState,
    TokenUsageMetrics,
    VerificationReport,
)
from studio.gui.desktop import (
    is_display_available,
    launch_desktop,
    launch_desktop_window,
    launch_browser,
    normalize_cross_platform_path,
)
from studio.gui.server import create_app
from studio.gui.websocket_manager import WebSocketTelemetryManager


class TestCliLauncherFeatures(StudioTestCase):
    """
    F-GUI-01: Single-Command CLI Launcher & Desktop Entrypoint Features.
    Validates default arguments, custom parameters, desktop launcher modes,
    headless build pipeline execution, and entrypoint exit codes.
    """

    def setUp(self) -> None:
        super().setUp()
        self.parser = build_parser()

    def test_cli_01_default_argument_parsing(self) -> None:
        """Verifies default arguments when invoked without flags."""
        args = self.parser.parse_args([])
        self.assertTrue(args.gui)
        self.assertFalse(args.headless)
        self.assertFalse(args.window)
        self.assertFalse(args.browser)
        self.assertFalse(args.no_browser)
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8080)
        self.assertEqual(args.engine, "godot4")
        self.assertEqual(args.genre, "platformer_2d")
        self.assertTrue(args.mock)
        self.assertTrue(args.verify)
        self.assertEqual(args.iterations, 3)
        self.assertEqual(args.output_dir, "./output")

    def test_cli_02_custom_flag_customizations(self) -> None:
        """Verifies parsing of customized CLI options."""
        args = self.parser.parse_args([
            "--host", "0.0.0.0",
            "--port", "8888",
            "--engine", "pygame",
            "--genre", "space_shooter",
            "--project-name", "StarCruiser",
            "--iterations", "5",
            "--log-level", "DEBUG",
            "--output-dir", "/tmp/game_out",
        ])
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 8888)
        self.assertEqual(args.engine, "pygame")
        self.assertEqual(args.genre, "space_shooter")
        self.assertEqual(args.project_name, "StarCruiser")
        self.assertEqual(args.iterations, 5)
        self.assertEqual(args.log_level, "DEBUG")
        self.assertEqual(args.output_dir, "/tmp/game_out")

    def test_cli_03_desktop_mode_switches(self) -> None:
        """Verifies desktop window, browser, and no-browser flags parse properly."""
        args_win = self.parser.parse_args(["--window"])
        self.assertTrue(args_win.window)

        args_browser = self.parser.parse_args(["--browser"])
        self.assertTrue(args_browser.browser)

        args_no_browser = self.parser.parse_args(["--no-browser"])
        self.assertTrue(args_no_browser.no_browser)

    def test_cli_04_headless_mock_build_execution(self) -> None:
        """Verifies execute_headless_pipeline completes mock generation, export, and verification with exit code 0."""
        target_dir = self.workspace_dir / "headless_build"
        args = self.parser.parse_args([
            "--headless",
            "--prompt", "Cyber Ninja Infiltration",
            "--engine", "godot4",
            "--output-dir", str(target_dir),
            "--mock",
            "--verify",
        ])
        exit_code = self.run_async(execute_headless_pipeline(args))
        self.assertEqual(exit_code, 0)

        # Verify exported hierarchy and files
        self.assertTrue((target_dir / "project_manifest.json").is_file())
        self.assertTrue((target_dir / "docs" / "GDD.md").is_file())
        self.assertTrue((target_dir / "scenes").is_dir())
        self.assertTrue((target_dir / "scripts").is_dir())

    def test_cli_05_main_entrypoint_exit_codes(self) -> None:
        """Verifies that cli_main returns deterministic exit codes."""
        # Help returns 0
        help_code = cli_main(["--help"])
        self.assertEqual(help_code, 0)

        # Invalid argument syntax returns 2
        bad_arg_code = cli_main(["--unknown-option-flag-xyz"])
        self.assertEqual(bad_arg_code, 2)

        # Missing required prompt in headless mode returns 1
        missing_prompt_code = cli_main(["--headless"])
        self.assertEqual(missing_prompt_code, 1)

    def test_cli_06_desktop_script_contract(self) -> None:
        """Verifies pyproject.toml entrypoint contract and module invocation syntax."""
        import studio.cli as cli_mod
        self.assertTrue(hasattr(cli_mod, "main"))
        self.assertTrue(callable(cli_mod.main))
        self.assertTrue(hasattr(cli_mod, "build_parser"))
        self.assertTrue(hasattr(cli_mod, "validate_arguments"))
        self.assertTrue(hasattr(cli_mod, "execute_headless_pipeline"))


class TestAgentGraphFeatures(StudioTestCase):
    """
    F-GUI-02: Interactive Agent Graph Features.
    Validates 5-agent node topologies, real-time dynamic state updates,
    inter-agent communication edge routing, and active task metadata.
    """

    def setUp(self) -> None:
        super().setUp()
        self.blackboard = Blackboard(project_name="AgentGraphTest")
        self.event_bus = EventBus()
        self.app = create_app(blackboard=self.blackboard, event_bus=self.event_bus)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.run_async(self.event_bus.close())
        super().tearDown()

    def test_graph_01_topology_initialization(self) -> None:
        """Verifies the 5 agent node topology is reported by GET /api/project/status."""
        res = self.client.get("/api/project/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("agents", data)
        agents = data["agents"]

        expected_roles = ["orchestrator", "openai_director", "gemini_artist", "claude_engineer", "validator"]
        for role in expected_roles:
            self.assertIn(role, agents, f"Expected agent role '{role}' missing from status")
            self.assertIn("state", agents[role])
            self.assertIn("name", agents[role])

    def test_graph_02_state_transitions(self) -> None:
        """Simulates agent_state telemetry events across idle, active, reviewing, complete, failed."""
        states = ["idle", "active", "reviewing", "complete", "failed"]
        with self.client.websocket_connect("/api/ws/telemetry") as ws:
            for s in states:
                event = GuiTelemetryEvent(
                    event_type="agent_state",
                    agent="claude_engineer",
                    state=s,
                    data={"task": f"State is now {s}"},
                )
                self.event_bus.publish_sync("telemetry.agent_state", event)
                msg = ws.receive_json()
                self.assertEqual(msg["event_type"], "agent_state")
                self.assertEqual(msg["agent"], "claude_engineer")
                self.assertEqual(msg["state"], s)

    def test_graph_03_message_edge_routing(self) -> None:
        """Verifies inter-agent communication event routing between sender and recipient."""
        with self.client.websocket_connect("/api/ws/telemetry") as ws:
            event = GuiTelemetryEvent(
                event_type="agent_state",
                agent="openai_director",
                state="active",
                data={
                    "sender": "openai_director",
                    "recipient": "claude_engineer",
                    "topic": "gdd_spec",
                    "message": "GDD High Concept ready for architecture review",
                },
            )
            self.event_bus.publish_sync("telemetry.agent_state", event)
            msg = ws.receive_json()
            self.assertEqual(msg["data"]["sender"], "openai_director")
            self.assertEqual(msg["data"]["recipient"], "claude_engineer")
            self.assertEqual(msg["data"]["topic"], "gdd_spec")

    def test_graph_04_active_task_metadata(self) -> None:
        """Verifies active task description and phase metadata contracts on agent nodes."""
        with self.client.websocket_connect("/api/ws/telemetry") as ws:
            event = GuiTelemetryEvent(
                event_type="build_progress",
                agent="gemini_artist",
                state="active",
                data={
                    "phase": "ASSET_SYNTHESIS",
                    "task_description": "Rendering 2D Pixel Art Sprites and BGM WAV Streams",
                    "percentage": 50,
                },
            )
            self.event_bus.publish_sync("telemetry.build_progress", event)
            msg = ws.receive_json()
            self.assertEqual(msg["event_type"], "build_progress")
            self.assertEqual(msg["agent"], "gemini_artist")
            self.assertEqual(msg["data"]["phase"], "ASSET_SYNTHESIS")
            self.assertEqual(msg["data"]["percentage"], 50)

    def test_graph_05_typography_departure_mono(self) -> None:
        """Verifies agent_graph.js declares Departure Mono font for canvas text contexts."""
        res = self.client.get("/static/agent_graph.js")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Departure Mono", res.text)
        self.assertIn("JetBrains Mono", res.text)

    def test_graph_06_dynamic_agent_status_endpoint(self) -> None:
        """Verifies GET /api/project/status dynamically updates agent status based on project state."""
        # When status is IN_PROGRESS and active phase is NARRATIVE, openai_director should be active
        self.run_async(self.blackboard.set_status(ProjectStatus.IN_PROGRESS, active_phase="NARRATIVE_GENERATION"))

        res = self.client.get("/api/project/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["agents"]["openai_director"]["state"], "active")
        self.assertEqual(data["agents"]["orchestrator"]["state"], "active")


class TestMediaGalleryFeatures(StudioTestCase):
    """
    F-GUI-03: Media & Asset Preview Gallery Features.
    Validates visual artwork image previews, audio player streaming,
    asset metadata inspection, and dynamic asset streaming injection.
    """

    def setUp(self) -> None:
        super().setUp()
        self.blackboard = Blackboard(project_name="MediaGalleryTest")
        self.event_bus = EventBus()
        self.app = create_app(blackboard=self.blackboard, event_bus=self.event_bus)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.run_async(self.event_bus.close())
        super().tearDown()

    def test_gallery_01_image_preview_endpoint(self) -> None:
        """Tests GET /api/project/assets/{id} serves PNG images with image/png MIME type and checksum."""
        png_bytes = self.create_test_image(width=64, height=64, color=(255, 128, 0, 255))
        asset = AssetItem(
            asset_id="hero_sprite",
            asset_type="sprite",
            rel_path="assets/sprites/hero.png",
            file_bytes=png_bytes,
            checksum="hero_checksum_sha256",
        )
        self.run_async(self.blackboard.add_asset_item(asset))

        res = self.client.get("/api/project/assets/hero_sprite")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "image/png")
        self.assertEqual(res.headers.get("x-asset-type"), "sprite")
        self.assertEqual(res.headers.get("x-asset-checksum"), "hero_checksum_sha256")
        self.assertEqual(res.content, png_bytes)

    def test_gallery_02_audio_player_endpoint(self) -> None:
        """Tests GET /api/project/assets/{id} serves audio WAV files with audio/wav MIME type."""
        wav_bytes = self.create_test_audio(duration=0.05, freq=523.25)
        asset = AssetItem(
            asset_id="bgm_main",
            asset_type="audio_music",
            rel_path="audio/music/main_theme.wav",
            file_bytes=wav_bytes,
            checksum="bgm_checksum_123",
        )
        self.run_async(self.blackboard.add_asset_item(asset))

        res = self.client.get("/api/project/assets/bgm_main")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "audio/wav")
        self.assertEqual(res.headers.get("x-asset-type"), "audio_music")
        self.assertEqual(res.content, wav_bytes)

    def test_gallery_03_asset_metadata_query(self) -> None:
        """Tests GET /api/project/assets/{id}?metadata=true returns JSON representation of the asset."""
        asset = AssetItem(
            asset_id="jump_sfx",
            asset_type="audio_sfx",
            rel_path="audio/sfx/jump.wav",
            file_bytes=b"dummy_wav_bytes",
            checksum="jump_checksum_999",
            properties={"sample_rate": 44100, "channels": 1},
        )
        self.run_async(self.blackboard.add_asset_item(asset))

        res = self.client.get("/api/project/assets/jump_sfx?metadata=true")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "application/json")
        data = res.json()
        self.assertEqual(data["asset_id"], "jump_sfx")
        self.assertEqual(data["asset_type"], "audio_sfx")
        self.assertEqual(data["rel_path"], "audio/sfx/jump.wav")
        self.assertEqual(data["checksum"], "jump_checksum_999")
        self.assertEqual(data["properties"]["sample_rate"], 44100)

    def test_gallery_04_multi_asset_manifest_structure(self) -> None:
        """Tests GET /api/project/state returns full asset manifest with all registered items."""
        asset1 = AssetItem(asset_id="sp1", asset_type="sprite", rel_path="assets/sp1.png", checksum="c1")
        asset2 = AssetItem(asset_id="bg1", asset_type="background", rel_path="assets/bg1.png", checksum="c2")
        asset3 = AssetItem(asset_id="sfx1", asset_type="audio_sfx", rel_path="audio/sfx1.wav", checksum="c3")

        self.run_async(self.blackboard.add_asset_item(asset1))
        self.run_async(self.blackboard.add_asset_item(asset2))
        self.run_async(self.blackboard.add_asset_item(asset3))

        res = self.client.get("/api/project/state")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("asset_manifest", data)
        self.assertIn("items", data["asset_manifest"])
        self.assertEqual(len(data["asset_manifest"]["items"]), 3)

    def test_gallery_05_dynamic_asset_stream_injection(self) -> None:
        """Verifies WebSocket broadcast of asset_generated event for dynamic gallery injection."""
        with self.client.websocket_connect("/api/ws/telemetry") as ws:
            event = GuiTelemetryEvent(
                event_type="asset_generated",
                agent="gemini_artist",
                state="complete",
                data={
                    "asset_id": "explosion_particle",
                    "asset_type": "sprite",
                    "rel_path": "assets/sprites/explosion.png",
                    "checksum": "expl_hash_123",
                },
            )
            self.event_bus.publish_sync("telemetry.asset_generated", event)

            msg = ws.receive_json()
            self.assertEqual(msg["event_type"], "asset_generated")
            self.assertEqual(msg["agent"], "gemini_artist")
            self.assertEqual(msg["data"]["asset_id"], "explosion_particle")
            self.assertEqual(msg["data"]["rel_path"], "assets/sprites/explosion.png")

    def test_gallery_06_card_metadata_attributes(self) -> None:
        """Verifies asset item models contain all required metadata for gallery cards."""
        asset = AssetItem(
            asset_id="laser_beam",
            asset_type="sprite",
            rel_path="assets/sprites/laser.png",
            checksum="laser_hash_abc",
            properties={"width": 32, "height": 8, "animated": False},
            is_mock=True,
        )
        data = asset.model_dump()
        self.assertEqual(data["asset_id"], "laser_beam")
        self.assertEqual(data["asset_type"], "sprite")
        self.assertEqual(data["properties"]["width"], 32)
        self.assertTrue(data["is_mock"])


class TestWorkspaceLogViewerFeatures(StudioTestCase):
    """
    F-GUI-04: Project Workspace & Log Viewer Features.
    Validates streaming execution log append, severity filtering,
    GDD section inspection, Scene & Script AST inspection, and token metrics.
    """

    def setUp(self) -> None:
        super().setUp()
        self.blackboard = Blackboard(project_name="WorkspaceLogTest")
        self.event_bus = EventBus()
        self.app = create_app(blackboard=self.blackboard, event_bus=self.event_bus)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.run_async(self.event_bus.close())
        super().tearDown()

    def test_log_01_streaming_append(self) -> None:
        """Tests logging events to Blackboard and retrieving them via GET /api/project/logs."""
        self.run_async(self.blackboard.add_log(level="INFO", message="Pipeline initialized", sender="orchestrator"))
        self.run_async(self.blackboard.add_log(level="DEBUG", message="Compiling AST", sender="claude_engineer"))

        res = self.client.get("/api/project/logs")
        self.assertEqual(res.status_code, 200)
        logs = res.json()
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0]["message"], "Pipeline initialized")
        self.assertEqual(logs[1]["message"], "Compiling AST")

    def test_log_02_log_limit_parameter(self) -> None:
        """Tests querying logs with limit parameter."""
        for i in range(10):
            self.run_async(self.blackboard.add_log(level="INFO", message=f"Log entry {i}", sender="tester"))

        res = self.client.get("/api/project/logs?limit=4")
        self.assertEqual(res.status_code, 200)
        logs = res.json()
        self.assertEqual(len(logs), 4)
        self.assertEqual(logs[-1]["message"], "Log entry 9")

    def test_log_03_severity_filtering(self) -> None:
        """Tests log entries contain valid severity levels for frontend filtering."""
        self.run_async(self.blackboard.add_log(level="INFO", message="Normal operation", sender="orchestrator"))
        self.run_async(self.blackboard.add_log(level="WARNING", message="Slow response", sender="gemini_artist"))
        self.run_async(self.blackboard.add_log(level="ERROR", message="Shader syntax error", sender="validator"))

        res = self.client.get("/api/project/logs")
        self.assertEqual(res.status_code, 200)
        logs = res.json()
        levels = [l["level"] for l in logs]
        self.assertIn("INFO", levels)
        self.assertIn("WARNING", levels)
        self.assertIn("ERROR", levels)

    def test_log_04_gdd_inspector_payload(self) -> None:
        """Tests setting a GameDesignDocument and verifying GDD sections in GET /api/project/state."""
        gdd = GameDesignDocument(
            title="Cosmic Odyssey",
            high_concept="Deep space exploration with quantum puzzles",
            genre="space_shooter",
            target_engine="godot4",
            gameplay_mechanics=["laser_firing", "inertial_drift", "shield_boost"],
            narrative_lore="In the year 3042, humanity reached the Omega nebula.",
            quests=[QuestSpec(quest_id="q1", title="Find the Beacon", description="Scan coordinates")],
        )
        self.run_async(self.blackboard.set_gdd(gdd))

        res = self.client.get("/api/project/state")
        self.assertEqual(res.status_code, 200)
        state_data = res.json()
        self.assertIn("gdd", state_data)
        self.assertEqual(state_data["gdd"]["title"], "Cosmic Odyssey")
        self.assertEqual(state_data["gdd"]["high_concept"], "Deep space exploration with quantum puzzles")
        self.assertEqual(len(state_data["gdd"]["gameplay_mechanics"]), 3)
        self.assertEqual(state_data["gdd"]["quests"][0]["quest_id"], "q1")

    def test_log_05_scene_and_script_inspector(self) -> None:
        """Tests setting SceneAST and ScriptSpec and verifying inspection via GET /api/project/state."""
        scene = SceneAST(
            name="MainScene",
            path="scenes/main.tscn",
            nodes=[NodeAST(name="RootNode", type="Node2D")],
        )
        script = ScriptSpec(
            rel_path="scripts/player.gd",
            code="extends CharacterBody2D\nfunc _physics_process(delta):\n\tpass\n",
        )
        self.run_async(self.blackboard.add_scene(scene))
        self.run_async(self.blackboard.add_script(script))

        res = self.client.get("/api/project/state")
        self.assertEqual(res.status_code, 200)
        state_data = res.json()
        self.assertIn("scenes", state_data)
        self.assertIn("scripts", state_data)
        self.assertEqual(len(state_data["scenes"]), 1)
        self.assertEqual(state_data["scenes"]["scenes/main.tscn"]["name"], "MainScene")
        self.assertEqual(len(state_data["scripts"]), 1)
        self.assertEqual(state_data["scripts"]["scripts/player.gd"]["rel_path"], "scripts/player.gd")

    def test_log_06_token_metrics_inspection(self) -> None:
        """Tests updating token usage metrics and inspecting them in project state."""
        self.run_async(self.blackboard.record_token_metrics(
            provider="claude",
            prompt_tokens=1500,
            completion_tokens=800,
            cost_usd=0.045,
        ))

        res = self.client.get("/api/project/state")
        self.assertEqual(res.status_code, 200)
        state_data = res.json()
        self.assertIn("token_metrics", state_data)
        self.assertIn("claude", state_data["token_metrics"])
        self.assertEqual(state_data["token_metrics"]["claude"]["prompt_tokens"], 1500)
        self.assertEqual(state_data["token_metrics"]["claude"]["completion_tokens"], 800)


class TestPromptDirectingPanelFeatures(StudioTestCase):
    """
    F-GUI-05: Prompt & Directing Panel Features.
    Validates autonomous build launch triggers, engine selection,
    parameter tuning, verification triggers, and project export triggers.
    """

    def setUp(self) -> None:
        super().setUp()
        self.blackboard = Blackboard(project_name="DirectingTest")
        self.event_bus = EventBus()
        self.app = create_app(blackboard=self.blackboard, event_bus=self.event_bus)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.run_async(self.event_bus.close())
        super().tearDown()

    def test_direct_01_trigger_generation(self) -> None:
        """Tests POST /api/direct accepts a prompt and starts generation with HTTP 202."""
        res = self.client.post("/api/direct", json={
            "prompt": "Pixel art metroidvania with grappling hook",
            "target_engine": "godot4",
            "project_name": "GrappleVania",
            "mock": True,
        })
        self.assertIn(res.status_code, (200, 202))
        data = res.json()
        self.assertEqual(data["status"], "started")
        self.assertEqual(data["target_engine"], "godot4")
        self.assertEqual(data["project_name"], "GrappleVania")

    def test_direct_02_engine_target_selection(self) -> None:
        """Tests POST /api/direct supports selecting alternative game engine targets."""
        res = self.client.post("/api/direct", json={
            "prompt": "Retro arcade space shooter",
            "target_engine": "pygame",
            "mock": True,
        })
        self.assertIn(res.status_code, (200, 202))
        data = res.json()
        self.assertEqual(data["target_engine"], "pygame")

    def test_direct_03_genre_archetype_parameter(self) -> None:
        """Tests POST /api/direct accepts optional genre archetype parameter."""
        res = self.client.post("/api/direct", json={
            "prompt": "Top-down dungeon exploration",
            "genre": "top_down_rpg",
            "mock": True,
        })
        self.assertIn(res.status_code, (200, 202))
        data = res.json()
        self.assertEqual(data["status"], "started")

    def test_direct_04_mock_and_review_controls(self) -> None:
        """Tests POST /api/direct accepts mock mode and review iterations configuration."""
        res = self.client.post("/api/direct", json={
            "prompt": "2D Puzzle Platformer",
            "mock": True,
            "max_iterations": 5,
        })
        self.assertIn(res.status_code, (200, 202))

    def test_direct_05_trigger_verification_endpoint(self) -> None:
        """Tests POST /api/project/verify executes verification pipeline and returns report."""
        res = self.client.post("/api/project/verify", json={"fail_fast": False, "simulation_frames": 30})
        self.assertEqual(res.status_code, 200)
        report = res.json()
        self.assertIn("passed", report)
        self.assertIn("errors", report)
        self.assertIn("stages_executed", report)

    def test_direct_06_trigger_export_endpoint(self) -> None:
        """Tests POST /api/project/export creates canonical structure on disk."""
        target_export_dir = self.workspace_dir / "exported_game"
        res = self.client.post("/api/project/export", json={
            "output_dir": str(target_export_dir),
            "target_engine": "godot4",
            "overwrite": True,
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], "exported")
        self.assertTrue((target_export_dir / "scenes").is_dir())
        self.assertTrue((target_export_dir / "scripts").is_dir())


class TestVisualAestheticsAndDesktopCompliance(StudioTestCase):
    """
    Mandatory Aesthetics & Cross-Platform Desktop Contracts.
    Validates Departure Mono typography, zero external CDN links,
    and cross-platform desktop application path and launcher contracts.
    """

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.app = create_app()
        cls.client = TestClient(cls.app)

    def test_aesthetics_01_style_css_departure_mono_declaration(self) -> None:
        """Verifies Departure Mono font declaration in style.css."""
        res = self.client.get("/static/style.css")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Departure Mono", res.text)
        self.assertIn("--font-mono", res.text)

    def test_aesthetics_02_zero_external_cdn_references(self) -> None:
        """Verifies zero external CDN references across all static files (air-gap safety)."""
        static_files = ["/", "/static/style.css", "/static/app.js", "/static/agent_graph.js"]
        forbidden_patterns = [
            "unpkg.com",
            "cdnjs.cloudflare",
            "jsdelivr.net",
            "fonts.googleapis",
            "http://",
            "https://",
        ]
        for url in static_files:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 200, f"Failed to retrieve static asset: {url}")
            content_lower = res.text.lower()
            for pattern in forbidden_patterns:
                self.assertNotIn(
                    pattern,
                    content_lower,
                    f"Found forbidden external CDN link '{pattern}' in static asset: {url}",
                )

    def test_aesthetics_03_cross_platform_path_normalization(self) -> None:
        """Verifies normalize_cross_platform_path handles Windows backslashes and engine URLs."""
        # Windows backslash path
        p1 = normalize_cross_platform_path("assets\\sprites\\character.png")
        self.assertTrue(str(p1).endswith(os.path.join("assets", "sprites", "character.png")))

        # Godot engine res:// URL
        p2 = normalize_cross_platform_path("res://scripts/player.gd")
        self.assertTrue(str(p2).endswith(os.path.join("scripts", "player.gd")))

    def test_aesthetics_04_desktop_launcher_modes(self) -> None:
        """Verifies launch_desktop handles headless mode and window mode fallbacks safely."""
        self.assertTrue(launch_desktop(host="127.0.0.1", port=8080, mode="headless"))
        # Window mode should safely execute and return a boolean without throwing an exception
        res = launch_desktop(host="127.0.0.1", port=8080, mode="window")
        self.assertIsInstance(res, bool)

    def test_aesthetics_05_display_environment_detection(self) -> None:
        """Verifies is_display_available returns boolean without raising exceptions."""
        avail = is_display_available()
        self.assertIsInstance(avail, bool)


if __name__ == "__main__":
    import unittest
    unittest.main()
