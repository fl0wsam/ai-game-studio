"""
tests/tier2_boundaries/test_verification_boundaries.py - Tier 2 Boundary & Corner Case Tests.
Implements systematic boundary, stress, and corner case testing (>=5 tests per category):
- Category 1: Syntax Validator Boundaries (deep nesting, quotes, UTF-8/BOM, empty files, mixed indent, binary)
- Category 2: TSCN Schema & Graph Boundaries (cycles, self-parenting, multiple roots, 0-nodes, duplicate IDs)
- Category 3: Asset Integrity Boundaries (zero-byte, truncated PNG, spoofed extension, path traversal, Unicode)
- Category 4: Engine Simulator Boundaries & Stress (0 frames, negative frames, missing config, $node lookup, runaway)
- Category 5: Pipeline Runner Boundaries (non-existent dir, empty dir, warnings-only, fail-fast vs complete, async)
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
# Category 1: Syntax Validator Boundaries
# ============================================================================

class TestSyntaxValidatorBoundaries(StudioTestCase):
    """Boundary, extreme value, and stress tests for language syntax validators."""

    def setUp(self) -> None:
        super().setUp()
        self.py_val = PythonSyntaxValidator()
        self.gd_val = GDScriptSyntaxValidator()
        self.js_val = JavaScriptSyntaxValidator(force_fallback=True)
        self.cs_val = CSharpSyntaxValidator()
        self.facade = MultiEngineSyntaxValidator()

    def test_boundary_deeply_nested_blocks(self) -> None:
        """Tracks 20 levels of nested indentation blocks in GDScript without recursion error."""
        code = "func deep_nest():\n"
        for i in range(1, 21):
            code += "    " * i + f"if level_{i} > 0:\n"
        code += "    " * 21 + "pass\n"

        errors = self.gd_val.validate(code, file_path="scripts/deep.gd")
        self.assertEqual(len(errors), 0, f"Deeply nested indentation produced errors: {errors}")

    def test_boundary_escaped_quotes_and_raw_strings(self) -> None:
        """Handles complex multiline strings, escaped quotes, and raw strings."""
        gd_code = (
            'extends Node\n'
            'var s1 = "He said \\"Hello\\" to me"\n'
            'var s2 = \'She replied \\\'Yes\\\'\'\n'
            'var multiline = """Line 1\nLine 2 with "quotes"\nLine 3"""\n'
            'func _ready():\n'
            '    pass\n'
        )
        errors = self.gd_val.validate(gd_code, file_path="scripts/strings.gd")
        self.assertEqual(len(errors), 0, f"Escaped quotes failed in GDScript: {errors}")

        py_code = (
            'import re\n'
            'raw_pattern = r"\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}\\b"\n'
            'escaped = "Text with \\"escaped\\" and \\\\ backslash"\n'
            'multi = """Triple "double" \'single\'"""\n'
        )
        py_errors = self.py_val.validate(py_code, file_path="scripts/regex.py")
        self.assertEqual(len(py_errors), 0, f"Raw/escaped strings failed in Python: {py_errors}")

    def test_boundary_utf8_special_chars_and_bom(self) -> None:
        """Validates scripts with UTF-8 BOM, emoji, and non-ASCII Unicode comments."""
        code_with_unicode = (
            "# \u2728 Cyber Runner Main Controller \U0001f3ae\n"
            "# \u65e5\u672c\u8a9e\u30b3\u30e1\u30f3\u30c8: \u30d7\u30ec\u30a4\u30e4\u30fc\u306e\u79fb\u52d5\n"
            "extends Node2D\n"
            "var greeting: String = \"\u3053\u3093\u306b\u3061\u306f\u4e16\u754c\"\n"
            "func _ready() -> void:\n"
            "    print(greeting)\n"
        )
        errors = self.gd_val.validate(code_with_unicode, file_path="scripts/unicode.gd")
        self.assertEqual(len(errors), 0, f"Unicode script failed: {errors}")

        # Test UTF-8 BOM file on disk
        bom_file = self.workspace_dir / "bom_script.gd"
        bom_file.write_bytes(b"\xef\xbb\xbfextends Node\nfunc _ready():\n    pass\n")
        file_errors = self.gd_val.validate_file(bom_file)
        self.assertEqual(len(file_errors), 0, f"UTF-8 BOM file failed: {file_errors}")

    def test_boundary_empty_and_whitespace_files(self) -> None:
        """Treats empty string, whitespace-only, and comments-only scripts cleanly without crash."""
        self.assertEqual(len(self.gd_val.validate("", file_path="empty.gd")), 0)
        self.assertEqual(len(self.gd_val.validate("   \n\n\t\t\n  ", file_path="ws.gd")), 0)
        self.assertEqual(len(self.gd_val.validate("# comment 1\n# comment 2\n", file_path="comments.gd")), 0)

        self.assertEqual(len(self.py_val.validate("", file_path="empty.py")), 0)
        self.assertEqual(len(self.py_val.validate("   \n\n  ", file_path="ws.py")), 0)

    def test_boundary_mixed_tabs_and_spaces_in_indentation(self) -> None:
        """Detects and flags leading indentation lines that mix tabs and spaces."""
        bad_indent_gd = "func _ready():\n \tpass\n"
        errors = self.gd_val.validate(bad_indent_gd, file_path="mixed.gd")
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("mixing tabs and spaces" in e.message.lower() for e in errors))

    def test_boundary_unclosed_delimiters(self) -> None:
        """Detects unclosed parentheses, brackets, or braces across all supported languages."""
        # GDScript unclosed bracket
        unclosed_gd = "func calc():\n    var list = [1, 2, 3\n"
        gd_errs = self.gd_val.validate(unclosed_gd, file_path="unclosed.gd")
        self.assertTrue(any("unclosed delimiter" in e.message.lower() for e in gd_errs))

        # Python unclosed parenthesis
        unclosed_py = "val = (1 + 2\n"
        py_errs = self.py_val.validate(unclosed_py, file_path="unclosed.py")
        self.assertGreater(len(py_errs), 0)

        # JavaScript unclosed brace
        unclosed_js = "function test() { if (true) { return 42; }"
        js_errs = self.js_val.validate(unclosed_js, file_path="unclosed.js")
        self.assertTrue(any("unclosed delimiter" in e.message.lower() for e in js_errs))

        # C# unclosed preprocessor directive
        unclosed_cs = "#if UNITY_EDITOR\npublic class Tool {}\n"
        cs_errs = self.cs_val.validate(unclosed_cs, file_path="unclosed.cs")
        self.assertTrue(any("unclosed preprocessor" in e.message.lower() for e in cs_errs))

    def test_boundary_non_utf8_binary_script(self) -> None:
        """Detects non-UTF8 binary byte sequences in script file without raising unhandled exception."""
        bin_script = self.workspace_dir / "corrupted_binary.gd"
        bin_script.write_bytes(b"\x80\x81\xff\xfe\x00\x00\xaa\xbb")

        errors = self.gd_val.validate_file(bin_script)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].stage, "syntax_check")
        self.assertEqual(errors[0].severity, "ERROR")
        self.assertIn("utf-8", errors[0].message.lower())


