"""
tests/test_milestone3_adversarial_challenger2.py - Adversarial Stress & Robustness Suite.
Milestone 3 Challenge Target: asset_verifier.py, engine_simulator.py, pipeline.py.
Tests corrupted binary headers, path traversal/symlink sandboxing, infinite loop timeout aborts,
malformed scene graphs (NaN/Inf/runaway), fail-fast vs complete multi-failure pipelines,
concurrency under multithreaded load, and review error routing.
"""
import concurrent.futures
import math
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional

from studio.core.blackboard import Blackboard
from studio.core.event_bus import EventBus
from studio.core.models import (
    AssetItem,
    AssetManifest,
    DiagnosticError,
    GameDesignDocument,
    ScriptSpec,
    StudioProjectState,
    VerificationReport,
)
from studio.engine.adapters.godot_adapter import (
    GodotProjectConfig,
    generate_project_godot,
    serialize_tscn,
)
from studio.engine.ast_models import ExtResourceAST, NodeAST, SceneAST
from studio.verification.asset_verifier import (
    AssetVerifier,
    DiscoveredAssetRef,
    validate_magic_bytes,
)
from studio.verification.engine_simulator import (
    DualModeEngineSimulator,
    MockEngineSimulator,
    SimulatedNode,
    SimulationResult,
)
from studio.verification.pipeline import (
    VerificationPipeline,
    route_errors_for_review,
)


class TestAdversarialAssetHeadersAndFormats(unittest.TestCase):
    """Adversarially probes binary headers and container signatures."""

    def test_corrupted_png_variants(self):
        # Truncated (<24 bytes)
        self.assertIn("truncated", validate_magic_bytes(b"\x89PNG\r\n\x1a\n", "png"))
        # 1-byte off signature
        self.assertIn("Missing PNG magic", validate_magic_bytes(b"\x88PNG\r\n\x1a\n" + b"\x00" * 20, "png"))
        self.assertIn("Missing PNG magic", validate_magic_bytes(b"\x89QNG\r\n\x1a\n" + b"\x00" * 20, "png"))
        # Missing IHDR chunk at offset 12
        bad_chunk = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rNOPE" + b"\x00" * 10
        self.assertIn("missing IHDR", validate_magic_bytes(bad_chunk, "png"))
        # Non-positive dimensions (0 width or 0 height)
        zero_w = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (0).to_bytes(4, "big") + (10).to_bytes(4, "big")
        self.assertIn("Invalid PNG dimensions", validate_magic_bytes(zero_w, "png"))
        zero_h = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (10).to_bytes(4, "big") + (0).to_bytes(4, "big")
        self.assertIn("Invalid PNG dimensions", validate_magic_bytes(zero_h, "png"))
        # Valid 1x1 PNG passes
        valid_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15c4"
        )
        self.assertIsNone(validate_magic_bytes(valid_png, "png"))

    def test_corrupted_wav_variants(self):
        # Truncated (<12 bytes)
        self.assertIn("truncated", validate_magic_bytes(b"RIFF1234", "wav"))
        # Missing RIFF
        self.assertIn("Missing WAV RIFF/WAVE", validate_magic_bytes(b"NOPE\x24\x00\x00\x00WAVEfmt ", "wav"))
        # Missing WAVE
        self.assertIn("Missing WAV RIFF/WAVE", validate_magic_bytes(b"RIFF\x24\x00\x00\x00NOPEfmt ", "wav"))
        # Valid minimal WAV header
        valid_wav = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        self.assertIsNone(validate_magic_bytes(valid_wav, "wav"))

    def test_corrupted_ogg_variants(self):
        # Truncated (<4 bytes)
        self.assertIn("Missing OGG", validate_magic_bytes(b"Ogg", "ogg"))
        # Bad container signature
        self.assertIn("Missing OGG", validate_magic_bytes(b"OggX\x00\x02\x00\x00", "ogg"))
        # Valid OggS header
        self.assertIsNone(validate_magic_bytes(b"OggS\x00\x02\x00\x00", "ogg"))

    def test_corrupted_mp3_variants(self):
        # Truncated (<4 bytes)
        self.assertIn("truncated", validate_magic_bytes(b"MP", "mp3"))
        # Missing ID3 and invalid frame sync
        self.assertIn("Missing MP3 ID3 header or MPEG sync frame", validate_magic_bytes(b"\x00\x00\x00\x00", "mp3"))
        # Valid ID3v2 header
        self.assertIsNone(validate_magic_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00", "mp3"))
        # Valid MPEG frame sync (0xFF followed by high 3 bits set e.g. 0xFB)
        self.assertIsNone(validate_magic_bytes(b"\xff\xfb\x90\x64", "mp3"))

    def test_corrupted_jpeg_variants(self):
        # Truncated (<4 bytes)
        self.assertIn("truncated", validate_magic_bytes(b"\xff\xd8", "jpg"))
        # Bad SOI
        self.assertIn("Missing JPEG SOI", validate_magic_bytes(b"\xff\xd7\xff\x00", "jpeg"))
        # Valid SOI
        self.assertIsNone(validate_magic_bytes(b"\xff\xd8\xff\xe0", "jpeg"))

    def test_corrupted_svg_and_json(self):
        # SVG invalid UTF-8
        self.assertIn("not valid UTF-8", validate_magic_bytes(b"\xff\xfe\x00\x00", "svg"))
        # SVG missing tags
        self.assertIn("missing <svg> or </svg>", validate_magic_bytes(b"<div>not an svg</div>", "svg"))
        # SVG valid
        self.assertIsNone(validate_magic_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "svg"))
        # JSON invalid UTF-8
        self.assertIn("not valid UTF-8", validate_magic_bytes(b"\xff\xfe\x00\x00", "json"))
        # JSON malformed
        self.assertIn("Malformed JSON", validate_magic_bytes(b'{"key": invalid_val}', "json"))
        # JSON valid
        self.assertIsNone(validate_magic_bytes(b'{"valid": true, "count": 42}', "json"))

    def test_unsupported_extensions_pass_through(self):
        # Formats without dedicated validators return None without error
        self.assertIsNone(validate_magic_bytes(b"ArbitraryBinaryContent", "bin"))
        self.assertIsNone(validate_magic_bytes(b"PlainTextContent", "txt"))


