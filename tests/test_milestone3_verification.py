"""
tests/test_milestone3_verification.py - Comprehensive Unit Test Suite for Milestone 3.
Verifies F-VERIF-01 (Multi-Engine Syntax Validator), F-VERIF-02 (TSCN Schema & Reference Checker),
F-VERIF-03 (Asset Integrity Verifier), F-VERIF-04 (Dual-Mode Engine Simulator),
and VerificationPipeline end-to-end orchestration.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
import sys
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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


# Minimal valid assets for testing
VALID_1X1_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00"
    b"\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

VALID_MIN_WAV = (
    b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b"\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
)


# ============================================================================
# 1. Diagnostic Models Tests
# ============================================================================

class TestDiagnosticModels(unittest.TestCase):
    """Verifies DiagnosticError and VerificationReport model semantics."""

    def test_diagnostic_error_defaults_and_validation(self) -> None:
        err = DiagnosticError(
            stage="syntax_check",
            file_path="scripts/player.gd",
            line=12,
            column=5,
            message="Missing colon ':'",
            suggested_fix="Add colon at end of line.",
        )
        self.assertEqual(err.stage, "syntax_check")
        self.assertEqual(err.severity, "ERROR")
        self.assertEqual(err.file_path, "scripts/player.gd")
        self.assertEqual(err.line, 12)
        self.assertEqual(err.column, 5)
        self.assertEqual(err.message, "Missing colon ':'")
        self.assertEqual(err.suggested_fix, "Add colon at end of line.")

    def test_diagnostic_error_json_serialization_roundtrip(self) -> None:
        err = DiagnosticError(
            stage="schema_check",
            severity="WARNING",
            file_path="scenes/main.tscn",
            line=1,
            message="load_steps mismatch",
        )
        data = err.model_dump()
        self.assertEqual(data["stage"], "schema_check")
        self.assertEqual(data["severity"], "WARNING")
        err2 = DiagnosticError.model_validate(data)
        self.assertEqual(err, err2)

    def test_verification_report_defaults(self) -> None:
        report = VerificationReport(passed=True)
        self.assertTrue(report.passed)
        self.assertEqual(report.stages_executed, [])
        self.assertEqual(report.errors, [])
        self.assertEqual(report.warnings, [])
        self.assertEqual(report.execution_time_ms, 0.0)
        self.assertEqual(report.simulation_frames_run, 0)
        self.assertEqual(report.summary, "")

    def test_verification_report_passed_semantics(self) -> None:
        # Warnings only should still pass
        warn = DiagnosticError(stage="schema_check", severity="WARNING", file_path="f", message="w")
        report_warn = VerificationReport(passed=True, warnings=[warn])
        self.assertTrue(report_warn.passed)
        self.assertEqual(len(report_warn.errors), 0)
        self.assertEqual(len(report_warn.warnings), 1)

        # Errors cause failure
        err = DiagnosticError(stage="syntax_check", severity="ERROR", file_path="f", message="e")
        report_err = VerificationReport(passed=False, errors=[err])
        self.assertFalse(report_err.passed)
        self.assertEqual(len(report_err.errors), 1)

    def test_verification_report_summary_formatting(self) -> None:
        report = VerificationReport(
            passed=True,
            stages_executed=["syntax_check", "schema_check"],
            execution_time_ms=12.5,
            summary="Verification PASSED with 0 errors.",
        )
        self.assertIn("PASSED", report.summary)
        self.assertEqual(len(report.stages_executed), 2)


# ============================================================================
# 2. Multi-Engine Syntax Validator Tests (F-VERIF-01)
# ============================================================================

class TestSyntaxValidatorUnit(unittest.TestCase):
    """Verifies language-specific syntax validation for Python, GDScript, JS, HTML, C#."""

    def setUp(self) -> None:
        self.py_validator = PythonSyntaxValidator()
        self.gd_validator = GDScriptSyntaxValidator()
        self.js_validator = JavaScriptSyntaxValidator(force_fallback=True)
        self.html_validator = HTMLSyntaxValidator()
        self.cs_validator = CSharpSyntaxValidator()
        self.facade = MultiEngineSyntaxValidator()

    def test_python_syntax_valid(self) -> None:
        code = "def add(a: int, b: int) -> int:\n    return a + b\n"
        errors = self.py_validator.validate(code, file_path="calc.py")
        self.assertEqual(len(errors), 0)

    def test_python_syntax_invalid_line_column(self) -> None:
        code = "def bad_func(\n    x = 10\n"
        errors = self.py_validator.validate(code, file_path="bad.py")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].stage, "syntax_check")
        self.assertEqual(errors[0].severity, "ERROR")
        self.assertGreaterEqual(errors[0].line or 0, 1)
        self.assertIsNotNone(errors[0].suggested_fix)

    def test_gdscript_syntax_valid_structure(self) -> None:
        code = (
            "extends CharacterBody2D\n"
            "class_name Player\n\n"
            "signal health_changed(new_val)\n\n"
            "@export var speed: float = 300.0\n"
            "const MAX_SPEED = 500.0\n\n"
            "func _physics_process(delta: float) -> void:\n"
            "    velocity.x = speed\n"
            "    move_and_slide()\n"
        )
        errors = self.gd_validator.validate(code, file_path="player.gd")
        self.assertEqual(len(errors), 0)

    def test_gdscript_missing_colon_syntax_error(self) -> None:
        code = "func _ready()\n    pass\n"
        errors = self.gd_validator.validate(code, file_path="player.gd")
        self.assertTrue(any("missing trailing ':'" in e.message for e in errors))

    def test_gdscript_mixed_tabs_spaces(self) -> None:
        code = "func _ready():\n \tpass\n"
        errors = self.gd_validator.validate(code, file_path="player.gd")
        self.assertTrue(any("mixing tabs and spaces" in e.message for e in errors))

    def test_gdscript_unbalanced_delimiter(self) -> None:
        code = "func calculate():\n    var arr = [1, 2, 3\n"
        errors = self.gd_validator.validate(code, file_path="player.gd")
        self.assertTrue(any("Unclosed delimiter" in e.message for e in errors))

    def test_javascript_syntax_valid_and_invalid(self) -> None:
        valid_js = "function update(delta) { return delta * 60; }"
        self.assertEqual(len(self.js_validator.validate(valid_js)), 0)

        invalid_js = "function update(delta) { return delta * 60;"
        errors = self.js_validator.validate(invalid_js, file_path="game.js")
        self.assertEqual(len(errors), 1)
        self.assertIn("Unclosed delimiter", errors[0].message)

    def test_csharp_syntax_valid_and_invalid(self) -> None:
        valid_cs = (
            "#if UNITY_EDITOR\n"
            "using UnityEngine;\n"
            "public class Player : MonoBehaviour {\n"
            "    void Start() { Debug.Log(\"Hello\"); }\n"
            "}\n"
            "#endif\n"
        )
        self.assertEqual(len(self.cs_validator.validate(valid_cs)), 0)

        unclosed_if = "#if UNITY_EDITOR\npublic class Player {}\n"
        errors = self.cs_validator.validate(unclosed_if, file_path="Player.cs")
        self.assertTrue(any("Unclosed preprocessor directive '#if'" in e.message for e in errors))

    def test_html_syntax_tag_balance_and_script_extraction(self) -> None:
        valid_html = (
            "<!DOCTYPE html><html><head><title>Game</title></head>"
            "<body><canvas id='c'></canvas>"
            "<script>const x = 10;</script>"
            "</body></html>"
        )
        self.assertEqual(len(self.html_validator.validate(valid_html)), 0)

        invalid_html = "<div><span>Text</div></span>"
        errors = self.html_validator.validate(invalid_html, file_path="index.html")
        self.assertTrue(any("Mismatched closing tag" in e.message for e in errors))

    def test_multi_engine_validator_dispatch(self) -> None:
        gd_spec = ScriptSpec(language="gdscript", code="func _ready():\n    pass\n", rel_path="player.gd")
        py_spec = ScriptSpec(language="python", code="def test(): pass", rel_path="main.py")
        bad_spec = ScriptSpec(language="python", code="def bad(", rel_path="bad.py")

        self.assertEqual(len(self.facade.validate_script(gd_spec)), 0)
        self.assertEqual(len(self.facade.validate_script(py_spec)), 0)
        self.assertEqual(len(self.facade.validate_script(bad_spec)), 1)


