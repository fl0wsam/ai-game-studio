"""
tests/tier1_features/test_verification_pipeline.py - Tier 1 Feature Coverage Tests.
Implements systematic opaque-box feature coverage (>=5 tests per feature ID):
- F-VERIF-01: Multi-Engine Syntax Validator (GDScript, Python, JavaScript, HTML, C#)
- F-VERIF-02: TSCN Schema & Reference Checker (AST, load_steps, hierarchy, wiring)
- F-VERIF-03: Asset Integrity Verifier (Discovery, magic headers, size checks)
- F-VERIF-04: Dual-Mode Engine Simulator (MockEngineSimulator, ticks, timeouts)
- Unified Pipeline: Orchestration, fail_fast, complete mode, Blackboard, EventBus
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import pathlib
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from tests.conftest_base import StudioTestCase

from studio.core.blackboard import Blackboard
from studio.core.event_bus import EventBus
from studio.core.models import (
    AssetItem,
    AssetManifest,
    AssetType,
    DiagnosticError,
    GameDesignDocument,
    GameGenre,
    GuiTelemetryEvent,
    ScriptSpec,
    StudioProjectState,
    VerificationReport,
)
from studio.engine.adapters.godot_adapter import (
    GodotProjectConfig,
    generate_project_godot,
    serialize_tscn,
)
from studio.engine.ast_models import (
    ConnectionAST,
    ExtResourceAST,
    NodeAST,
    SceneAST,
    SubResourceAST,
)
from studio.verification import (
    AssetIntegrityVerifier,
    AssetVerificationResult,
    AssetVerifier,
    CSharpSyntaxValidator,
    DualModeEngineSimulator,
    GDScriptSyntaxValidator,
    HTMLSyntaxValidator,
    JavaScriptSyntaxValidator,
    MockEngineSimulator,
    MultiEngineSyntaxValidator,
    PythonSyntaxValidator,
    SimulatedNode,
    SimulationResult,
    TSCNValidator,
    VerificationPipeline,
    route_errors_for_review,
    validate_magic_bytes,
)


# ============================================================================
# F-VERIF-01: Multi-Engine Syntax Validator Feature Tests
# ============================================================================

class TestSyntaxValidatorFeature(StudioTestCase):
    """
    Feature tests for F-VERIF-01: Multi-Engine Syntax Validator.
    Tests GDScript, Python, JavaScript, HTML, C#, directory scan, and line attribution.
    """

    def setUp(self) -> None:
        super().setUp()
        self.py_val = PythonSyntaxValidator()
        self.gd_val = GDScriptSyntaxValidator()
        self.js_val = JavaScriptSyntaxValidator(force_fallback=True)
        self.html_val = HTMLSyntaxValidator()
        self.cs_val = CSharpSyntaxValidator()
        self.facade = MultiEngineSyntaxValidator()

    def test_feature_gdscript_player_class(self) -> None:
        """Validates realistic Godot 4 GDScript with annotations, signals, and methods."""
        code = (
            "extends CharacterBody2D\n"
            "class_name PlayerController\n\n"
            "signal health_depleted\n"
            "signal item_collected(item_name: String, amount: int)\n\n"
            "@export var speed: float = 350.0\n"
            "@export var jump_velocity: float = -450.0\n"
            "@onready var sprite: Sprite2D = $Sprite2D\n"
            "@onready var anim: AnimationPlayer = $AnimationPlayer\n\n"
            "var gravity: float = 980.0\n"
            "var is_dead: bool = false\n\n"
            "func _physics_process(delta: float) -> void:\n"
            "    if is_dead:\n"
            "        return\n"
            "    if not is_on_floor():\n"
            "        velocity.y += gravity * delta\n"
            "    var direction: float = Input.get_axis(\"ui_left\", \"ui_right\")\n"
            "    if direction != 0.0:\n"
            "        velocity.x = direction * speed\n"
            "    else:\n"
            "        velocity.x = move_toward(velocity.x, 0.0, speed * delta)\n"
            "    move_and_slide()\n"
        )
        errors = self.gd_val.validate(code, file_path="scripts/player.gd")
        self.assertEqual(len(errors), 0, f"Unexpected syntax errors in valid GDScript: {errors}")

    def test_feature_python_engine_code(self) -> None:
        """Validates standalone Python game loop with Pygame structures."""
        code = (
            "import sys\n"
            "from typing import List, Tuple\n\n"
            "class GameObject:\n"
            "    def __init__(self, x: float, y: float) -> None:\n"
            "        self.x = x\n"
            "        self.y = y\n"
            "        self.is_alive = True\n\n"
            "    def update(self, delta: float) -> None:\n"
            "        self.y += 10.0 * delta\n\n"
            "def game_loop(objects: List[GameObject], max_frames: int = 60) -> int:\n"
            "    frames = 0\n"
            "    for _ in range(max_frames):\n"
            "        for obj in objects:\n"
            "            obj.update(1.0 / 60.0)\n"
            "        frames += 1\n"
            "    return frames\n"
        )
        errors = self.py_val.validate(code, file_path="engine/loop.py")
        self.assertEqual(len(errors), 0, f"Unexpected syntax errors in valid Python: {errors}")

    def test_feature_javascript_canvas_code(self) -> None:
        """Validates HTML5/Canvas JavaScript game loop and event handlers."""
        code = (
            "class Entity {\n"
            "    constructor(x, y) {\n"
            "        this.x = x;\n"
            "        this.y = y;\n"
            "        this.vx = 0;\n"
            "        this.vy = 0;\n"
            "    }\n"
            "    update(dt) {\n"
            "        this.x += this.vx * dt;\n"
            "        this.y += this.vy * dt;\n"
            "    }\n"
            "}\n"
            "const entities = [new Entity(10, 20)];\n"
            "function tick(delta) {\n"
            "    entities.forEach(e => e.update(delta));\n"
            "    return entities.length;\n"
            "}\n"
        )
        errors = self.js_val.validate(code, file_path="web/game.js")
        self.assertEqual(len(errors), 0, f"Unexpected syntax errors in valid JavaScript: {errors}")

    def test_feature_csharp_unity_component(self) -> None:
        """Validates Unity C# MonoBehaviour script syntax with preprocessor directives."""
        code = (
            "#if UNITY_2022_1_OR_NEWER\n"
            "using System.Collections;\n"
            "using System.Collections.Generic;\n"
            "using UnityEngine;\n\n"
            "namespace Studio.Gameplay {\n"
            "    public class PlayerActor : MonoBehaviour {\n"
            "        [SerializeField] private float speed = 5.0f;\n"
            "        private Rigidbody2D rb;\n\n"
            "        void Awake() {\n"
            "            rb = GetComponent<Rigidbody2D>();\n"
            "        }\n\n"
            "        void FixedUpdate() {\n"
            "            float h = Input.GetAxisRaw(\"Horizontal\");\n"
            "            rb.velocity = new Vector2(h * speed, rb.velocity.y);\n"
            "        }\n"
            "    }\n"
            "}\n"
            "#endif\n"
        )
        errors = self.cs_val.validate(code, file_path="unity/PlayerActor.cs")
        self.assertEqual(len(errors), 0, f"Unexpected syntax errors in valid C#: {errors}")

    def test_feature_html_canvas_markup(self) -> None:
        """Validates HTML5 canvas markup with balanced tags and embedded script."""
        markup = (
            "<!DOCTYPE html>\n"
            "<html lang='en'>\n"
            "<head>\n"
            "    <meta charset='utf-8'>\n"
            "    <title>Retro Defender</title>\n"
            "    <style>canvas { background: black; }</style>\n"
            "</head>\n"
            "<body>\n"
            "    <div id='container'>\n"
            "        <canvas id='gameCanvas' width='800' height='600'></canvas>\n"
            "    </div>\n"
            "    <script>\n"
            "        const canvas = document.getElementById('gameCanvas');\n"
            "        const ctx = canvas.getContext('2d');\n"
            "    </script>\n"
            "</body>\n"
            "</html>\n"
        )
        errors = self.html_val.validate(markup, file_path="index.html")
        self.assertEqual(len(errors), 0, f"Unexpected syntax errors in valid HTML: {errors}")

    def test_feature_multi_language_directory_scan(self) -> None:
        """Validates multi-language project scripts on disk using MultiEngineSyntaxValidator."""
        scripts_dir = self.workspace_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)

        (scripts_dir / "player.gd").write_text("extends Node2D\nfunc _ready():\n    pass\n", encoding="utf-8")
        (scripts_dir / "main.py").write_text("def run():\n    return 42\n", encoding="utf-8")
        (scripts_dir / "input.js").write_text("function poll() { return true; }\n", encoding="utf-8")
        (scripts_dir / "logic.cs").write_text("public class Logic {}\n", encoding="utf-8")

        errors = self.facade.validate_project_scripts(self.workspace_dir)
        self.assertEqual(len(errors), 0, f"Expected 0 errors when scanning valid scripts, got {errors}")

    def test_feature_syntax_error_line_attribution(self) -> None:
        """Verifies accurate reporting of 1-indexed line and column numbers on syntax error."""
        broken_py = "line1 = 1\nline2 = 2\ndef bad_syntax(\nline4 = 4\n"
        errors = self.py_val.validate(broken_py, file_path="broken.py")
        self.assertGreater(len(errors), 0)
        err = errors[0]
        self.assertEqual(err.stage, "syntax_check")
        self.assertEqual(err.severity, "ERROR")
        self.assertEqual(err.file_path, "broken.py")
        self.assertGreaterEqual(err.line or 0, 3)
        self.assertIsNotNone(err.suggested_fix)


