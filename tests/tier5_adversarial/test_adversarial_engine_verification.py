"""
tests/tier5_adversarial/test_adversarial_engine_verification.py - Tier 5 Adversarial Stress & Vulnerability Suite.
Authored by challenger_m5_2.

Systematically stress-tests:
1. Scene AST Models: Circular parent loops, duplicate node names, duplicate resource IDs,
   missing/multiple root nodes, invalid property references, missing script references,
   and cross-model compatibility (core.models.SceneAST vs engine.ast_models.SceneAST).
2. Dynamic Asset Linker: Non-existent asset resolution, empty manifests/GDDs, prefix stripping,
   preserving existing bindings, complex actor synthesis, and binding validation diagnostics.
3. TSCN Validator: Malformed header lines, format version enforcement, load_steps discrepancies,
   out-of-order nodes, path traversal in ext_resource paths, missing disk resources, and signal connections.
4. Asset Integrity Verifier: Zero-byte files, corrupted magic bytes, truncated headers,
   path traversal in asset paths, SVG/JSON malformation, and orphaned asset detection.
5. Dual-Mode Engine Simulator: Frame stepper boundaries (NaN/Inf coordinates, runaway coordinates),
   missing $ChildNode references in GDScript, zero/negative frame boundaries, and timeout handling.
6. Multi-Engine Hub: Unknown target engines, duplicate adapter registration collisions,
   adapter lifecycle (unregistration), and simultaneous multi-engine export isolation.
"""
from __future__ import annotations

import io
import math
import os
import pathlib
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional

from tests.conftest_base import StudioTestCase

import studio.core.models as core_models
from studio.core.models import (
    AssetItem,
    AssetManifest,
    AssetRequirement,
    AssetType,
    DiagnosticError,
    EngineTarget,
    GameDesignDocument,
    GameGenre,
    ScenePlan,
    ScriptSpec,
)
from studio.engine.adapters.base import BaseEngineAdapter, EngineProjectManifest
from studio.engine.adapters.godot_adapter import parse_tscn, serialize_tscn
import studio.engine.ast_models as engine_ast
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
from studio.engine.hub import EngineHub
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
from studio.verification.tscn_validator import TSCNValidator


# ============================================================================
# Category 1: Adversarial Scene AST & Graph Malformation
# ============================================================================

