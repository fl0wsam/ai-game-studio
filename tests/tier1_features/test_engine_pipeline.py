"""
tests/tier1_features/test_engine_pipeline.py - Tier 1 Systematic Feature Tests (F-ENG-01 through F-ENG-06)
Opaque-box, requirement-driven tests covering modular engine pipeline in isolation:
- F-ENG-01: Modular Engine Adapter Hub (>=5 tests)
- F-ENG-02: Godot 4 AST & Serializer (>=5 tests)
- F-ENG-03: Clean Hierarchy Packager (>=5 tests)
- F-ENG-04: Dynamic Asset Linker (>=5 tests)
- F-ENG-05: Game Archetype Templates (>=5 tests)
- F-ENG-06: Python/Web/Unity Multi-Engine Adapters (>=5 tests)
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from tests.conftest_base import StudioTestCase

from studio.core.models import (
    AssetItem,
    AssetManifest,
    AssetRequirement,
    AssetType,
    EngineTarget,
    GameDesignDocument,
    GameGenre,
    ScenePlan,
    ScriptSpec,
)
from studio.engine.adapters.base import (
    BaseEngineAdapter,
    EngineProjectManifest,
)
from studio.engine.adapters.godot_adapter import (
    GodotAdapter,
    GodotProjectConfig,
    format_godot_value,
    generate_project_godot,
    parse_tscn,
    serialize_tscn,
    sort_nodes_topologically,
)
from studio.engine.adapters.pygame_adapter import PygameAdapter
from studio.engine.adapters.unity_adapter import UnityAdapter
from studio.engine.adapters.web_adapter import WebAdapter
from studio.engine.archetypes.base import (
    ArchetypeRegistry,
    BaseArchetypeTemplate,
    get_archetype,
)
from studio.engine.archetypes.grid_puzzle import GridPuzzleTemplate
from studio.engine.archetypes.platformer import PlatformerTemplate
from studio.engine.archetypes.space_shooter import SpaceShooterTemplate
from studio.engine.archetypes.top_down_rpg import TopDownRPGTemplate
from studio.engine.ast_models import (
    Color,
    ConnectionAST,
    ExtResourceAST,
    ExtResourceRef,
    NodeAST,
    NodePathRef,
    Rect2,
    SceneAST,
    StringNameRef,
    SubResourceAST,
    SubResourceRef,
    Vector2,
    Vector2i,
    Vector3,
    generate_godot_uid,
)
from studio.engine.dynamic_asset_linker import (
    AssetBinding,
    DynamicAssetLinker,
)
from studio.engine.hierarchy import (
    CleanHierarchyPackager,
    HierarchyManifest,
    HierarchyValidationReport,
    PackagedFileMetadata,
)
from studio.engine.hub import (
    EngineHub,
    get_engine_hub,
)


# ============================================================================
# Helper Builders for Engine AST Models
# ============================================================================

def make_engine_scene(name: str = "MainLevel", path: str = "scenes/main_level.tscn") -> SceneAST:
    """Creates a valid SceneAST populated with Engine AST NodeAST instances."""
    scene = SceneAST(name=name, path=path)
    root = NodeAST(name=name, type="Node2D", parent=None)
    player = NodeAST(name="Player", type="CharacterBody2D", parent=".")
    sprite = NodeAST(name="Sprite2D", type="Sprite2D", parent="Player")
    scene.nodes = [root, player, sprite]
    return scene


# ============================================================================
# F-ENG-01: Modular Engine Adapter Hub Tests
# ============================================================================

class TestFeatureEng01EngineHub(StudioTestCase):
    """Systematic tests validating F-ENG-01: Modular Engine Adapter Hub."""

    def test_eng_01_01_default_hub_initialization(self) -> None:
        """Verify default EngineHub registers all built-in adapters and reports metadata."""
        hub = EngineHub(register_defaults=True)
        supported = hub.list_supported_engines()

        self.assertGreaterEqual(len(supported), 4)
        target_keys = {item["target"] for item in supported}
        self.assertIn("godot4", target_keys)
        self.assertIn("pygame", target_keys)
        self.assertIn("web_html5", target_keys)
        self.assertIn("unity", target_keys)

        for target in (EngineTarget.GODOT4, EngineTarget.PYGAME, EngineTarget.WEB_HTML5, EngineTarget.UNITY):
            self.assertTrue(hub.has_adapter(target))
            adapter = hub.get_adapter(target)
            self.assertIsInstance(adapter, BaseEngineAdapter)
            self.assertEqual(adapter.target, target)

        # Singleton getter
        global_hub = get_engine_hub()
        self.assertIsInstance(global_hub, EngineHub)
        self.assertTrue(global_hub.has_adapter(EngineTarget.GODOT4))

    def test_eng_01_02_target_resolution_and_aliases(self) -> None:
        """Verify target resolution handles strings, enums, case variations, and aliases."""
        hub = EngineHub(register_defaults=False)

        # Direct enum
        self.assertEqual(hub.resolve_target(EngineTarget.GODOT4), EngineTarget.GODOT4)
        self.assertEqual(hub.resolve_target(EngineTarget.PYGAME), EngineTarget.PYGAME)

        # Strings & aliases
        self.assertEqual(hub.resolve_target("godot"), EngineTarget.GODOT4)
        self.assertEqual(hub.resolve_target("godot4"), EngineTarget.GODOT4)
        self.assertEqual(hub.resolve_target("GODOT4"), EngineTarget.GODOT4)
        self.assertEqual(hub.resolve_target("pygame"), EngineTarget.PYGAME)
        self.assertEqual(hub.resolve_target("python"), EngineTarget.PYGAME)
        self.assertEqual(hub.resolve_target("web"), EngineTarget.WEB_HTML5)
        self.assertEqual(hub.resolve_target("html5"), EngineTarget.WEB_HTML5)
        self.assertEqual(hub.resolve_target("web_html5"), EngineTarget.WEB_HTML5)
        self.assertEqual(hub.resolve_target("phaser"), EngineTarget.WEB_HTML5)
        self.assertEqual(hub.resolve_target("canvas"), EngineTarget.WEB_HTML5)
        self.assertEqual(hub.resolve_target("unity"), EngineTarget.UNITY)

        # None or unknown fallback to godot4
        self.assertEqual(hub.resolve_target(None), EngineTarget.GODOT4)
        self.assertEqual(hub.resolve_target("unknown_engine_target"), EngineTarget.GODOT4)

    def test_eng_01_03_custom_adapter_registration_and_lifecycle(self) -> None:
        """Verify dynamic adapter registration, duplicate rejection, override, and unregistration."""
        hub = EngineHub(register_defaults=False)
        self.assertEqual(len(hub._adapters), 0)

        class MockTestAdapter(BaseEngineAdapter):
            @property
            def target(self) -> EngineTarget:
                return EngineTarget.GODOT4

            @property
            def display_name(self) -> str:
                return "Mock Godot Adapter"

            def export_project(self, gdd, manifest, scenes, scripts, output_dir, **kwargs):
                return EngineProjectManifest(
                    target=self.target,
                    project_name=gdd.title,
                    output_dir=str(output_dir),
                    entry_file="project.godot",
                    files_created=["project.godot"],
                )

        adapter1 = MockTestAdapter()
        hub.register_adapter(adapter1)
        self.assertTrue(hub.has_adapter(EngineTarget.GODOT4))
        self.assertEqual(hub.get_adapter(EngineTarget.GODOT4).display_name, "Mock Godot Adapter")

        # Duplicate registration without override must raise ValueError
        adapter2 = MockTestAdapter()
        with self.assertRaises(ValueError):
            hub.register_adapter(adapter2, override=False)

        # Duplicate registration with override must succeed
        hub.register_adapter(adapter2, override=True)
        self.assertIs(hub.get_adapter(EngineTarget.GODOT4), adapter2)

        # Unregister
        unreg = hub.unregister_adapter(EngineTarget.GODOT4)
        self.assertIs(unreg, adapter2)
        self.assertFalse(hub.has_adapter(EngineTarget.GODOT4))

    def test_eng_01_04_adapter_lookup_error_handling(self) -> None:
        """Verify get_adapter on missing target raises KeyError with helpful list of available engines."""
        hub = EngineHub(register_defaults=False)
        with self.assertRaises(KeyError) as ctx:
            hub.get_adapter(EngineTarget.PYGAME)
        self.assertIn("No adapter registered for engine", str(ctx.exception))
        self.assertIn("Available:", str(ctx.exception))

    def test_eng_01_05_single_export_dispatch(self) -> None:
        """Verify hub.export delegates to the correct adapter and produces valid project manifest."""
        hub = EngineHub(register_defaults=True)
        gdd = self.get_mock_gdd(title="HubRunner")
        manifest = self.get_mock_manifest("HubRunner")
        scene = make_engine_scene()
        script = self.get_mock_script()

        target_dir = self.workspace_dir / "hub_export_godot"
        result = hub.export(
            target="godot4",
            gdd=gdd,
            manifest=manifest,
            scenes=[scene],
            scripts=[script],
            output_dir=target_dir,
        )

        self.assertIsInstance(result, EngineProjectManifest)
        self.assertEqual(result.target, EngineTarget.GODOT4)
        self.assertEqual(result.project_name, "HubRunner")
        self.assertTrue(target_dir.is_dir())
        self.assertTrue((target_dir / "project.godot").is_file())
        self.assertGreater(len(result.files_created), 0)

    def test_eng_01_06_multi_export_dispatch(self) -> None:
        """Verify hub.export_multi dispatches across multiple target subdirectories."""
        hub = EngineHub(register_defaults=True)
        gdd = self.get_mock_gdd(title="MultiGame")
        manifest = self.get_mock_manifest("MultiGame")
        scene = make_engine_scene()
        script = self.get_mock_script()

        base_dir = self.workspace_dir / "multi_export"
        targets = ["godot4", "pygame", "web_html5"]
        results = hub.export_multi(
            targets=targets,
            gdd=gdd,
            manifest=manifest,
            scenes=[scene],
            scripts=[script],
            base_output_dir=base_dir,
        )

        self.assertEqual(set(results.keys()), {"godot4", "pygame", "web_html5"})
        for t_key, manifest_res in results.items():
            self.assertIsInstance(manifest_res, EngineProjectManifest)
            sub_dir = base_dir / t_key
            self.assertTrue(sub_dir.is_dir(), f"Expected directory {sub_dir} to exist")


# ============================================================================
# F-ENG-02: Godot 4 AST & Serializer Tests
# ============================================================================

class TestFeatureEng02GodotAST(StudioTestCase):
    """Systematic tests validating F-ENG-02: Godot 4 AST dataclass model and serializer."""

    def test_eng_02_01_variant_formatting(self) -> None:
        """Verify Godot Variant wrapper types format accurately into Godot text representation."""
        self.assertEqual(Vector2(10.5, 20.0).to_godot(), "Vector2(10.5, 20.0)")
        self.assertEqual(Vector2i(3, 4).to_godot(), "Vector2i(3, 4)")
        self.assertEqual(Vector3(1.0, 2.0, 3.0).to_godot(), "Vector3(1.0, 2.0, 3.0)")
        self.assertEqual(Color(1.0, 0.5, 0.25, 1.0).to_godot(), "Color(1.0, 0.5, 0.25, 1.0)")
        self.assertEqual(Rect2(0.0, 0.0, 100.0, 200.0).to_godot(), "Rect2(0.0, 0.0, 100.0, 200.0)")
        self.assertEqual(ExtResourceRef("1_tex").to_godot(), 'ExtResource("1_tex")')
        self.assertEqual(SubResourceRef("sub_1").to_godot(), 'SubResource("sub_1")')
        self.assertEqual(StringNameRef("SFX").to_godot(), '&"SFX"')
        self.assertEqual(NodePathRef("Player/Sprite2D").to_godot(), 'NodePath("Player/Sprite2D")')

        # format_godot_value helper
        self.assertEqual(format_godot_value(True), "true")
        self.assertEqual(format_godot_value(False), "false")
        self.assertEqual(format_godot_value(None), "null")
        self.assertEqual(format_godot_value(42), "42")
        self.assertEqual(format_godot_value(300.0), "300.0")
        self.assertEqual(format_godot_value("hello world"), '"hello world"')
        self.assertEqual(format_godot_value('quote "test"'), '"quote \\"test\\""')

        # List of primitives
        self.assertEqual(format_godot_value(["a", "b"]), '["a", "b"]')
        # 2-element list interpreted as Vector2
        self.assertEqual(format_godot_value([10, 20]), "Vector2(10, 20)")
        # 3-element list interpreted as Vector3
        self.assertEqual(format_godot_value([1, 2, 3]), "Vector3(1, 2, 3)")

    def test_eng_02_02_scene_ast_hierarchy_and_roots(self) -> None:
        """Verify SceneAST tree navigation, root detection, and single-root enforcement."""
        scene = SceneAST(name="LevelOne", path="scenes/level_one.tscn")
        root = NodeAST(name="LevelOne", type="Node2D")
        player = NodeAST(name="Player", type="CharacterBody2D", parent=".")
        sprite = NodeAST(name="Sprite2D", type="Sprite2D", parent="Player")

        scene.add_node(root)
        scene.add_node(player)
        scene.add_node(sprite)

        self.assertIs(scene.get_root(), root)
        self.assertIs(scene.find_node("Player"), player)
        self.assertIs(scene.find_node("Sprite2D"), sprite)
        self.assertIsNone(scene.find_node("NonExistent"))

        children_of_root = scene.get_children("LevelOne")
        self.assertIn(player, children_of_root)

        # Enforce single root: adding a second root node must raise ValueError
        second_root = NodeAST(name="SecondRoot", type="Node2D", parent=None)
        with self.assertRaises(ValueError):
            scene.add_node(second_root)

    def test_eng_02_03_resource_registration_and_deduplication(self) -> None:
        """Verify ext_resource and sub_resource addition, deduplication, and load_steps count."""
        scene = SceneAST(name="SceneA", path="scenes/scene_a.tscn")
        self.assertEqual(scene.load_steps, 0)

        id1 = scene.add_ext_resource("Texture2D", "assets/sprites/player.png")
        self.assertEqual(len(scene.ext_resources), 1)
        self.assertTrue(scene.ext_resources[0].path.startswith("res://"))
        self.assertTrue(scene.ext_resources[0].uid.startswith("uid://"))

        # Deduplication: re-adding same path returns identical ID
        id2 = scene.add_ext_resource("Texture2D", "res://assets/sprites/player.png")
        self.assertEqual(id1, id2)
        self.assertEqual(len(scene.ext_resources), 1)

        # SubResource addition and update
        sub_id = scene.add_sub_resource("CapsuleShape2D", "capsule_1", {"radius": 12.0})
        self.assertEqual(sub_id, "capsule_1")
        self.assertEqual(len(scene.sub_resources), 1)

        # load_steps = count(ext) + count(sub) + 1 = 1 + 1 + 1 = 3
        self.assertEqual(scene.load_steps, 3)

        # Update sub_resource
        scene.add_sub_resource("CapsuleShape2D", "capsule_1", {"height": 36.0})
        self.assertEqual(scene.sub_resources[0].properties["height"], 36.0)
        self.assertEqual(scene.sub_resources[0].properties["radius"], 12.0)

    def test_eng_02_04_topological_sort_and_tscn_serialization(self) -> None:
        """Verify nodes are topologically sorted before serialization and .tscn output is valid."""
        scene = SceneAST(name="Main", path="scenes/main.tscn")
        ext_id = scene.add_ext_resource("Texture2D", "res://assets/sprites/icon.png", "1_icon")
        sub_id = scene.add_sub_resource("RectangleShape2D", "rect_1", {"size": Vector2(32.0, 32.0)})

        # Intentionally append in reverse hierarchy order: grandchild -> child -> root
        grandchild = NodeAST(name="ChildSprite", type="Sprite2D", parent="Player", properties={"texture": ExtResourceRef(ext_id)})
        child = NodeAST(name="Player", type="CharacterBody2D", parent=".")
        root = NodeAST(name="Main", type="Node2D")

        scene.nodes = [grandchild, child, root]

        # Topological sort verification
        sorted_nodes = sort_nodes_topologically(scene.nodes)
        self.assertEqual(sorted_nodes[0].name, "Main")
        self.assertEqual(sorted_nodes[1].name, "Player")
        self.assertEqual(sorted_nodes[2].name, "ChildSprite")

        # Serializer output
        tscn_text = serialize_tscn(scene)
        self.assertTrue(tscn_text.startswith("[gd_scene load_steps=3 format=3 uid="))
        self.assertIn('[ext_resource type="Texture2D"', tscn_text)
        self.assertIn('[sub_resource type="RectangleShape2D"', tscn_text)
        self.assertIn('[node name="Main" type="Node2D"]', tscn_text)
        self.assertIn('[node name="Player" type="CharacterBody2D" parent="."]', tscn_text)
        self.assertIn('[node name="ChildSprite" type="Sprite2D" parent="Player"]', tscn_text)
        self.assertIn('texture = ExtResource("1_icon")', tscn_text)

    def test_eng_02_05_roundtrip_tscn_parsing(self) -> None:
        """Verify serialize_tscn and parse_tscn roundtrip fidelity."""
        scene = SceneAST(name="RoundtripScene", path="scenes/roundtrip.tscn")
        tex_id = scene.add_ext_resource("Texture2D", "res://assets/sprites/hero.png", "1_hero")
        sub_id = scene.add_sub_resource("CircleShape2D", "circle_1", {"radius": 16.0})

        root = NodeAST(name="RoundtripScene", type="Node2D")
        hero = NodeAST(
            name="Hero",
            type="CharacterBody2D",
            parent=".",
            properties={"collision_layer": 2, "motion_mode": 0},
        )
        sprite = NodeAST(
            name="HeroSprite",
            type="Sprite2D",
            parent="Hero",
            properties={"texture": ExtResourceRef(tex_id)},
        )
        scene.nodes = [root, hero, sprite]
        scene.add_connection("body_entered", "Hero", ".", "_on_hero_body_entered")

        # Serialize
        serialized = serialize_tscn(scene)
        # Parse back
        parsed = parse_tscn(serialized, scene_path="scenes/roundtrip.tscn")

        self.assertEqual(parsed.name, "RoundtripScene")
        self.assertEqual(len(parsed.ext_resources), 1)
        self.assertEqual(parsed.ext_resources[0].id, "1_hero")
        self.assertEqual(parsed.ext_resources[0].path, "res://assets/sprites/hero.png")

        self.assertEqual(len(parsed.sub_resources), 1)
        self.assertEqual(parsed.sub_resources[0].id, "circle_1")

        self.assertEqual(len(parsed.nodes), 3)
        parsed_hero = parsed.find_node("Hero")
        self.assertIsNotNone(parsed_hero)
        self.assertEqual(parsed_hero.properties.get("collision_layer"), 2)

        self.assertEqual(len(parsed.connections), 1)
        self.assertEqual(parsed.connections[0].signal, "body_entered")
        self.assertEqual(parsed.connections[0].method, "_on_hero_body_entered")

    def test_eng_02_06_project_godot_generation(self) -> None:
        """Verify project.godot generation creates canonical sections and configurations."""
        cfg = GodotProjectConfig(
            project_name="CyberStudioTest",
            main_scene_path="res://scenes/main_level.tscn",
            viewport_width=1920,
            viewport_height=1080,
            pixel_art_mode=True,
            default_gravity=980.0,
            autoloads={"GameManager": "res://scripts/game_manager.gd"},
        )
        text = generate_project_godot(cfg)

        self.assertIn("config_version=5", text)
        self.assertIn('[application]', text)
        self.assertIn('config/name="CyberStudioTest"', text)
        self.assertIn('run/main_scene="res://scenes/main_level.tscn"', text)
        self.assertIn('[autoload]', text)
        self.assertIn('GameManager="*res://scripts/game_manager.gd"', text)
        self.assertIn('[display]', text)
        self.assertIn('window/size/viewport_width=1920', text)
        self.assertIn('window/size/viewport_height=1080', text)
        self.assertIn('[physics]', text)
        self.assertIn('2d/default_gravity=980.0', text)
        self.assertIn('[rendering]', text)
        self.assertIn('renderer/rendering_method="gl_compatibility"', text)
        self.assertIn('textures/canvas_textures/default_texture_filter=0', text)


# ============================================================================
# F-ENG-03: Clean Hierarchy Packager Tests
# ============================================================================

class TestFeatureEng03Hierarchy(StudioTestCase):
    """Systematic tests validating F-ENG-03: Clean Hierarchy Packager."""

    def test_eng_03_01_canonical_directory_provisioning(self) -> None:
        """Verify all 10 canonical subdirectories are created cleanly and idempotently."""
        packager = CleanHierarchyPackager(target_engine="godot4")
        created_paths = packager.create_hierarchy(self.workspace_dir)

        expected_dirs = [
            "assets/sprites",
            "assets/textures",
            "assets/backgrounds",
            "assets/tilesets",
            "assets/ui",
            "audio/sfx",
            "audio/music",
            "scenes",
            "scripts",
            "docs",
        ]
        for rel in expected_dirs:
            target = self.workspace_dir / rel
            self.assertTrue(target.is_dir(), f"Expected canonical directory {rel} to exist")

        # Idempotence: calling create_hierarchy again does not error
        second_run = packager.create_hierarchy(self.workspace_dir)
        self.assertGreaterEqual(len(second_run), 10)

    def test_eng_03_02_package_assets_with_routing_and_checksums(self) -> None:
        """Verify binary assets from manifest are routed to disk with valid SHA-256 hashes."""
        packager = CleanHierarchyPackager()
        manifest = self.get_mock_manifest("PackagerTest")

        meta_list = packager.package_assets(self.workspace_dir, manifest, overwrite=True)
        self.assertEqual(len(meta_list), len(manifest.items))

        for meta in meta_list:
            file_on_disk = self.workspace_dir / meta.rel_path
            self.assertTrue(file_on_disk.is_file())
            content = file_on_disk.read_bytes()
            self.assertEqual(len(content), meta.byte_size)
            self.assertEqual(hashlib.sha256(content).hexdigest(), meta.checksum_sha256)
            self.assertIn(meta.category, ("asset", "audio"))

    def test_eng_03_03_package_scripts_with_mime_types(self) -> None:
        """Verify engine scripts are packaged to scripts/ with correct mime types."""
        packager = CleanHierarchyPackager()
        scripts = [
            ScriptSpec(rel_path="player.gd", language="gdscript", code="extends Node2D\n"),
            ScriptSpec(rel_path="scripts/main.py", language="python", code="print('hello')\n"),
        ]

        meta_list = packager.package_scripts(self.workspace_dir, scripts, overwrite=True)
        self.assertEqual(len(meta_list), 2)

        for meta in meta_list:
            disk_path = self.workspace_dir / meta.rel_path
            self.assertTrue(disk_path.is_file())
            self.assertTrue(meta.rel_path.startswith("scripts/"))
            self.assertEqual(meta.category, "script")
            if meta.rel_path.endswith(".gd"):
                self.assertEqual(meta.mime_type, "text/x-gdscript")
            elif meta.rel_path.endswith(".py"):
                self.assertEqual(meta.mime_type, "text/x-python")

    def test_eng_03_04_markdown_documentation_generation(self) -> None:
        """Verify GDD.md, LORE.md, and CONTROLS.md are generated and contain required sections."""
        packager = CleanHierarchyPackager()
        gdd = self.get_mock_gdd(title="NeonCyber")

        doc_meta = packager.package_docs(self.workspace_dir, gdd)
        self.assertGreaterEqual(len(doc_meta), 3)

        gdd_file = self.workspace_dir / "docs" / "GDD.md"
        self.assertTrue(gdd_file.is_file())
        gdd_text = gdd_file.read_text(encoding="utf-8")
        self.assertIn("# NeonCyber", gdd_text)
        self.assertIn("High Concept", gdd_text)
        self.assertIn("Core Mechanics", gdd_text)

        controls_file = self.workspace_dir / "docs" / "CONTROLS.md"
        self.assertTrue(controls_file.is_file())
        controls_text = controls_file.read_text(encoding="utf-8")
        self.assertIn("Controls Guide", controls_text)
        self.assertIn("Move Left", controls_text)

        lore_file = self.workspace_dir / "docs" / "LORE.md"
        self.assertTrue(lore_file.is_file())

    def test_eng_03_05_hierarchy_verification_pass_and_fail(self) -> None:
        """Verify verify_hierarchy accurately checks compliance, detects missing dirs and stray files."""
        packager = CleanHierarchyPackager()
        packager.create_hierarchy(self.workspace_dir)

        # Baseline clean project check
        report = packager.verify_hierarchy(self.workspace_dir)
        self.assertTrue(report.passed)
        self.assertEqual(len(report.missing_directories), 0)
        self.assertEqual(len(report.empty_files), 0)

        # Failure condition 1: Delete a canonical directory
        (self.workspace_dir / "scenes").rmdir()
        report_missing = packager.verify_hierarchy(self.workspace_dir)
        self.assertFalse(report_missing.passed)
        self.assertIn("scenes", report_missing.missing_directories)

        # Restore directory
        (self.workspace_dir / "scenes").mkdir()

        # Failure condition 2: Stray root file
        stray = self.workspace_dir / "unknown_junk.bin"
        stray.write_bytes(b"data")
        report_stray = packager.verify_hierarchy(self.workspace_dir)
        self.assertIn("unknown_junk.bin", report_stray.stray_root_files)

        # Failure condition 3: Empty file
        empty = self.workspace_dir / "project.godot"
        empty.write_text("")
        report_empty = packager.verify_hierarchy(self.workspace_dir)
        self.assertFalse(report_empty.passed)
        self.assertIn("project.godot", report_empty.empty_files)

    def test_eng_03_06_path_traversal_sanitization(self) -> None:
        """Verify sanitize_path strictly prevents directory traversal attacks outside the root."""
        packager = CleanHierarchyPackager()

        # Valid subpaths
        p1 = packager.sanitize_path(self.workspace_dir, "assets/sprites/player.png")
        self.assertEqual(p1, (self.workspace_dir / "assets" / "sprites" / "player.png").resolve())

        # Path traversal attempts must raise ValueError
        with self.assertRaises(ValueError):
            packager.sanitize_path(self.workspace_dir, "../../etc/passwd")

        with self.assertRaises(ValueError):
            packager.sanitize_path(self.workspace_dir, "assets/../../../shadow")

        with self.assertRaises(ValueError):
            packager.sanitize_path(self.workspace_dir, "/etc/shadow")


# ============================================================================
# F-ENG-04: Dynamic Asset Linker Tests
# ============================================================================

class TestFeatureEng04AssetLinker(StudioTestCase):
    """Systematic tests validating F-ENG-04: Dynamic Asset Linker."""

    def test_eng_04_01_multi_stage_asset_resolution(self) -> None:
        """Verify fuzzy asset resolution by exact ID, case-insensitivity, category prefix, and filename."""
        manifest = self.get_mock_manifest("ResolutionTest")
        linker = DynamicAssetLinker(manifest=manifest)

        # Exact match
        item = linker.resolve_asset("sprite_player")
        self.assertIsNotNone(item)
        self.assertEqual(item.asset_id, "sprite_player")

        # Case-insensitive match
        item_ci = linker.resolve_asset("SPRITE_PLAYER")
        self.assertIsNotNone(item_ci)
        self.assertEqual(item_ci.asset_id, "sprite_player")

        # Prefix stripped: "player" matches "sprite_player"
        item_stripped = linker.resolve_asset("player")
        self.assertIsNotNone(item_stripped)
        self.assertEqual(item_stripped.asset_id, "sprite_player")

        # Audio prefix stripped: "jump" matches "sfx_jump"
        item_sfx = linker.resolve_asset("jump")
        self.assertIsNotNone(item_sfx)
        self.assertEqual(item_sfx.asset_id, "sfx_jump")

        # Filename match: "theme.wav" matches "audio/music/theme.wav"
        item_file = linker.resolve_asset("theme.wav")
        self.assertIsNotNone(item_file)
        self.assertEqual(item_file.asset_id, "bgm_theme")

        # Non-existent asset
        self.assertIsNone(linker.resolve_asset("completely_nonexistent_asset_xyz"))

    def test_eng_04_02_texture_node_binding(self) -> None:
        """Verify linker binds sprite texture strings to ExtResourceRefs with proper res:// paths."""
        manifest = self.get_mock_manifest("TextureBindTest")
        linker = DynamicAssetLinker(manifest=manifest)

        scene = SceneAST(name="MainLevel", path="scenes/main_level.tscn")
        root = NodeAST(name="MainLevel", type="Node2D")
        sprite_node = NodeAST(
            name="PlayerSprite",
            type="Sprite2D",
            parent=".",
            properties={"texture": "sprite_player"},
        )
        scene.nodes = [root, sprite_node]

        linked = linker.link_scene(scene)

        # Texture property must be transformed to ExtResourceRef
        tex_ref = linked.nodes[1].properties["texture"]
        self.assertIsInstance(tex_ref, ExtResourceRef)

        # ExtResource must be registered with Texture2D and res:// path
        self.assertEqual(len(linked.ext_resources), 1)
        ext = linked.ext_resources[0]
        self.assertEqual(ext.id, tex_ref.id)
        self.assertEqual(ext.type, "Texture2D")
        self.assertEqual(ext.path, "res://assets/sprites/player.png")

    def test_eng_04_03_audio_stream_binding_and_bus_assignment(self) -> None:
        """Verify linker binds AudioStreamPlayer nodes, sets autoplay/looping for BGM, and routes buses."""
        manifest = self.get_mock_manifest("AudioBindTest")
        linker = DynamicAssetLinker(manifest=manifest)

        scene = SceneAST(name="AudioScene", path="scenes/audio_scene.tscn")
        root = NodeAST(name="AudioScene", type="Node2D")
        sfx_node = NodeAST(
            name="JumpAudio",
            type="AudioStreamPlayer2D",
            parent=".",
            properties={"stream": "sfx_jump"},
        )
        bgm_node = NodeAST(
            name="ThemeBGM",
            type="AudioStreamPlayer",
            parent=".",
            properties={"stream": "bgm_theme"},
        )
        scene.nodes = [root, sfx_node, bgm_node]

        linked = linker.link_scene(scene)

        # Verify SFX binding
        self.assertIsInstance(sfx_node.properties["stream"], ExtResourceRef)
        self.assertEqual(sfx_node.properties["bus"], StringNameRef("SFX"))

        # Verify BGM binding
        self.assertIsInstance(bgm_node.properties["stream"], ExtResourceRef)
        self.assertEqual(bgm_node.properties["bus"], StringNameRef("Music"))
        self.assertTrue(bgm_node.properties["autoplay"])
        self.assertTrue(bgm_node.properties["parameters/looping"])

    def test_eng_04_04_resource_deduplication(self) -> None:
        """Verify multiple nodes referencing the same asset share a single ExtResourceAST definition."""
        manifest = self.get_mock_manifest("DedupTest")
        linker = DynamicAssetLinker(manifest=manifest)

        scene = SceneAST(name="MultiSprite", path="scenes/multi.tscn")
        root = NodeAST(name="MultiSprite", type="Node2D")
        s1 = NodeAST(name="Sprite1", type="Sprite2D", parent=".", properties={"texture": "sprite_player"})
        s2 = NodeAST(name="Sprite2", type="Sprite2D", parent=".", properties={"texture": "player"})
        scene.nodes = [root, s1, s2]

        linked = linker.link_scene(scene)

        # Exactly 1 external resource registered despite 2 references
        self.assertEqual(len(linked.ext_resources), 1)
        res_id = linked.ext_resources[0].id
        self.assertEqual(linked.nodes[1].properties["texture"].id, res_id)
        self.assertEqual(linked.nodes[2].properties["texture"].id, res_id)

    def test_eng_04_05_actor_sprite_synthesis(self) -> None:
        """Verify linker automatically synthesizes child Sprite2D nodes for actors lacking them."""
        manifest = self.get_mock_manifest("SynthesisTest")
        linker = DynamicAssetLinker(manifest=manifest)

        scene = SceneAST(name="ActorScene", path="scenes/actor.tscn")
        player_body = NodeAST(name="Player", type="CharacterBody2D", parent=None)
        scene.nodes = [player_body]

        linked = linker.link_scene(scene)

        # Must have synthesized a child Sprite2D
        child_sprite = next((n for n in linked.nodes if n.type == "Sprite2D"), None)
        self.assertIsNotNone(child_sprite, "Linker should have synthesized a Sprite2D for Player")
        self.assertEqual(child_sprite.parent, ".")
        self.assertIsInstance(child_sprite.properties["texture"], ExtResourceRef)

    def test_eng_04_06_physics_body_collision_shape_synthesis(self) -> None:
        """Verify linker automatically synthesizes CollisionShape2D and SubResource for bodies lacking them."""
        manifest = self.get_mock_manifest("CollisionSynthTest")
        linker = DynamicAssetLinker(manifest=manifest)

        scene = SceneAST(name="PhysicsScene", path="scenes/physics.tscn")
        body = NodeAST(name="Player", type="CharacterBody2D", parent=None)
        scene.nodes = [body]

        linked = linker.link_scene(scene)

        # Must have synthesized a child CollisionShape2D
        child_col = next((n for n in linked.nodes if n.type == "CollisionShape2D"), None)
        self.assertIsNotNone(child_col, "Linker should have synthesized a CollisionShape2D")
        shape_ref = child_col.properties["shape"]
        self.assertIsInstance(shape_ref, SubResourceRef)

        # Sub-resource must be defined in sub_resources
        sub_res = next((s for s in linked.sub_resources if s.id == shape_ref.id), None)
        self.assertIsNotNone(sub_res)
        self.assertEqual(sub_res.type, "CapsuleShape2D")

    def test_eng_04_07_procedural_scene_building_from_plan(self) -> None:
        """Verify build_scene_from_plan constructs complete valid SceneAST from GDD ScenePlan."""
        manifest = self.get_mock_manifest("PlanTest")
        linker = DynamicAssetLinker(manifest=manifest)

        plan = ScenePlan(
            scene_id="main_level",
            name="MainLevel",
            path="scenes/main_level.tscn",
            description="Test level",
            root_node_type="Node2D",
            required_assets=["sprite_player", "sfx_jump"],
            script_name="scripts/main_level.gd",
        )

        built_scene = linker.build_scene_from_plan(plan)
        self.assertEqual(built_scene.name, "MainLevel")
        self.assertEqual(built_scene.path, "scenes/main_level.tscn")

        root = built_scene.get_root()
        self.assertIsNotNone(root)
        self.assertEqual(root.type, "Node2D")
        self.assertIsNotNone(root.script_id)

        # Sprite2D and AudioStreamPlayer2D nodes created
        node_types = [n.type for n in built_scene.nodes]
        self.assertIn("Sprite2D", node_types)
        self.assertIn("AudioStreamPlayer2D", node_types)


