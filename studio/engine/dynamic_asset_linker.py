"""
studio/engine/dynamic_asset_linker.py - Dynamic Asset Linker & Scene Binder.
Resolves asset IDs from GDD and manifests to engine paths (res://...),
binds sprites to Sprite2D and audio to AudioStreamPlayer2D nodes,
synthesizes missing collision shapes, and validates scene asset integrity.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from studio.core.models import (
    AssetItem,
    AssetManifest,
    AssetRequirement,
    DiagnosticError,
    GameDesignDocument,
    ScenePlan,
)
from studio.engine.ast_models import (
    ExtResourceAST,
    ExtResourceRef,
    NodeAST,
    SceneAST,
    StringNameRef,
    SubResourceAST,
    SubResourceRef,
    Vector2,
    generate_godot_uid,
)

logger = logging.getLogger("studio.engine.linker")


@dataclass
class AssetBinding:
    """Record of a resolved asset binding to a node property."""
    node_name: str
    property_name: str
    asset_id: str
    engine_path: str
    resource_type: str
    ext_resource_id: str


class DynamicAssetLinker:
    """
    Binds multimodal visual and audio assets into Godot Scene ASTs (F-ENG-04).
    Resolves assets, registers ExtResource tags, binds node properties,
    and synthesizes missing collision shapes.
    """

    def __init__(
        self,
        manifest: Optional[AssetManifest] = None,
        gdd: Optional[GameDesignDocument] = None,
    ) -> None:
        self.manifest = manifest
        self.gdd = gdd

    def set_manifest(self, manifest: AssetManifest) -> None:
        self.manifest = manifest

    def set_gdd(self, gdd: GameDesignDocument) -> None:
        self.gdd = gdd

    # ------------------------------------------------------------------------
    # 1. Multi-Stage Asset Resolution
    # ------------------------------------------------------------------------

    def resolve_asset(self, asset_ref: str) -> Optional[AssetItem]:
        """
        Resolves an asset reference (ID, relative path, or logical name)
        against the AssetManifest and GDD requirements.
        """
        if not self.manifest:
            # Fallback to GDD required assets even without manifest
            if self.gdd:
                return self._resolve_from_gdd(asset_ref)
            return None

        # 1. Exact asset_id match
        item = self.manifest.get_item(asset_ref)
        if item:
            return item

        # 2. Case-insensitive asset_id match
        ref_lower = asset_ref.lower().strip()
        for it in self.manifest.items:
            if it.asset_id.lower() == ref_lower:
                return it

        # 3. Strip common category prefixes (sprite_, sfx_, bgm_, tex_, item_)
        clean_ref = ref_lower
        for prefix in ("sprite_", "sfx_", "bgm_", "music_", "tex_", "texture_", "item_"):
            if clean_ref.startswith(prefix):
                clean_ref = clean_ref[len(prefix):]
                break

        for it in self.manifest.items:
            it_clean = it.asset_id.lower()
            for prefix in ("sprite_", "sfx_", "bgm_", "music_", "tex_", "texture_", "item_"):
                if it_clean.startswith(prefix):
                    it_clean = it_clean[len(prefix):]
                    break
            if it_clean == clean_ref:
                return it

        # 4. Match by filename in relative path
        for it in self.manifest.items:
            filename = it.rel_path.split("/")[-1]
            if clean_ref in filename.lower() or filename.lower().startswith(clean_ref):
                return it

        # 5. Fallback to GDD required assets
        if self.gdd:
            return self._resolve_from_gdd(asset_ref)

        return None

    def _resolve_from_gdd(self, asset_ref: str) -> Optional[AssetItem]:
        if not self.gdd:
            return None
        ref_lower = asset_ref.lower().strip()
        clean_ref = ref_lower
        for prefix in ("sprite_", "sfx_", "bgm_", "music_", "tex_", "texture_", "item_"):
            if clean_ref.startswith(prefix):
                clean_ref = clean_ref[len(prefix):]
                break

        for req in self.gdd.required_assets:
            if req.asset_id.lower() == ref_lower or clean_ref in req.asset_id.lower():
                rel_path = req.get_rel_path() if hasattr(req, "get_rel_path") else getattr(req, "target_rel_path", None) or f"assets/{req.asset_id}"
                return AssetItem(
                    asset_id=req.asset_id,
                    asset_type=req.asset_type,
                    rel_path=rel_path,
                    engine_path=f"res://{rel_path.lstrip('/')}",
                    is_mock=True,
                )
        return None

    # ------------------------------------------------------------------------
    # 2. Scene AST Asset Linking
    # ------------------------------------------------------------------------

    def link_scene(self, scene: SceneAST) -> SceneAST:
        """
        Processes a SceneAST, resolves referenced assets, registers ExtResource tags,
        and binds textures and audio streams to scene nodes.
        """
        # Phase 1: Reconcile existing ExtResources to ensure valid paths and UIDs
        for ext in scene.ext_resources:
            if not ext.path.startswith("res://"):
                ext.path = f"res://{ext.path.lstrip('/')}"
            if not ext.uid:
                ext.uid = generate_godot_uid(ext.path)

        # Phase 2: Traverse nodes and link property bindings
        actor_nodes_without_sprite: List[NodeAST] = []
        physics_nodes_without_shape: List[NodeAST] = []

        for node in scene.nodes:
            ntype = node.type or ""

            # Check for actor bodies needing sprite or collision shape
            if ntype in ("CharacterBody2D", "Area2D", "RigidBody2D", "StaticBody2D"):
                has_sprite = any(
                    c.type in ("Sprite2D", "AnimatedSprite2D")
                    for c in scene.get_children(node.name)
                )
                if not has_sprite:
                    actor_nodes_without_sprite.append(node)

                has_collision = any(
                    c.type in ("CollisionShape2D", "CollisionPolygon2D")
                    for c in scene.get_children(node.name)
                )
                if not has_collision:
                    physics_nodes_without_shape.append(node)

            # A. Texture Binding for Sprite2D / TextureRect
            if ntype in ("Sprite2D", "TextureRect"):
                tex_val = node.properties.get("texture")
                if tex_val and isinstance(tex_val, str) and not tex_val.startswith('ExtResource("'):
                    asset = self.resolve_asset(tex_val)
                    if asset:
                        res_id = scene.add_ext_resource("Texture2D", asset.engine_path)
                        node.properties["texture"] = ExtResourceRef(res_id)

            # B. Audio Stream Binding for AudioStreamPlayer2D / AudioStreamPlayer
            elif ntype in ("AudioStreamPlayer2D", "AudioStreamPlayer"):
                stream_val = node.properties.get("stream")
                if stream_val and isinstance(stream_val, str) and not stream_val.startswith('ExtResource("'):
                    asset = self.resolve_asset(stream_val)
                    if asset:
                        res_id = scene.add_ext_resource("AudioStream", asset.engine_path)
                        node.properties["stream"] = ExtResourceRef(res_id)

                        atype_str = str(getattr(asset, "asset_type", "")).lower()
                        is_music = "music" in atype_str or "bgm" in asset.asset_id.lower()
                        if is_music:
                            node.properties.setdefault("autoplay", True)
                            node.properties.setdefault("bus", StringNameRef("Music"))
                            node.properties.setdefault("parameters/looping", True)
                        else:
                            node.properties.setdefault("bus", StringNameRef("SFX"))

        # Phase 3: Synthesize missing child Sprite2D nodes for actors
        for actor in actor_nodes_without_sprite:
            matched_asset = self.resolve_asset(actor.name) or self.resolve_asset(f"sprite_{actor.name.lower()}")
            if matched_asset:
                res_id = scene.add_ext_resource("Texture2D", matched_asset.engine_path)
                sprite_node = NodeAST(
                    name=f"{actor.name}Sprite",
                    type="Sprite2D",
                    parent="." if actor.is_root else actor.name,
                    properties={"texture": ExtResourceRef(res_id)},
                )
                scene.nodes.append(sprite_node)
                logger.info(f"Synthesized child Sprite2D for actor '{actor.name}' with asset '{matched_asset.asset_id}'.")

        # Phase 4: Synthesize missing CollisionShape2D nodes for physics bodies
        for body in physics_nodes_without_shape:
            sub_id = f"CapsuleShape2D_{body.name.lower()}"
            scene.add_sub_resource("CapsuleShape2D", sub_id, {"radius": 14.0, "height": 44.0})
            collision_node = NodeAST(
                name="CollisionShape2D",
                type="CollisionShape2D",
                parent="." if body.is_root else body.name,
                properties={
                    "position": Vector2(0.0, 4.0),
                    "shape": SubResourceRef(sub_id),
                },
            )
            scene.nodes.append(collision_node)
            logger.info(f"Synthesized CollisionShape2D for physics node '{body.name}'.")

        return scene

    def link_all_scenes(self, scenes: List[SceneAST]) -> List[SceneAST]:
        """Runs link_scene across all scenes in project."""
        return [self.link_scene(s) for s in scenes]

    # ------------------------------------------------------------------------
    # 3. Procedural Scene Builder from Plan
    # ------------------------------------------------------------------------

    def build_scene_from_plan(
        self,
        plan: ScenePlan,
        script_path: Optional[str] = None,
    ) -> SceneAST:
        """
        Procedurally constructs a valid SceneAST from a GDD ScenePlan,
        binding all planned assets and creating standard node hierarchies.
        """
        scene_name = plan.get_name()
        scene_path = plan.get_path()
        root_type = plan.root_node_type or "Node2D"

        scene = SceneAST(name=scene_name, path=scene_path)
        root_node = NodeAST(name=scene_name, type=root_type)

        target_script = script_path or plan.script_name
        if target_script:
            norm_script = target_script if target_script.startswith("res://") else f"res://{target_script.lstrip('/')}"
            script_id = scene.add_ext_resource("Script", norm_script)
            root_node.script_id = script_id

        scene.nodes.append(root_node)

        for asset_ref in plan.required_assets:
            asset = self.resolve_asset(asset_ref)
            if not asset:
                continue

            atype = str(asset.asset_type).lower()
            if "sprite" in atype or "texture" in atype:
                res_id = scene.add_ext_resource("Texture2D", asset.engine_path)
                node_name = "Sprite2D" if len(plan.required_assets) <= 2 else f"{asset.asset_id.capitalize()}Sprite"
                scene.nodes.append(
                    NodeAST(
                        name=node_name,
                        type="Sprite2D",
                        parent=".",
                        properties={"texture": ExtResourceRef(res_id)},
                    )
                )
            elif "audio" in atype or "sfx" in atype:
                res_id = scene.add_ext_resource("AudioStream", asset.engine_path)
                scene.nodes.append(
                    NodeAST(
                        name=f"{asset.asset_id.capitalize()}Audio",
                        type="AudioStreamPlayer2D",
                        parent=".",
                        properties={
                            "stream": ExtResourceRef(res_id),
                            "bus": StringNameRef("SFX"),
                        },
                    )
                )

        return self.link_scene(scene)

    # ------------------------------------------------------------------------
    # 4. Link & Integrity Validation
    # ------------------------------------------------------------------------

    def validate_bindings(self, scene: SceneAST) -> List[DiagnosticError]:
        """
        Validates all external and sub-resource references in scene nodes.
        Returns a list of structured DiagnosticErrors for the verification pipeline.
        """
        errors: List[DiagnosticError] = []
        ext_ids = {r.id for r in scene.ext_resources}
        sub_ids = {s.id for s in scene.sub_resources}

        if self.manifest:
            manifest_engine_paths = {item.engine_path for item in self.manifest.items}
            for ext in scene.ext_resources:
                if ext.type != "Script" and ext.path not in manifest_engine_paths:
                    errors.append(
                        DiagnosticError(
                            stage="asset_integrity",
                            severity="WARNING",
                            file_path=scene.path,
                            message=f"External resource '{ext.id}' ({ext.path}) not found in AssetManifest.",
                        )
                    )

        for node in scene.nodes:
            if node.script_id and node.script_id not in ext_ids:
                errors.append(
                    DiagnosticError(
                        stage="schema_check",
                        severity="ERROR",
                        file_path=scene.path,
                        message=f"Node '{node.name}' references unknown script_id '{node.script_id}'.",
                    )
                )

            for p_key, p_val in node.properties.items():
                if isinstance(p_val, ExtResourceRef):
                    if p_val.id not in ext_ids:
                        errors.append(
                            DiagnosticError(
                                stage="schema_check",
                                severity="ERROR",
                                file_path=scene.path,
                                message=f"Node '{node.name}' property '{p_key}' references non-existent ExtResource '{p_val.id}'.",
                            )
                        )
                elif isinstance(p_val, SubResourceRef):
                    if p_val.id not in sub_ids:
                        errors.append(
                            DiagnosticError(
                                stage="schema_check",
                                severity="ERROR",
                                file_path=scene.path,
                                message=f"Node '{node.name}' property '{p_key}' references non-existent SubResource '{p_val.id}'.",
                            )
                        )

        return errors