class TestAdversarialEngineAST(StudioTestCase):
    """Adversarial stress testing of in-memory AST models and validation contracts."""

    def setUp(self) -> None:
        super().setUp()
        self.tscn_validator = TSCNValidator(project_root=self.workspace_dir)

    def test_ast_circular_parent_loop_direct(self) -> None:
        """
        Adversarial Scenario: Direct circular parent loop between two nodes (Node A -> Node B -> Node A).
        Verifies that cyclic node parenting is caught and does not cause infinite recursion.
        """
        scene = SceneAST(name="DirectCycleScene", path="scenes/direct_cycle.tscn")
        # Root node
        root = NodeAST(name="Root", type="Node2D")
        # Cycle: NodeA -> NodeB, NodeB -> NodeA
        node_a = NodeAST(name="NodeA", type="Node2D", parent="NodeB")
        node_b = NodeAST(name="NodeB", type="Node2D", parent="NodeA")

        scene.nodes = [root, node_a, node_b]

        errors = self.tscn_validator.validate_scene_ast(scene)
        cycle_errors = [e for e in errors if "cyclic node parenting" in e.message.lower()]
        self.assertGreater(
            len(cycle_errors),
            0,
            f"Expected cyclic parenting error for direct loop, got: {[e.message for e in errors]}",
        )
        self.assertEqual(cycle_errors[0].severity, "ERROR")

    def test_ast_circular_parent_loop_transitive(self) -> None:
        """
        Adversarial Scenario: Transitive circular parenting across 3 nodes (A -> B -> C -> A).
        """
        scene = SceneAST(name="TransitiveCycleScene", path="scenes/transitive_cycle.tscn")
        root = NodeAST(name="Root", type="Node2D")
        node_a = NodeAST(name="NodeA", type="Node2D", parent="NodeC")
        node_b = NodeAST(name="NodeB", type="Node2D", parent="NodeA")
        node_c = NodeAST(name="NodeC", type="Node2D", parent="NodeB")

        scene.nodes = [root, node_a, node_b, node_c]

        errors = self.tscn_validator.validate_scene_ast(scene)
        cycle_errors = [e for e in errors if "cyclic" in e.message.lower()]
        self.assertGreater(
            len(cycle_errors),
            0,
            f"Expected cyclic parenting error for 3-node transitive cycle, got: {[e.message for e in errors]}",
        )

    def test_ast_self_parenting(self) -> None:
        """
        Adversarial Scenario: Node designates itself as its parent (Node A parent='NodeA').
        """
        scene = SceneAST(name="SelfParentScene", path="scenes/self_parent.tscn")
        root = NodeAST(name="Root", type="Node2D")
        node_self = NodeAST(name="Narcissus", type="Sprite2D", parent="Narcissus")
        scene.nodes = [root, node_self]

        errors = self.tscn_validator.validate_scene_ast(scene)
        self_parent_errors = [e for e in errors if "cannot be its own parent" in e.message.lower()]
        self.assertEqual(
            len(self_parent_errors),
            1,
            f"Expected self-parenting error, got: {[e.message for e in errors]}",
        )

    def test_ast_duplicate_root_nodes_prevention(self) -> None:
        """
        Adversarial Scenario: Attempting to register multiple root nodes via add_node().
        Must raise a ValueError immediately upon addition of the second root.
        """
        scene = SceneAST(name="MultiRootScene", path="scenes/multi_root.tscn")
        root1 = NodeAST(name="MainRoot", type="Node2D")
        root2 = NodeAST(name="ImposterRoot", type="Node2D")

        scene.add_node(root1)
        with self.assertRaises(ValueError) as ctx:
            scene.add_node(root2)
        self.assertIn("Cannot add second root node", str(ctx.exception))

    def test_ast_missing_root_node_detection(self) -> None:
        """
        Adversarial Scenario: SceneAST where all nodes declare a parent, leaving 0 root nodes.
        """
        scene = SceneAST(name="NoRootScene", path="scenes/no_root.tscn")
        node1 = NodeAST(name="Orphan1", type="Node2D", parent="NonExistentParent")
        node2 = NodeAST(name="Orphan2", type="Node2D", parent="Orphan1")
        scene.nodes = [node1, node2]

        # Check ast_models validate_tree
        tree_issues = scene.validate_tree()
        self.assertTrue(any("must contain exactly 1 root node, found 0" in i for i in tree_issues))

        # Check TSCNValidator
        errors = self.tscn_validator.validate_scene_ast(scene)
        root_errors = [e for e in errors if "found 0" in e.message]
        self.assertGreaterEqual(len(root_errors), 1)

    def test_ast_multiple_roots_validation(self) -> None:
        """
        Adversarial Scenario: SceneAST populated with multiple root nodes via direct node list assignment.
        """
        scene = SceneAST(name="DirectMultiRootScene", path="scenes/multi_root.tscn")
        root1 = NodeAST(name="RootOne", type="Node2D", parent=None)
        root2 = NodeAST(name="RootTwo", type="Node2D", parent="")
        scene.nodes = [root1, root2]

        tree_issues = scene.validate_tree()
        self.assertTrue(any("must contain exactly 1 root node, found 2" in i for i in tree_issues))

        errors = self.tscn_validator.validate_scene_ast(scene)
        root_errors = [e for e in errors if "found 2" in e.message]
        self.assertGreaterEqual(len(root_errors), 1)

    def test_ast_duplicate_ext_resource_id_collision(self) -> None:
        """
        Adversarial Scenario: Attempting to register two different external resources with the identical ID.
        Verifies that duplicate external resource IDs are identified as schema errors.
        """
        scene = SceneAST(name="DuplicateExtScene", path="scenes/dup_ext.tscn")
        scene.add_node(NodeAST(name="Root", type="Node2D"))

        # Explicitly register two conflicting paths with the same ID
        scene.add_ext_resource("Texture2D", "res://assets/hero.png", resource_id="res_shared_id")
        scene.add_ext_resource("Texture2D", "res://assets/enemy.png", resource_id="res_shared_id")

        errors = self.tscn_validator.validate_scene_ast(scene)
        dup_errors = [e for e in errors if "duplicate external resource id" in e.message.lower()]
        self.assertGreaterEqual(
            len(dup_errors),
            1,
            f"Expected duplicate resource ID error, got: {[e.message for e in errors]}",
        )

    def test_ast_duplicate_sub_resource_id_collision(self) -> None:
        """
        Adversarial Scenario: Multiple SubResourceAST entries declared with identical ID.
        """
        scene = SceneAST(name="DuplicateSubScene", path="scenes/dup_sub.tscn")
        scene.add_node(NodeAST(name="Root", type="Node2D"))

        scene.sub_resources.append(SubResourceAST(id="shape_box", type="RectangleShape2D"))
        scene.sub_resources.append(SubResourceAST(id="shape_box", type="CircleShape2D"))

        errors = self.tscn_validator.validate_scene_ast(scene)
        dup_errors = [e for e in errors if "duplicate sub-resource id 'shape_box'" in e.message.lower()]
        self.assertEqual(len(dup_errors), 1)

    def test_ast_missing_script_ext_resource_reference(self) -> None:
        """
        Adversarial Scenario: NodeAST assigns script_id to an ID not in ext_resources.
        """
        scene = SceneAST(name="MissingScriptScene", path="scenes/missing_script.tscn")
        root = NodeAST(name="Root", type="Node2D", script_id="999_missing_script")
        scene.nodes = [root]

        # validate_tree()
        issues = scene.validate_tree()
        self.assertTrue(any("references unknown script_id '999_missing_script'" in i for i in issues))

        # validate_scene_ast()
        errors = self.tscn_validator.validate_scene_ast(scene)
        script_errors = [e for e in errors if "unknown script_id '999_missing_script'" in e.message]
        self.assertEqual(len(script_errors), 1)

    def test_ast_script_reference_wrong_resource_type(self) -> None:
        """
        Adversarial Scenario: NodeAST attaches a resource as script_id, but resource has type='Texture2D'.
        """
        scene = SceneAST(name="WrongScriptTypeScene", path="scenes/wrong_script.tscn")
        ext_id = scene.add_ext_resource("Texture2D", "res://assets/texture.png")
        root = NodeAST(name="Root", type="Node2D", script_id=ext_id)
        scene.nodes = [root]

        errors = self.tscn_validator.validate_scene_ast(scene)
        type_errors = [e for e in errors if "expected 'script'" in e.message.lower()]
        self.assertEqual(len(type_errors), 1)

    def test_ast_missing_parent_reference(self) -> None:
        """
        Adversarial Scenario: Child node references non-existent parent node name.
        """
        scene = SceneAST(name="GhostParentScene", path="scenes/ghost_parent.tscn")
        root = NodeAST(name="Root", type="Node2D")
        child = NodeAST(name="Child", type="Sprite2D", parent="PhantomNode")
        scene.nodes = [root, child]

        issues = scene.validate_tree()
        self.assertTrue(any("references non-existent parent 'PhantomNode'" in i for i in issues))

        errors = self.tscn_validator.validate_scene_ast(scene)
        parent_errors = [e for e in errors if "references non-existent parent 'PhantomNode'" in e.message]
        self.assertEqual(len(parent_errors), 1)

    def test_ast_duplicate_sibling_node_names(self) -> None:
        """
        Adversarial Scenario: Two sibling nodes under identical parent have identical names.
        """
        scene = SceneAST(name="DuplicateSiblingScene", path="scenes/dup_sibling.tscn")
        root = NodeAST(name="Root", type="Node2D")
        child1 = NodeAST(name="Hitbox", type="Area2D", parent=".")
        child2 = NodeAST(name="Hitbox", type="Area2D", parent=".")
        scene.nodes = [root, child1, child2]

        errors = self.tscn_validator.validate_scene_ast(scene)
        sibling_errors = [e for e in errors if "duplicate sibling node name 'hitbox'" in e.message.lower()]
        self.assertEqual(len(sibling_errors), 1)

    def test_ast_empty_node_tree(self) -> None:
        """
        Adversarial Scenario: SceneAST constructed with completely empty nodes list.
        """
        scene = SceneAST(name="EmptyScene", path="scenes/empty.tscn")
        scene.nodes = []

        issues = scene.validate_tree()
        self.assertIn("Scene contains no nodes.", issues)

        errors = self.tscn_validator.validate_scene_ast(scene)
        self.assertTrue(any("Scene contains no nodes." in e.message for e in errors))

    def test_ast_core_vs_engine_model_compatibility(self) -> None:
        """
        Adversarial Scenario: Validating compatibility between studio.core.models.SceneAST
        and studio.engine.ast_models.SceneAST across TSCNValidator.
        Verifies that both representations pass through validate_scene_ast without unhandled exceptions.
        """
        # Core model
        core_scene = core_models.SceneAST(
            name="CoreScene",
            path="scenes/core_scene.tscn",
            nodes=[
                core_models.NodeAST(name="Root", type="Node2D", parent=None),
                core_models.NodeAST(name="Child", type="Sprite2D", parent="."),
            ],
        )

        # Engine model
        engine_scene = engine_ast.SceneAST(
            name="EngineScene",
            path="scenes/engine_scene.tscn",
            nodes=[
                engine_ast.NodeAST(name="Root", type="Node2D", parent=None),
                engine_ast.NodeAST(name="Child", type="Sprite2D", parent="."),
            ],
        )

        errors_core = self.tscn_validator.validate_scene_ast(core_scene)
        errors_engine = self.tscn_validator.validate_scene_ast(engine_scene)

        # Both should complete cleanly with zero errors
        self.assertEqual(errors_core, [], f"Core SceneAST validation failed: {errors_core}")
        self.assertEqual(errors_engine, [], f"Engine SceneAST validation failed: {errors_engine}")