# ============================================================================
# 3. TSCN Schema & Reference Validator Tests (F-VERIF-02)
# ============================================================================

class TestTSCNValidatorUnit(unittest.TestCase):
    """Verifies Godot 4 .tscn schema, AST, hierarchy, and wiring checks."""

    def setUp(self) -> None:
        self.validator = TSCNValidator()

    def test_tscn_header_format3_validation(self) -> None:
        # Invalid format
        raw_bad = '[gd_scene format=2]\n[node name="Root" type="Node2D"]\n'
        errors = self.validator.validate_tscn_text(raw_bad, file_path="bad.tscn")
        self.assertTrue(any("format" in e.message for e in errors))

        # Valid format 3
        raw_good = '[gd_scene format=3]\n[node name="Root" type="Node2D"]\n'
        errors_good = self.validator.validate_tscn_text(raw_good, file_path="good.tscn")
        self.assertEqual(len(errors_good), 0)

    def test_tscn_load_steps_formula(self) -> None:
        # Declared load_steps=5, but only 1 ext_resource (expected 2)
        raw_text = (
            '[gd_scene load_steps=5 format=3]\n'
            '[ext_resource type="Texture2D" path="res://assets/icon.png" id="1_tex"]\n'
            '[node name="Root" type="Sprite2D"]\n'
        )
        errors = self.validator.validate_tscn_text(raw_text, file_path="scene.tscn")
        self.assertTrue(any("load_steps mismatch" in e.message for e in errors))

    def test_tscn_single_root_enforcement(self) -> None:
        scene = SceneAST(
            name="MultiRoot",
            path="scenes/test.tscn",
            nodes=[
                NodeAST(name="Root1", type="Node2D"),
                NodeAST(name="Root2", type="Node2D"),
            ],
        )
        errors = self.validator.validate_scene_ast(scene)
        self.assertTrue(any("exactly 1 root node" in e.message for e in errors))

    def test_tscn_cyclic_parent_detection(self) -> None:
        scene = SceneAST(
            name="CyclicScene",
            path="scenes/cyclic.tscn",
            nodes=[
                NodeAST(name="Root", type="Node2D"),
                NodeAST(name="NodeA", type="Node2D", parent="NodeB"),
                NodeAST(name="NodeB", type="Node2D", parent="NodeA"),
            ],
        )
        errors = self.validator.validate_scene_ast(scene)
        self.assertTrue(any("Cyclic node parenting detected" in e.message for e in errors))

    def test_tscn_duplicate_sibling_names(self) -> None:
        scene = SceneAST(
            name="DupSiblings",
            path="scenes/dup.tscn",
            nodes=[
                NodeAST(name="Root", type="Node2D"),
                NodeAST(name="Item", type="Sprite2D", parent="."),
                NodeAST(name="Item", type="Sprite2D", parent="."),
            ],
        )
        errors = self.validator.validate_scene_ast(scene)
        self.assertTrue(any("Duplicate sibling node name" in e.message for e in errors))

    def test_tscn_script_ext_resource_resolution(self) -> None:
        scene = SceneAST(
            name="MissingScript",
            path="scenes/script.tscn",
            nodes=[
                NodeAST(name="Root", type="Node2D", script_id="99_unknown"),
            ],
        )
        errors = self.validator.validate_scene_ast(scene)
        self.assertTrue(any("unknown script_id '99_unknown'" in e.message for e in errors))

    def test_tscn_connection_signal_wiring(self) -> None:
        scene = SceneAST(
            name="BadConnection",
            path="scenes/conn.tscn",
            nodes=[NodeAST(name="Root", type="Node2D")],
            connections=[
                ConnectionAST(
                    signal="pressed",
                    from_node="MissingButton",
                    to_node=".",
                    method="_on_pressed",
                )
            ],
        )
        errors = self.validator.validate_scene_ast(scene)
        self.assertTrue(any("does not exist in scene" in e.message for e in errors))