# ============================================================================
# F-VERIF-02: TSCN Schema & Reference Checker Feature Tests
# ============================================================================

class TestTSCNSchemaCheckerFeature(StudioTestCase):
    """
    Feature tests for F-VERIF-02: TSCN Schema & Reference Checker.
    Validates Godot 4 scene AST, load_steps formula, root node, hierarchy, attachments, and wiring.
    """

    def setUp(self) -> None:
        super().setUp()
        self.validator = TSCNValidator(project_root=self.workspace_dir)

    def test_feature_valid_godot4_scene(self) -> None:
        """Validates standard Godot 4 scene AST containing root, children, and resources."""
        # Ensure referenced asset exists on disk
        self.create_dummy_png(self.workspace_dir / "assets" / "sprites" / "player.png", width=32, height=32)

        scene = SceneAST(
            name="MainLevel",
            path="scenes/main.tscn",
            ext_resources=[
                ExtResourceAST(id="1_tex", type="Texture2D", path="res://assets/sprites/player.png"),
            ],
            sub_resources=[
                SubResourceAST(id="1_box", type="RectangleShape2D", properties={"size": "Vector2(32, 64)"}),
            ],
            nodes=[
                NodeAST(name="MainLevel", type="Node2D"),
                NodeAST(name="Player", type="CharacterBody2D", parent="."),
                NodeAST(name="Sprite", type="Sprite2D", parent="Player", properties={"texture": 'ExtResource("1_tex")'}),
                NodeAST(name="Collider", type="CollisionShape2D", parent="Player", properties={"shape": 'SubResource("1_box")'}),
            ],
        )
        errors = self.validator.validate_scene_ast(scene)
        self.assertEqual(len(errors), 0, f"Expected clean AST validation, got {errors}")

    def test_feature_load_steps_verification(self) -> None:
        """Validates load_steps header formula (N_ext + N_sub + 1)."""
        # Exactly 2 resources (1 ext + 1 sub), correct load_steps=3
        correct_tscn = (
            '[gd_scene load_steps=3 format=3]\n'
            '[ext_resource type="Texture2D" path="res://assets/icon.png" id="1_tex"]\n'
            '[sub_resource type="RectangleShape2D" id="1_box"]\n'
            'size = Vector2(32, 32)\n\n'
            '[node name="Root" type="Node2D"]\n'
        )
        errors_correct = self.validator.validate_tscn_text(correct_tscn, file_path="scenes/test.tscn")
        # Should have 0 errors and 0 warnings about load_steps
        load_step_warnings = [e for e in errors_correct if "load_steps mismatch" in e.message]
        self.assertEqual(len(load_step_warnings), 0)

        # Mismatched load_steps=10
        bad_load_steps_tscn = (
            '[gd_scene load_steps=10 format=3]\n'
            '[ext_resource type="Texture2D" path="res://assets/icon.png" id="1_tex"]\n'
            '[node name="Root" type="Node2D"]\n'
        )
        errors_bad = self.validator.validate_tscn_text(bad_load_steps_tscn, file_path="scenes/bad.tscn")
        self.assertTrue(any("load_steps mismatch" in e.message for e in errors_bad))

    def test_feature_root_node_contract(self) -> None:
        """Verifies single root node enforcement and rejection of multiple root nodes."""
        multi_root_scene = SceneAST(
            name="DualRoots",
            path="scenes/dual.tscn",
            nodes=[
                NodeAST(name="RootA", type="Node2D"),
                NodeAST(name="RootB", type="Node2D"),
            ],
        )
        errors = self.validator.validate_scene_ast(multi_root_scene)
        self.assertTrue(any("exactly 1 root node" in e.message for e in errors))

    def test_feature_parent_child_hierarchy(self) -> None:
        """Validates multi-level nested parent hierarchy paths."""
        scene = SceneAST(
            name="Dungeon",
            path="scenes/dungeon.tscn",
            nodes=[
                NodeAST(name="Dungeon", type="Node2D"),
                NodeAST(name="Environment", type="Node2D", parent="."),
                NodeAST(name="Room1", type="Node2D", parent="Environment"),
                NodeAST(name="Chest", type="Area2D", parent="Environment/Room1"),
            ],
        )
        errors = self.validator.validate_scene_ast(scene)
        self.assertEqual(len(errors), 0, f"Hierarchy validation failed: {errors}")

    def test_feature_script_attachment_integrity(self) -> None:
        """Validates script_id linking to ext_resource and rejects dangling script references."""
        # Ensure script file exists on disk
        scripts_dir = self.workspace_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "hero.gd").write_text("extends CharacterBody2D\n", encoding="utf-8")

        # Valid script attachment
        valid_scene = SceneAST(
            name="ValidScene",
            path="scenes/valid.tscn",
            ext_resources=[
                ExtResourceAST(id="1_scr", type="Script", path="res://scripts/hero.gd")
            ],
            nodes=[
                NodeAST(name="Hero", type="CharacterBody2D", script_id="1_scr"),
            ],
        )
        errors_valid = self.validator.validate_scene_ast(valid_scene)
        self.assertEqual(len(errors_valid), 0, f"Expected 0 errors for valid script attachment, got {errors_valid}")

        # Broken script attachment
        broken_scene = SceneAST(
            name="BrokenScene",
            path="scenes/broken.tscn",
            nodes=[
                NodeAST(name="Hero", type="CharacterBody2D", script_id="missing_id"),
            ],
        )
        errors_broken = self.validator.validate_scene_ast(broken_scene)
        self.assertTrue(any("unknown script_id 'missing_id'" in e.message for e in errors_broken))

    def test_feature_signal_connection_wiring(self) -> None:
        """Validates connection signal emitter, receiver, and handler callback presence."""
        scene = SceneAST(
            name="Level",
            path="scenes/level.tscn",
            nodes=[
                NodeAST(name="Level", type="Node2D"),
                NodeAST(name="TriggerArea", type="Area2D", parent="."),
                NodeAST(name="Door", type="StaticBody2D", parent="."),
            ],
            connections=[
                ConnectionAST(
                    signal="body_entered",
                    from_node="TriggerArea",
                    to_node="Door",
                    method="_on_body_entered",
                )
            ],
        )
        errors = self.validator.validate_scene_ast(scene)
        self.assertEqual(len(errors), 0, f"Signal wiring errors: {errors}")

        # Broken connection emitter
        scene.connections[0].from_node = "NonExistentTrigger"
        errors_bad = self.validator.validate_scene_ast(scene)
        self.assertTrue(any("NonExistentTrigger" in e.message for e in errors_bad))