# ============================================================================
# Category 2: Adversarial Dynamic Asset Linker
# ============================================================================

class TestAdversarialDynamicAssetLinker(StudioTestCase):
    """Adversarial stress testing of asset resolution, linking, and synthesis."""

    def test_linker_resolve_nonexistent_asset(self) -> None:
        """
        Adversarial Scenario: Attempting to resolve non-existent asset IDs.
        Must return None gracefully without throwing KeyError or AttributeError.
        """
        manifest = AssetManifest(project_name="TestProject", items=[])
        linker = DynamicAssetLinker(manifest=manifest)

        self.assertIsNone(linker.resolve_asset("completely_unknown_asset_id_9999"))
        self.assertIsNone(linker.resolve_asset(""))
        self.assertIsNone(linker.resolve_asset("   "))

    def test_linker_empty_manifest_and_gdd(self) -> None:
        """
        Adversarial Scenario: DynamicAssetLinker initialized with None manifest and None GDD.
        Must handle requests safely.
        """
        linker = DynamicAssetLinker(manifest=None, gdd=None)
        self.assertIsNone(linker.resolve_asset("player_sprite"))

        scene = SceneAST(name="Test", path="scenes/test.tscn")
        scene.nodes.append(NodeAST(name="Root", type="Node2D"))

        # link_scene should execute without AttributeError
        linked = linker.link_scene(scene)
        self.assertEqual(len(linked.nodes), 1)

    def test_linker_prefix_stripping_and_case_insensitivity(self) -> None:
        """
        Adversarial Scenario: Resolving asset references with mismatched casing and category prefixes.
        (e.g., 'sprite_hero_run' referencing asset 'HERO_RUN').
        """
        item = AssetItem(
            asset_id="hero_run",
            asset_type=AssetType.SPRITE,
            rel_path="assets/sprites/hero_run.png",
            engine_path="res://assets/sprites/hero_run.png",
        )
        manifest = AssetManifest(project_name="TestProject", items=[item])
        linker = DynamicAssetLinker(manifest=manifest)

        # Case variation
        self.assertEqual(linker.resolve_asset("HERO_RUN"), item)
        # Category prefix variation
        self.assertEqual(linker.resolve_asset("sprite_hero_run"), item)
        self.assertEqual(linker.resolve_asset("SPRITE_HERO_RUN"), item)
        self.assertEqual(linker.resolve_asset("tex_hero_run"), item)
        self.assertEqual(linker.resolve_asset("item_hero_run"), item)

    def test_linker_already_bound_properties(self) -> None:
        """
        Adversarial Scenario: Node properties already contain ExtResourceRef.
        Linker must preserve existing valid references and avoid redundant duplicate ExtResource creation.
        """
        scene = SceneAST(name="BoundScene", path="scenes/bound.tscn")
        res_id = scene.add_ext_resource("Texture2D", "res://assets/sprites/original.png")
        sprite_node = NodeAST(
            name="PlayerSprite",
            type="Sprite2D",
            parent=".",
            properties={"texture": ExtResourceRef(res_id)},
        )
        scene.nodes.append(sprite_node)

        manifest = AssetManifest(
            project_name="TestProject",
            items=[
                AssetItem(
                    asset_id="PlayerSprite",
                    asset_type=AssetType.SPRITE,
                    rel_path="assets/sprites/different.png",
                    engine_path="res://assets/sprites/different.png",
                )
            ],
        )
        linker = DynamicAssetLinker(manifest=manifest)
        linked = linker.link_scene(scene)

        # ExtResource count must remain 1 (not duplicated)
        self.assertEqual(len(linked.ext_resources), 1)
        self.assertEqual(linked.ext_resources[0].id, res_id)
        self.assertEqual(linked.nodes[0].properties["texture"].id, res_id)

    def test_linker_synthesis_under_complex_hierarchy(self) -> None:
        """
        Adversarial Scenario: CharacterBody2D has no Sprite2D and no CollisionShape2D.
        Linker must synthesize a child Sprite2D (matching asset) and CollisionShape2D (with CapsuleShape2D).
        """
        scene = SceneAST(name="HeroScene", path="scenes/hero.tscn")
        hero_body = NodeAST(name="Hero", type="CharacterBody2D", parent=None)
        scene.nodes.append(hero_body)

        manifest = AssetManifest(
            project_name="TestProject",
            items=[
                AssetItem(
                    asset_id="sprite_hero",
                    asset_type=AssetType.SPRITE,
                    rel_path="assets/sprites/hero.png",
                    engine_path="res://assets/sprites/hero.png",
                )
            ],
        )
        linker = DynamicAssetLinker(manifest=manifest)
        linked = linker.link_scene(scene)

        # Verified synthesized nodes
        synthesized_names = [n.name for n in linked.nodes]
        self.assertIn("HeroSprite", synthesized_names)
        self.assertIn("CollisionShape2D", synthesized_names)

        # Verify CollisionShape2D sub_resource was generated
        self.assertEqual(len(linked.sub_resources), 1)
        self.assertEqual(linked.sub_resources[0].type, "CapsuleShape2D")

    def test_linker_validate_bindings_flags_missing_manifest_assets(self) -> None:
        """
        Adversarial Scenario: Scene AST references external resources that do not exist in the manifest.
        Linker's validate_bindings() must flag this as a WARNING.
        """
        scene = SceneAST(name="UnmanifestedScene", path="scenes/unmanifested.tscn")
        scene.add_ext_resource("Texture2D", "res://assets/sprites/ghost_asset.png")
        manifest = AssetManifest(project_name="TestProject", items=[])

        linker = DynamicAssetLinker(manifest=manifest)
        errors = linker.validate_bindings(scene)

        warns = [e for e in errors if e.severity == "WARNING" and "not found in assetmanifest" in e.message.lower()]
        self.assertEqual(len(warns), 1)

    def test_linker_validate_bindings_flags_missing_ext_and_sub_resources(self) -> None:
        """
        Adversarial Scenario: NodeAST properties reference non-existent ExtResource and SubResource IDs.
        Linker's validate_bindings() must flag these with ERROR severity.
        """
        scene = SceneAST(name="BrokenBindingsScene", path="scenes/broken.tscn")
        node = NodeAST(
            name="Sprite",
            type="Sprite2D",
            parent=None,
            properties={
                "texture": ExtResourceRef("missing_ext_id"),
                "shape": SubResourceRef("missing_sub_id"),
            },
        )
        scene.nodes.append(node)

        linker = DynamicAssetLinker()
        errors = linker.validate_bindings(scene)

        ext_errors = [e for e in errors if "references non-existent extresource 'missing_ext_id'" in e.message.lower()]
        sub_errors = [e for e in errors if "references non-existent subresource 'missing_sub_id'" in e.message.lower()]

        self.assertEqual(len(ext_errors), 1)
        self.assertEqual(len(sub_errors), 1)

    def test_linker_procedural_build_scene_from_malformed_plan(self) -> None:
        """
        Adversarial Scenario: ScenePlan requests non-existent assets and lacks a script.
        Procedural builder must gracefully build a valid minimal SceneAST.
        """
        plan = ScenePlan(
            scene_name="MalformedPlanScene",
            scene_path="scenes/malformed.tscn",
            root_node_type="Node2D",
            required_assets=["missing_sound_effect_99", "ghost_sprite_101"],
            script_name=None,
        )
        linker = DynamicAssetLinker(manifest=AssetManifest(project_name="P", items=[]))
        scene = linker.build_scene_from_plan(plan)

        self.assertIsNotNone(scene)
        self.assertEqual(scene.name, "MalformedPlanScene")
        self.assertEqual(len(scene.nodes), 1)
        self.assertEqual(scene.nodes[0].name, "MalformedPlanScene")