# ============================================================================
# Category 2: TSCN Schema & Graph Boundaries
# ============================================================================

class TestTSCNSchemaBoundaries(StudioTestCase):
    """Boundary and corner case tests for Godot 4 .tscn schema and scene graph validation."""

    def setUp(self) -> None:
        super().setUp()
        self.validator = TSCNValidator()

    def test_boundary_cyclic_node_parenting(self) -> None:
        """Detects cyclic parent loops (NodeA -> NodeB -> NodeA)."""
        scene = SceneAST(
            name="Cyclic",
            path="scenes/cyclic.tscn",
            nodes=[
                NodeAST(name="Root", type="Node2D"),
                NodeAST(name="NodeA", type="Node2D", parent="NodeB"),
                NodeAST(name="NodeB", type="Node2D", parent="NodeA"),
            ],
        )
        errors = self.validator.validate_scene_ast(scene)
        self.assertTrue(any("cyclic node parenting" in e.message.lower() for e in errors))

    def test_boundary_self_parenting_node(self) -> None:
        """Detects self-referencing parent where Node A declares parent 'Node A'."""
        scene = SceneAST(
            name="SelfParent",
            path="scenes/self.tscn",
            nodes=[
                NodeAST(name="Root", type="Node2D"),
                NodeAST(name="Actor", type="CharacterBody2D", parent="Actor"),
            ],
        )
        errors = self.validator.validate_scene_ast(scene)
        self.assertTrue(any("cannot be its own parent" in e.message.lower() for e in errors))

    def test_boundary_multiple_root_nodes(self) -> None:
        """Flags scene with 3 root nodes (parent=None)."""
        scene = SceneAST(
            name="TriRoot",
            path="scenes/tri.tscn",
            nodes=[
                NodeAST(name="Root1", type="Node2D"),
                NodeAST(name="Root2", type="Node2D"),
                NodeAST(name="Root3", type="Node2D"),
            ],
        )
        errors = self.validator.validate_scene_ast(scene)
        self.assertTrue(any("exactly 1 root node" in e.message.lower() for e in errors))

    def test_boundary_zero_nodes_scene(self) -> None:
        """Flags scene containing 0 nodes as schema ERROR."""
        scene = SceneAST(
            name="EmptyScene",
            path="scenes/empty.tscn",
            nodes=[],
        )
        errors = self.validator.validate_scene_ast(scene)
        self.assertTrue(any("contains no nodes" in e.message.lower() for e in errors))

    def test_boundary_duplicate_ext_resource_ids(self) -> None:
        """Flags scene header containing duplicate ext_resource IDs."""
        scene = SceneAST(
            name="DupRes",
            path="scenes/dupres.tscn",
            ext_resources=[
                ExtResourceAST(id="1_tex", type="Texture2D", path="res://assets/a.png"),
                ExtResourceAST(id="1_tex", type="Texture2D", path="res://assets/b.png"),
            ],
            nodes=[NodeAST(name="Root", type="Node2D")],
        )
        errors = self.validator.validate_scene_ast(scene)
        self.assertTrue(any("duplicate external resource id" in e.message.lower() for e in errors))

    def test_boundary_duplicate_sibling_nodes(self) -> None:
        """Flags two sibling nodes having identical names under the same parent."""
        scene = SceneAST(
            name="DupSiblings",
            path="scenes/dupsib.tscn",
            nodes=[
                NodeAST(name="Root", type="Node2D"),
                NodeAST(name="Coin", type="Area2D", parent="."),
                NodeAST(name="Coin", type="Area2D", parent="."),
            ],
        )
        errors = self.validator.validate_scene_ast(scene)
        self.assertTrue(any("duplicate sibling node name" in e.message.lower() for e in errors))

    def test_boundary_missing_parent_reference(self) -> None:
        """Flags node referencing non-existent parent path."""
        scene = SceneAST(
            name="MissingParent",
            path="scenes/missparent.tscn",
            nodes=[
                NodeAST(name="Root", type="Node2D"),
                NodeAST(name="Orphan", type="Sprite2D", parent="NonExistentAncestor"),
            ],
        )
        errors = self.validator.validate_scene_ast(scene)
        self.assertTrue(any("references non-existent parent" in e.message.lower() for e in errors))

    def test_boundary_deeply_nested_scene_paths(self) -> None:
        """Validates scene with 15 nested hierarchy levels without recursion limit failure."""
        nodes = [NodeAST(name="Root", type="Node2D")]
        prev_parent = "."
        for i in range(1, 16):
            n_name = f"Node_{i}"
            nodes.append(NodeAST(name=n_name, type="Node2D", parent=prev_parent))
            prev_parent = n_name if prev_parent == "." else f"{prev_parent}/{n_name}"

        scene = SceneAST(name="DeepScene", path="scenes/deep.tscn", nodes=nodes)
        errors = self.validator.validate_scene_ast(scene)
        self.assertEqual(len(errors), 0, f"Deep scene hierarchy failed: {errors}")