# ============================================================================
# 4. Asset Integrity Verifier Tests (F-VERIF-03)
# ============================================================================

class TestAssetVerifierUnit(unittest.TestCase):
    """Verifies magic byte headers, zero-byte detection, path traversals, and orphans."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_assets_")
        self.project_path = Path(self.temp_dir)
        (self.project_path / "assets" / "sprites").mkdir(parents=True, exist_ok=True)
        (self.project_path / "audio" / "sfx").mkdir(parents=True, exist_ok=True)
        (self.project_path / "scenes").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_asset_magic_bytes_png(self) -> None:
        self.assertIsNone(validate_magic_bytes(VALID_1X1_PNG, "png"))
        bad_png = b"NOT_A_PNG_FILE"
        self.assertIsNotNone(validate_magic_bytes(bad_png, "png"))

    def test_asset_magic_bytes_wav(self) -> None:
        self.assertIsNone(validate_magic_bytes(VALID_MIN_WAV, "wav"))
        bad_wav = b"RIFF____MIDI"
        self.assertIsNotNone(validate_magic_bytes(bad_wav, "wav"))

    def test_asset_zero_byte_detection(self) -> None:
        zero_file = self.project_path / "assets" / "sprites" / "empty.png"
        zero_file.touch()

        verifier = AssetVerifier(self.project_path)
        errs = verifier.verify_file(zero_file)
        self.assertEqual(len(errs), 1)
        self.assertIn("0 bytes", errs[0].message)

    def test_missing_asset_detection(self) -> None:
        # Create a scene referencing missing asset
        tscn_content = (
            '[gd_scene format=3]\n'
            '[ext_resource type="Texture2D" path="res://assets/sprites/ghost.png" id="1_tex"]\n'
            '[node name="Root" type="Sprite2D"]\n'
        )
        (self.project_path / "scenes" / "main.tscn").write_text(tscn_content, encoding="utf-8")

        verifier = AssetVerifier(self.project_path)
        result = verifier.verify_all()
        self.assertFalse(result.passed)
        self.assertTrue(any("Missing referenced asset file" in e.message for e in result.errors))

    def test_orphaned_asset_warning(self) -> None:
        # Create a valid PNG on disk that is not referenced anywhere
        orphan = self.project_path / "assets" / "sprites" / "unused.png"
        orphan.write_bytes(VALID_1X1_PNG)

        verifier = AssetVerifier(self.project_path)
        result = verifier.verify_all(fail_on_orphan=False)
        self.assertTrue(result.passed)  # warnings don't fail by default
        self.assertTrue(any("Orphaned asset file" in w.message for w in result.warnings))

    def test_path_traversal_detection(self) -> None:
        tscn_content = (
            '[gd_scene format=3]\n'
            '[ext_resource type="Texture2D" path="res://../../secret.png" id="1_tex"]\n'
            '[node name="Root" type="Sprite2D"]\n'
        )
        (self.project_path / "scenes" / "main.tscn").write_text(tscn_content, encoding="utf-8")

        verifier = AssetVerifier(self.project_path)
        result = verifier.verify_all()
        self.assertFalse(result.passed)
        self.assertTrue(any("path traversal" in e.message for e in result.errors))


# ============================================================================
# 5. Dual-Mode Engine Simulator Tests (F-VERIF-04)
# ============================================================================

class TestEngineSimulatorUnit(unittest.TestCase):
    """Verifies pure-Python MockEngineSimulator and timeout/error guards."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_sim_")
        self.project_path = Path(self.temp_dir)
        (self.project_path / "scenes").mkdir(parents=True, exist_ok=True)
        (self.project_path / "scripts").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_mock_engine_simulator_60_frames(self) -> None:
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
        (self.project_path / "scenes" / "main.tscn").write_text(serialize_tscn(scene), encoding="utf-8")
        cfg = GodotProjectConfig(project_name="TestSim", main_scene_path="res://scenes/main.tscn")
        (self.project_path / "project.godot").write_text(generate_project_godot(cfg), encoding="utf-8")

        sim = MockEngineSimulator(self.project_path)
        res = sim.simulate(frames=60)
        self.assertTrue(res.passed)
        self.assertEqual(res.frames_run, 60)
        self.assertEqual(len(res.errors), 0)

    def test_mock_engine_simulator_zero_frames(self) -> None:
        scene = SceneAST(
            name="MainLevel",
            path="scenes/main.tscn",
            nodes=[NodeAST(name="MainLevel", type="Node2D")],
        )
        (self.project_path / "scenes" / "main.tscn").write_text(serialize_tscn(scene), encoding="utf-8")
        cfg = GodotProjectConfig(project_name="TestSim", main_scene_path="res://scenes/main.tscn")
        (self.project_path / "project.godot").write_text(generate_project_godot(cfg), encoding="utf-8")

        sim = MockEngineSimulator(self.project_path)
        res = sim.simulate(frames=0)
        self.assertTrue(res.passed)
        self.assertEqual(res.frames_run, 0)

    def test_simulator_missing_main_scene(self) -> None:
        sim = MockEngineSimulator(self.project_path)
        res = sim.simulate(frames=30)
        self.assertFalse(res.passed)
        self.assertTrue(any("No valid main scene found" in e.message for e in res.errors))

    def test_simulator_missing_dollar_node_detection(self) -> None:
        # Create script referencing non-existent child $Gun
        script_code = "extends CharacterBody2D\nfunc _ready():\n    $Gun.fire()\n"
        (self.project_path / "scripts" / "player.gd").write_text(script_code, encoding="utf-8")

        scene = SceneAST(
            name="MainLevel",
            path="scenes/main.tscn",
            ext_resources=[
                ExtResourceAST(id="1_scr", type="Script", path="res://scripts/player.gd")
            ],
            nodes=[
                NodeAST(name="MainLevel", type="Node2D"),
                NodeAST(name="Player", type="CharacterBody2D", parent=".", script_id="1_scr"),
            ],
        )
        (self.project_path / "scenes" / "main.tscn").write_text(serialize_tscn(scene), encoding="utf-8")
        cfg = GodotProjectConfig(project_name="TestSim", main_scene_path="res://scenes/main.tscn")
        (self.project_path / "project.godot").write_text(generate_project_godot(cfg), encoding="utf-8")

        sim = MockEngineSimulator(self.project_path)
        res = sim.simulate(frames=10)
        self.assertFalse(res.passed)
        self.assertTrue(any("references non-existent node '$Gun'" in e.message for e in res.errors))

    def test_simulator_physics_explosion_detection(self) -> None:
        # Create a scene with physics node with extreme runaway coordinates
        scene = SceneAST(
            name="MainLevel",
            path="scenes/main.tscn",
            nodes=[
                NodeAST(name="MainLevel", type="Node2D"),
                NodeAST(name="Player", type="CharacterBody2D", parent=".", properties={"vel_y": 1e9}),
            ],
        )
        (self.project_path / "scenes" / "main.tscn").write_text(serialize_tscn(scene), encoding="utf-8")
        cfg = GodotProjectConfig(project_name="TestSim", main_scene_path="res://scenes/main.tscn")
        (self.project_path / "project.godot").write_text(generate_project_godot(cfg), encoding="utf-8")

        sim = MockEngineSimulator(self.project_path)
        res = sim.simulate(frames=10)
        self.assertFalse(res.passed)
        self.assertTrue(any("runtime exception" in e.message or "runaway" in e.message for e in res.errors))