class TestAdversarialAssetSecurityAndFuzzing(unittest.TestCase):
    """Stress-tests path traversal, sandbox escapes, symlinks, zero-byte and huge files."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adv_asset_test_")
        self.proj_dir = Path(self.temp_dir)
        (self.proj_dir / "assets" / "sprites").mkdir(parents=True, exist_ok=True)
        (self.proj_dir / "audio" / "sfx").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_path_traversal_detection_in_references(self):
        # Injected path traversal references in manifest
        manifest = AssetManifest(
            project_name="security_test",
            items=[
                AssetItem(asset_id="traversal_1", asset_type="sprite", rel_path="../../../etc/shadow"),
                AssetItem(asset_id="traversal_2", asset_type="audio_sfx", rel_path="assets/../../etc/passwd"),
            ],
        )
        verifier = AssetVerifier(self.proj_dir, manifest=manifest)
        res = verifier.verify_all()
        self.assertFalse(res.passed)
        self.assertEqual(len(res.errors), 2)
        messages = [e.message for e in res.errors]
        self.assertTrue(any("path traversal in asset reference" in m for m in messages))

    def test_symlink_escaping_sandbox_detected(self):
        # Create external file outside project
        outside_file = self.proj_dir.parent / "secret_file.png"
        outside_file.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
        )
        # Create symlink inside assets/ pointing to outside file
        symlink_path = self.proj_dir / "assets" / "sprites" / "leak.png"
        os.symlink(outside_file, symlink_path)

        manifest = AssetManifest(
            project_name="symlink_test",
            items=[
                AssetItem(asset_id="symlink_leak", asset_type="sprite", rel_path="assets/sprites/leak.png"),
            ],
        )
        verifier = AssetVerifier(self.proj_dir, manifest=manifest)
        res = verifier.verify_all()
        self.assertFalse(res.passed)
        self.assertEqual(len(res.errors), 1)
        self.assertIn("resolves outside project root", res.errors[0].message)

        # Cleanup external file
        outside_file.unlink(missing_ok=True)

    def test_zero_byte_asset_flagged(self):
        zero_file = self.proj_dir / "assets" / "sprites" / "empty.png"
        zero_file.write_bytes(b"")

        manifest = AssetManifest(
            project_name="zero_byte_test",
            items=[
                AssetItem(asset_id="empty_sprite", asset_type="sprite", rel_path="assets/sprites/empty.png"),
            ],
        )
        verifier = AssetVerifier(self.proj_dir, manifest=manifest)
        res = verifier.verify_all()
        self.assertFalse(res.passed)
        self.assertIn("assets/sprites/empty.png", res.corrupted_files)
        self.assertTrue(any("empty (0 bytes)" in e.message for e in res.errors))

    def test_huge_asset_file_handled_without_oom(self):
        # 10 MB file with valid PNG header
        huge_file = self.proj_dir / "assets" / "sprites" / "huge.png"
        png_hdr = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
        )
        huge_file.write_bytes(png_hdr + b"\x00" * (10 * 1024 * 1024))

        manifest = AssetManifest(
            project_name="huge_file_test",
            items=[
                AssetItem(asset_id="huge_sprite", asset_type="sprite", rel_path="assets/sprites/huge.png"),
            ],
        )
        verifier = AssetVerifier(self.proj_dir, manifest=manifest)
        res = verifier.verify_all()
        self.assertTrue(res.passed)
        self.assertEqual(len(res.errors), 0)

    def test_missing_referenced_asset_flagged(self):
        manifest = AssetManifest(
            project_name="missing_asset_test",
            items=[
                AssetItem(asset_id="ghost", asset_type="audio_sfx", rel_path="audio/sfx/nonexistent.wav"),
            ],
        )
        verifier = AssetVerifier(self.proj_dir, manifest=manifest)
        res = verifier.verify_all()
        self.assertFalse(res.passed)
        self.assertIn("audio/sfx/nonexistent.wav", res.missing_files)
        self.assertTrue(any("Missing referenced asset file" in e.message for e in res.errors))

    def test_orphan_asset_detection_modes(self):
        # Create unreferenced audio file on disk
        orphan_path = self.proj_dir / "audio" / "sfx" / "orphan.wav"
        orphan_path.write_bytes(
            b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        )

        verifier = AssetVerifier(self.proj_dir)
        # Mode 1: fail_on_orphan=False -> WARNING, passed=True
        res_warn = verifier.verify_all(fail_on_orphan=False)
        self.assertTrue(res_warn.passed)
        self.assertEqual(len(res_warn.errors), 0)
        self.assertEqual(len(res_warn.warnings), 1)
        self.assertEqual(res_warn.warnings[0].severity, "WARNING")
        self.assertIn("Orphaned asset file", res_warn.warnings[0].message)

        # Mode 2: fail_on_orphan=True -> ERROR, passed=False
        res_err = verifier.verify_all(fail_on_orphan=True)
        self.assertFalse(res_err.passed)
        self.assertEqual(len(res_err.errors), 1)
        self.assertEqual(res_err.errors[0].severity, "ERROR")
        self.assertIn("Orphaned asset file", res_err.errors[0].message)

    def test_empirical_vulnerability_embedded_null_byte_in_path(self):
        """
        Adversarial probe: embedded null byte in asset reference path.
        Directly observes if verify_all catches or crashes on ValueError from Path.resolve().
        """
        manifest = AssetManifest(
            project_name="null_byte_injection",
            items=[
                AssetItem(asset_id="null_asset", asset_type="sprite", rel_path="assets/sprites/test\x00.png"),
            ],
        )
        verifier = AssetVerifier(self.proj_dir, manifest=manifest)
        # Empirical test: Path.resolve() raises ValueError: lstat: embedded null character in path
        try:
            res = verifier.verify_all()
            # If handled in future, should fail verification gracefully
            self.assertFalse(res.passed)
        except ValueError as ex:
            # Empirical verification of finding: uncaught ValueError from filesystem API
            self.assertIn("embedded null", str(ex).lower())


class TestAdversarialEngineSimulator(unittest.TestCase):
    """Stress-tests engine simulation under timeouts, NaN/Inf, runaway physics, and broken scenes."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adv_sim_test_")
        self.proj_dir = Path(self.temp_dir)
        (self.proj_dir / "scenes").mkdir(parents=True, exist_ok=True)
        (self.proj_dir / "scripts").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _setup_minimal_scene(self, scene_name="main.tscn", root_props=None, root_type="Node2D"):
        scene = SceneAST(name="main", path=f"scenes/{scene_name}")
        scene.nodes.append(
            NodeAST(name="Root", type=root_type, is_root=True, properties=root_props or {})
        )
        (self.proj_dir / "scenes" / scene_name).write_text(serialize_tscn(scene), encoding="utf-8")
        cfg = GodotProjectConfig(project_name="test_sim", main_scene_path=f"res://scenes/{scene_name}")
        (self.proj_dir / "project.godot").write_text(generate_project_godot(cfg), encoding="utf-8")

    def test_simulation_timeout_guard_aborts_gracefully(self):
        # Request 10,000,000 frames with strict 0.05s timeout guard
        self._setup_minimal_scene()
        sim = MockEngineSimulator(self.proj_dir)
        res = sim.simulate(frames=10_000_000, timeout_seconds=0.05)
        self.assertFalse(res.passed)
        self.assertGreater(res.frames_run, 0)
        self.assertLess(res.frames_run, 10_000_000)
        self.assertEqual(len(res.errors), 1)
        self.assertIn("Simulation timed out", res.errors[0].message)

    def test_nan_coordinate_detection(self):
        self._setup_minimal_scene(
            root_props={"pos_x": float("nan")}, root_type="CharacterBody2D"
        )
        sim = MockEngineSimulator(self.proj_dir)
        res = sim.simulate(frames=10)
        self.assertFalse(res.passed)
        self.assertEqual(len(res.errors), 1)
        self.assertIn("NaN coordinate detected", res.errors[0].message)

    def test_inf_velocity_detection(self):
        self._setup_minimal_scene(
            root_props={"vel_y": float("inf")}, root_type="CharacterBody2D"
        )
        sim = MockEngineSimulator(self.proj_dir)
        res = sim.simulate(frames=10)
        self.assertFalse(res.passed)
        self.assertEqual(len(res.errors), 1)
        self.assertIn("Inf coordinate detected", res.errors[0].message)

    def test_physics_runaway_detection(self):
        # Velocity of 200,000 exceeds 100,000 bounds within 31 frames at 60 FPS
        self._setup_minimal_scene(
            root_props={"vel_x": 200_000.0}, root_type="CharacterBody2D"
        )
        sim = MockEngineSimulator(self.proj_dir)
        res = sim.simulate(frames=60)
        self.assertFalse(res.passed)
        self.assertEqual(len(res.errors), 1)
        self.assertIn("Physics runaway", res.errors[0].message)
        self.assertIn("exceeds bounds", res.errors[0].message)

    def test_missing_main_scene_fails_gracefully(self):
        # project.godot points to non-existent scene
        cfg = GodotProjectConfig(project_name="broken", main_scene_path="res://scenes/missing.tscn")
        (self.proj_dir / "project.godot").write_text(generate_project_godot(cfg), encoding="utf-8")
        sim = MockEngineSimulator(self.proj_dir)
        res = sim.simulate(frames=60)
        self.assertFalse(res.passed)
        self.assertEqual(res.frames_run, 0)
        self.assertTrue(any("No valid main scene found" in e.message for e in res.errors))

    def test_scene_with_no_root_node_fails_gracefully(self):
        scene = SceneAST(name="empty", path="scenes/empty.tscn")
        (self.proj_dir / "scenes" / "empty.tscn").write_text(serialize_tscn(scene), encoding="utf-8")
        sim = MockEngineSimulator(self.proj_dir)
        res = sim.simulate(frames=60, main_scene_path="scenes/empty.tscn")
        self.assertFalse(res.passed)
        self.assertTrue(any("no root node to instantiate" in e.message.lower() for e in res.errors))

    def test_broken_dollar_node_script_reference_flagged(self):
        # Create GDScript referencing non-existent $GhostSprite child
        script_code = "extends CharacterBody2D\nfunc _ready():\n    $GhostSprite.play()\n"
        (self.proj_dir / "scripts" / "player.gd").write_text(script_code, encoding="utf-8")

        scene = SceneAST(name="main", path="scenes/main.tscn")
        scene.ext_resources.append(ExtResourceAST(id="1_s", type="Script", path="res://scripts/player.gd"))
        scene.nodes.append(NodeAST(name="Player", type="CharacterBody2D", is_root=True, script_id="1_s"))
        (self.proj_dir / "scenes" / "main.tscn").write_text(serialize_tscn(scene), encoding="utf-8")

        cfg = GodotProjectConfig(project_name="dollar_test", main_scene_path="res://scenes/main.tscn")
        (self.proj_dir / "project.godot").write_text(generate_project_godot(cfg), encoding="utf-8")

        sim = MockEngineSimulator(self.proj_dir)
        res = sim.simulate(frames=10)
        self.assertFalse(res.passed)
        self.assertTrue(any("references non-existent node '$GhostSprite'" in e.message for e in res.errors))

    def test_frame_count_boundary_conditions(self):
        self._setup_minimal_scene()
        sim = MockEngineSimulator(self.proj_dir)
        # Negative frames must raise ValueError
        with self.assertRaises(ValueError):
            sim.simulate(frames=-1)
        # Zero frames must succeed cleanly without stepping physics
        res_zero = sim.simulate(frames=0)
        self.assertTrue(res_zero.passed)
        self.assertEqual(res_zero.frames_run, 0)