# ============================================================================
# Category 3: Asset Integrity Boundaries
# ============================================================================

class TestAssetIntegrityBoundaries(StudioTestCase):
    """Boundary, security, and corruption tests for Asset Integrity Verifier."""

    def setUp(self) -> None:
        super().setUp()
        self.sprites_dir = self.workspace_dir / "assets" / "sprites"
        self.audio_dir = self.workspace_dir / "audio" / "sfx"
        self.scenes_dir = self.workspace_dir / "scenes"
        self.sprites_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.scenes_dir.mkdir(parents=True, exist_ok=True)

    def test_boundary_zero_byte_asset(self) -> None:
        """Identifies 0-byte asset file as ERROR."""
        zero_file = self.sprites_dir / "zero.png"
        zero_file.touch()

        verifier = AssetVerifier(self.workspace_dir)
        errs = verifier.verify_file(zero_file)
        self.assertEqual(len(errs), 1)
        self.assertIn("0 bytes", errs[0].message)

    def test_boundary_truncated_png(self) -> None:
        """Detects truncated PNG missing IHDR or image chunk payload."""
        # 8 bytes of PNG signature only, missing IHDR chunk (needs >= 24)
        truncated_png = self.sprites_dir / "truncated.png"
        truncated_png.write_bytes(b"\x89PNG\r\n\x1a\n")

        verifier = AssetVerifier(self.workspace_dir)
        errs = verifier.verify_file(truncated_png)
        self.assertEqual(len(errs), 1)
        self.assertIn("truncated", errs[0].message.lower())

    def test_boundary_spoofed_file_extension(self) -> None:
        """Detects ASCII plain text file renamed with .png extension."""
        spoofed = self.sprites_dir / "spoofed.png"
        spoofed.write_text("This is an ASCII text file disguised as a PNG graphic.", encoding="utf-8")

        verifier = AssetVerifier(self.workspace_dir)
        errs = verifier.verify_file(spoofed)
        self.assertEqual(len(errs), 1)
        self.assertTrue(any("magic" in e.message.lower() or "header" in e.message.lower() for e in errs))

    def test_boundary_path_traversal_attempt(self) -> None:
        """Blocks and flags path traversal attempts referencing files outside project root."""
        tscn_content = (
            '[gd_scene format=3]\n'
            '[ext_resource type="Texture2D" path="res://../../etc/passwd" id="1_tex"]\n'
            '[node name="Root" type="Sprite2D"]\n'
        )
        (self.scenes_dir / "traversal.tscn").write_text(tscn_content, encoding="utf-8")

        verifier = AssetVerifier(self.workspace_dir)
        res = verifier.verify_all()
        self.assertFalse(res.passed)
        self.assertTrue(any("path traversal" in e.message.lower() for e in res.errors))

    def test_boundary_filenames_with_spaces_and_unicode(self) -> None:
        """Validates assets with spaces, brackets, and Unicode emoji characters in filenames."""
        asset_name = "player run [v1] \u2b50.png"
        self.create_dummy_png(self.sprites_dir / asset_name, width=32, height=32)

        tscn_content = (
            f'[gd_scene format=3]\n'
            f'[ext_resource type="Texture2D" path="res://assets/sprites/{asset_name}" id="1_tex"]\n'
            f'[node name="Root" type="Sprite2D"]\n'
        )
        (self.scenes_dir / "unicode_asset.tscn").write_text(tscn_content, encoding="utf-8")

        verifier = AssetVerifier(self.workspace_dir)
        res = verifier.verify_all()
        self.assertTrue(res.passed, f"Asset with Unicode/spaces failed verification: {res.errors}")

    def test_boundary_orphaned_assets(self) -> None:
        """Reports unreferenced files in assets/ as WARNING without failing verification."""
        orphan = self.sprites_dir / "unreferenced.png"
        self.create_dummy_png(orphan, width=32, height=32)

        verifier = AssetVerifier(self.workspace_dir)
        res = verifier.verify_all(fail_on_orphan=False)
        self.assertTrue(res.passed)
        self.assertTrue(any("orphaned" in w.message.lower() for w in res.warnings))


