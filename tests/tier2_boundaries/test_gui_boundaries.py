"""
tests/tier2_boundaries/test_gui_boundaries.py - Tier 2 Boundary, Stress, and Corner Case Tests
Opaque-box tests for boundary conditions, stress scenarios, and error handling across Milestone 4 GUI and CLI:
- Category 1: CLI Argument & Flag Boundaries (>=5 tests)
- Category 2: REST API Input & Payload Boundaries (>=5 tests)
- Category 3: WebSocket Streaming & Stress Boundaries (>=5 tests)
- Category 4: Cross-Platform Path & Desktop Environment Boundaries (>=5 tests)
"""
from __future__ import annotations

import asyncio
import os
import pathlib
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
    validate_arguments,
)
from studio.core.blackboard import Blackboard
from studio.core.event_bus import EventBus
from studio.core.models import (
    AssetItem,
    AssetManifest,
    GuiTelemetryEvent,
    ProjectStatus,
    StudioProjectState,
)
from studio.gui.desktop import (
    is_display_available,
    launch_browser,
    launch_desktop,
    launch_desktop_window,
    normalize_cross_platform_path,
)
from studio.gui.server import create_app
from studio.gui.websocket_manager import WebSocketTelemetryManager


class TestCliBoundaries(StudioTestCase):
    """
    Category 1: CLI Argument & Flag Boundaries.
    Validates extreme, invalid, and corner-case command-line configurations.
    """

    def setUp(self) -> None:
        super().setUp()
        self.parser = build_parser()

    def test_bnd_cli_01_port_lower_boundary(self) -> None:
        """Verifies port numbers <= 0 return exit code 1."""
        args_zero = self.parser.parse_args(["--port", "0"])
        self.assertEqual(validate_arguments(args_zero), 1)

        args_neg = self.parser.parse_args(["--port", "-80"])
        self.assertEqual(validate_arguments(args_neg), 1)

    def test_bnd_cli_02_port_upper_boundary(self) -> None:
        """Verifies port numbers > 65535 return exit code 1."""
        args_high = self.parser.parse_args(["--port", "65536"])
        self.assertEqual(validate_arguments(args_high), 1)

        args_extreme = self.parser.parse_args(["--port", "999999"])
        self.assertEqual(validate_arguments(args_extreme), 1)

    def test_bnd_cli_03_port_valid_boundaries(self) -> None:
        """Verifies valid boundary ports 1 and 65535 are accepted."""
        args_min = self.parser.parse_args(["--port", "1"])
        self.assertIsNone(validate_arguments(args_min))

        args_max = self.parser.parse_args(["--port", "65535"])
        self.assertIsNone(validate_arguments(args_max))

    def test_bnd_cli_04_port_non_numeric(self) -> None:
        """Verifies non-numeric port argument triggers argparse exit code 2."""
        code = cli_main(["--port", "not_a_port_number"])
        self.assertEqual(code, 2)

    def test_bnd_cli_05_headless_missing_prompt(self) -> None:
        """Verifies headless mode without prompt returns exit code 1."""
        args = self.parser.parse_args(["--headless"])
        self.assertEqual(validate_arguments(args), 1)

    def test_bnd_cli_06_headless_whitespace_prompt(self) -> None:
        """Verifies headless mode with empty or whitespace-only prompt returns exit code 1."""
        args_empty = self.parser.parse_args(["--headless", "--prompt", ""])
        self.assertEqual(validate_arguments(args_empty), 1)

        args_ws = self.parser.parse_args(["--headless", "--prompt", "   \t\n  "])
        self.assertEqual(validate_arguments(args_ws), 1)

    def test_bnd_cli_07_invalid_engine_choice(self) -> None:
        """Verifies invalid engine choice triggers argparse exit code 2."""
        code = cli_main(["--engine", "unreal_engine_5"])
        self.assertEqual(code, 2)

    def test_bnd_cli_08_iterations_boundaries(self) -> None:
        """Verifies iterations outside [1, 10] return exit code 1, while boundaries 1 and 10 succeed."""
        args_low = self.parser.parse_args(["--iterations", "0"])
        self.assertEqual(validate_arguments(args_low), 1)

        args_high = self.parser.parse_args(["--iterations", "11"])
        self.assertEqual(validate_arguments(args_high), 1)

        args_min = self.parser.parse_args(["--iterations", "1"])
        self.assertIsNone(validate_arguments(args_min))

        args_max = self.parser.parse_args(["--iterations", "10"])
        self.assertIsNone(validate_arguments(args_max))