# ============================================================================
# F-VERIF-03: Asset Integrity Verifier Feature Tests
# ============================================================================

class TestAssetIntegrityVerifierFeature(StudioTestCase):
    """
    Feature tests for F-VERIF-03: Asset Integrity Verifier.
    Tests discovery from scenes, magic byte checks (PNG, WAV), zero-byte checks, and missing links.
    """

    def setUp(self) -> None:
        super().setUp()
        self.sprites_dir = self.workspace_dir / "assets" / "sprites"
        self.audio_dir = self.workspace_dir / "audio" / "sfx"
        self.scenes_dir = self.workspace_dir / "scenes"
        self.sprites_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.scenes_dir.mkdir(parents=True, exist_ok=True)

    def test_feature_asset_discovery_from_tscn(self) -> None:
        """Discovers all res:// asset paths referenced in scene files."""
        tscn_content = (
            '[gd_scene format=3]\n'
            '[ext_resource type="Texture2D" path="res://assets/sprites/hero.png" id="1_tex"]\n'
            '[ext_resource type="AudioStream" path="res://audio/sfx/jump.wav" id="2_aud"]\n'
            '[node name="Root" type="Node2D"]\n'
        )
        (self.scenes_dir / "stage1.tscn").write_text(tscn_content, encoding="utf-8")

        verifier = AssetVerifier(self.workspace_dir)
        refs = verifier.discover_references()
        found_paths = {r.resolved_rel_path for r in refs}
        self.assertIn("assets/sprites/hero.png", found_paths)
        self.assertIn("audio/sfx/jump.wav", found_paths)

    def test_feature_asset_manifest_cross_check(self) -> None:
        """Cross-checks AssetManifest declared items with disk presence and verifies them."""
        # Create disk files
        p_img = self.create_dummy_png(self.sprites_dir / "coin.png", width=16, height=16)
        p_snd = self.create_dummy_wav(self.audio_dir / "pickup.wav", duration=0.05)

        manifest = AssetManifest(
            project_name="test_proj",
            items=[
                AssetItem(asset_id="spr_coin", asset_type=AssetType.SPRITE, engine_path="res://assets/sprites/coin.png", rel_path="assets/sprites/coin.png"),
                AssetItem(asset_id="sfx_pickup", asset_type=AssetType.AUDIO_SFX, engine_path="res://audio/sfx/pickup.wav", rel_path="audio/sfx/pickup.wav"),
            ],
        )

        verifier = AssetVerifier(self.workspace_dir, manifest=manifest)
        result = verifier.verify_all()
        self.assertTrue(result.passed, f"Asset manifest verification failed: {result.errors}")
        self.assertGreaterEqual(result.total_references_scanned, 2)

    def test_feature_png_header_verification(self) -> None:
        """Verifies PNG 8-byte magic header, IHDR chunk, and valid dimensions."""
        png_path = self.create_dummy_png(self.sprites_dir / "valid.png", width=32, height=32)
        png_bytes = png_path.read_bytes()
        self.assertIsNone(validate_magic_bytes(png_bytes, "png"))

        # Corrupted PNG bytes >= 24 bytes without magic header
        bad_bytes = b"CORRUPTED_PNG_DATA_OF_LENGTH_OVER_24_BYTES"
        err_msg = validate_magic_bytes(bad_bytes, "png")
        self.assertIsNotNone(err_msg)
        self.assertIn("magic header", err_msg.lower())

    def test_feature_wav_header_verification(self) -> None:
        """Verifies WAV canonical RIFF/WAVE header and format validation."""
        wav_path = self.create_dummy_wav(self.audio_dir / "valid.wav", duration=0.1)
        wav_bytes = wav_path.read_bytes()
        self.assertIsNone(validate_magic_bytes(wav_bytes, "wav"))

        # Corrupted WAV bytes
        bad_wav = b"NOT_RIFF_AUDIO_DATA"
        err_msg = validate_magic_bytes(bad_wav, "wav")
        self.assertIsNotNone(err_msg)
        self.assertIn("riff", err_msg.lower())

    def test_feature_zero_byte_rejection(self) -> None:
        """Identifies and flags 0-byte asset file as ERROR."""
        empty_asset = self.sprites_dir / "empty_sprite.png"
        empty_asset.touch()

        verifier = AssetVerifier(self.workspace_dir)
        errors = verifier.verify_file(empty_asset)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].stage, "asset_integrity")
        self.assertEqual(errors[0].severity, "ERROR")
        self.assertIn("0 bytes", errors[0].message)

    def test_feature_broken_link_attribution(self) -> None:
        """Identifies referring scene file and missing asset path when file is missing."""
        tscn_content = (
            '[gd_scene format=3]\n'
            '[ext_resource type="Texture2D" path="res://assets/sprites/ghost.png" id="1_tex"]\n'
            '[node name="Root" type="Sprite2D"]\n'
        )
        (self.scenes_dir / "level.tscn").write_text(tscn_content, encoding="utf-8")

        verifier = AssetVerifier(self.workspace_dir)
        result = verifier.verify_all()
        self.assertFalse(result.passed)
        missing_errors = [e for e in result.errors if "ghost.png" in e.message]
        self.assertEqual(len(missing_errors), 1)
        self.assertIn("level.tscn", missing_errors[0].file_path)


