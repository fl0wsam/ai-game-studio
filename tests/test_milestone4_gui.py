"""
tests/test_milestone4_gui.py - Unit Test Suite for Milestone 4 Visual Studio GUI & CLI
Comprehensive unit tests covering:
1. CLI Argument Parsing and Validation (defaults, custom flags, desktop/browser modes, boundaries).
2. Static File Serving (HTML, CSS, JS, MIME types, zero CDN, Departure Mono typography).
3. FastAPI REST Endpoints (/health, /state, /status, /direct, /assets, /verify, /export, error handling).
4. Real-Time WebSocket Telemetry Streaming (catch-up replay, broadcast, ping/pong, bursts).
5. Cross-Platform Desktop & Path Utilities (display detection, path normalization, traversal guards).
6. Headless CLI Execution (end-to-end mock generation, export, and verification).
"""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from fastapi.testclient import TestClient

from studio.cli import (
    build_parser,
    execute_headless_pipeline,
    main as cli_main,
    validate_arguments,
)
from studio.core.blackboard import Blackboard
from studio.core.event_bus import EventBus
from studio.core.models import (
    AssetItem,
    AssetManifest,
    EngineTarget,
    GameDesignDocument,
    GuiTelemetryEvent,
    ProjectStatus,
    StudioProjectState,
)
from studio.gui.desktop import (
    is_display_available,
    launch_desktop,
    launch_desktop_window,
    normalize_cross_platform_path,
)
from studio.gui.server import create_app
from studio.gui.websocket_manager import WebSocketTelemetryManager


class TestCliArgumentParser(unittest.TestCase):
    """Exercises CLI argument parsing, defaults, flags, and boundary validations (F-GUI-01)."""

    def setUp(self) -> None:
        self.parser = build_parser()

    def test_cli_default_arguments(self) -> None:
        """Verifies default argument values when invoked without flags."""
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

    def test_cli_custom_flags(self) -> None:
        """Verifies parsing of customized CLI parameters."""
        args = self.parser.parse_args([
            "--host", "0.0.0.0",
            "--port", "9090",
            "--engine", "pygame",
            "--genre", "space_shooter",
            "--iterations", "5",
            "--project-name", "CustomGame",
            "--log-level", "DEBUG",
        ])
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 9090)
        self.assertEqual(args.engine, "pygame")
        self.assertEqual(args.genre, "space_shooter")
        self.assertEqual(args.iterations, 5)
        self.assertEqual(args.project_name, "CustomGame")
        self.assertEqual(args.log_level, "DEBUG")

    def test_cli_desktop_mode_flags(self) -> None:
        """Verifies desktop window, browser, and no-browser flags."""
        args_window = self.parser.parse_args(["--window"])
        self.assertTrue(args_window.window)

        args_browser = self.parser.parse_args(["--browser"])
        self.assertTrue(args_browser.browser)

        args_no_browser = self.parser.parse_args(["--no-browser"])
        self.assertTrue(args_no_browser.no_browser)

    def test_cli_headless_mode_flag(self) -> None:
        """Verifies headless mode parsing with prompt."""
        args = self.parser.parse_args(["--headless", "--prompt", "Cyber RPG", "--no-mock"])
        self.assertTrue(args.headless)
        self.assertEqual(args.prompt, "Cyber RPG")
        self.assertFalse(args.mock)

    def test_cli_invalid_port_validation(self) -> None:
        """Verifies that out-of-range port numbers return exit code 1."""
        args_zero = self.parser.parse_args(["--port", "0"])
        self.assertEqual(validate_arguments(args_zero), 1)

        args_neg = self.parser.parse_args(["--port", "-5"])
        self.assertEqual(validate_arguments(args_neg), 1)

        args_huge = self.parser.parse_args(["--port", "70000"])
        self.assertEqual(validate_arguments(args_huge), 1)

    def test_cli_headless_missing_prompt_validation(self) -> None:
        """Verifies that headless mode without a prompt returns exit code 1."""
        args_none = self.parser.parse_args(["--headless"])
        self.assertEqual(validate_arguments(args_none), 1)

        args_empty = self.parser.parse_args(["--headless", "--prompt", "   "])
        self.assertEqual(validate_arguments(args_empty), 1)

    def test_cli_iterations_boundary_validation(self) -> None:
        """Verifies that iterations < 1 or > 10 fail validation."""
        args_low = self.parser.parse_args(["--iterations", "0"])
        self.assertEqual(validate_arguments(args_low), 1)

        args_high = self.parser.parse_args(["--iterations", "15"])
        self.assertEqual(validate_arguments(args_high), 1)