# ============================================================================
# Category 4: Engine Simulator Boundaries & Stress
# ============================================================================

class TestEngineSimulatorBoundaries(StudioTestCase):
    """Boundary and stress tests for headless simulation and pure-Python stepper."""

    def setUp(self) -> None:
        super().setUp()
        self.scenes_dir = self.workspace_dir / "scenes"
        self.scripts_dir = self.workspace_dir / "scripts"
        self.scenes_dir.mkdir(parents=True, exist_ok=True)
        self.scripts_dir.mkdir(parents=True, exist_ok=True)

        scene = SceneAST(
            name="MainLevel",
            path="scenes/main.tscn",
            nodes=[
                NodeAST(name="MainLevel", type="Node2D"),
                NodeAST(name="Player", type="CharacterBody2D", parent="."),
            ],
        )
        (self.scenes_dir / "main.tscn").write_text(serialize_tscn(scene), encoding="utf-8")
        cfg = GodotProjectConfig(project_name="SimBoundary", main_scene_path="res://scenes/main.tscn")
        (self.workspace_dir / "project.godot").write_text(generate_project_godot(cfg), encoding="utf-8")

    def test_boundary_zero_simulation_frames(self) -> None:
        """Simulating 0 frames verifies scene load and exits immediately with frames_run=0."""
        sim = MockEngineSimulator(self.workspace_dir)
        res = sim.simulate(frames=0)
        self.assertTrue(res.passed)
        self.assertEqual(res.frames_run, 0)

    def test_boundary_negative_simulation_frames(self) -> None:
        """Negative simulation frame count raises ValueError before stepping."""
        sim = MockEngineSimulator(self.workspace_dir)
        with self.assertRaises(ValueError):
            sim.simulate(frames=-10)

    def test_boundary_missing_project_godot_fallback(self) -> None:
        """Simulator cleanly falls back to finding scenes/*.tscn when project.godot is absent."""
        (self.workspace_dir / "project.godot").unlink()

        sim = MockEngineSimulator(self.workspace_dir)
        res = sim.simulate(frames=10)
        self.assertTrue(res.passed, f"Fallback scene search failed: {res.errors}")
        self.assertEqual(res.frames_run, 10)

    def test_boundary_missing_dollar_node_lookup(self) -> None:
        """Detects script referencing non-existent $Gun node as simulation runtime error."""
        (self.scripts_dir / "hero.gd").write_text("extends CharacterBody2D\nfunc _ready():\n    $MissingGun.shoot()\n", encoding="utf-8")

        scene = SceneAST(
            name="MainLevel",
            path="scenes/main.tscn",
            ext_resources=[ExtResourceAST(id="1_scr", type="Script", path="res://scripts/hero.gd")],
            nodes=[
                NodeAST(name="MainLevel", type="Node2D"),
                NodeAST(name="Player", type="CharacterBody2D", parent=".", script_id="1_scr"),
            ],
        )
        (self.scenes_dir / "main.tscn").write_text(serialize_tscn(scene), encoding="utf-8")

        sim = MockEngineSimulator(self.workspace_dir)
        res = sim.simulate(frames=10)
        self.assertFalse(res.passed)
        self.assertTrue(any("$MissingGun" in e.message for e in res.errors))

    def test_boundary_physics_runaway_coordinates(self) -> None:
        """Detects extreme runaway physics velocity coordinates as runtime exception."""
        scene = SceneAST(
            name="MainLevel",
            path="scenes/main.tscn",
            nodes=[
                NodeAST(name="MainLevel", type="Node2D"),
                NodeAST(name="Particle", type="CharacterBody2D", parent=".", properties={"vel_y": 1e12}),
            ],
        )
        (self.scenes_dir / "main.tscn").write_text(serialize_tscn(scene), encoding="utf-8")

        sim = MockEngineSimulator(self.workspace_dir)
        res = sim.simulate(frames=10)
        self.assertFalse(res.passed)
        self.assertTrue(any("runtime exception" in e.message.lower() or "runaway" in e.message.lower() for e in res.errors))

    def test_boundary_empty_project_simulation(self) -> None:
        """Simulating completely empty directory reports missing main scene without crash."""
        empty_dir = self.workspace_dir / "empty_sim_proj"
        empty_dir.mkdir(parents=True, exist_ok=True)

        sim = MockEngineSimulator(empty_dir)
        res = sim.simulate(frames=10)
        self.assertFalse(res.passed)
        self.assertTrue(any("no valid main scene" in e.message.lower() for e in res.errors))