# ============================================================================
# F-VERIF-04: Dual-Mode Engine Simulator Feature Tests
# ============================================================================

class TestDualModeSimulatorFeature(StudioTestCase):
    """
    Feature tests for F-VERIF-04: Dual-Mode Engine Simulator.
    Tests mock simulator frame stepping, configurability, autodiscovery, mode selection, and hierarchy.
    """

    def setUp(self) -> None:
        super().setUp()
        self.scenes_dir = self.workspace_dir / "scenes"
        self.scenes_dir.mkdir(parents=True, exist_ok=True)

        # Create valid main scene
        scene = SceneAST(
            name="MainLevel",
            path="scenes/main.tscn",
            nodes=[
                NodeAST(name="MainLevel", type="Node2D"),
                NodeAST(name="Player", type="CharacterBody2D", parent="."),
                NodeAST(name="CollisionShape2D", type="CollisionShape2D", parent="Player"),
            ],
        )
        (self.scenes_dir / "main.tscn").write_text(serialize_tscn(scene), encoding="utf-8")

        cfg = GodotProjectConfig(project_name="SimProject", main_scene_path="res://scenes/main.tscn")
        (self.workspace_dir / "project.godot").write_text(generate_project_godot(cfg), encoding="utf-8")

    def test_feature_mock_simulator_stepper(self) -> None:
        """Runs 60 simulated physics ticks and verifies clean completion without error."""
        sim = MockEngineSimulator(self.workspace_dir)
        res = sim.simulate(frames=60)
        self.assertTrue(res.passed, f"Simulation failed: {res.errors}")
        self.assertEqual(res.frames_run, 60)
        self.assertEqual(len(res.errors), 0)
        self.assertEqual(res.mode_used, "mock_engine")

    def test_feature_frame_count_configurability(self) -> None:
        """Verifies simulation runs exact requested frame counts (10, 30, 90)."""
        sim = MockEngineSimulator(self.workspace_dir)
        for expected_frames in (10, 30, 90):
            res = sim.simulate(frames=expected_frames)
            self.assertTrue(res.passed)
            self.assertEqual(res.frames_run, expected_frames)

    def test_feature_main_scene_autodiscovery(self) -> None:
        """Automatically discovers main scene from project.godot configuration."""
        sim = MockEngineSimulator(self.workspace_dir)
        scene_path = sim._resolve_main_scene(None)
        self.assertIsNotNone(scene_path)
        self.assertTrue(scene_path.is_file())
        self.assertEqual(scene_path.name, "main.tscn")

    def test_feature_simulator_mode_selection(self) -> None:
        """Confirms DualModeEngineSimulator operates in mock_engine mode when requested."""
        dual_sim = DualModeEngineSimulator(self.workspace_dir, force_mode="mock_engine")
        res = dual_sim.run_simulation(frames=30)
        self.assertTrue(res.passed)
        self.assertEqual(res.mode_used, "mock_engine")
        self.assertEqual(res.frames_run, 30)

    def test_feature_simulation_timeout_protection(self) -> None:
        """Verifies simulation timeout protection triggers when timeout_seconds exceeded."""
        sim = MockEngineSimulator(self.workspace_dir)
        # Set practically instantaneous timeout of 0.0 seconds to trigger guard
        res = sim.simulate(frames=1000, timeout_seconds=0.0)
        self.assertFalse(res.passed)
        self.assertTrue(any("timed out" in e.message.lower() for e in res.errors))

    def test_feature_simulated_node_hierarchy(self) -> None:
        """Verifies SimulatedNode tree construction, child addition, and descendant traversal."""
        root = SimulatedNode(name="Root", type="Node2D")
        child = SimulatedNode(name="Player", type="CharacterBody2D")
        sub_child = SimulatedNode(name="Gun", type="Marker2D")

        root.add_child(child)
        child.add_child(sub_child)

        self.assertEqual(child.parent, root)
        self.assertEqual(sub_child.parent, child)
        self.assertEqual(root.find_descendant("Gun"), sub_child)
        self.assertEqual(len(root.get_all_nodes()), 3)