# ============================================================================
# Category 3: Adversarial TSCN Validator
# ============================================================================

class TestAdversarialTSCNValidator(StudioTestCase):
    """Adversarial stress testing of raw .tscn text parsing and schema validation."""

    def setUp(self) -> None:
        super().setUp()
        self.validator = TSCNValidator(project_root=self.workspace_dir)

    def test_tscn_empty_text(self) -> None:
        """
        Adversarial Scenario: Empty string or whitespace-only .tscn text.
        """
        errors = self.validator.validate_tscn_text("", file_path="scenes/empty.tscn")
        self.assertTrue(any("Scene file is empty." in e.message for e in errors))

        errors_whitespace = self.validator.validate_tscn_text("   \n\n\t  \n")
        self.assertTrue(any("Scene file is empty." in e.message for e in errors_whitespace))

    def test_tscn_missing_gd_scene_header(self) -> None:
        """
        Adversarial Scenario: .tscn text missing the required [gd_scene ...] header.
        """
        raw_text = """
[node name="Main" type="Node2D"]
"""
        errors = self.validator.validate_tscn_text(raw_text, file_path="scenes/no_header.tscn")
        self.assertTrue(any("Missing '[gd_scene ...]' scene header tag" in e.message for e in errors))

    def test_tscn_invalid_format_version(self) -> None:
        """
        Adversarial Scenario: .tscn declares format=2 (Godot 3) or format=4 (unsupported future).
        Godot 4 requires format=3.
        """
        tscn_f2 = """[gd_scene format=2]

[node name="Main" type="Node2D"]
"""
        errors = self.validator.validate_tscn_text(tscn_f2, file_path="scenes/f2.tscn")
        self.assertTrue(any("Unsupported scene format '2'; Godot 4 requires format=3." in e.message for e in errors))

        tscn_f4 = """[gd_scene format=4]

[node name="Main" type="Node2D"]
"""
        errors4 = self.validator.validate_tscn_text(tscn_f4, file_path="scenes/f4.tscn")
        self.assertTrue(any("Unsupported scene format '4'; Godot 4 requires format=3." in e.message for e in errors4))

    def test_tscn_malformed_header_syntax(self) -> None:
        """
        Adversarial Scenario: [gd_scene] tag is missing format attribute.
        """
        tscn_no_fmt = """[gd_scene load_steps=1]

[node name="Main" type="Node2D"]
"""
        errors = self.validator.validate_tscn_text(tscn_no_fmt, file_path="scenes/no_fmt.tscn")
        self.assertTrue(any("Missing 'format' attribute in [gd_scene] header." in e.message for e in errors))

    def test_tscn_load_steps_discrepancy(self) -> None:
        """
        Adversarial Scenario: Declared load_steps does not match actual count (ext + sub + 1).
        Must produce a WARNING diagnostic.
        """
        tscn_bad_steps = """[gd_scene load_steps=10 format=3]

[ext_resource type="Texture2D" path="res://assets/tex.png" id="1_tex"]

[node name="Main" type="Node2D"]
"""
        errors = self.validator.validate_tscn_text(tscn_bad_steps, file_path="scenes/bad_steps.tscn")
        warns = [e for e in errors if e.severity == "WARNING" and "load_steps mismatch in header" in e.message]
        self.assertEqual(len(warns), 1)
        self.assertIn("declared 10, expected 2", warns[0].message)

    def test_tscn_out_of_order_topological_nodes(self) -> None:
        """
        Adversarial Scenario: Child node declared in file before parent node.
        """
        tscn_out_of_order = """[gd_scene format=3]

[node name="Root" type="Node2D"]

[node name="GrandChild" type="Sprite2D" parent="Child"]

[node name="Child" type="Node2D" parent="."]
"""
        errors = self.validator.validate_tscn_text(tscn_out_of_order, file_path="scenes/order.tscn")
        self.assertTrue(any("Node 'GrandChild' references non-existent parent 'Child'." in e.message for e in errors))

    def test_tscn_path_traversal_in_ext_resource(self) -> None:
        """
        Adversarial Scenario: External resource path contains relative directory traversal '..'.
        """
        tscn_traversal = """[gd_scene load_steps=2 format=3]

[ext_resource type="Texture2D" path="res://../secret/key.pem" id="1_key"]

[node name="Root" type="Node2D"]
"""
        errors = self.validator.validate_tscn_text(tscn_traversal, file_path="scenes/traversal.tscn")
        self.assertTrue(any("Path traversal detected in resource path" in e.message for e in errors))

    def test_tscn_resource_path_not_res(self) -> None:
        """
        Adversarial Scenario: External resource path does not begin with 'res://'.
        """
        tscn_bad_path = """[gd_scene load_steps=2 format=3]

[ext_resource type="Texture2D" path="/usr/local/share/texture.png" id="1_tex"]

[node name="Root" type="Node2D"]
"""
        errors = self.validator.validate_tscn_text(tscn_bad_path, file_path="scenes/bad_path.tscn")
        self.assertTrue(any("does not start with 'res://'" in e.message for e in errors))

    def test_tscn_nonexistent_disk_resource(self) -> None:
        """
        Adversarial Scenario: External resource is not present on disk in project_root.
        """
        tscn_missing_disk = """[gd_scene load_steps=2 format=3]

[ext_resource type="Texture2D" path="res://assets/ghost.png" id="1_ghost"]

[node name="Root" type="Node2D"]
"""
        errors = self.validator.validate_tscn_text(
            tscn_missing_disk,
            file_path="scenes/missing_disk.tscn",
            project_root=self.workspace_dir,
        )
        self.assertTrue(any("Referenced external resource does not exist on disk" in e.message for e in errors))

    def test_tscn_instance_unknown_resource(self) -> None:
        """
        Adversarial Scenario: Node specifies an instance resource ID that was never registered.
        """
        tscn_unknown_instance = """[gd_scene format=3]

[node name="Root" type="Node2D"]

[node name="SubScene" parent="." instance=ExtResource("99_missing_scene")]
"""
        errors = self.validator.validate_tscn_text(tscn_unknown_instance, file_path="scenes/instance.tscn")
        self.assertTrue(any("instances unknown resource id '99_missing_scene'" in e.message for e in errors))

    def test_tscn_instance_wrong_type(self) -> None:
        """
        Adversarial Scenario: Node instances an ExtResource whose type is 'Texture2D' rather than 'PackedScene'.
        """
        tscn_wrong_inst_type = """[gd_scene load_steps=2 format=3]

[ext_resource type="Texture2D" path="res://assets/tex.png" id="1_tex"]

[node name="Root" type="Node2D"]

[node name="SubScene" parent="." instance=ExtResource("1_tex")]
"""
        errors = self.validator.validate_tscn_text(tscn_wrong_inst_type, file_path="scenes/inst_type.tscn")
        self.assertTrue(any("expected 'packedscene'" in e.message.lower() for e in errors))

    def test_tscn_signal_connection_missing_emitter_or_receiver(self) -> None:
        """
        Adversarial Scenario: Signal connection references non-existent emitter or recipient node.
        """
        tscn_bad_conn = """[gd_scene format=3]

[node name="Root" type="Node2D"]

[connection signal="pressed" from="GhostButton" to="Root" method="_on_button_pressed"]
[connection signal="pressed" from="Root" to="GhostReceiver" method="_on_root_pressed"]
"""
        errors = self.validator.validate_tscn_text(tscn_bad_conn, file_path="scenes/bad_conn.tscn")
        self.assertTrue(any("Signal connection 'from' node 'GhostButton' does not exist" in e.message for e in errors))
        self.assertTrue(any("Signal connection 'to' node 'GhostReceiver' does not exist" in e.message for e in errors))

    def test_tscn_signal_connection_empty_signal_or_method(self) -> None:
        """
        Adversarial Scenario: Signal connection has blank signal name or method name.
        """
        scene = SceneAST(name="EmptySignalScene", path="scenes/empty_signal.tscn")
        scene.nodes.append(NodeAST(name="Root", type="Node2D"))
        scene.connections.append(ConnectionAST(signal="", from_node=".", to_node=".", method="_on_test"))
        scene.connections.append(ConnectionAST(signal="custom_sig", from_node=".", to_node=".", method=""))

        errors = self.validator.validate_scene_ast(scene)
        self.assertTrue(any("Signal connection has empty signal name." in e.message for e in errors))
        self.assertTrue(any("Signal connection has empty callback method name." in e.message for e in errors))

    def test_tscn_project_scenes_nonexistent_directory(self) -> None:
        """
        Adversarial Scenario: Calling validate_project_scenes on a non-existent path.
        """
        errors = self.validator.validate_project_scenes(self.workspace_dir / "non_existent_folder_404")
        self.assertEqual(len(errors), 1)
        self.assertIn("Project directory does not exist", errors[0].message)