# ============================================================================
# F-ENG-05: Game Archetype Templates Tests
# ============================================================================

class TestFeatureEng05Archetypes(StudioTestCase):
    """Systematic tests validating F-ENG-05: Game Archetype Templates."""

    def test_eng_05_01_archetype_registry_lookup_and_aliases(self) -> None:
        """Verify ArchetypeRegistry resolves all 4 genres, string aliases, and default fallback."""
        # 1. 2D Platformer
        p1 = ArchetypeRegistry.get(GameGenre.PLATFORMER_2D)
        self.assertIsInstance(p1, PlatformerTemplate)
        self.assertIsInstance(ArchetypeRegistry.get("platformer"), PlatformerTemplate)
        self.assertIsInstance(ArchetypeRegistry.get("2d_platformer"), PlatformerTemplate)

        # 2. Top-Down RPG
        r1 = ArchetypeRegistry.get(GameGenre.TOP_DOWN_RPG)
        self.assertIsInstance(r1, TopDownRPGTemplate)
        self.assertIsInstance(ArchetypeRegistry.get("rpg"), TopDownRPGTemplate)
        self.assertIsInstance(ArchetypeRegistry.get("top_down_rpg"), TopDownRPGTemplate)

        # 3. Space Shooter
        s1 = ArchetypeRegistry.get(GameGenre.SPACE_SHOOTER)
        self.assertIsInstance(s1, SpaceShooterTemplate)
        self.assertIsInstance(ArchetypeRegistry.get("shmup"), SpaceShooterTemplate)
        self.assertIsInstance(ArchetypeRegistry.get("shooter"), SpaceShooterTemplate)

        # 4. Grid Match Puzzle
        g1 = ArchetypeRegistry.get(GameGenre.GRID_PUZZLE)
        self.assertIsInstance(g1, GridPuzzleTemplate)
        self.assertIsInstance(ArchetypeRegistry.get("puzzle"), GridPuzzleTemplate)
        self.assertIsInstance(ArchetypeRegistry.get("match3"), GridPuzzleTemplate)

        # Graceful fallback on unknown
        fallback = ArchetypeRegistry.get("completely_unknown_genre_xyz")
        self.assertIsInstance(fallback, PlatformerTemplate)

    def test_eng_05_02_platformer_template_scenes_and_scripts(self) -> None:
        """Verify PlatformerTemplate provides complete GDD, scenes, scripts, and asset declarations."""
        template = PlatformerTemplate()
        gdd = template.get_gdd(prompt="Cyber Platformer", seed=101)

        self.assertEqual(gdd.genre, GameGenre.PLATFORMER_2D.value)
        self.assertGreater(len(gdd.gameplay_mechanics), 0)
        self.assertGreater(len(gdd.required_assets), 0)

        # Scenes
        scenes = template.get_scenes(gdd)
        scene_names = [s.name for s in scenes]
        self.assertIn("MainLevel", scene_names)
        self.assertIn("Player", scene_names)
        self.assertIn("Coin", scene_names)
        self.assertIn("Hazard", scene_names)
        self.assertIn("HUD", scene_names)

        # Scripts
        scripts = template.get_scripts(gdd, target_engine="godot4")
        script_paths = [s.rel_path for s in scripts]
        self.assertIn("scripts/player.gd", script_paths)
        self.assertIn("scripts/coin.gd", script_paths)

        player_script = next(s for s in scripts if s.rel_path == "scripts/player.gd")
        self.assertIn("CharacterBody2D", player_script.code)
        self.assertIn("move_and_slide", player_script.code)

    def test_eng_05_03_top_down_rpg_template_scenes_and_scripts(self) -> None:
        """Verify TopDownRPGTemplate provides 8-directional hero mechanics, NPC interaction, and dialogue."""
        template = TopDownRPGTemplate()
        gdd = template.get_gdd(prompt="Fantasy RPG", seed=202)

        self.assertEqual(gdd.genre, GameGenre.TOP_DOWN_RPG.value)
        scenes = template.get_scenes(gdd)
        scene_names = [s.name for s in scenes]
        self.assertIn("WorldMap", scene_names)
        self.assertIn("Hero", scene_names)
        self.assertIn("NPC", scene_names)

        scripts = template.get_scripts(gdd, target_engine="godot4")
        hero_script = next(s for s in scripts if s.rel_path == "scripts/hero.gd")
        self.assertIn("speed", hero_script.code)
        self.assertIn("attack", hero_script.code)
        self.assertIn("SwordHitbox", hero_script.code)

    def test_eng_05_04_space_shooter_template_scenes_and_scripts(self) -> None:
        """Verify SpaceShooterTemplate provides clamped starship movement, laser projectile, and asteroids."""
        template = SpaceShooterTemplate()
        gdd = template.get_gdd(prompt="Cosmic Shmup", seed=303)

        self.assertEqual(gdd.genre, GameGenre.SPACE_SHOOTER.value)
        scenes = template.get_scenes(gdd)
        scene_names = [s.name for s in scenes]
        self.assertIn("SpaceArena", scene_names)
        self.assertIn("Starship", scene_names)
        self.assertIn("Laser", scene_names)
        self.assertIn("Asteroid", scene_names)

        scripts = template.get_scripts(gdd, target_engine="godot4")
        ship_script = next(s for s in scripts if s.rel_path == "scripts/starship.gd")
        self.assertIn("clamp", ship_script.code)
        self.assertIn("fire_laser", ship_script.code)

    def test_eng_05_05_grid_puzzle_template_scenes_and_scripts(self) -> None:
        """Verify GridPuzzleTemplate generates discrete grid state array and match-3 swap logic."""
        template = GridPuzzleTemplate()
        gdd = template.get_gdd(prompt="Gem Matcher", seed=404)

        self.assertEqual(gdd.genre, GameGenre.GRID_PUZZLE.value)
        scenes = template.get_scenes(gdd)
        scene_names = [s.name for s in scenes]
        self.assertIn("PuzzleBoard", scene_names)

        scripts = template.get_scripts(gdd, target_engine="godot4")
        board_script = next(s for s in scripts if s.rel_path == "scripts/puzzle_board.gd")
        self.assertIn("GRID_WIDTH", board_script.code)
        self.assertIn("find_matches", board_script.code)

    def test_eng_05_06_multi_engine_script_generation(self) -> None:
        """Verify archetypes produce both GDScript and Python scripts based on target_engine."""
        template = TopDownRPGTemplate()
        gdd = template.get_gdd(seed=505)

        all_scripts = template.get_scripts(gdd, target_engine="godot4")
        self.assertTrue(any(s.rel_path.endswith(".gd") for s in all_scripts))
        self.assertTrue(any("hero.gd" in s.rel_path for s in all_scripts))
        self.assertTrue(any(s.rel_path.endswith(".py") for s in all_scripts))
        self.assertTrue(any("rpg_game.py" in s.rel_path for s in all_scripts))