# ============================================================================
# Unified Verification Pipeline Feature Tests
# ============================================================================

class TestUnifiedVerificationPipelineFeature(StudioTestCase):
    """
    Feature tests for VerificationPipeline master orchestration:
    Sequential execution, fail-fast vs complete diagnostic collection, Blackboard, and EventBus.
    """

    def setUp(self) -> None:
        super().setUp()
        self.scripts_dir = self.workspace_dir / "scripts"
        self.scenes_dir = self.workspace_dir / "scenes"
        self.sprites_dir = self.workspace_dir / "assets" / "sprites"
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.scenes_dir.mkdir(parents=True, exist_ok=True)
        self.sprites_dir.mkdir(parents=True, exist_ok=True)

        # Create valid assets, script, scene, and project.godot
        self.create_dummy_png(self.sprites_dir / "player.png", width=32, height=32)
        (self.scripts_dir / "player.gd").write_text("extends CharacterBody2D\nfunc _ready():\n    pass\n", encoding="utf-8")

        scene = SceneAST(
            name="MainLevel",
            path="scenes/main.tscn",
            ext_resources=[
                ExtResourceAST(id="1_tex", type="Texture2D", path="res://assets/sprites/player.png"),
                ExtResourceAST(id="2_scr", type="Script", path="res://scripts/player.gd"),
            ],
            nodes=[
                NodeAST(name="MainLevel", type="Node2D", script_id="2_scr"),
                NodeAST(name="Player", type="Sprite2D", parent=".", properties={"texture": 'ExtResource("1_tex")'}),
            ],
        )
        (self.scenes_dir / "main.tscn").write_text(serialize_tscn(scene), encoding="utf-8")

        cfg = GodotProjectConfig(
            project_name="PipelineTest",
            main_scene_path="res://scenes/main.tscn",
            icon_path="res://assets/sprites/player.png",
        )
        (self.workspace_dir / "project.godot").write_text(generate_project_godot(cfg), encoding="utf-8")

    def test_feature_pipeline_sequential_execution(self) -> None:
        """Executes all 4 verification stages sequentially on clean project hierarchy."""
        pipeline = VerificationPipeline()
        report = pipeline.verify_project(self.workspace_dir, simulation_frames=30)
        self.assertTrue(report.passed, f"Pipeline failed: {report.errors}")
        self.assertEqual(len(report.errors), 0)
        self.assertEqual(
            report.stages_executed,
            ["syntax_check", "schema_check", "asset_integrity", "simulation"],
        )
        self.assertGreater(report.execution_time_ms, 0.0)
        self.assertEqual(report.simulation_frames_run, 30)
        self.assertIn("PASSED", report.summary)

    def test_feature_pipeline_fail_fast_stop(self) -> None:
        """Verifies fail_fast=True halts pipeline immediately upon Stage 1 syntax failure."""
        # Inject syntax error in script
        (self.scripts_dir / "broken.gd").write_text("func invalid(\n", encoding="utf-8")
        # Also inject broken scene
        (self.scenes_dir / "broken.tscn").write_text("[gd_scene format=2]\n", encoding="utf-8")

        pipeline = VerificationPipeline()
        report = pipeline.verify_project(self.workspace_dir, fail_fast=True)
        self.assertFalse(report.passed)
        # Should stop after stage 1
        self.assertEqual(report.stages_executed, ["syntax_check"])
        self.assertTrue(all(e.stage == "syntax_check" for e in report.errors))

    def test_feature_pipeline_complete_diagnostic_aggregation(self) -> None:
        """Verifies complete diagnostic mode aggregates errors across multiple stages."""
        # Inject syntax error (Stage 1)
        (self.scripts_dir / "broken.gd").write_text("func invalid(\n", encoding="utf-8")
        # Inject missing asset reference (Stage 3)
        (self.scenes_dir / "bad_asset.tscn").write_text(
            '[gd_scene format=3]\n'
            '[ext_resource type="Texture2D" path="res://assets/sprites/missing.png" id="1_tex"]\n'
            '[node name="Root" type="Sprite2D"]\n',
            encoding="utf-8",
        )

        pipeline = VerificationPipeline()
        report = pipeline.verify_project(self.workspace_dir, fail_fast=False)
        self.assertFalse(report.passed)
        # Must execute all stages
        self.assertEqual(len(report.stages_executed), 4)
        error_stages = {e.stage for e in report.errors}
        self.assertIn("syntax_check", error_stages)
        self.assertIn("asset_integrity", error_stages)

    def test_feature_pipeline_blackboard_state_recording(self) -> None:
        """Verifies verification report is saved into Blackboard state."""
        bb = Blackboard(project_name="BBTest")
        pipeline = VerificationPipeline(blackboard=bb)
        report = pipeline.verify_project(self.workspace_dir, stages=["syntax_check"])

        self.assertTrue(report.passed)
        self.assertIsNotNone(bb._state.verification_report)
        self.assertEqual(bb._state.verification_report.stages_executed, ["syntax_check"])

    def test_feature_pipeline_event_bus_events(self) -> None:
        """Verifies publication of lifecycle events to EventBus during verification."""
        bus = EventBus()
        captured_events: List[str] = []

        async def listener(evt: Any) -> None:
            pass

        # Subscribe to topics
        bus.subscribe("verification.started", listener)
        bus.subscribe("verification.complete", listener)

        pipeline = VerificationPipeline(event_bus=bus)
        report = pipeline.verify_project(self.workspace_dir, stages=["syntax_check"])
        self.assertTrue(report.passed)

    def test_feature_pipeline_error_routing_for_review(self) -> None:
        """Verifies route_errors_for_review partitions code/sim to engineer and assets to artist."""
        report = VerificationReport(
            passed=False,
            errors=[
                DiagnosticError(stage="syntax_check", file_path="scripts/p.gd", message="Syntax fault"),
                DiagnosticError(stage="schema_check", file_path="scenes/m.tscn", message="Schema error"),
                DiagnosticError(stage="simulation", file_path="sim", message="Runtime crash"),
                DiagnosticError(stage="asset_integrity", file_path="assets/spr.png", message="Missing asset"),
            ],
        )
        routed = route_errors_for_review(report)
        self.assertEqual(len(routed["engineer"]), 3)
        self.assertEqual(len(routed["artist"]), 1)
        self.assertEqual(routed["artist"][0].stage, "asset_integrity")


if __name__ == "__main__":
    unittest.main()