class TestStaticFileServing(unittest.TestCase):
    """Verifies static asset hosting, MIME types, Departure Mono typography, and zero CDN."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app()
        cls.client = TestClient(cls.app)

    def test_serve_root_index_html(self) -> None:
        """Tests that GET / serves index.html with HTTP 200 OK and text/html MIME type."""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers.get("content-type", ""))
        self.assertIn("AI GAME STUDIO", res.text)
        self.assertIn("agentGraphCanvas", res.text)

    def test_serve_style_css_content_and_mime(self) -> None:
        """Tests that GET /static/style.css returns 200 with text/css MIME type."""
        res = self.client.get("/static/style.css")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/css", res.headers.get("content-type", ""))
        self.assertIn("--font-mono", res.text)

    def test_style_css_declares_departure_mono(self) -> None:
        """Verifies mandatory Departure Mono font family declaration in style.css."""
        res = self.client.get("/static/style.css")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Departure Mono", res.text)
        self.assertIn("JetBrains Mono", res.text)

    def test_zero_external_cdn_in_html(self) -> None:
        """Verifies zero external CDN links in index.html for 100% air-gap safety."""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        content = res.text.lower()
        forbidden_tokens = ["unpkg.com", "cdnjs.cloudflare", "jsdelivr.net", "fonts.googleapis", "http://", "https://"]
        for token in forbidden_tokens:
            self.assertNotIn(token, content, f"Found forbidden external CDN link: '{token}'")

    def test_serve_app_js_content_and_mime(self) -> None:
        """Tests that GET /static/app.js returns 200 with javascript MIME type."""
        res = self.client.get("/static/app.js")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(any(t in res.headers.get("content-type", "") for t in ("javascript", "text/plain")))
        self.assertIn("StudioApp", res.text)

    def test_serve_agent_graph_js_and_typography(self) -> None:
        """Tests that GET /static/agent_graph.js returns 200 and incorporates Departure Mono."""
        res = self.client.get("/static/agent_graph.js")
        self.assertEqual(res.status_code, 200)
        self.assertIn("AgentGraph", res.text)
        self.assertIn("Departure Mono", res.text)


class TestRestApiEndpoints(unittest.TestCase):
    """Exercises FastAPI REST API routes: /health, /state, /status, /direct, /assets, /verify, /export."""

    def setUp(self) -> None:
        self.blackboard = Blackboard(project_name="RestApiTestProject")
        self.event_bus = EventBus()
        self.app = create_app(blackboard=self.blackboard, event_bus=self.event_bus)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        asyncio.run(self.event_bus.close())

    def test_api_health_endpoint(self) -> None:
        """Tests GET /api/health returns 200 OK with status='healthy' and metadata."""
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["version"], "0.1.0")
        self.assertIn("uptime_seconds", data)
        self.assertEqual(data["active_project"], "RestApiTestProject")

    def test_api_project_state_endpoint(self) -> None:
        """Tests GET /api/project/state returns 200 OK with serialized project state."""
        res = self.client.get("/api/project/state")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["project_name"], "RestApiTestProject")
        self.assertIn("status", data)
        self.assertIn("active_phase", data)
        self.assertIn("scenes", data)
        self.assertIn("scripts", data)

    def test_api_project_status_agents(self) -> None:
        """Tests GET /api/project/status returns operational status of all agents."""
        res = self.client.get("/api/project/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("agents", data)
        agents = data["agents"]
        self.assertIn("orchestrator", agents)
        self.assertIn("openai_director", agents)
        self.assertIn("gemini_artist", agents)
        self.assertIn("claude_engineer", agents)
        self.assertIn("validator", agents)

    def test_api_direct_valid_prompt(self) -> None:
        """Tests POST /api/direct accepts a prompt and starts generation."""
        res = self.client.post("/api/direct", json={
            "prompt": "2D neon runner with lasers",
            "target_engine": "godot4",
            "mock": True,
        })
        self.assertIn(res.status_code, (200, 202))
        data = res.json()
        self.assertEqual(data["status"], "started")
        self.assertEqual(data["target_engine"], "godot4")

    def test_api_direct_empty_prompt_400(self) -> None:
        """Tests POST /api/direct rejects empty prompt with 400 Bad Request."""
        res = self.client.post("/api/direct", json={"prompt": "    \t"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("empty", res.json()["detail"].lower())

    def test_api_direct_in_progress_conflict_409(self) -> None:
        """Tests POST /api/direct returns 409 Conflict if pipeline is already running."""
        asyncio.run(self.blackboard.set_status(ProjectStatus.IN_PROGRESS))
        res = self.client.post("/api/direct", json={"prompt": "Another game"})
        self.assertEqual(res.status_code, 409)

    def test_api_project_assets_success(self) -> None:
        """Tests GET /api/project/assets/{id} returns binary payload with correct headers."""
        asset = AssetItem(
            asset_id="player_sprite",
            asset_type="sprite",
            rel_path="assets/sprites/player.png",
            file_bytes=b"\x89PNG\r\n\x1a\nfake_png_data",
            checksum="abcd1234sha",
        )
        asyncio.run(self.blackboard.add_asset_item(asset))

        res = self.client.get("/api/project/assets/player_sprite")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, b"\x89PNG\r\n\x1a\nfake_png_data")
        self.assertEqual(res.headers.get("content-type"), "image/png")
        self.assertEqual(res.headers.get("x-asset-type"), "sprite")
        self.assertEqual(res.headers.get("x-asset-checksum"), "abcd1234sha")

    def test_api_project_assets_metadata_param(self) -> None:
        """Tests GET /api/project/assets/{id}?metadata=true returns JSON representation."""
        asset = AssetItem(
            asset_id="laser_sfx",
            asset_type="audio_sfx",
            rel_path="audio/sfx/laser.wav",
            file_bytes=b"RIFFWAVEfmt ",
            checksum="wave1234",
        )
        asyncio.run(self.blackboard.add_asset_item(asset))

        res = self.client.get("/api/project/assets/laser_sfx?metadata=true")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "application/json")
        data = res.json()
        self.assertEqual(data["asset_id"], "laser_sfx")
        self.assertEqual(data["rel_path"], "audio/sfx/laser.wav")

    def test_api_project_assets_not_found_and_traversal(self) -> None:
        """Tests GET /api/project/assets/{id} returns 404 for missing IDs and path traversal."""
        # Non-existent asset
        res_missing = self.client.get("/api/project/assets/nonexistent_xyz")
        self.assertEqual(res_missing.status_code, 404)

        # Path traversal attempt
        res_trav = self.client.get("/api/project/assets/..%2F..%2Fetc%2Fpasswd")
        self.assertIn(res_trav.status_code, (404, 400))


class TestVerificationAndExportApis(unittest.TestCase):
    """Exercises POST /api/project/verify and POST /api/project/export endpoints."""

    def setUp(self) -> None:
        self.blackboard = Blackboard(project_name="ExportVerifyTest")
        self.event_bus = EventBus()
        self.app = create_app(blackboard=self.blackboard, event_bus=self.event_bus)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        asyncio.run(self.event_bus.close())

    def test_api_project_verify(self) -> None:
        """Tests POST /api/project/verify runs verification pipeline and returns report."""
        res = self.client.post("/api/project/verify", json={"fail_fast": False})
        self.assertEqual(res.status_code, 200)
        report = res.json()
        self.assertIn("passed", report)
        self.assertIn("errors", report)
        self.assertIn("stages_executed", report)

    def test_api_project_verify_invalid_stage_400(self) -> None:
        """Tests POST /api/project/verify returns 400 for unknown verification stage."""
        res = self.client.post("/api/project/verify", json={"stages": ["invalid_stage_xyz"]})
        self.assertEqual(res.status_code, 400)

    def test_api_project_export_success(self) -> None:
        """Tests POST /api/project/export creates canonical hierarchy and manifest."""
        with tempfile.TemporaryDirectory(prefix="export_test_") as tmp_dir:
            res = self.client.post("/api/project/export", json={
                "output_dir": tmp_dir,
                "target_engine": "godot4",
                "overwrite": True,
            })
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertTrue(data["success"])
            self.assertEqual(data["status"], "exported")
            self.assertEqual(data["engine"], "godot4")

            # Check that canonical directories were created on disk
            out_p = Path(tmp_dir)
            self.assertTrue((out_p / "assets" / "sprites").is_dir())
            self.assertTrue((out_p / "audio" / "sfx").is_dir())
            self.assertTrue((out_p / "scenes").is_dir())
            self.assertTrue((out_p / "scripts").is_dir())

    def test_api_project_export_path_traversal_400(self) -> None:
        """Tests POST /api/project/export blocks directory traversal outside root."""
        res = self.client.post("/api/project/export", json={"output_dir": "../../escaped_dir"})
        self.assertEqual(res.status_code, 400)


class TestWebSocketTelemetry(unittest.TestCase):
    """Exercises WebSocket live telemetry broadcasting, historical replay, and ping/pong."""

    def setUp(self) -> None:
        self.blackboard = Blackboard(project_name="WebSocketTest")
        self.event_bus = EventBus()
        self.app = create_app(blackboard=self.blackboard, event_bus=self.event_bus)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        asyncio.run(self.event_bus.close())

    def test_ws_connect_and_catchup_history(self) -> None:
        """Verifies that new WebSocket clients immediately receive historical telemetry backlog."""
        event1 = GuiTelemetryEvent(
            event_type="agent_state",
            agent="openai_director",
            state="active",
            data={"action": "Drafting GDD"},
        )
        self.event_bus.publish_sync("telemetry.agent_state", event1)

        with self.client.websocket_connect("/api/ws/telemetry") as ws:
            msg = ws.receive_json()
            self.assertEqual(msg["event_type"], "agent_state")
            self.assertEqual(msg["agent"], "openai_director")
            self.assertEqual(msg["state"], "active")

    def test_ws_live_telemetry_broadcast(self) -> None:
        """Verifies that live GuiTelemetryEvents emitted on EventBus are received by connected clients."""
        with self.client.websocket_connect("/api/ws/telemetry") as ws:
            event = GuiTelemetryEvent(
                event_type="asset_generated",
                agent="gemini_artist",
                state="complete",
                data={"asset_id": "laser_beam", "asset_type": "sprite"},
            )
            self.event_bus.publish_sync("telemetry.asset_generated", event)

            msg = ws.receive_json()
            self.assertEqual(msg["event_type"], "asset_generated")
            self.assertEqual(msg["agent"], "gemini_artist")
            self.assertEqual(msg["data"]["asset_id"], "laser_beam")

    def test_ws_ping_pong_text(self) -> None:
        """Verifies plaintext ping/pong heartbeat handling."""
        with self.client.websocket_connect("/api/ws/telemetry") as ws:
            ws.send_text("ping")
            reply = ws.receive_text()
            self.assertEqual(reply, "pong")

    def test_ws_ping_pong_json(self) -> None:
        """Verifies JSON-wrapped ping/pong heartbeat handling."""
        with self.client.websocket_connect("/api/ws/telemetry") as ws:
            ws.send_json({"type": "ping"})
            reply = ws.receive_json()
            self.assertEqual(reply, {"type": "pong"})

    def test_ws_rapid_telemetry_burst(self) -> None:
        """Verifies that bursts of 20 rapid telemetry events are delivered without socket drops."""
        with self.client.websocket_connect("/api/ws/telemetry") as ws:
            for i in range(20):
                ev = GuiTelemetryEvent(
                    event_type="log_chunk",
                    agent="orchestrator",
                    data={"level": "INFO", "message": f"Burst message {i}"},
                )
                self.event_bus.publish_sync("telemetry.log_chunk", ev)

            received_count = 0
            for i in range(20):
                msg = ws.receive_json()
                if msg["event_type"] == "log_chunk":
                    received_count += 1
            self.assertEqual(received_count, 20)


class TestDesktopAndCrossPlatformUtilities(unittest.TestCase):
    """Exercises cross-platform desktop window runners and path sanitization utilities."""

    def test_cross_platform_path_normalization(self) -> None:
        """Verifies converting Windows backslashes and engine URLs to standardized Path."""
        p1 = normalize_cross_platform_path("assets\\sprites\\player.png")
        self.assertTrue(str(p1).endswith(os.path.join("assets", "sprites", "player.png")))

        p2 = normalize_cross_platform_path("res://scenes/main.tscn")
        self.assertTrue(str(p2).endswith(os.path.join("scenes", "main.tscn")))

    def test_cross_platform_path_traversal_guard(self) -> None:
        """Verifies directory traversal escapes raise ValueError when base_dir is enforced."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(ValueError):
                normalize_cross_platform_path("../../etc/shadow", base_dir=tmp_dir)

    def test_desktop_display_detection(self) -> None:
        """Verifies is_display_available returns boolean without raising exceptions."""
        avail = is_display_available()
        self.assertIsInstance(avail, bool)

    def test_desktop_launcher_fallback(self) -> None:
        """Verifies launch_desktop handles headless mode and window mode fallbacks safely."""
        # Headless mode should return True immediately
        self.assertTrue(launch_desktop(host="127.0.0.1", port=8080, mode="headless"))

        # Window mode in headless environment returns boolean (False or True if browser fallback runs)
        res = launch_desktop(host="127.0.0.1", port=8080, mode="window")
        self.assertIsInstance(res, bool)


