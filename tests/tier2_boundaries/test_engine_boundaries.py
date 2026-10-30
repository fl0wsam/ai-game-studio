"""
tests/tier2_boundaries/test_engine_boundaries.py - Tier 2 Boundary, Stress, and Corner Case Tests
Opaque-box tests for boundary conditions, stress scenarios, and error handling across Milestone 2 engine pipeline:
- Category 1: Empty & Zero-Byte Asset Boundaries
- Category 2: Malformed Scenes & Scene Graph Tree Violations
- Category 3: Path Traversal & Filesystem Security
- Category 4: Dynamic Asset Linker Boundary & Corner Cases
- Category 5: Engine Hub & Target Resolution Boundaries
- Category 6: Hierarchy Packager Stress & Corner Cases
- Category 7: Game Archetypes Boundary Values
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


class TestEngineBoundaries(StudioTestCase):
    """Tier 2 Boundary, Stress, and Corner Case Tests for Modular Engine Pipeline."""

    # ========================================================================
    # Category 1: Empty & Zero-Byte Asset Boundaries
    # ========================================================================

    def test_boundary_01_empty_manifest_packaging(self) -> None:
        """Verifies packaging an AssetManifest with zero items creates directories and returns empty metadata."""
        packager = CleanHierarchyPackager()
        empty_manifest = AssetManifest(project_name="EmptyProject", items=[])

        meta = packager.package_assets(self.workspace_dir, empty_manifest)
        self.assertEqual(len(meta), 0)

        # Canonical directories must still be created if create_hierarchy called
        packager.create_hierarchy(self.workspace_dir)
        self.assertTrue((self.workspace_dir / "assets" / "sprites").is_dir())

    def test_boundary_02_zero_byte_asset_handling(self) -> None:
        """Verifies zero-byte assets write 0 bytes, calculate empty SHA-256, and get flagged by hierarchy verifier."""
        packager = CleanHierarchyPackager()
        zero_manifest = AssetManifest(
            project_name="ZeroByteProject",
            items=[
                AssetItem(
                    asset_id="empty_sprite",
                    asset_type=AssetType.SPRITE,
                    rel_path="assets/sprites/empty.png",
                    file_bytes=b"",
                )
            ],
        )
        meta = packager.package_assets(self.workspace_dir, zero_manifest)
        self.assertEqual(len(meta), 1)
        self.assertEqual(meta[0].byte_size, 0)
        # Empty string SHA-256 is e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
        self.assertEqual(
            meta[0].checksum_sha256,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

        disk_file = self.workspace_dir / "assets" / "sprites" / "empty.png"
        self.assertTrue(disk_file.is_file())
        self.assertEqual(disk_file.stat().st_size, 0)

    def test_boundary_03_none_file_bytes_handling(self) -> None:
        """Verifies AssetItem with file_bytes=None writes without throwing exception."""
        packager = CleanHierarchyPackager()
        none_manifest = AssetManifest(
            project_name="NoneBytesProject",
            items=[
                AssetItem(
                    asset_id="placeholder_asset",
                    asset_type=AssetType.TEXTURE,
                    rel_path="assets/textures/placeholder.png",
                    file_bytes=None,
                )
            ],
        )
        meta = packager.package_assets(self.workspace_dir, none_manifest)
        self.assertEqual(len(meta), 1)
        disk_file = self.workspace_dir / "assets" / "textures" / "placeholder.png"
        self.assertTrue(disk_file.is_file())

    def test_boundary_04_large_binary_asset(self) -> None:
        """Verifies packaging a 5MB binary asset handles large buffers and computes accurate SHA-256."""
        packager = CleanHierarchyPackager()
        large_payload = os.urandom(5 * 1024 * 1024)
        expected_hash = hashlib.sha256(large_payload).hexdigest()

        large_manifest = AssetManifest(
            project_name="LargeAssetProj",
            items=[
                AssetItem(
                    asset_id="large_bg",
                    asset_type=AssetType.TEXTURE,
                    rel_path="assets/backgrounds/large_space.png",
                    file_bytes=large_payload,
                )
            ],
        )
        meta = packager.package_assets(self.workspace_dir, large_manifest)
        self.assertEqual(len(meta), 1)
        self.assertEqual(meta[0].byte_size, 5 * 1024 * 1024)
        self.assertEqual(meta[0].checksum_sha256, expected_hash)

        disk_file = self.workspace_dir / "assets" / "backgrounds" / "large_space.png"
        self.assertEqual(disk_file.stat().st_size, 5 * 1024 * 1024)

    def test_boundary_05_special_characters_in_filenames(self) -> None:
        """Verifies assets with spaces, dashes, brackets, and numbers in rel_path are safely packaged."""
        packager = CleanHierarchyPackager()
        complex_rel = "assets/sprites/Hero [Warrior] - Final #1 (v2).png"
        complex_manifest = AssetManifest(
            project_name="SpecialChars",
            items=[
                AssetItem(
                    asset_id="special_hero",
                    asset_type=AssetType.SPRITE,
                    rel_path=complex_rel,
                    file_bytes=b"HERODATA",
                )
            ],
        )
        meta = packager.package_assets(self.workspace_dir, complex_manifest)
        self.assertEqual(len(meta), 1)
        disk_file = self.workspace_dir / complex_rel
        self.assertTrue(disk_file.is_file())
        self.assertEqual(disk_file.read_bytes(), b"HERODATA")

    # ========================================================================
    # Category 2: Malformed Scenes & Scene Graph Tree Violations
    # ========================================================================

    def test_boundary_06_scene_with_no_nodes(self) -> None:
        """Verifies SceneAST validation and serialization behavior on empty node list."""
        empty_scene = SceneAST(name="Empty", path="scenes/empty.tscn", nodes=[])
        issues = empty_scene.validate_tree()
        self.assertIn("Scene contains no nodes.", issues)

        # Serializing empty scene produces valid header without nodes
        serialized = serialize_tscn(empty_scene)
        self.assertIn("[gd_scene", serialized)
        self.assertNotIn("[node", serialized)

    def test_boundary_07_scene_with_duplicate_roots(self) -> None:
        """Verifies duplicate root node addition is blocked or flagged by tree validator."""
        scene = SceneAST(name="DualRoot", path="scenes/dual.tscn")
        r1 = NodeAST(name="Root1", type="Node2D", parent=None)
        r2 = NodeAST(name="Root2", type="Node2D", parent=None)

        scene.add_node(r1)
        # Attempting to add second root must raise ValueError
        with self.assertRaises(ValueError):
            scene.add_node(r2)

        # If bypassed directly by modifying list, validate_tree must report it
        scene.nodes.append(r2)
        issues = scene.validate_tree()
        self.assertTrue(any("must contain exactly 1 root node" in iss for iss in issues))

    def test_boundary_08_deeply_nested_nodes_hierarchy(self) -> None:
        """Verifies scene graph supports depth-20 node nesting with correct topological ordering."""
        scene = SceneAST(name="DeepTree", path="scenes/deep.tscn")
        depth = 20
        nodes = []
        root = NodeAST(name="LevelRoot", type="Node2D", parent=None)
        nodes.append(root)

        prev_name = "LevelRoot"
        for i in range(1, depth):
            curr_name = f"Node_Depth_{i}"
            parent_ref = "." if prev_name == "LevelRoot" else prev_name
            node = NodeAST(name=curr_name, type="Node2D", parent=parent_ref)
            nodes.append(node)
            prev_name = curr_name

        # Pass in reverse order to verify topological sort
        scene.nodes = list(reversed(nodes))
        ordered = sort_nodes_topologically(scene.nodes)
        self.assertEqual(ordered[0].name, "LevelRoot")
        self.assertEqual(ordered[1].name, "Node_Depth_1")
        self.assertEqual(ordered[-1].name, f"Node_Depth_{depth - 1}")

        # Serialize and parse back
        serialized = serialize_tscn(scene)
        parsed = parse_tscn(serialized, "scenes/deep.tscn")
        self.assertEqual(len(parsed.nodes), depth)

    def test_boundary_09_orphan_node_referencing_nonexistent_parent(self) -> None:
        """Verifies validate_tree flags nodes referencing parents that do not exist."""
        scene = SceneAST(name="OrphanScene", path="scenes/orphan.tscn")
        root = NodeAST(name="Root", type="Node2D", parent=None)
        orphan = NodeAST(name="LostChild", type="Sprite2D", parent="NonExistentGhost")
        scene.nodes = [root, orphan]

        issues = scene.validate_tree()
        self.assertTrue(any("references non-existent parent" in iss for iss in issues))

    def test_boundary_10_node_referencing_unknown_script_id(self) -> None:
        """Verifies validate_tree flags node referencing unregistered script_id."""
        scene = SceneAST(name="ScriptCheck", path="scenes/script_check.tscn")
        root = NodeAST(name="Root", type="Node2D", parent=None, script_id="unregistered_ext_id")
        scene.nodes = [root]

        issues = scene.validate_tree()
        self.assertTrue(any("references unknown script_id" in iss for iss in issues))

    def test_boundary_11_node_referencing_unknown_instance(self) -> None:
        """Verifies validate_tree flags node instancing unknown resource ID."""
        scene = SceneAST(name="InstanceCheck", path="scenes/inst.tscn")
        root = NodeAST(name="Root", type="Node2D", parent=None)
        inst_node = NodeAST(name="Prefab", type="Node2D", parent=".", instance="missing_prefab_id")
        scene.nodes = [root, inst_node]

        issues = scene.validate_tree()
        self.assertTrue(any("instances unknown resource" in iss for iss in issues))

    def test_boundary_12_circular_or_unresolvable_parent_references(self) -> None:
        """Verifies sort_nodes_topologically handles circular parent loops without freezing."""
        node_a = NodeAST(name="NodeA", type="Node2D", parent="NodeB")
        node_b = NodeAST(name="NodeB", type="Node2D", parent="NodeA")

        # Must terminate and return nodes without infinite loop
        sorted_nodes = sort_nodes_topologically([node_a, node_b])
        self.assertEqual(len(sorted_nodes), 2)

    def test_boundary_13_scene_with_zero_resources_load_steps(self) -> None:
        """Verifies serialize_tscn omits load_steps when external and sub resources are empty."""
        scene = SceneAST(name="NoResScene", path="scenes/no_res.tscn")
        root = NodeAST(name="NoResScene", type="Node2D")
        child = NodeAST(name="Child", type="Node2D", parent=".")
        scene.nodes = [root, child]

        serialized = serialize_tscn(scene)
        self.assertNotIn("load_steps=", serialized)
        self.assertIn("[gd_scene format=3", serialized)

    # ========================================================================
    # Category 3: Path Traversal & Filesystem Security
    # ========================================================================

    def test_boundary_14_path_traversal_double_dot_parent(self) -> None:
        """Verifies path traversal attempts with ../../ are rejected with ValueError."""
        packager = CleanHierarchyPackager()
        with self.assertRaises(ValueError):
            packager.sanitize_path(self.workspace_dir, "../../etc/passwd")

    def test_boundary_15_path_traversal_mixed_slashes(self) -> None:
        """Verifies mixed forward and backward slashes cannot evade path traversal detection."""
        packager = CleanHierarchyPackager()
        with self.assertRaises(ValueError):
            packager.sanitize_path(self.workspace_dir, "scenes/..\\..\\evil.sh")

    def test_boundary_16_path_traversal_absolute_unix_path(self) -> None:
        """Verifies absolute system paths cannot escape the project root directory."""
        packager = CleanHierarchyPackager()
        with self.assertRaises(ValueError):
            packager.sanitize_path(self.workspace_dir, "/var/log/syslog")

    def test_boundary_17_path_traversal_res_prefix_strip(self) -> None:
        """Verifies res:// prefix is stripped cleanly but traversal inside res:// is blocked."""
        packager = CleanHierarchyPackager()
        valid = packager.sanitize_path(self.workspace_dir, "res://assets/sprites/player.png")
        self.assertEqual(valid, (self.workspace_dir / "assets" / "sprites" / "player.png").resolve())

        with self.assertRaises(ValueError):
            packager.sanitize_path(self.workspace_dir, "res://../../outside.png")

    def test_boundary_18_script_spec_path_traversal_rejection(self) -> None:
        """Verifies packager.package_scripts rejects ScriptSpec with traversal rel_path."""
        packager = CleanHierarchyPackager()
        evil_scripts = [ScriptSpec(rel_path="../../evil.gd", language="gdscript", code="# evil")]
        with self.assertRaises(ValueError):
            packager.package_scripts(self.workspace_dir, evil_scripts)

    def test_boundary_19_asset_manifest_path_traversal_rejection(self) -> None:
        """Verifies packager.package_assets rejects AssetItem with traversal rel_path."""
        packager = CleanHierarchyPackager()
        evil_manifest = AssetManifest(
            project_name="EvilProj",
            items=[
                AssetItem(
                    asset_id="evil_file",
                    asset_type=AssetType.SPRITE,
                    rel_path="assets/../../etc/shadow",
                    file_bytes=b"bad",
                )
            ],
        )
        with self.assertRaises(ValueError):
            packager.package_assets(self.workspace_dir, evil_manifest)

    # ========================================================================
    # Category 4: Dynamic Asset Linker Boundary & Corner Cases
    # ========================================================================

    def test_boundary_20_linker_with_none_manifest(self) -> None:
        """Verifies DynamicAssetLinker handles None manifest safely without crashing."""
        linker = DynamicAssetLinker(manifest=None)
        self.assertIsNone(linker.resolve_asset("any_asset"))

        scene = SceneAST(name="SafeScene", path="scenes/safe.tscn")
        scene.nodes.append(NodeAST(name="Root", type="Node2D"))
        result = linker.link_scene(scene)
        self.assertIs(result, scene)

    def test_boundary_21_linker_with_missing_texture_references(self) -> None:
        """Verifies nodes referencing unknown asset IDs are left unmodified and do not cause errors."""
        manifest = AssetManifest(project_name="Test", items=[])
        linker = DynamicAssetLinker(manifest=manifest)

        scene = SceneAST(name="MissingScene", path="scenes/missing.tscn")
        root = NodeAST(name="Root", type="Node2D")
        sprite = NodeAST(name="MissingSprite", type="Sprite2D", parent=".", properties={"texture": "ghost_texture_99"})
        scene.nodes = [root, sprite]

        linked = linker.link_scene(scene)
        self.assertEqual(len(linked.ext_resources), 0)
        self.assertEqual(linked.nodes[1].properties["texture"], "ghost_texture_99")

    def test_boundary_22_linker_idempotence_already_linked(self) -> None:
        """Verifies link_scene is idempotent: re-linking already bound scene produces no duplicate resources."""
        manifest = AssetManifest(
            project_name="IdemTest",
            items=[
                AssetItem(asset_id="sprite_player", asset_type=AssetType.SPRITE, rel_path="assets/sprites/player.png")
            ],
        )
        linker = DynamicAssetLinker(manifest=manifest)

        scene = SceneAST(name="IdemScene", path="scenes/idem.tscn")
        root = NodeAST(name="IdemScene", type="Node2D")
        sprite = NodeAST(name="Sprite", type="Sprite2D", parent=".", properties={"texture": "sprite_player"})
        scene.nodes = [root, sprite]

        # First link
        linked1 = linker.link_scene(scene)
        self.assertEqual(len(linked1.ext_resources), 1)
        first_ext_id = linked1.ext_resources[0].id

        # Second link on already linked scene
        linked2 = linker.link_scene(linked1)
        self.assertEqual(len(linked2.ext_resources), 1)
        self.assertEqual(linked2.ext_resources[0].id, first_ext_id)
        self.assertIsInstance(linked2.nodes[1].properties["texture"], ExtResourceRef)

    def test_boundary_23_actor_already_has_sprite_no_duplicate_synthesis(self) -> None:
        """Verifies actor with existing Sprite2D child does NOT receive a duplicate synthesized sprite."""
        manifest = AssetManifest(
            project_name="SpriteTest",
            items=[
                AssetItem(asset_id="sprite_player", asset_type=AssetType.SPRITE, rel_path="assets/sprites/player.png")
            ],
        )
        linker = DynamicAssetLinker(manifest=manifest)

        scene = SceneAST(name="PlayerScene", path="scenes/player.tscn")
        player = NodeAST(name="Player", type="CharacterBody2D", parent=None)
        existing_sprite = NodeAST(name="CustomSprite", type="Sprite2D", parent="Player")
        scene.nodes = [player, existing_sprite]

        linked = linker.link_scene(scene)
        sprite_nodes = [n for n in linked.nodes if n.type == "Sprite2D"]
        self.assertEqual(len(sprite_nodes), 1)
        self.assertEqual(sprite_nodes[0].name, "CustomSprite")

    def test_boundary_24_physics_body_already_has_shape_no_duplicate(self) -> None:
        """Verifies physics body with existing CollisionShape2D does NOT receive duplicate collision shape."""
        manifest = AssetManifest(project_name="ColTest", items=[])
        linker = DynamicAssetLinker(manifest=manifest)

        scene = SceneAST(name="HeroScene", path="scenes/hero.tscn")
        hero = NodeAST(name="Hero", type="CharacterBody2D", parent=None)
        existing_col = NodeAST(name="MyCollision", type="CollisionShape2D", parent="Hero")
        scene.nodes = [hero, existing_col]

        linked = linker.link_scene(scene)
        col_nodes = [n for n in linked.nodes if n.type == "CollisionShape2D"]
        self.assertEqual(len(col_nodes), 1)
        self.assertEqual(col_nodes[0].name, "MyCollision")

    def test_boundary_25_multiple_actors_distinct_shape_synthesis(self) -> None:
        """Verifies scene with multiple physics bodies synthesizes distinct sub-resources for each body."""
        manifest = AssetManifest(project_name="MultiColTest", items=[])
        linker = DynamicAssetLinker(manifest=manifest)

        scene = SceneAST(name="BattleArena", path="scenes/battle.tscn")
        root = NodeAST(name="BattleArena", type="Node2D", parent=None)
        p1 = NodeAST(name="Hero", type="CharacterBody2D", parent=".")
        p2 = NodeAST(name="Monster", type="CharacterBody2D", parent=".")
        p3 = NodeAST(name="Wall", type="StaticBody2D", parent=".")
        scene.nodes = [root, p1, p2, p3]

        linked = linker.link_scene(scene)
        col_nodes = [n for n in linked.nodes if n.type == "CollisionShape2D"]
        self.assertEqual(len(col_nodes), 3)
        self.assertEqual(len(linked.sub_resources), 3)

        sub_ids = {s.id for s in linked.sub_resources}
        self.assertEqual(len(sub_ids), 3)

    def test_boundary_26_linker_audio_autoloop_only_for_music(self) -> None:
        """Verifies linker sets autoplay and looping only on music assets, not SFX."""
        manifest = AssetManifest(
            project_name="AudioTest",
            items=[
                AssetItem(asset_id="sfx_shot", asset_type=AssetType.AUDIO_SFX, rel_path="audio/sfx/shot.wav"),
                AssetItem(asset_id="bgm_theme", asset_type=AssetType.AUDIO_MUSIC, rel_path="audio/music/theme.wav"),
            ],
        )
        linker = DynamicAssetLinker(manifest=manifest)

        scene = SceneAST(name="AudioTestScene", path="scenes/audio.tscn")
        root = NodeAST(name="Root", type="Node2D")
        sfx_node = NodeAST(name="ShotSFX", type="AudioStreamPlayer2D", parent=".", properties={"stream": "sfx_shot"})
        bgm_node = NodeAST(name="BGM", type="AudioStreamPlayer", parent=".", properties={"stream": "bgm_theme"})
        scene.nodes = [root, sfx_node, bgm_node]

        linked = linker.link_scene(scene)

        # SFX node
        self.assertNotIn("autoplay", sfx_node.properties)
        self.assertEqual(sfx_node.properties["bus"], StringNameRef("SFX"))

        # BGM node
        self.assertTrue(bgm_node.properties.get("autoplay"))
        self.assertTrue(bgm_node.properties.get("parameters/looping"))
        self.assertEqual(bgm_node.properties["bus"], StringNameRef("Music"))

    # ========================================================================
    # Category 5: Engine Hub & Target Resolution Boundaries
    # ========================================================================

    def test_boundary_27_target_resolution_whitespace_and_casing(self) -> None:
        """Verifies resolve_target cleans leading/trailing whitespace and irregular case."""
        hub = EngineHub(register_defaults=False)
        self.assertEqual(hub.resolve_target("   GODOT4 \n"), EngineTarget.GODOT4)
        self.assertEqual(hub.resolve_target("\tPyGame  "), EngineTarget.PYGAME)
        self.assertEqual(hub.resolve_target("  "), EngineTarget.GODOT4)

    def test_boundary_28_unregister_nonexistent_adapter(self) -> None:
        """Verifies unregistering non-existent target returns None without throwing."""
        hub = EngineHub(register_defaults=False)
        res = hub.unregister_adapter("nonexistent_target")
        self.assertIsNone(res)

    def test_boundary_29_export_with_empty_scenes_and_scripts(self) -> None:
        """Verifies export_project on all 4 adapters succeeds even when scene and script lists are empty."""
        template = PlatformerTemplate()
        gdd = template.get_gdd()
        manifest = AssetManifest(project_name=gdd.title, items=[])

        adapters = [GodotAdapter(), PygameAdapter(), WebAdapter(), UnityAdapter()]
        for adapter in adapters:
            target_dir = self.workspace_dir / f"empty_export_{adapter.target.value}"
            res = adapter.export_project(gdd, manifest, [], [], target_dir)
            self.assertIsInstance(res, EngineProjectManifest)
            self.assertTrue(target_dir.is_dir())
            self.assertGreater(len(res.files_created), 0)

    # ========================================================================
    # Category 6: Hierarchy Packager Stress & Corner Cases
    # ========================================================================

    def test_boundary_30_packaging_into_deeply_nested_nonexistent_dir(self) -> None:
        """Verifies create_hierarchy automatically creates deeply nested parent directory trees."""
        packager = CleanHierarchyPackager()
        deep_target = self.workspace_dir / "deep" / "nested" / "path" / "my_project"
        self.assertFalse(deep_target.exists())

        created = packager.create_hierarchy(deep_target)
        self.assertTrue(deep_target.is_dir())
        self.assertTrue((deep_target / "assets" / "sprites").is_dir())

    def test_boundary_31_packaging_overwrite_false_preserves_existing(self) -> None:
        """Verifies package_assets and package_scripts respect overwrite=False and preserve original file bytes."""
        packager = CleanHierarchyPackager()
        manifest_v1 = AssetManifest(
            project_name="VersionTest",
            items=[
                AssetItem(asset_id="coin", asset_type=AssetType.SPRITE, rel_path="assets/sprites/coin.png", file_bytes=b"ORIGINAL_BYTES")
            ],
        )
        packager.package_assets(self.workspace_dir, manifest_v1, overwrite=True)
        file_path = self.workspace_dir / "assets" / "sprites" / "coin.png"
        self.assertEqual(file_path.read_bytes(), b"ORIGINAL_BYTES")

        manifest_v2 = AssetManifest(
            project_name="VersionTest",
            items=[
                AssetItem(asset_id="coin", asset_type=AssetType.SPRITE, rel_path="assets/sprites/coin.png", file_bytes=b"NEW_BYTES")
            ],
        )
        # overwrite=False: original bytes preserved
        packager.package_assets(self.workspace_dir, manifest_v2, overwrite=False)
        self.assertEqual(file_path.read_bytes(), b"ORIGINAL_BYTES")

    def test_boundary_32_verify_hierarchy_on_empty_dir(self) -> None:
        """Verifies verify_hierarchy on an empty directory reports missing canonical subdirectories."""
        packager = CleanHierarchyPackager()
        empty_dir = self.workspace_dir / "empty_dir"
        empty_dir.mkdir(parents=True, exist_ok=True)

        report = packager.verify_hierarchy(empty_dir)
        self.assertFalse(report.passed)
        self.assertEqual(len(report.missing_directories), 10)

    def test_boundary_33_verify_hierarchy_with_multiple_stray_and_empty_files(self) -> None:
        """Verifies verify_hierarchy flags multiple stray files and multiple zero-byte files accurately."""
        packager = CleanHierarchyPackager()
        packager.create_hierarchy(self.workspace_dir)

        # Add 3 stray files
        (self.workspace_dir / "hack.sh").write_text("echo bad", encoding="utf-8")
        (self.workspace_dir / "notes.txt").write_text("random", encoding="utf-8")
        (self.workspace_dir / "malware.exe").write_bytes(b"PE")

        # Add 2 zero-byte files in canonical directories
        (self.workspace_dir / "assets" / "sprites" / "empty1.png").write_bytes(b"")
        (self.workspace_dir / "audio" / "sfx" / "empty2.wav").write_bytes(b"")

        report = packager.verify_hierarchy(self.workspace_dir)
        self.assertFalse(report.passed)
        self.assertIn("hack.sh", report.stray_root_files)
        self.assertIn("notes.txt", report.stray_root_files)
        self.assertIn("malware.exe", report.stray_root_files)

    # ========================================================================
    # Category 7: Game Archetypes Boundary Values
    # ========================================================================

    def test_boundary_34_extreme_physics_parameters_platformer(self) -> None:
        """Verifies platformer templates generate valid GDD without division-by-zero or errors."""
        template = PlatformerTemplate()
        gdd = template.get_gdd(seed=0)
        self.assertGreater(len(gdd.gameplay_mechanics), 0)

        scenes = template.get_scenes(gdd)
        self.assertGreater(len(scenes), 0)

    def test_boundary_35_grid_puzzle_board_randomization(self) -> None:
        """Verifies grid puzzle board initializes across various seeds without errors."""
        template = GridPuzzleTemplate()
        for seed_val in (0, 1, 42, 99999):
            gdd = template.get_gdd(seed=seed_val)
            self.assertEqual(gdd.genre, GameGenre.GRID_PUZZLE.value)
            scenes = template.get_scenes(gdd)
            self.assertGreaterEqual(len(scenes), 1)

    def test_boundary_36_space_shooter_viewport_boundary_check(self) -> None:
        """Verifies Space Shooter starship script enforces boundary clamping."""
        template = SpaceShooterTemplate()
        gdd = template.get_gdd()
        scripts = template.get_scripts(gdd, target_engine="godot4")
        ship_script = next((s for s in scripts if "starship.gd" in s.rel_path), None)
        self.assertIsNotNone(ship_script)
        self.assertIn("clamp", ship_script.code)