# ============================================================================
# Category 4: Adversarial Asset Integrity Verifier
# ============================================================================

class TestAdversarialAssetVerifier(StudioTestCase):
    """Adversarial stress testing of binary headers, corrupted formats, and path traversals."""

    def setUp(self) -> None:
        super().setUp()
        self.assets_dir = self.workspace_dir / "assets"
        self.assets_dir.mkdir(parents=True, exist_ok=True)

    def test_asset_zero_byte_png_wav_files(self) -> None:
        """
        Adversarial Scenario: 0-byte asset files on disk.
        Verifier must flag them as empty (0 bytes) errors.
        """
        empty_png = self.assets_dir / "empty.png"
        empty_wav = self.assets_dir / "empty.wav"
        empty_png.write_bytes(b"")
        empty_wav.write_bytes(b"")

        verifier = AssetVerifier(self.workspace_dir)
        errs_png = verifier.verify_file(empty_png)
        errs_wav = verifier.verify_file(empty_wav)

        self.assertTrue(any("Asset file is empty (0 bytes)" in e.message for e in errs_png))
        self.assertTrue(any("Asset file is empty (0 bytes)" in e.message for e in errs_wav))

    def test_asset_corrupted_png_magic_bytes(self) -> None:
        """
        Adversarial Scenario: PNG file with invalid header signature.
        """
        corrupted = b"NOT_PNG_HEADER_GARBAGE_BYTES_12345"
        err = validate_magic_bytes(corrupted, "png")
        self.assertIsNotNone(err)
        self.assertIn("Missing PNG magic header signature", err)

    def test_asset_truncated_png_less_than_24_bytes(self) -> None:
        """
        Adversarial Scenario: PNG file truncated before standard 24-byte IHDR header.
        """
        truncated = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHD"  # 15 bytes
        err = validate_magic_bytes(truncated, "png")
        self.assertIsNotNone(err)
        self.assertIn("PNG file is truncated (less than 24 bytes)", err)

    def test_asset_invalid_png_dimensions_zero_or_negative(self) -> None:
        """
        Adversarial Scenario: PNG header declares 0 width or 0 height.
        """
        # Valid PNG magic + IHDR tag, but width = 0, height = 0
        zero_dim = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x00"  # width = 0
            b"\x00\x00\x00\x00"  # height = 0
        )
        err = validate_magic_bytes(zero_dim, "png")
        self.assertIsNotNone(err)
        self.assertIn("Invalid PNG dimensions: 0x0", err)

    def test_asset_corrupted_wav_missing_riff_or_wave(self) -> None:
        """
        Adversarial Scenario: WAV file missing RIFF or WAVE signature.
        """
        bad_riff = b"ABCD\x00\x00\x00\x00WAVE"
        err1 = validate_magic_bytes(bad_riff, "wav")
        self.assertIn("Missing WAV RIFF/WAVE container signature", err1)

        bad_wave = b"RIFF\x00\x00\x00\x00FAIL"
        err2 = validate_magic_bytes(bad_wave, "wav")
        self.assertIn("Missing WAV RIFF/WAVE container signature", err2)

    def test_asset_truncated_wav_less_than_12_bytes(self) -> None:
        """
        Adversarial Scenario: WAV file truncated to less than 12 bytes.
        """
        truncated = b"RIFF\x04\x00\x00\x00"
        err = validate_magic_bytes(truncated, "wav")
        self.assertIn("WAV file is truncated (less than 12 bytes)", err)

    def test_asset_corrupted_jpeg_ogg_mp3(self) -> None:
        """
        Adversarial Scenario: Truncated or corrupted headers for JPEG, OGG, and MP3 formats.
        """
        # JPEG truncated & missing SOI
        self.assertIn("truncated", validate_magic_bytes(b"\xff\xd8", "jpg"))
        self.assertIn("Missing JPEG SOI", validate_magic_bytes(b"GARBAGE_JPG", "jpg"))

        # OGG truncated & missing OggS
        self.assertIn("Missing OGG", validate_magic_bytes(b"NOT_OGG", "ogg"))

        # MP3 truncated & missing sync
        self.assertIn("Missing MP3 ID3 header or MPEG sync frame", validate_magic_bytes(b"NOPE_MP3", "mp3"))

    def test_asset_corrupted_svg_missing_tags_or_non_utf8(self) -> None:
        """
        Adversarial Scenario: SVG containing binary invalid UTF-8 or missing <svg> tags.
        """
        non_utf8 = b"\xff\xfe\x00\x00\x12\x34"
        self.assertIn("SVG is not valid UTF-8 text", validate_magic_bytes(non_utf8, "svg"))

        no_svg_tags = b"<html><body>Not an SVG file</body></html>"
        self.assertIn("SVG missing <svg> or </svg> XML tags", validate_magic_bytes(no_svg_tags, "svg"))

    def test_asset_corrupted_json_syntax(self) -> None:
        """
        Adversarial Scenario: JSON asset file containing malformed syntax.
        """
        bad_json = b'{"unclosed": "string'
        self.assertIn("Malformed JSON content", validate_magic_bytes(bad_json, "json"))

    def test_asset_path_traversal_relative_dot_dot(self) -> None:
        """
        Adversarial Scenario: Asset reference in manifest contains '../' path traversal.
        Must trigger security violation DiagnosticError.
        """
        manifest = AssetManifest(
            project_name="TestProject",
            items=[
                AssetItem(
                    asset_id="exploit",
                    asset_type=AssetType.SPRITE,
                    rel_path="../../etc/shadow",
                    engine_path="res://../../etc/shadow",
                )
            ],
        )
        verifier = AssetVerifier(self.workspace_dir, manifest=manifest)
        result = verifier.verify_all()

        self.assertFalse(result.passed)
        traversal_errors = [e for e in result.errors if "security violation: path traversal" in e.message.lower()]
        self.assertGreaterEqual(len(traversal_errors), 1)

    def test_asset_path_traversal_absolute_escape(self) -> None:
        """
        Adversarial Scenario: Asset reference attempts to escape project sandbox via absolute path.
        """
        outside_file = tempfile.NamedTemporaryFile(delete=False)
        outside_file.write(b"EXT")
        outside_file.close()

        ref = DiscoveredAssetRef(
            target_path=outside_file.name,
            resolved_rel_path=outside_file.name,
            referring_file="scenes/main.tscn",
        )
        verifier = AssetVerifier(self.workspace_dir)
        verifier.discover_references = lambda: [ref]  # type: ignore

        result = verifier.verify_all()
        outside_errors = [e for e in result.errors if "resolves outside project root" in e.message.lower()]
        self.assertGreaterEqual(len(outside_errors), 1)

        os.unlink(outside_file.name)

    def test_asset_orphan_detection_warning_vs_error(self) -> None:
        """
        Adversarial Scenario: Physical asset exists on disk in assets/sprites/ but is unreferenced.
        Verifies fail_on_orphan=False yields WARNING while fail_on_orphan=True yields ERROR.
        """
        orphan_path = self.assets_dir / "sprites" / "orphan_player.png"
        self.create_dummy_png(orphan_path)

        verifier = AssetVerifier(self.workspace_dir)

        # Warning mode
        result_warn = verifier.verify_all(fail_on_orphan=False)
        self.assertIn("assets/sprites/orphan_player.png", result_warn.orphaned_files)
        self.assertEqual(len(result_warn.warnings), 1)
        self.assertEqual(len(result_warn.errors), 0)
        self.assertTrue(result_warn.passed)

        # Strict error mode
        result_err = verifier.verify_all(fail_on_orphan=True)
        self.assertEqual(len(result_err.errors), 1)
        self.assertFalse(result_err.passed)

    def test_asset_missing_referenced_file(self) -> None:
        """
        Adversarial Scenario: Asset declared in manifest does not exist on disk.
        """
        manifest = AssetManifest(
            project_name="TestProject",
            items=[
                AssetItem(
                    asset_id="ghost_sfx",
                    asset_type=AssetType.AUDIO_SFX,
                    rel_path="audio/sfx/missing.wav",
                    engine_path="res://audio/sfx/missing.wav",
                )
            ],
        )
        verifier = AssetVerifier(self.workspace_dir, manifest=manifest)
        result = verifier.verify_all()

        self.assertFalse(result.passed)
        missing_errors = [e for e in result.errors if "missing referenced asset file" in e.message.lower()]
        self.assertEqual(len(missing_errors), 1)