# ============================================================================
# Category 5: Pipeline Runner Boundaries
# ============================================================================

class TestPipelineRunnerBoundaries(StudioTestCase):
    """Boundary and corner case tests for VerificationPipeline master runner."""

    def setUp(self) -> None:
        super().setUp()
        self.scripts_dir = self.workspace_dir / "scripts"
        self.scenes_dir = self.workspace_dir / "scenes"
        self.sprites_dir = self.workspace_dir / "assets" / "sprites"
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.scenes_dir.mkdir(parents=True, exist_ok=True)
        self.sprites_dir.mkdir(parents=True, exist_ok=True)

        self.create_dummy_png(self.sprites_dir / "icon.png", width=32, height=32)
        (self.scripts_dir / "main.gd").write_text("extends Node2D\nfunc _ready():\n    pass\n", encoding="utf-8")

        scene = SceneAST(
            name="MainLevel",
            path="scenes/main.tscn",
            ext_resources=[
                ExtResourceAST(id="1_tex", type="Texture2D", path="res://assets/sprites/icon.png"),
                ExtResourceAST(id="2_scr", type="Script", path="res://scripts/main.gd"),
            ],
            nodes=[
                NodeAST(name="MainLevel", type="Node2D", script_id="2_scr"),
                NodeAST(name="Icon", type="Sprite2D", parent=".", properties={"texture": 'ExtResource("1_tex")'}),
            ],
        )
        (self.scenes_dir / "main.tscn").write_text(serialize_tscn(scene), encoding="utf-8")

        cfg = GodotProjectConfig(project_name="BoundTest", main_scene_path="res://scenes/main.tscn")
        (self.workspace_dir / "project.godot").write_text(generate_project_godot(cfg), encoding="utf-8")

    def test_boundary_nonexistent_project_directory(self) -> None:
        """Passing non-existent path returns structured report with passed=False and system error."""
        pipeline = VerificationPipeline()
        report = pipeline.verify_project(self.workspace_dir / "non_existent_dir_12345")
        self.assertFalse(report.passed)
        self.assertEqual(len(report.errors), 1)
        self.assertEqual(report.errors[0].stage, "system")
        self.assertEqual(report.stages_executed, [])

    def test_boundary_empty_project_directory(self) -> None:
        """Passing empty directory returns structured failure report without unhandled exception."""
        empty_dir = self.workspace_dir / "empty_dir"
        empty_dir.mkdir(parents=True, exist_ok=True)

        pipeline = VerificationPipeline()
        report = pipeline.verify_project(empty_dir)
        self.assertFalse(report.passed)
        self.assertGreater(len(report.errors), 0)

    def test_boundary_warnings_only_passes_pipeline(self) -> None:
        """Project with WARNING diagnostics only (and 0 errors) results in report.passed == True."""
        # Unlink project.godot to produce warnings/fallbacks but clean execution
        # Add scene with load_steps mismatch (which produces WARNING, not ERROR)
        tscn_warn = (
            '[gd_scene load_steps=9 format=3]\n'
            '[ext_resource type="Texture2D" path="res://assets/sprites/icon.png" id="1_tex"]\n'
            '[node name="Root" type="Sprite2D"]\n'
        )
        (self.scenes_dir / "warn.tscn").write_text(tscn_warn, encoding="utf-8")

        pipeline = VerificationPipeline()
        report = pipeline.verify_project(self.workspace_dir, stages=["syntax_check", "schema_check"])
        self.assertTrue(report.passed)
        self.assertGreater(len(report.warnings), 0)
        self.assertEqual(len(report.errors), 0)

    def test_boundary_fail_fast_vs_complete_mode(self) -> None:
        """Verifies fail_fast stops at stage 1 while fail_fast=False executes all 4 stages."""
        # Inject syntax error (Stage 1)
        (self.scripts_dir / "bad.gd").write_text("func bad(\n", encoding="utf-8")
        # Inject missing asset reference (Stage 3)
        (self.scenes_dir / "bad_asset.tscn").write_text(
            '[gd_scene format=3]\n'
            '[ext_resource type="Texture2D" path="res://assets/sprites/ghost.png" id="1_tex"]\n'
            '[node name="Root" type="Sprite2D"]\n',
            encoding="utf-8",
        )

        pipeline = VerificationPipeline()

        # Fail-fast run
        report_fast = pipeline.verify_project(self.workspace_dir, fail_fast=True)
        self.assertFalse(report_fast.passed)
        self.assertEqual(report_fast.stages_executed, ["syntax_check"])

        # Complete run
        report_complete = pipeline.verify_project(self.workspace_dir, fail_fast=False)
        self.assertFalse(report_complete.passed)
        self.assertEqual(len(report_complete.stages_executed), 4)

    def test_boundary_error_routing_partition(self) -> None:
        """Verifies error partitioning matches reviewer expectations exactly."""
        report = VerificationReport(
            passed=False,
            errors=[
                DiagnosticError(stage="syntax_check", file_path="s.gd", message="e1"),
                DiagnosticError(stage="schema_check", file_path="s.tscn", message="e2"),
                DiagnosticError(stage="simulation", file_path="sim", message="e3"),
                DiagnosticError(stage="asset_integrity", file_path="a.png", message="e4"),
            ],
        )
        routed = route_errors_for_review(report)
        self.assertEqual(len(routed["engineer"]), 3)
        self.assertEqual(len(routed["artist"]), 1)
        self.assertEqual(routed["engineer"][0].stage, "syntax_check")
        self.assertEqual(routed["engineer"][1].stage, "schema_check")
        self.assertEqual(routed["engineer"][2].stage, "simulation")
        self.assertEqual(routed["artist"][0].stage, "asset_integrity")

    def test_boundary_async_verification_method(self) -> None:
        """Verifies asynchronous verification method updates Blackboard in async loop."""
        bb = Blackboard(project_name="AsyncTest")
        pipeline = VerificationPipeline(blackboard=bb)

        async def run_async_test() -> VerificationReport:
            return await pipeline.verify_project_async(self.workspace_dir, stages=["syntax_check"])

        report = self.run_async(run_async_test())
        self.assertTrue(report.passed)
        saved_report = self.run_async(bb.get_verification_report())
        self.assertIsNotNone(saved_report)
        self.assertTrue(saved_report.passed)


if __name__ == "__main__":
    unittest.main()