class TestCliHeadlessExecution(unittest.TestCase):
    """Exercises end-to-end headless CLI pipeline execution (F-GUI-01)."""

    def test_cli_headless_build_success(self) -> None:
        """Tests executing complete headless build with mock provider."""
        parser = build_parser()
        with tempfile.TemporaryDirectory(prefix="cli_headless_test_") as tmp_dir:
            args = parser.parse_args([
                "--headless",
                "--prompt", "Cyber Ninja",
                "--engine", "godot4",
                "--output-dir", tmp_dir,
                "--mock",
                "--verify",
            ])
            exit_code = asyncio.run(execute_headless_pipeline(args))
            self.assertEqual(exit_code, 0)

            # Assert exported files were written to disk
            out_p = Path(tmp_dir)
            self.assertTrue((out_p / "project_manifest.json").is_file())
            self.assertTrue((out_p / "docs" / "GDD.md").is_file())

    def test_cli_main_entrypoint_exit_codes(self) -> None:
        """Tests that invoking main with invalid arguments returns non-zero exit code."""
        # Headless without prompt returns 1
        code_no_prompt = cli_main(["--headless"])
        self.assertEqual(code_no_prompt, 1)

        # Invalid port returns 1
        code_bad_port = cli_main(["--port", "99999"])
        self.assertEqual(code_bad_port, 1)


if __name__ == "__main__":
    unittest.main()
