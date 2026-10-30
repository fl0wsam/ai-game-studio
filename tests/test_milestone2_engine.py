"""
tests/test_milestone2_engine.py - Comprehensive Unit & Integration Test Suite for Milestone 2.
Verifies F-ENG-01 (EngineHub), F-ENG-02 (Godot 4 AST & Serializer), F-ENG-03 (Hierarchy Packager),
F-ENG-04 (Dynamic Asset Linker), F-ENG-05 (Game Archetypes), and F-ENG-06 (Multi-Engine Adapters).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from studio.core.models import (
    AssetItem,
    AssetManifest,
    AssetType,
    EngineTarget,
    GameDesignDocument,
    GameGenre,
    ScenePlan,
    ScriptSpec,
)
from studio.engine.adapters.base import BaseEngineAdapter, EngineProjectManifest
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
from studio.engine.archetypes.base import ArchetypeRegistry, get_archetype
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
from studio.engine.dynamic_asset_linker import DynamicAssetLinker
from studio.engine.hierarchy import CleanHierarchyPackager
from studio.engine.hub import EngineHub, get_engine_hub
from studio.providers.mock_provider import MockGDDGenerator


# ============================================================================
# 1. Tests for F-ENG-01: EngineHub & Registry
# ============================================================================

class TestEngineHub(unittest.TestCase):
    """Test suite for EngineHub registry and multi-engine export dispatch (F-ENG-01)."""

    def setUp(self) -> None:
        self.hub = EngineHub(register_defaults=True)

    def test_builtin_adapters_registered(self) -> None:
        """Verifies that all 4 default adapters are loaded."""
        self.assertTrue(self.hub.has_adapter(EngineTarget.GODOT4))
        self.assertTrue(self.hub.has_adapter(EngineTarget.PYGAME))
        self.assertTrue(self.hub.has_adapter(EngineTarget.WEB_HTML5))
        self.assertTrue(self.hub.has_adapter(EngineTarget.UNITY))

    def test_target_resolution_and_aliases(self) -> None:
        """Verifies string resolution and aliases."""
        self.assertEqual(self.hub.resolve_target("godot"), EngineTarget.GODOT4)
        self.assertEqual(self.hub.resolve_target("godot4"), EngineTarget.GODOT4)
        self.assertEqual(self.hub.resolve_target("pygame"), EngineTarget.PYGAME)
        self.assertEqual(self.hub.resolve_target("python"), EngineTarget.PYGAME)
        self.assertEqual(self.hub.resolve_target("web"), EngineTarget.WEB_HTML5)
        self.assertEqual(self.hub.resolve_target("html5"), EngineTarget.WEB_HTML5)
        self.assertEqual(self.hub.resolve_target("phaser"), EngineTarget.WEB_HTML5)
        self.assertEqual(self.hub.resolve_target("unity"), EngineTarget.UNITY)
        self.assertEqual(self.hub.resolve_target("unknown_engine"), EngineTarget.GODOT4)
        self.assertEqual(self.hub.resolve_target(None), EngineTarget.GODOT4)

    def test_duplicate_registration_guard(self) -> None:
        """Verifies that duplicate registration raises ValueError unless override=True."""
        adapter = GodotAdapter()
        with self.assertRaises(ValueError):
            self.hub.register_adapter(adapter, override=False)
        # Should succeed with override=True
        self.hub.register_adapter(adapter, override=True)

    def test_unregistered_adapter_error(self) -> None:
        """Verifies that accessing missing adapter raises KeyError."""
        self.hub.unregister_adapter(EngineTarget.UNITY)
        self.assertFalse(self.hub.has_adapter(EngineTarget.UNITY))
        with self.assertRaises(KeyError):
            self.hub.get_adapter(EngineTarget.UNITY)

    def test_list_supported_engines(self) -> None:
        """Verifies metadata listing."""
        engines = self.hub.list_supported_engines()
        self.assertGreaterEqual(len(engines), 4)
        targets = [e["target"] for e in engines]
        self.assertIn("godot4", targets)
        self.assertIn("pygame", targets)
        self.assertIn("web_html5", targets)
        self.assertIn("unity", targets)

    def test_single_export_dispatch(self) -> None:
        """Verifies export dispatch through hub."""
        template = PlatformerTemplate()
        gdd = template.get_gdd()
        manifest = AssetManifest(project_name=gdd.title, items=[])
        scenes = template.get_scenes(gdd, manifest)
        scripts = template.get_scripts(gdd)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = self.hub.export("godot4", gdd, manifest, scenes, scripts, tmpdir)
            self.assertIsInstance(result, EngineProjectManifest)
            self.assertEqual(result.target, EngineTarget.GODOT4)
            self.assertTrue((Path(tmpdir) / "project.godot").is_file())

    def test_multi_export_dispatch(self) -> None:
        """Verifies batch multi-engine export under subdirectories."""
        template = PlatformerTemplate()
        gdd = template.get_gdd()
        manifest = AssetManifest(project_name=gdd.title, items=[])
        scenes = template.get_scenes(gdd, manifest)
        scripts = template.get_scripts(gdd)

        with tempfile.TemporaryDirectory() as tmpdir:
            results = self.hub.export_multi(
                ["godot4", "pygame", "web_html5", "unity"],
                gdd, manifest, scenes, scripts, tmpdir,
            )
            self.assertEqual(len(results), 4)
            self.assertIn("godot4", results)
            self.assertIn("pygame", results)
            self.assertIn("web_html5", results)
            self.assertIn("unity", results)

            self.assertTrue((Path(tmpdir) / "godot4" / "project.godot").is_file())
            self.assertTrue((Path(tmpdir) / "pygame" / "main.py").is_file())
            self.assertTrue((Path(tmpdir) / "web_html5" / "index.html").is_file())
            self.assertTrue((Path(tmpdir) / "unity" / "Assets" / "Editor" / "EditorSceneBuilder.cs").is_file())


# ============================================================================
# 2. Tests for F-ENG-02: Godot 4 AST & Serializer
# ============================================================================

class TestGodotASTAndSerializer(unittest.TestCase):
    """Test suite for Godot 4 SceneAST, NodeAST, and .tscn serialization (F-ENG-02)."""

    def test_variant_formatting(self) -> None:
        """Verifies conversion of Python types to Godot 4 Variant strings."""
        self.assertEqual(format_godot_value(None), "null")
        self.assertEqual(format_godot_value(True), "true")
        self.assertEqual(format_godot_value(False), "false")
        self.assertEqual(format_godot_value(42), "42")
        self.assertEqual(format_godot_value(14.0), "14.0")
        self.assertEqual(format_godot_value(3.1415), "3.1415")
        self.assertEqual(format_godot_value("hello world"), '"hello world"')
        self.assertEqual(format_godot_value(Vector2(10.5, 20.0)), "Vector2(10.5, 20.0)")
        self.assertEqual(format_godot_value(Vector2i(5, 8)), "Vector2i(5, 8)")
        self.assertEqual(format_godot_value(Vector3(1.0, 2.0, 3.0)), "Vector3(1.0, 2.0, 3.0)")
        self.assertEqual(format_godot_value(Color(1.0, 0.5, 0.0, 1.0)), "Color(1.0, 0.5, 0.0, 1.0)")
        self.assertEqual(format_godot_value(Rect2(0.0, 0.0, 64.0, 64.0)), "Rect2(0.0, 0.0, 64.0, 64.0)")
        self.assertEqual(format_godot_value(ExtResourceRef("1_tex")), 'ExtResource("1_tex")')
        self.assertEqual(format_godot_value(SubResourceRef("sub_1")), 'SubResource("sub_1")')
        self.assertEqual(format_godot_value(StringNameRef("SFX")), '&"SFX"')
        self.assertEqual(format_godot_value(NodePathRef("Player/Sprite2D")), 'NodePath("Player/Sprite2D")')
        self.assertEqual(format_godot_value(["a", "b"]), '["a", "b"]')
        self.assertEqual(format_godot_value({"key": 10}), '{"key": 10}')

    def test_uid_generation_deterministic(self) -> None:
        """Verifies that generate_godot_uid derives identical base36 string for same path."""
        uid1 = generate_godot_uid("res://assets/sprites/player.png")
        uid2 = generate_godot_uid("res://assets/sprites/player.png")
        uid3 = generate_godot_uid("res://assets/sprites/enemy.png")
        self.assertEqual(uid1, uid2)
        self.assertNotEqual(uid1, uid3)
        self.assertTrue(uid1.startswith("uid://"))
        self.assertEqual(len(uid1), 18)  # "uid://" (6) + 12 chars

    def test_load_steps_and_header(self) -> None:
        """Verifies load_steps calculation in serialized scene."""
        scene_empty = SceneAST(name="Empty", path="scenes/empty.tscn")
        scene_empty.nodes.append(NodeAST(name="Empty", type="Node2D"))
        tscn_empty = serialize_tscn(scene_empty)
        # 0 resources -> no load_steps
        self.assertNotIn("load_steps=", tscn_empty)
        self.assertIn("[gd_scene format=3", tscn_empty)

        scene_res = SceneAST(name="WithRes", path="scenes/res.tscn")
        scene_res.add_ext_resource("Texture2D", "res://icon.png")
        scene_res.add_sub_resource("CircleShape2D", "c1", {"radius": 10.0})
        scene_res.nodes.append(NodeAST(name="WithRes", type="Node2D"))
        tscn_res = serialize_tscn(scene_res)
        # 2 resources -> load_steps=3
        self.assertIn("load_steps=3", tscn_res)

    def test_topological_sorting(self) -> None:
        """Verifies that nodes are serialized in parent-before-child order."""
        root = NodeAST(name="Root", type="Node2D")
        child1 = NodeAST(name="Child1", type="Node2D", parent=".")
        child2 = NodeAST(name="GrandChild", type="Sprite2D", parent="Child1")
        # Pass nodes in reversed order
        disordered = [child2, child1, root]
        sorted_nodes = sort_nodes_topologically(disordered)
        self.assertEqual([n.name for n in sorted_nodes], ["Root", "Child1", "GrandChild"])

    def test_round_trip_serialization_and_parsing(self) -> None:
        """Verifies that parse_tscn(serialize_tscn(scene)) produces equivalent SceneAST."""
        scene = SceneAST(name="Level", path="scenes/level.tscn")
        scene.add_ext_resource("Texture2D", "res://assets/player.png", "1_tex")
        scene.add_sub_resource("RectangleShape2D", "sub_box", {"size": Vector2(32.0, 32.0)})
        scene.nodes.append(NodeAST(name="Level", type="Node2D"))
        scene.nodes.append(NodeAST(
            name="Player",
            type="CharacterBody2D",
            parent=".",
            properties={"collision_layer": 2},
        ))
        scene.nodes.append(NodeAST(
            name="Sprite2D",
            type="Sprite2D",
            parent="Player",
            properties={"texture": ExtResourceRef("1_tex")},
        ))
        scene.add_connection("body_entered", "Player", "Level", "_on_player_entered")

        tscn_text = serialize_tscn(scene)
        parsed = parse_tscn(tscn_text, "scenes/level.tscn")

        self.assertEqual(len(parsed.ext_resources), 1)
        self.assertEqual(parsed.ext_resources[0].id, "1_tex")
        self.assertEqual(parsed.ext_resources[0].path, "res://assets/player.png")
        self.assertEqual(len(parsed.sub_resources), 1)
        self.assertEqual(parsed.sub_resources[0].id, "sub_box")
        self.assertEqual(len(parsed.nodes), 3)
        self.assertEqual(parsed.nodes[0].name, "Level")
        self.assertEqual(parsed.nodes[1].name, "Player")
        self.assertEqual(parsed.nodes[2].name, "Sprite2D")
        self.assertEqual(len(parsed.connections), 1)
        self.assertEqual(parsed.connections[0].signal, "body_entered")

    def test_project_godot_generation(self) -> None:
        """Verifies project.godot content."""
        cfg = GodotProjectConfig(
            project_name="CosmicVoyage",
            main_scene_path="res://scenes/main_level.tscn",
            viewport_width=1280,
            viewport_height=720,
            autoloads={"GameManager": "*res://scripts/game_manager.gd"},
        )
        content = generate_project_godot(cfg)
        self.assertIn("config_version=5", content)
        self.assertIn('config/name="CosmicVoyage"', content)
        self.assertIn('run/main_scene="res://scenes/main_level.tscn"', content)
        self.assertIn("window/size/viewport_width=1280", content)
        self.assertIn('GameManager="*res://scripts/game_manager.gd"', content)
        self.assertIn('renderer/rendering_method="gl_compatibility"', content)

    def test_scene_ast_validation(self) -> None:
        """Verifies SceneAST.validate_tree() detects errors."""
        bad_scene = SceneAST(name="BadScene", path="scenes/bad.tscn")
        # Empty scene
        self.assertIn("Scene contains no nodes.", bad_scene.validate_tree())

        # Broken parent reference
        bad_scene.nodes.append(NodeAST(name="Root", type="Node2D"))
        bad_scene.nodes.append(NodeAST(name="Orphan", type="Sprite2D", parent="NonExistentParent"))
        issues = bad_scene.validate_tree()
        self.assertTrue(any("references non-existent parent" in iss for iss in issues))


# ============================================================================
# 3. Tests for F-ENG-03: Clean Hierarchy Packager
# ============================================================================

class TestCleanHierarchyPackager(unittest.TestCase):
    """Test suite for CleanHierarchyPackager file management and verification (F-ENG-03)."""

    def setUp(self) -> None:
        self.packager = CleanHierarchyPackager(target_engine="godot4")

    def test_canonical_directory_creation(self) -> None:
        """Verifies creation of all 10 standard subdirectories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            created = self.packager.create_hierarchy(tmpdir)
            root = Path(tmpdir)
            for d in self.packager.CANONICAL_DIRECTORIES:
                self.assertTrue((root / d).is_dir(), f"Expected directory {d} to exist")

    def test_path_traversal_protection(self) -> None:
        """Verifies that path traversal attempts raise ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                self.packager.sanitize_path(tmpdir, "../../etc/passwd")
            with self.assertRaises(ValueError):
                self.packager.sanitize_path(tmpdir, "/etc/shadow")
            with self.assertRaises(ValueError):
                self.packager.sanitize_path(tmpdir, "scenes/../../outside.txt")

    def test_package_assets_with_checksums(self) -> None:
        """Verifies writing binary assets and computing SHA-256."""
        manifest = AssetManifest(
            project_name="TestProj",
            items=[
                AssetItem(asset_id="sprite_hero", asset_type=AssetType.SPRITE, rel_path="assets/sprites/hero.png", file_bytes=b"PNGDATA123"),
                AssetItem(asset_id="sfx_jump", asset_type=AssetType.AUDIO_SFX, rel_path="audio/sfx/jump.wav", file_bytes=b"WAVDATA456"),
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            meta = self.packager.package_assets(tmpdir, manifest)
            self.assertEqual(len(meta), 2)
            self.assertTrue((Path(tmpdir) / "assets/sprites/hero.png").is_file())
            self.assertTrue((Path(tmpdir) / "audio/sfx/jump.wav").is_file())
            self.assertIsNotNone(meta[0].checksum_sha256)

    def test_package_scripts_and_docs(self) -> None:
        """Verifies script and documentation packaging."""
        gdd = PlatformerTemplate().get_gdd()
        scripts = [ScriptSpec(rel_path="scripts/player.gd", language="gdscript", code="extends CharacterBody2D\n")]

        with tempfile.TemporaryDirectory() as tmpdir:
            self.packager.create_hierarchy(tmpdir)
            s_meta = self.packager.package_scripts(tmpdir, scripts)
            d_meta = self.packager.package_docs(tmpdir, gdd)

            self.assertEqual(len(s_meta), 1)
            self.assertEqual(len(d_meta), 3)
            self.assertTrue((Path(tmpdir) / "scripts/player.gd").is_file())
            self.assertTrue((Path(tmpdir) / "docs/GDD.md").is_file())
            self.assertTrue((Path(tmpdir) / "docs/LORE.md").is_file())
            self.assertTrue((Path(tmpdir) / "docs/CONTROLS.md").is_file())

    def test_manifest_and_hierarchy_verification(self) -> None:
        """Verifies project_manifest.json generation and verify_hierarchy validation."""
        gdd = PlatformerTemplate().get_gdd()
        with tempfile.TemporaryDirectory() as tmpdir:
            self.packager.create_hierarchy(tmpdir)
            self.packager.package_docs(tmpdir, gdd)
            # Add a root file
            (Path(tmpdir) / "project.godot").write_text("config_version=5\n", encoding="utf-8")

            manifest = self.packager.generate_manifest(tmpdir, "MyGame")
            self.assertGreater(manifest.total_files, 0)
            self.assertTrue((Path(tmpdir) / "project_manifest.json").is_file())

            report = self.packager.verify_hierarchy(tmpdir)
            self.assertTrue(report.passed)
            self.assertEqual(len(report.missing_directories), 0)
            self.assertEqual(len(report.empty_files), 0)


# ============================================================================
# 4. Tests for F-ENG-04: Dynamic Asset Linker
# ============================================================================

class TestDynamicAssetLinker(unittest.TestCase):
    """Test suite for DynamicAssetLinker fuzzy resolution and node binding (F-ENG-04)."""

    def setUp(self) -> None:
        self.manifest = AssetManifest(
            project_name="Test",
            items=[
                AssetItem(asset_id="sprite_player", asset_type=AssetType.SPRITE, rel_path="assets/sprites/player.png"),
                AssetItem(asset_id="sfx_jump", asset_type=AssetType.AUDIO_SFX, rel_path="audio/sfx/jump.wav"),
                AssetItem(asset_id="bgm_main", asset_type=AssetType.AUDIO_MUSIC, rel_path="audio/music/main_theme.wav"),
            ]
        )
        self.linker = DynamicAssetLinker(manifest=self.manifest)

    def test_fuzzy_asset_resolution(self) -> None:
        """Verifies resolution with exact, case-insensitive, and stripped prefixes."""
        # Exact
        item1 = self.linker.resolve_asset("sprite_player")
        self.assertIsNotNone(item1)
        self.assertEqual(item1.asset_id, "sprite_player")

        # Case-insensitive
        item2 = self.linker.resolve_asset("SPRITE_PLAYER")
        self.assertIsNotNone(item2)

        # Prefix stripped
        item3 = self.linker.resolve_asset("player")
        self.assertIsNotNone(item3)
        self.assertEqual(item3.asset_id, "sprite_player")

        item4 = self.linker.resolve_asset("jump")
        self.assertIsNotNone(item4)
        self.assertEqual(item4.asset_id, "sfx_jump")

    def test_texture_and_audio_binding(self) -> None:
        """Verifies that Sprite2D and AudioStreamPlayer nodes receive ExtResourceRefs."""
        scene = SceneAST(name="TestScene", path="scenes/test.tscn")
        scene.nodes.append(NodeAST(name="Root", type="Node2D"))
        scene.nodes.append(NodeAST(
            name="PlayerSprite",
            type="Sprite2D",
            parent=".",
            properties={"texture": "player"},
        ))
        scene.nodes.append(NodeAST(
            name="BGM",
            type="AudioStreamPlayer",
            parent=".",
            properties={"stream": "bgm_main"},
        ))

        linked = self.linker.link_scene(scene)
        self.assertEqual(len(linked.ext_resources), 2)
        sprite_node = linked.find_node("PlayerSprite")
        self.assertIsInstance(sprite_node.properties["texture"], ExtResourceRef)

        bgm_node = linked.find_node("BGM")
        self.assertIsInstance(bgm_node.properties["stream"], ExtResourceRef)
        self.assertTrue(bgm_node.properties.get("autoplay"))
        self.assertEqual(bgm_node.properties.get("bus"), StringNameRef("Music"))

    def test_collision_shape_synthesis(self) -> None:
        """Verifies automatic synthesis of CollisionShape2D for physics bodies."""
        scene = SceneAST(name="PhysicsScene", path="scenes/physics.tscn")
        scene.nodes.append(NodeAST(name="HeroBody", type="CharacterBody2D"))

        linked = self.linker.link_scene(scene)
        # Should synthesize a CollisionShape2D and CapsuleShape2D
        shapes = [n for n in linked.nodes if n.type == "CollisionShape2D"]
        self.assertEqual(len(shapes), 1)
        self.assertEqual(len(linked.sub_resources), 1)
        self.assertEqual(linked.sub_resources[0].type, "CapsuleShape2D")

    def test_build_scene_from_plan(self) -> None:
        """Verifies procedural scene building from ScenePlan."""
        plan = ScenePlan(
            name="Level1",
            path="scenes/level1.tscn",
            root_node_type="Node2D",
            required_assets=["sprite_player", "sfx_jump"],
            script_name="scripts/level1.gd",
        )
        built = self.linker.build_scene_from_plan(plan)
        self.assertEqual(built.name, "Level1")
        self.assertIsNotNone(built.find_node("Level1"))
        self.assertGreaterEqual(len(built.ext_resources), 2)


# ============================================================================
# 5. Tests for F-ENG-05: Game Archetype Templates
# ============================================================================

class TestGameArchetypes(unittest.TestCase):
    """Test suite for all 4 game archetypes (F-ENG-05)."""

    def test_registry_lookup(self) -> None:
        """Verifies ArchetypeRegistry lookup and aliases."""
        p = get_archetype("platformer_2d")
        self.assertIsInstance(p, PlatformerTemplate)
        r = get_archetype("top_down_rpg")
        self.assertIsInstance(r, TopDownRPGTemplate)
        s = get_archetype("space_shooter")
        self.assertIsInstance(s, SpaceShooterTemplate)
        g = get_archetype("grid_puzzle")
        self.assertIsInstance(g, GridPuzzleTemplate)

        # Fallback
        fallback = get_archetype("non_existent_genre")
        self.assertIsInstance(fallback, PlatformerTemplate)

    def test_platformer_template(self) -> None:
        """Verifies 2D Platformer template."""
        tmpl = PlatformerTemplate()
        gdd = tmpl.get_gdd()
        self.assertEqual(gdd.genre, GameGenre.PLATFORMER_2D.value)
        scenes = tmpl.get_scenes(gdd)
        self.assertEqual(len(scenes), 5)
        scripts = tmpl.get_scripts(gdd)
        self.assertGreaterEqual(len(scripts), 4)

    def test_rpg_template(self) -> None:
        """Verifies Top-Down RPG template."""
        tmpl = TopDownRPGTemplate()
        gdd = tmpl.get_gdd()
        self.assertEqual(gdd.genre, GameGenre.TOP_DOWN_RPG.value)
        scenes = tmpl.get_scenes(gdd)
        self.assertEqual(len(scenes), 4)
        scripts = tmpl.get_scripts(gdd)
        self.assertGreaterEqual(len(scripts), 3)

    def test_space_shooter_template(self) -> None:
        """Verifies Space Shooter template."""
        tmpl = SpaceShooterTemplate()
        gdd = tmpl.get_gdd()
        self.assertEqual(gdd.genre, GameGenre.SPACE_SHOOTER.value)
        scenes = tmpl.get_scenes(gdd)
        self.assertEqual(len(scenes), 5)
        scripts = tmpl.get_scripts(gdd)
        self.assertGreaterEqual(len(scripts), 4)

    def test_grid_puzzle_template(self) -> None:
        """Verifies Grid Match Puzzle template."""
        tmpl = GridPuzzleTemplate()
        gdd = tmpl.get_gdd()
        self.assertEqual(gdd.genre, GameGenre.GRID_PUZZLE.value)
        scenes = tmpl.get_scenes(gdd)
        self.assertEqual(len(scenes), 2)
        scripts = tmpl.get_scripts(gdd)
        self.assertGreaterEqual(len(scripts), 2)


# ============================================================================
# 6. Tests for F-ENG-06: Multi-Engine Adapters (Pygame, Web, Unity)
# ============================================================================

class TestMultiEngineAdapters(unittest.TestCase):
    """Test suite for auxiliary export bridges: Pygame, Web, Unity (F-ENG-06)."""

    def setUp(self) -> None:
        self.gdd = PlatformerTemplate().get_gdd()
        self.manifest = AssetManifest(project_name=self.gdd.title, items=[])

    def test_pygame_adapter_export_and_headless_simulation(self) -> None:
        """Verifies Pygame export and headless simulation CLI execution."""
        adapter = PygameAdapter()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = adapter.export_project(self.gdd, self.manifest, [], [], tmpdir)
            self.assertEqual(result.entry_file, "main.py")
            self.assertTrue((Path(tmpdir) / "main.py").is_file())
            self.assertTrue((Path(tmpdir) / "game" / "engine_shim.py").is_file())

            # Run headless simulation CLI
            proc = subprocess.run(
                [sys.executable, "main.py", "--headless", "--frames", "15"],
                cwd=tmpdir,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, f"Pygame headless run failed: {proc.stderr}")
            self.assertIn("[SIMULATION] Completed 15 frames successfully", proc.stdout)

    def test_web_adapter_export_and_js_syntax(self) -> None:
        """Verifies Web HTML5 export and JS syntax checking via Node."""
        adapter = WebAdapter()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = adapter.export_project(self.gdd, self.manifest, [], [], tmpdir)
            self.assertEqual(result.entry_file, "index.html")
            self.assertTrue((Path(tmpdir) / "index.html").is_file())
            self.assertTrue((Path(tmpdir) / "game.js").is_file())
            self.assertTrue((Path(tmpdir) / "style.css").is_file())

            # Check JS syntax with node if node is available
            node_path = shutil.which("node")
            if node_path:
                proc = subprocess.run(
                    [node_path, "--check", "game.js"],
                    cwd=tmpdir,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc.returncode, 0, f"Node syntax check failed: {proc.stderr}")

    def test_unity_adapter_export_and_csharp_validation(self) -> None:
        """Verifies Unity C# script export and brace balancing."""
        adapter = UnityAdapter()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = adapter.export_project(self.gdd, self.manifest, [], [], tmpdir)
            self.assertEqual(result.entry_file, "Assets/Editor/EditorSceneBuilder.cs")

            builder_file = Path(tmpdir) / "Assets/Editor/EditorSceneBuilder.cs"
            self.assertTrue(builder_file.is_file())
            controller_file = Path(tmpdir) / "Assets/Scripts/PlayerController.cs"
            self.assertTrue(controller_file.is_file())

            # Verify C# brace balancing
            for cs_file in (builder_file, controller_file):
                code = cs_file.read_text(encoding="utf-8")
                open_braces = code.count("{")
                close_braces = code.count("}")
                self.assertEqual(open_braces, close_braces, f"Mismatched braces in {cs_file.name}")


# ============================================================================
# 7. Tests for Mock Provider Hardening
# ============================================================================

class TestMockProviderHardening(unittest.TestCase):
    """Verifies deterministic quest objective IDs in MockGDDGenerator."""

    def test_deterministic_quest_objective_ids(self) -> None:
        gdd = MockGDDGenerator.generate("space runner", seed=123)
        self.assertGreater(len(gdd.quests), 0)
        first_quest = gdd.quests[0]
        objectives = getattr(first_quest, "objectives", [])
        self.assertEqual(len(objectives), 3)
        self.assertEqual(objectives[0].objective_id, "obj_0")
        self.assertEqual(objectives[1].objective_id, "obj_1")
        self.assertEqual(objectives[2].objective_id, "obj_2")


if __name__ == "__main__":
    unittest.main()