# ============================================================================
# Category 5: Adversarial Dual-Mode Engine Simulator
# ============================================================================

class TestAdversarialEngineSimulator(StudioTestCase):
    """Adversarial stress testing of frame stepping, physics boundaries, and runaways."""

    def setUp(self) -> None:
        super().setUp()
        self.simulator = MockEngineSimulator(self.workspace_dir)

    def _create_minimal_project(self, main_scene_content: str) -> pathlib.Path:
        cfg = self.workspace_dir / "project.godot"
        cfg.write_text('[application]\nconfig/name="TestSim"\nrun/main_scene="res://scenes/main.tscn"\n', encoding="utf-8")
        scene_path = self.workspace_dir / "scenes" / "main.tscn"
        scene_path.parent.mkdir(parents=True, exist_ok=True)
        scene_path.write_text(main_scene_content, encoding="utf-8")
        return scene_path

    def test_sim_negative_frames_raises_value_error(self) -> None:
        """
        Adversarial Scenario: Calling simulate() with negative frame count.
        """
        with self.assertRaises(ValueError) as ctx:
            self.simulator.simulate(frames=-10)
        self.assertIn("cannot be negative", str(ctx.exception))

    def test_sim_zero_frames_instant_success(self) -> None:
        """
        Adversarial Scenario: Calling simulate() with 0 frames.
        Should instantiate the tree, validate contracts, and return passed=True without stepping.
        """
        self._create_minimal_project('[gd_scene format=3]\n\n[node name="Root" type="Node2D"]\n')
        res = self.simulator.simulate(frames=0)
        self.assertTrue(res.passed)
        self.assertEqual(res.frames_run, 0)
        self.assertIn("0 frames", res.stdout)

    def test_sim_missing_main_scene_fails_gracefully(self) -> None:
        """
        Adversarial Scenario: Directory has no project.godot and no .tscn files.
        """
        res = self.simulator.simulate(frames=10)
        self.assertFalse(res.passed)
        self.assertTrue(any("No valid main scene found" in e.message for e in res.errors))

    def test_sim_corrupted_main_scene_tscn(self) -> None:
        """
        Adversarial Scenario: Main scene file contains corrupted non-UTF8 binary data.
        read_text() fails with UnicodeDecodeError, triggering 'Failed to load main scene during simulation'.
        """
        scene_path = self._create_minimal_project("")
        # Overwrite with invalid binary bytes
        scene_path.write_bytes(b"\xff\xfe\x00\x00\xfa\xfb\xcc\xdd")
        res = self.simulator.simulate(frames=10)
        self.assertFalse(res.passed)
        self.assertTrue(any("Failed to load main scene during simulation" in e.message for e in res.errors))

    def test_sim_missing_root_node_in_scene(self) -> None:
        """
        Adversarial Scenario: Scene file defines 0 nodes.
        Simulator cannot instantiate tree and reports 'Scene has no root node to instantiate.'.
        """
        self._create_minimal_project("[gd_scene format=3]\n\n")
        res = self.simulator.simulate(frames=10)
        self.assertFalse(res.passed)
        self.assertTrue(any("Scene has no root node to instantiate." in e.message for e in res.errors))

    def test_sim_orphan_nodes_without_root_fallback_behavior(self) -> None:
        """
        Adversarial Finding: Scene has only child nodes referencing a parent, but no true root.
        ast_models.SceneAST.get_root() falls back to self.nodes[0] when no node has is_root=True.
        Documents this runtime fallback behavior where MockEngineSimulator instantiates nodes[0]
        as the simulated root if static TSCN validation was bypassed.
        """
        content = """[gd_scene format=3]

[node name="Child" type="Node2D" parent="PhantomRoot"]
"""
        self._create_minimal_project(content)
        res = self.simulator.simulate(frames=5)
        # Empirical finding: passes runtime frame stepping because get_root() fallback to nodes[0]
        self.assertTrue(res.passed)
        self.assertEqual(res.frames_run, 5)

    def test_sim_nan_coordinates_boundary_condition(self) -> None:
        """
        Adversarial Scenario: A physics node has NaN initial position.
        Frame stepper must catch NaN coordinate runtime error.
        """
        scene = SceneAST(name="NaNScene", path="scenes/main.tscn")
        root = NodeAST(
            name="Player",
            type="CharacterBody2D",
            parent=None,
            properties={"pos_x": float("nan"), "pos_y": 0.0},
        )
        scene.nodes.append(root)

        # Write project
        cfg = self.workspace_dir / "project.godot"
        cfg.write_text('[application]\nrun/main_scene="res://scenes/main.tscn"\n', encoding="utf-8")
        (self.workspace_dir / "scenes").mkdir(parents=True, exist_ok=True)
        (self.workspace_dir / "scenes" / "main.tscn").write_text(serialize_tscn(scene), encoding="utf-8")

        res = self.simulator.simulate(frames=10)
        self.assertFalse(res.passed)
        nan_errs = [e for e in res.errors if "nan coordinate detected on node 'player'" in e.message.lower()]
        self.assertEqual(len(nan_errs), 1)

    def test_sim_inf_coordinates_boundary_condition(self) -> None:
        """
        Adversarial Scenario: A physics node has infinite velocity.
        Frame stepper must catch Inf coordinate runtime error.
        """
        scene = SceneAST(name="InfScene", path="scenes/main.tscn")
        root = NodeAST(
            name="Missile",
            type="CharacterBody2D",
            parent=None,
            properties={"vel_y": float("inf")},
        )
        scene.nodes.append(root)

        cfg = self.workspace_dir / "project.godot"
        cfg.write_text('[application]\nrun/main_scene="res://scenes/main.tscn"\n', encoding="utf-8")
        (self.workspace_dir / "scenes").mkdir(parents=True, exist_ok=True)
        (self.workspace_dir / "scenes" / "main.tscn").write_text(serialize_tscn(scene), encoding="utf-8")

        res = self.simulator.simulate(frames=10)
        self.assertFalse(res.passed)
        inf_errs = [e for e in res.errors if "inf coordinate detected on node 'missile'" in e.message.lower()]
        self.assertEqual(len(inf_errs), 1)

    def test_sim_physics_runaway_coordinates_exceed_bounds(self) -> None:
        """
        Adversarial Scenario: Excessive velocity causes position runaway (> 100,000).
        """
        scene = SceneAST(name="RunawayScene", path="scenes/main.tscn")
        root = NodeAST(
            name="Rocket",
            type="RigidBody2D",
            parent=None,
            properties={"pos_x": 0.0, "vel_x": 10_000_000.0},
        )
        scene.nodes.append(root)

        cfg = self.workspace_dir / "project.godot"
        cfg.write_text('[application]\nrun/main_scene="res://scenes/main.tscn"\n', encoding="utf-8")
        (self.workspace_dir / "scenes").mkdir(parents=True, exist_ok=True)
        (self.workspace_dir / "scenes" / "main.tscn").write_text(serialize_tscn(scene), encoding="utf-8")

        res = self.simulator.simulate(frames=10)
        self.assertFalse(res.passed)
        runaway_errs = [e for e in res.errors if "physics runaway" in e.message.lower()]
        self.assertEqual(len(runaway_errs), 1)

    def test_sim_script_missing_child_dollar_node_reference(self) -> None:
        """
        Adversarial Scenario: Attached GDScript references $MissingChild, which does not exist in the scene.
        Simulator must identify the Godot null reference exception contract violation.
        """
        script_code = """extends Node2D

func _ready():
    $PhantomChild.play()
"""
        script_path = self.workspace_dir / "scripts" / "player.gd"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script_code, encoding="utf-8")

        scene_content = """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/player.gd" id="1_scr"]

[node name="Player" type="Node2D"]
script = ExtResource("1_scr")
"""
        self._create_minimal_project(scene_content)
        res = self.simulator.simulate(frames=10)
        self.assertFalse(res.passed)
        dollar_errs = [e for e in res.errors if "$PhantomChild" in e.message and "null reference" in e.message.lower()]
        self.assertEqual(len(dollar_errs), 1)

    def test_sim_timeout_boundary_condition(self) -> None:
        """
        Adversarial Scenario: Simulation step timeout boundary triggers cleanly without deadlock.
        """
        self._create_minimal_project('[gd_scene format=3]\n\n[node name="Root" type="Node2D"]\n')
        # Setting timeout to 0.0 forces an immediate timeout on frame step
        res = self.simulator.simulate(frames=500, timeout_seconds=0.0)
        self.assertFalse(res.passed)
        timeout_errs = [e for e in res.errors if "timed out" in e.message.lower()]
        self.assertEqual(len(timeout_errs), 1)

    def test_sim_dual_mode_fallback_to_mock_when_godot_missing(self) -> None:
        """
        Adversarial Scenario: DualModeEngineSimulator with no godot binary present or forced mock mode.
        Must execute cleanly in Mode 2 (mock_engine).
        """
        self._create_minimal_project('[gd_scene format=3]\n\n[node name="Root" type="Node2D"]\n')
        dual_sim = DualModeEngineSimulator(
            project_dir=self.workspace_dir,
            godot_binary="/usr/bin/non_existent_godot_bin_404",
            force_mode="mock_engine",
        )
        res = dual_sim.run_simulation(frames=15)
        self.assertEqual(res.mode_used, "mock_engine")
        self.assertEqual(res.frames_run, 15)
        self.assertTrue(res.passed)