class TestAdversarialVerificationPipeline(unittest.TestCase):
    """Stress-tests fail-fast vs complete modes, concurrency, target types, and routing."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adv_pipe_test_")
        self.proj_dir = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _setup_multi_stage_failure_project(self, base_dir: Path):
        (base_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (base_dir / "scenes").mkdir(parents=True, exist_ok=True)
        (base_dir / "assets").mkdir(parents=True, exist_ok=True)

        # Stage 1 Failure: Python Syntax Error
        (base_dir / "scripts" / "broken.py").write_text("def broken_syntax(\n", encoding="utf-8")

        # Stage 2 Failure: Broken TSCN Schema (Invalid parent reference)
        bad_scene = (
            '[gd_scene load_steps=1 format=3 uid="uid://test"]\n\n'
            '[node name="Root" type="Node2D"]\n\n'
            '[node name="Child" type="Node2D" parent="NonExistentParent"]\n'
        )
        (base_dir / "scenes" / "broken.tscn").write_text(bad_scene, encoding="utf-8")

        # Stage 3 Failure: Missing Referenced Asset in project.godot
        (base_dir / "project.godot").write_text(
            '[application]\nrun/main_scene="res://scenes/broken.tscn"\nconfig/icon="res://assets/sprites/missing.svg"\n',
            encoding="utf-8",
        )

    def test_fail_fast_vs_complete_mode(self):
        self._setup_multi_stage_failure_project(self.proj_dir)
        pipeline = VerificationPipeline()

        # 1. Fail-Fast Mode (Must halt at first failed stage: syntax_check)
        rep_ff = pipeline.verify_project(self.proj_dir, fail_fast=True)
        self.assertFalse(rep_ff.passed)
        self.assertEqual(rep_ff.stages_executed, ["syntax_check"])
        self.assertEqual(len(rep_ff.errors), 1)
        self.assertEqual(rep_ff.errors[0].stage, "syntax_check")

        # 2. Complete Mode (Must execute all stages and aggregate diagnostics)
        rep_full = pipeline.verify_project(self.proj_dir, fail_fast=False)
        self.assertFalse(rep_full.passed)
        self.assertEqual(
            rep_full.stages_executed,
            ["syntax_check", "schema_check", "asset_integrity", "simulation"],
        )
        stages_with_errors = {e.stage for e in rep_full.errors}
        self.assertTrue({"syntax_check", "schema_check", "asset_integrity"}.issubset(stages_with_errors))

    def test_multithreaded_concurrency_stress(self):
        """Stress-tests 10 concurrent verify_project invocations across separate project directories."""
        pipeline = VerificationPipeline()
        project_dirs: List[Path] = []

        # Create 10 valid independent Godot projects
        for i in range(10):
            p = self.proj_dir / f"proj_concurrent_{i}"
            (p / "scenes").mkdir(parents=True, exist_ok=True)
            (p / "scripts").mkdir(parents=True, exist_ok=True)
            (p / "assets" / "sprites").mkdir(parents=True, exist_ok=True)
            # Valid icon
            (p / "assets" / "sprites" / "icon.svg").write_text("<svg></svg>", encoding="utf-8")
            # Valid scene
            scene = SceneAST(name="main", path="scenes/main.tscn")
            scene.nodes.append(NodeAST(name="Root", type="Node2D", is_root=True))
            (p / "scenes" / "main.tscn").write_text(serialize_tscn(scene), encoding="utf-8")
            cfg = GodotProjectConfig(project_name=f"Game_{i}", main_scene_path="res://scenes/main.tscn")
            (p / "project.godot").write_text(generate_project_godot(cfg), encoding="utf-8")
            project_dirs.append(p)

        def verify_task(p_path: Path) -> VerificationReport:
            return pipeline.verify_project(p_path, fail_fast=False, simulation_frames=10)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            reports = list(executor.map(verify_task, project_dirs))

        self.assertEqual(len(reports), 10)
        for i, rep in enumerate(reports):
            self.assertTrue(rep.passed, f"Project {i} failed verification: {rep.errors}")
            self.assertEqual(len(rep.errors), 0)

    def test_target_polymorphism_and_error_handling(self):
        pipeline = VerificationPipeline()

        # Non-existent project path
        rep_missing = pipeline.verify_project(self.proj_dir / "does_not_exist")
        self.assertFalse(rep_missing.passed)
        self.assertTrue(any("does not exist" in e.message for e in rep_missing.errors))

        # Unsupported target type
        with self.assertRaises(TypeError):
            pipeline.verify_project(12345)  # type: ignore

        # Blackboard target
        bb = Blackboard(project_name="bb_test")
        rep_bb = pipeline.verify_project(bb)
        self.assertTrue(isinstance(rep_bb, VerificationReport))

    def test_route_errors_for_review_partitioning(self):
        errors = [
            DiagnosticError(stage="syntax_check", severity="ERROR", file_path="main.gd", line=5, message="Syntax err"),
            DiagnosticError(stage="schema_check", severity="ERROR", file_path="main.tscn", line=1, message="Schema err"),
            DiagnosticError(stage="asset_integrity", severity="ERROR", file_path="hero.png", line=1, message="Corrupt PNG"),
            DiagnosticError(stage="simulation", severity="ERROR", file_path="main.tscn", line=1, message="NaN coord"),
        ]
        rep = VerificationReport(
            passed=False,
            stages_executed=["syntax_check", "schema_check", "asset_integrity", "simulation"],
            errors=errors,
            warnings=[],
            execution_time_ms=12.5,
        )

        partitioned = route_errors_for_review(rep)
        self.assertIn("engineer", partitioned)
        self.assertIn("artist", partitioned)

        engineer_stages = {e.stage for e in partitioned["engineer"]}
        artist_stages = {e.stage for e in partitioned["artist"]}

        self.assertEqual(engineer_stages, {"syntax_check", "schema_check", "simulation"})
        self.assertEqual(artist_stages, {"asset_integrity"})
        self.assertEqual(len(partitioned["engineer"]), 3)
        self.assertEqual(len(partitioned["artist"]), 1)

    def test_event_bus_and_telemetry_streaming(self):
        event_bus = EventBus()
        captured_events: List[str] = []

        event_bus.subscribe("verification.started", lambda e: captured_events.append("started"))
        event_bus.subscribe("verification.stage_started", lambda e: captured_events.append(f"stage_start:{e.get('stage') if isinstance(e, dict) else getattr(e, 'stage', '')}"))
        event_bus.subscribe("verification.stage_completed", lambda e: captured_events.append(f"stage_done:{e.get('stage') if isinstance(e, dict) else getattr(e, 'stage', '')}"))
        event_bus.subscribe("verification.error", lambda e: captured_events.append("verif_error"))
        event_bus.subscribe("telemetry.verification_done", lambda e: captured_events.append("telem_done"))

        self._setup_multi_stage_failure_project(self.proj_dir)
        pipeline = VerificationPipeline(event_bus=event_bus)
        rep = pipeline.verify_project(self.proj_dir, fail_fast=True)

        self.assertIn("started", captured_events)
        self.assertIn("stage_start:syntax_check", captured_events)
        self.assertIn("stage_done:syntax_check", captured_events)
        self.assertIn("verif_error", captured_events)
        self.assertIn("telem_done", captured_events)


if __name__ == "__main__":
    unittest.main()