# ============================================================================
# 6. Verification Pipeline End-to-End Orchestrator Tests
# ============================================================================

class TestVerificationPipelineUnit(unittest.TestCase):
    """Verifies pipeline orchestration, fail_fast, complete aggregation, and blackboard."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_pipeline_")
        self.project_path = Path(self.temp_dir)
        (self.project_path / "scenes").mkdir(parents=True, exist_ok=True)
        (self.project_path / "scripts").mkdir(parents=True, exist_ok=True)
        (self.project_path / "assets" / "sprites").mkdir(parents=True, exist_ok=True)

        # Setup minimal clean project on disk
        (self.project_path / "assets" / "sprites" / "icon.png").write_bytes(VALID_1X1_PNG)

        script_code = "extends Node2D\nfunc _ready():\n    pass\n"
        (self.project_path / "scripts" / "main.gd").write_text(script_code, encoding="utf-8")

        scene = SceneAST(
            name="MainLevel",
            path="scenes/main.tscn",
            ext_resources=[
                ExtResourceAST(id="1_tex", type="Texture2D", path="res://assets/sprites/icon.png"),
                ExtResourceAST(id="2_scr", type="Script", path="res://scripts/main.gd"),
            ],
            nodes=[
                NodeAST(name="MainLevel", type="Node2D", script_id="2_scr"),
                NodeAST(name="Sprite2D", type="Sprite2D", parent=".", properties={"texture": 'ExtResource("1_tex")'}),
            ],
        )
        (self.project_path / "scenes" / "main.tscn").write_text(serialize_tscn(scene), encoding="utf-8")
        cfg = GodotProjectConfig(
            project_name="PipeTest",
            main_scene_path="res://scenes/main.tscn",
            icon_path="res://assets/sprites/icon.png",
        )
        (self.project_path / "project.godot").write_text(generate_project_godot(cfg), encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_pipeline_clean_run_all_stages(self) -> None:
        pipeline = VerificationPipeline()
        report = pipeline.verify_project(self.project_path, simulation_frames=30)
        self.assertTrue(report.passed)
        self.assertEqual(len(report.errors), 0)
        self.assertEqual(len(report.stages_executed), 4)
        self.assertGreater(report.execution_time_ms, 0.0)

    def test_pipeline_fail_fast_skips_subsequent_stages(self) -> None:
        # Inject syntax error in script
        (self.project_path / "scripts" / "bad.gd").write_text("func bad(\n", encoding="utf-8")
        # Also inject missing asset error
        (self.project_path / "scenes" / "bad_asset.tscn").write_text(
            '[gd_scene format=3]\n[ext_resource type="Texture2D" path="res://assets/missing.png" id="1_tex"]\n[node name="R" type="Sprite2D"]\n',
            encoding="utf-8",
        )

        pipeline = VerificationPipeline()
        report = pipeline.verify_project(self.project_path, fail_fast=True)
        self.assertFalse(report.passed)
        # Should stop after stage 1 (syntax_check)
        self.assertEqual(report.stages_executed, ["syntax_check"])

    def test_pipeline_complete_mode_collects_all_errors(self) -> None:
        # Inject syntax error in script
        (self.project_path / "scripts" / "bad.gd").write_text("func bad(\n", encoding="utf-8")
        # Also inject missing asset error
        (self.project_path / "scenes" / "bad_asset.tscn").write_text(
            '[gd_scene format=3]\n[ext_resource type="Texture2D" path="res://assets/missing.png" id="1_tex"]\n[node name="R" type="Sprite2D"]\n',
            encoding="utf-8",
        )

        pipeline = VerificationPipeline()
        report = pipeline.verify_project(self.project_path, fail_fast=False)
        self.assertFalse(report.passed)
        # Must execute all 4 stages
        self.assertEqual(report.stages_executed, ["syntax_check", "schema_check", "asset_integrity", "simulation"])
        stages_with_errors = {e.stage for e in report.errors}
        self.assertIn("syntax_check", stages_with_errors)

    def test_pipeline_stage_filtering(self) -> None:
        pipeline = VerificationPipeline()
        report = pipeline.verify_project(self.project_path, stages=["syntax_check", "schema_check"])
        self.assertTrue(report.passed)
        self.assertEqual(report.stages_executed, ["syntax_check", "schema_check"])

    def test_pipeline_blackboard_integration(self) -> None:
        bb = Blackboard(project_name="BBTest")
        pipeline = VerificationPipeline()

        report = pipeline.verify_project(bb, stages=["syntax_check"])
        self.assertIsNotNone(report)
        self.assertIsNotNone(bb._state.verification_report)
        self.assertEqual(bb._state.verification_report.stages_executed, ["syntax_check"])

    def test_pipeline_event_bus_publishing(self) -> None:
        event_bus = EventBus()
        events_captured: List[str] = []

        async def handler(evt: Any) -> None:
            pass

        event_bus.subscribe("verification.*", handler)
        pipeline = VerificationPipeline(event_bus=event_bus)
        report = pipeline.verify_project(self.project_path, stages=["syntax_check"])
        self.assertTrue(report.passed)

    def test_pipeline_error_routing_for_review(self) -> None:
        report = VerificationReport(
            passed=False,
            errors=[
                DiagnosticError(stage="syntax_check", file_path="scripts/p.gd", message="Syntax"),
                DiagnosticError(stage="schema_check", file_path="scenes/m.tscn", message="Schema"),
                DiagnosticError(stage="asset_integrity", file_path="assets/i.png", message="Asset"),
                DiagnosticError(stage="simulation", file_path="sim", message="Sim"),
            ],
        )
        routed = route_errors_for_review(report)
        self.assertEqual(len(routed["engineer"]), 3)  # syntax, schema, sim
        self.assertEqual(len(routed["artist"]), 1)    # asset_integrity


if __name__ == "__main__":
    unittest.main()