# ============================================================================
# F-ENG-06: Multi-Engine Export Adapters Tests
# ============================================================================

class TestFeatureEng06Adapters(StudioTestCase):
    """Systematic tests validating F-ENG-06: Python, Web, and Unity Export Adapters."""

    def test_eng_06_01_base_engine_adapter_protocol(self) -> None:
        """Verify BaseEngineAdapter protocol, prerequisite checking, and shared documentation helpers."""
        adapter = GodotAdapter()
        prereqs = adapter.validate_prerequisites()
        self.assertIsInstance(prereqs, dict)
        self.assertIn("available", prereqs)

        gdd = self.get_mock_gdd("ProtocolTest")
        doc_files = adapter.write_documentation(gdd, self.workspace_dir)
        self.assertIn("docs/GDD.md", doc_files)
        self.assertIn("docs/CONTROLS.md", doc_files)
        self.assertTrue((self.workspace_dir / "docs" / "GDD.md").is_file())

    def test_eng_06_02_godot_adapter_export(self) -> None:
        """Verify GodotAdapter exports project.godot, .tscn scenes, .gd scripts, and assets."""
        adapter = GodotAdapter()
        gdd = self.get_mock_gdd("GodotExportTest")
        manifest = self.get_mock_manifest("GodotExportTest")
        scene = make_engine_scene()
        script = self.get_mock_script()

        out_dir = self.workspace_dir / "godot_project"
        res = adapter.export_project(gdd, manifest, [scene], [script], out_dir)

        self.assertEqual(res.target, EngineTarget.GODOT4)
        self.assertTrue((out_dir / "project.godot").is_file())
        self.assertTrue((out_dir / "scenes" / "main_level.tscn").is_file())
        self.assertTrue((out_dir / "scripts" / "player.gd").is_file())
        self.assertTrue((out_dir / "assets" / "sprites" / "player.png").is_file())
        self.assertTrue((out_dir / "docs" / "GDD.md").is_file())

    def test_eng_06_03_pygame_adapter_export_and_headless_run(self) -> None:
        """Verify PygameAdapter exports runnable Python game and executes in headless simulation mode."""
        adapter = PygameAdapter()
        gdd = self.get_mock_gdd("PygameSimTest", genre=GameGenre.PLATFORMER_2D.value)
        manifest = self.get_mock_manifest("PygameSimTest")
        scene = make_engine_scene()
        script = self.get_mock_script("game/scenes/gameplay_scene.py", "python")

        out_dir = self.workspace_dir / "pygame_project"
        res = adapter.export_project(gdd, manifest, [scene], [script], out_dir)

        self.assertEqual(res.target, EngineTarget.PYGAME)
        self.assertEqual(res.entry_file, "main.py")
        self.assertTrue((out_dir / "main.py").is_file())
        self.assertTrue((out_dir / "game" / "engine_shim.py").is_file())
        self.assertTrue((out_dir / "requirements.txt").is_file())

        # Execute main.py in headless mode to verify genuine simulation execution
        proc = subprocess.run(
            [sys.executable, "main.py", "--headless", "--frames", "5"],
            cwd=str(out_dir),
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0, f"Headless run failed: {proc.stderr}")
        self.assertIn("[SIMULATION]", proc.stdout)

    def test_eng_06_04_web_adapter_export_and_canvas_engine(self) -> None:
        """Verify WebAdapter exports HTML5 single-page arcade game with Canvas 2D engine."""
        adapter = WebAdapter()
        gdd = self.get_mock_gdd("WebArcadeTest")
        manifest = self.get_mock_manifest("WebArcadeTest")
        scene = make_engine_scene()
        script = self.get_mock_script("game.js", "javascript")

        out_dir = self.workspace_dir / "web_project"
        res = adapter.export_project(gdd, manifest, [scene], [script], out_dir)

        self.assertEqual(res.target, EngineTarget.WEB_HTML5)
        self.assertEqual(res.entry_file, "index.html")
        self.assertTrue((out_dir / "index.html").is_file())
        self.assertTrue((out_dir / "game.js").is_file())
        self.assertTrue((out_dir / "style.css").is_file())

        html_text = (out_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn("<canvas", html_text)
        self.assertIn('src="game.js"', html_text)

        js_text = (out_dir / "game.js").read_text(encoding="utf-8")
        self.assertIn("requestAnimationFrame", js_text)
        self.assertIn("AudioContext", js_text)

    def test_eng_06_05_unity_adapter_export_and_csharp_generation(self) -> None:
        """Verify UnityAdapter exports C# MonoBehaviour scripts and EditorSceneBuilder.cs."""
        adapter = UnityAdapter()
        gdd = self.get_mock_gdd("UnityExportTest")
        manifest = self.get_mock_manifest("UnityExportTest")
        scene = make_engine_scene()
        script = self.get_mock_script("Assets/Scripts/PlayerController.cs", "csharp")

        out_dir = self.workspace_dir / "unity_project"
        res = adapter.export_project(gdd, manifest, [scene], [script], out_dir)

        self.assertEqual(res.target, EngineTarget.UNITY)
        self.assertTrue((out_dir / "Assets" / "Scripts" / "PlayerController.cs").is_file())
        self.assertTrue((out_dir / "Assets" / "Editor" / "EditorSceneBuilder.cs").is_file())
        self.assertTrue((out_dir / "Packages" / "manifest.json").is_file())
        self.assertTrue((out_dir / "ProjectSettings").is_dir())

        editor_cs = (out_dir / "Assets" / "Editor" / "EditorSceneBuilder.cs").read_text(encoding="utf-8")
        self.assertIn("#if UNITY_EDITOR", editor_cs)
        self.assertIn("MenuItem", editor_cs)
        self.assertIn("BuildScene", editor_cs)

    def test_eng_06_06_launcher_commands_and_prerequisites(self) -> None:
        """Verify get_run_command returns correct launcher vectors for each engine adapter."""
        godot_adapter = GodotAdapter()
        pygame_adapter = PygameAdapter()
        web_adapter = WebAdapter()
        unity_adapter = UnityAdapter()

        proj_path = "/home/saman/my_game"
        self.assertEqual(godot_adapter.get_run_command(proj_path), ["godot", "--path", str(proj_path)])
        self.assertEqual(pygame_adapter.get_run_command(proj_path), ["python3", "main.py"])
        self.assertEqual(web_adapter.get_run_command(proj_path), ["python3", "-m", "http.server", "8000"])
        self.assertEqual(unity_adapter.get_run_command(proj_path), ["unity-editor", "-projectPath", str(proj_path)])