class TestRestApiBoundaries(StudioTestCase):
    """
    Category 2: REST API Input & Payload Boundaries.
    Validates edge cases in HTTP requests: empty payloads, malformed JSON,
    path traversal attempts, missing resources, and concurrent run conflicts.
    """

    def setUp(self) -> None:
        super().setUp()
        self.blackboard = Blackboard(project_name="RestBoundaryTest")
        self.event_bus = EventBus()
        self.app = create_app(blackboard=self.blackboard, event_bus=self.event_bus)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.run_async(self.event_bus.close())
        super().tearDown()

    def test_bnd_rest_01_empty_prompt_400(self) -> None:
        """Tests POST /api/direct with empty prompt returns 400 Bad Request."""
        res = self.client.post("/api/direct", json={"prompt": ""})
        self.assertEqual(res.status_code, 400)
        self.assertIn("empty", res.json()["detail"].lower())

    def test_bnd_rest_02_whitespace_prompt_400(self) -> None:
        """Tests POST /api/direct with whitespace-only prompt returns 400 Bad Request."""
        res = self.client.post("/api/direct", json={"prompt": "   \n\t   "})
        self.assertEqual(res.status_code, 400)
        self.assertIn("empty", res.json()["detail"].lower())

    def test_bnd_rest_03_malformed_json_body_422(self) -> None:
        """Tests POST /api/direct with malformed JSON body returns 422 Unprocessable Entity."""
        res = self.client.post(
            "/api/direct",
            content="{'invalid_json': True, missing_quote}",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(res.status_code, 422)

    def test_bnd_rest_04_huge_prompt_payload(self) -> None:
        """Tests POST /api/direct with a very large 100KB prompt string handled safely."""
        large_prompt = "Build a space game with stars and asteroids. " * 2500  # ~100KB
        res = self.client.post("/api/direct", json={"prompt": large_prompt, "mock": True})
        self.assertIn(res.status_code, (200, 202))

    def test_bnd_rest_05_concurrent_run_conflict_409(self) -> None:
        """Tests POST /api/direct returns 409 Conflict if project status is IN_PROGRESS."""
        self.run_async(self.blackboard.set_status(ProjectStatus.IN_PROGRESS))
        res = self.client.post("/api/direct", json={"prompt": "Conflicting game idea"})
        self.assertEqual(res.status_code, 409)
        self.assertIn("already running", res.json()["detail"].lower())

    def test_bnd_rest_06_missing_asset_id_404(self) -> None:
        """Tests GET /api/project/assets/{id} returns 404 for non-existent asset ID."""
        res = self.client.get("/api/project/assets/non_existent_asset_id_xyz")
        self.assertEqual(res.status_code, 404)
        self.assertIn("not found", res.json()["detail"].lower())

    def test_bnd_rest_07_asset_path_traversal_attack_dot_dot(self) -> None:
        """Tests GET /api/project/assets/{id} blocks directory traversal attempts returning 404."""
        res = self.client.get("/api/project/assets/..%2F..%2Fetc%2Fpasswd")
        self.assertIn(res.status_code, (400, 404))

    def test_bnd_rest_08_asset_path_traversal_attack_slash(self) -> None:
        """Tests GET /api/project/assets/{id} blocks slash-separated paths in asset ID returning 404."""
        res = self.client.get("/api/project/assets/subdir/secret.txt")
        self.assertIn(res.status_code, (400, 404))

    def test_bnd_rest_09_asset_with_empty_or_none_bytes(self) -> None:
        """Tests GET /api/project/assets/{id} when asset has file_bytes=None does not throw 500 error."""
        asset = AssetItem(
            asset_id="empty_bytes_asset",
            asset_type="sprite",
            rel_path="assets/sprites/empty.png",
            file_bytes=None,
            checksum="empty_hash",
        )
        self.run_async(self.blackboard.add_asset_item(asset))

        res = self.client.get("/api/project/assets/empty_bytes_asset")
        # Should return 200 with 0 bytes or valid handled response, not 500 internal server error
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.content), 0)

    def test_bnd_rest_10_export_path_traversal_dot_dot(self) -> None:
        """Tests POST /api/project/export blocks path traversal in output_dir returning 400."""
        res = self.client.post("/api/project/export", json={"output_dir": "../../escaped_dir"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("path traversal", res.json()["detail"].lower())

    def test_bnd_rest_11_export_path_traversal_windows_backslash(self) -> None:
        """Tests POST /api/project/export blocks Windows backslash path traversal in output_dir."""
        res = self.client.post("/api/project/export", json={"output_dir": "..\\..\\escaped_dir"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("path traversal", res.json()["detail"].lower())

    def test_bnd_rest_12_verify_invalid_stage_400(self) -> None:
        """Tests POST /api/project/verify rejects unrecognized verification stage with 400."""
        res = self.client.post("/api/project/verify", json={"stages": ["non_existent_stage_xyz"]})
        self.assertEqual(res.status_code, 400)
        self.assertIn("invalid verification stage", res.json()["detail"].lower())

    def test_bnd_rest_13_verify_simulation_frames_range(self) -> None:
        """Tests POST /api/project/verify validates simulation_frames range (0..1000)."""
        res_neg = self.client.post("/api/project/verify", json={"simulation_frames": -5})
        self.assertEqual(res_neg.status_code, 422)

        res_huge = self.client.post("/api/project/verify", json={"simulation_frames": 99999})
        self.assertEqual(res_huge.status_code, 422)

    def test_bnd_rest_14_logs_limit_boundaries(self) -> None:
        """Tests GET /api/project/logs validates limit query parameter (1..1000)."""
        res_zero = self.client.get("/api/project/logs?limit=0")
        self.assertEqual(res_zero.status_code, 422)

        res_huge = self.client.get("/api/project/logs?limit=50000")
        self.assertEqual(res_huge.status_code, 422)

    def test_bnd_rest_15_empty_blackboard_state_schema(self) -> None:
        """Tests GET /api/project/state returns valid schema even when project is completely fresh."""
        res = self.client.get("/api/project/state")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["project_name"], "RestBoundaryTest")
        self.assertEqual(data["status"], "PENDING")
        self.assertIsNone(data["gdd"])
        self.assertIsNone(data["asset_manifest"])
        self.assertEqual(data["scenes"], {})
        self.assertEqual(data["scripts"], {})


class TestWebSocketBoundaries(StudioTestCase):
    """
    Category 3: WebSocket Streaming & Stress Boundaries.
    Validates extreme bursts, invalid payloads, client disconnects, and catch-up backlog.
    """

    def setUp(self) -> None:
        super().setUp()
        self.blackboard = Blackboard(project_name="WsBoundaryTest")
        self.event_bus = EventBus(telemetry_buffer_size=1000)
        self.app = create_app(blackboard=self.blackboard, event_bus=self.event_bus)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.run_async(self.event_bus.close())
        super().tearDown()

    def test_bnd_ws_01_rapid_telemetry_burst_500(self) -> None:
        """Emits 500 telemetry events rapidly; verifies WebSocket receives them without crash or socket drops."""
        burst_size = 500
        with self.client.websocket_connect("/api/ws/telemetry") as ws:
            for i in range(burst_size):
                ev = GuiTelemetryEvent(
                    event_type="log_chunk",
                    agent="orchestrator",
                    data={"index": i, "message": f"Burst telemetry payload #{i}"},
                )
                self.event_bus.publish_sync("telemetry.log_chunk", ev)

            received = 0
            for i in range(burst_size):
                msg = ws.receive_json()
                if msg["event_type"] == "log_chunk":
                    received += 1

            self.assertEqual(received, burst_size)

    def test_bnd_ws_02_invalid_non_json_client_message(self) -> None:
        """Client sends non-JSON text frame; verifies server handles safely and connection stays alive."""
        with self.client.websocket_connect("/api/ws/telemetry") as ws:
            # Send raw non-JSON text
            ws.send_text("THIS IS NOT JSON AT ALL {{[[")

            # Follow up with ping to verify connection remains operational
            ws.send_text("ping")
            reply = ws.receive_text()
            self.assertEqual(reply, "pong")

    def test_bnd_ws_03_client_disconnect_pruning(self) -> None:
        """Verifies WebSocketManager cleanly prunes disconnected clients without leaking sockets."""
        ws_mgr: WebSocketTelemetryManager = self.app.state.websocket_manager
        initial_count = len(ws_mgr.active_connections)

        with self.client.websocket_connect("/api/ws/telemetry") as ws:
            self.assertEqual(len(ws_mgr.active_connections), initial_count + 1)
            ws.send_text("ping")
            self.assertEqual(ws.receive_text(), "pong")

        # After exiting context, socket is closed and should be pruned
        self.assertEqual(len(ws_mgr.active_connections), initial_count)

    def test_bnd_ws_04_reconnection_catchup_backlog(self) -> None:
        """Verifies a newly connected client receives historical backlog events emitted prior to connecting."""
        for i in range(5):
            ev = GuiTelemetryEvent(
                event_type="build_progress",
                agent="orchestrator",
                data={"step": i, "progress": i * 20},
            )
            self.event_bus.publish_sync("telemetry.build_progress", ev)

        with self.client.websocket_connect("/api/ws/telemetry") as ws:
            # First message should be historical event
            msg = ws.receive_json()
            self.assertEqual(msg["event_type"], "build_progress")
            self.assertEqual(msg["data"]["step"], 0)

    def test_bnd_ws_05_heartbeat_json_and_plaintext_variations(self) -> None:
        """Verifies both plaintext 'ping' and JSON '{"type": "ping"}' receive proper responses."""
        with self.client.websocket_connect("/api/ws/telemetry") as ws:
            # Plaintext
            ws.send_text("ping")
            self.assertEqual(ws.receive_text(), "pong")

            # JSON
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json(), {"type": "pong"})

    def test_bnd_ws_06_concurrent_broadcast_to_multiple_clients(self) -> None:
        """Verifies that multiple concurrent WebSocket clients all receive broadcast events."""
        with self.client.websocket_connect("/api/ws/telemetry") as ws1:
            with self.client.websocket_connect("/api/ws/telemetry") as ws2:
                event = GuiTelemetryEvent(
                    event_type="pipeline_completed",
                    state="complete",
                    data={"result": "all_green"},
                )
                self.event_bus.publish_sync("telemetry.pipeline_completed", event)

                msg1 = ws1.receive_json()
                msg2 = ws2.receive_json()
                self.assertEqual(msg1["event_type"], "pipeline_completed")
                self.assertEqual(msg2["event_type"], "pipeline_completed")
                self.assertEqual(msg1["data"]["result"], "all_green")
                self.assertEqual(msg2["data"]["result"], "all_green")


class TestDesktopAndPathBoundaries(StudioTestCase):
    """
    Category 4: Cross-Platform Path & Desktop Environment Boundaries.
    Validates Windows/Unix path normalization, drive letters, path traversal guards,
    and display server environment detection.
    """

    def test_bnd_path_01_windows_backslashes_deep_nesting(self) -> None:
        """Verifies normalizing deeply nested Windows backslash paths."""
        p = normalize_cross_platform_path("assets\\audio\\music\\boss\\intro.wav")
        expected_suffix = os.path.join("assets", "audio", "music", "boss", "intro.wav")
        self.assertTrue(str(p).endswith(expected_suffix))

    def test_bnd_path_02_windows_drive_letter_handling(self) -> None:
        """Verifies handling paths with Windows drive letters (e.g. C:\\Projects)."""
        p = normalize_cross_platform_path("C:\\Projects\\AIStudio\\game")
        # Ensure path normalizes without unhandled crash
        self.assertIsInstance(p, Path)

    def test_bnd_path_03_mixed_slashes_and_double_separators(self) -> None:
        """Verifies normalizing paths containing mixed slashes and redundant separators."""
        p = normalize_cross_platform_path("assets//sprites\\\\ui///button.png")
        expected_suffix = os.path.join("assets", "sprites", "ui", "button.png")
        self.assertTrue(str(p).endswith(expected_suffix))

    def test_bnd_path_04_engine_res_scheme_stripping(self) -> None:
        """Verifies res:// URL prefix is stripped and converted to clean relative path."""
        p = normalize_cross_platform_path("res://scenes/levels/level_01.tscn")
        expected_suffix = os.path.join("scenes", "levels", "level_01.tscn")
        self.assertTrue(str(p).endswith(expected_suffix))

    def test_bnd_path_05_base_dir_traversal_detection(self) -> None:
        """Verifies normalize_cross_platform_path raises ValueError on traversal escaping base_dir."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Traversal escape
            with self.assertRaises(ValueError):
                normalize_cross_platform_path("../../../etc/shadow", base_dir=tmp_dir)

            with self.assertRaises(ValueError):
                normalize_cross_platform_path("..\\..\\Windows\\System32", base_dir=tmp_dir)

    def test_bnd_path_06_empty_string_normalization(self) -> None:
        """Verifies empty string input resolves safely to current or base directory."""
        p_cwd = normalize_cross_platform_path("")
        self.assertEqual(p_cwd, Path(".").resolve())

        with tempfile.TemporaryDirectory() as tmp_dir:
            p_base = normalize_cross_platform_path("", base_dir=tmp_dir)
            self.assertEqual(p_base, Path(tmp_dir).resolve())

    def test_bnd_env_07_display_detection_with_display_env(self) -> None:
        """Verifies is_display_available returns True when DISPLAY is set."""
        orig_disp = os.environ.get("DISPLAY")
        try:
            os.environ["DISPLAY"] = ":99"
            self.assertTrue(is_display_available())
        finally:
            if orig_disp is not None:
                os.environ["DISPLAY"] = orig_disp
            else:
                os.environ.pop("DISPLAY", None)

    def test_bnd_env_08_desktop_window_headless_fallback(self) -> None:
        """Verifies launch_desktop_window safely returns False when no display server is available."""
        orig_disp = os.environ.get("DISPLAY")
        orig_way = os.environ.get("WAYLAND_DISPLAY")
        try:
            os.environ.pop("DISPLAY", None)
            os.environ.pop("WAYLAND_DISPLAY", None)
            # In headless environment, launch_desktop_window must return False without crashing
            res = launch_desktop_window(host="127.0.0.1", port=8080)
            self.assertFalse(res)
        finally:
            if orig_disp is not None:
                os.environ["DISPLAY"] = orig_disp
            if orig_way is not None:
                os.environ["WAYLAND_DISPLAY"] = orig_way


if __name__ == "__main__":
    import unittest
    unittest.main()