# ============================================================================
# Category 6: Adversarial Multi-Engine Hub
# ============================================================================

class TestAdversarialMultiEngineHub(StudioTestCase):
    """Adversarial stress testing of engine target resolution, adapter registries, and multi-exports."""

    def setUp(self) -> None:
        super().setUp()
        self.hub = EngineHub(register_defaults=True)

    def test_hub_unknown_target_engine_defaults_to_godot4(self) -> None:
        """
        Adversarial Scenario: Requesting unsupported / unknown engine targets ('unreal', 'cryengine', 'custom').
        Hub must log a warning and safely default to GODOT4.
        """
        self.assertEqual(self.hub.resolve_target("unreal_engine_5"), EngineTarget.GODOT4)
        self.assertEqual(self.hub.resolve_target("cryengine"), EngineTarget.GODOT4)
        self.assertEqual(self.hub.resolve_target("custom_engine_xyz"), EngineTarget.GODOT4)
        self.assertEqual(self.hub.resolve_target(None), EngineTarget.GODOT4)

    def test_hub_duplicate_adapter_registration_without_override(self) -> None:
        """
        Adversarial Scenario: Registering an adapter for an already registered target without override=True.
        Must raise a ValueError.
        """
        godot_adapter = self.hub.get_adapter(EngineTarget.GODOT4)
        with self.assertRaises(ValueError) as ctx:
            self.hub.register_adapter(godot_adapter, override=False)
        self.assertIn("already registered. Use override=True", str(ctx.exception))

    def test_hub_override_adapter_registration(self) -> None:
        """
        Adversarial Scenario: Registering adapter with override=True must successfully replace it.
        """
        class MockCustomGodotAdapter(BaseEngineAdapter):
            @property
            def target(self) -> EngineTarget:
                return EngineTarget.GODOT4

            @property
            def display_name(self) -> str:
                return "Custom Overridden Godot Adapter"

            @property
            def file_extensions(self) -> List[str]:
                return [".tscn", ".gd"]

            def export_project(self, *args: Any, **kwargs: Any) -> EngineProjectManifest:
                return EngineProjectManifest(
                    target_engine="godot4",
                    project_name="Mock",
                    project_root="/tmp",
                    files_created=[],
                )

        custom = MockCustomGodotAdapter()
        self.hub.register_adapter(custom, override=True)
        self.assertEqual(self.hub.get_adapter(EngineTarget.GODOT4).display_name, "Custom Overridden Godot Adapter")

    def test_hub_unregister_adapter_and_query_missing(self) -> None:
        """
        Adversarial Scenario: Unregistering an adapter and querying it.
        get_adapter must raise KeyError with available engines listed.
        """
        unregistered = self.hub.unregister_adapter(EngineTarget.UNITY)
        self.assertIsNotNone(unregistered)
        self.assertFalse(self.hub.has_adapter(EngineTarget.UNITY))

        with self.assertRaises(KeyError) as ctx:
            self.hub.get_adapter(EngineTarget.UNITY)
        self.assertIn("No adapter registered for engine 'unity'", str(ctx.exception))

    def test_hub_export_multi_engine_isolation(self) -> None:
        """
        Adversarial Scenario: Exporting a project simultaneously to both 'godot4' and 'pygame'
        under the same base directory.
        Verifies clean isolation into independent subdirectories without cross-directory file pollution.
        """
        gdd = GameDesignDocument(
            title="MultiExportGame",
            genre=GameGenre.PLATFORMER_2D.value,
            high_concept="Multi-target export stress test",
            gameplay_mechanics=["jump", "run"],
        )
        manifest = AssetManifest(project_name="MultiExportGame", items=[])
        scene = SceneAST(name="Main", path="scenes/main.tscn")
        scene.nodes.append(NodeAST(name="Root", type="Node2D"))
        script = ScriptSpec(rel_path="scripts/main.gd", language="gdscript", code="extends Node2D\n")

        results = self.hub.export_multi(
            targets=["godot4", "pygame"],
            gdd=gdd,
            manifest=manifest,
            scenes=[scene],
            scripts=[script],
            base_output_dir=self.workspace_dir,
        )

        self.assertIn("godot4", results)
        self.assertIn("pygame", results)

        godot_dir = self.workspace_dir / "godot4"
        pygame_dir = self.workspace_dir / "pygame"

        self.assertTrue(godot_dir.is_dir())
        self.assertTrue(pygame_dir.is_dir())

        # Verify engine-specific files are strictly isolated
        self.assertTrue((godot_dir / "project.godot").is_file())
        self.assertFalse((pygame_dir / "project.godot").exists())
        self.assertTrue((pygame_dir / "main.py").is_file())
        self.assertFalse((godot_dir / "main.py").exists())

    def test_hub_list_supported_engines_contains_all_targets(self) -> None:
        """
        Adversarial Scenario: Querying supported engines list from default hub.
        Must report all built-in engines with complete prerequisite validation reports.
        """
        engines = self.hub.list_supported_engines()
        targets = {e["target"] for e in engines}
        expected = {"godot4", "pygame", "web_html5", "unity"}
        self.assertTrue(expected.issubset(targets))

        for eng in engines:
            self.assertIn("display_name", eng)
            self.assertIn("file_extensions", eng)
            self.assertIn("prerequisites", eng)
            self.assertIsInstance(eng["prerequisites"], dict)


if __name__ == "__main__":
    unittest.main()
