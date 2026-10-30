"""
studio/verification/tscn_validator.py - Godot 4 .tscn Scene AST & File Schema Validator.
Implements F-VERIF-02: 7-layer validation of Godot 4 text scenes and SceneAST representations,
detecting load_steps discrepancies, cyclic or invalid node hierarchies, broken resource links,
script attachment defects, and signal wiring errors.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from studio.core.models import DiagnosticError
from studio.engine.adapters.godot_adapter import parse_tscn
from studio.engine.ast_models import ConnectionAST, ExtResourceAST, NodeAST, SceneAST, SubResourceAST


# ============================================================================
# Recognized Godot 4 Node Classes and Resource Types
# ============================================================================

GODOT4_NODE_CLASSES: Set[str] = {
    # Base
    "Node",
    # 2D Hierarchy
    "Node2D",
    "Sprite2D",
    "AnimatedSprite2D",
    "CollisionShape2D",
    "CollisionPolygon2D",
    "Area2D",
    "CharacterBody2D",
    "RigidBody2D",
    "StaticBody2D",
    "AnimatableBody2D",
    "Camera2D",
    "TileMap",
    "TileMapLayer",
    "Marker2D",
    "RayCast2D",
    "ShapeCast2D",
    "PointLight2D",
    "DirectionalLight2D",
    "VisibleOnScreenNotifier2D",
    "VisibleOnScreenEnabler2D",
    "ParallaxBackground",
    "ParallaxLayer",
    "Path2D",
    "PathFollow2D",
    "Line2D",
    "Polygon2D",
    "GPUParticles2D",
    "CPUParticles2D",
    "AudioStreamPlayer2D",
    # UI / Control Hierarchy
    "Control",
    "CanvasItem",
    "CanvasLayer",
    "CanvasModulate",
    "Label",
    "Button",
    "TextureRect",
    "ColorRect",
    "Panel",
    "PanelContainer",
    "MarginContainer",
    "BoxContainer",
    "VBoxContainer",
    "HBoxContainer",
    "GridContainer",
    "ScrollContainer",
    "CenterContainer",
    "SubViewportContainer",
    "SubViewport",
    "ProgressBar",
    "CheckBox",
    "OptionButton",
    "LineEdit",
    "TextEdit",
    "RichTextLabel",
    "NinePatchRect",
    # Audio & Animation
    "AudioStreamPlayer",
    "AnimationPlayer",
    "AnimationTree",
    "Timer",
    "WorldEnvironment",
    # 3D Hierarchy
    "Node3D",
    "Sprite3D",
    "AnimatedSprite3D",
    "MeshInstance3D",
    "CollisionShape3D",
    "CollisionPolygon3D",
    "Area3D",
    "CharacterBody3D",
    "RigidBody3D",
    "StaticBody3D",
    "Camera3D",
    "Marker3D",
    "RayCast3D",
    "DirectionalLight3D",
    "OmniLight3D",
    "SpotLight3D",
    "AudioStreamPlayer3D",
    "Path3D",
    "PathFollow3D",
    "GPUParticles3D",
    "CPUParticles3D",
}

GODOT4_RESOURCE_TYPES: Set[str] = {
    "Resource",
    "Texture",
    "Texture2D",
    "CompressedTexture2D",
    "ImageTexture",
    "AtlasTexture",
    "AudioStream",
    "AudioStreamWAV",
    "AudioStreamOggVorbis",
    "AudioStreamMP3",
    "Script",
    "GDScript",
    "CSharpScript",
    "PackedScene",
    "Shape2D",
    "RectangleShape2D",
    "CircleShape2D",
    "CapsuleShape2D",
    "SegmentShape2D",
    "SeparationRayShape2D",
    "WorldBoundaryShape2D",
    "TileSet",
    "Shader",
    "VisualShader",
    "StandardMaterial3D",
    "ORMMaterial3D",
    "ShaderMaterial",
    "Font",
    "FontFile",
    "Theme",
    "Animation",
    "AnimationLibrary",
}


# ============================================================================
# TSCN Validator Implementation
# ============================================================================

class TSCNValidator:
    """
    Validates Godot 4 .tscn text scenes and in-memory SceneAST representations.
    Enforces format standards, resource resolution, node hierarchy integrity,
    topological ordering, script attachments, and signal wiring.
    """

    def __init__(self, project_root: Optional[Union[str, Path]] = None) -> None:
        self.project_root = Path(project_root).resolve() if project_root else None

    # ------------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------------

    def validate_scene_ast(
        self,
        scene: SceneAST,
        project_root: Optional[Union[str, Path]] = None,
        file_path: Optional[str] = None,
    ) -> List[DiagnosticError]:
        """Validates in-memory SceneAST instance."""
        p_root = Path(project_root).resolve() if project_root else self.project_root
        f_path = file_path or scene.path or "scenes/unnamed_scene.tscn"
        errors: List[DiagnosticError] = []

        # 1. Header & Format Checks
        errors.extend(self._validate_ast_header(scene, f_path))

        # 2. External Resource Checks
        errors.extend(self._validate_ast_ext_resources(scene, f_path, p_root))

        # 3. Sub-Resource Checks
        errors.extend(self._validate_ast_sub_resources(scene, f_path))

        # 4. Node Hierarchy, Ordering, Sibling Uniqueness & Types
        errors.extend(self._validate_ast_nodes(scene, f_path))

        # 5. Script Attachments
        errors.extend(self._validate_ast_scripts(scene, f_path, p_root))

        # 6. Signal Connections
        errors.extend(self._validate_ast_connections(scene, f_path))

        return errors

    def validate_tscn_text(
        self,
        tscn_text: str,
        file_path: str = "scenes/scene.tscn",
        project_root: Optional[Union[str, Path]] = None,
    ) -> List[DiagnosticError]:
        """Validates raw .tscn text file."""
        p_root = Path(project_root).resolve() if project_root else self.project_root
        errors: List[DiagnosticError] = []

        if not tscn_text.strip():
            return [
                DiagnosticError(
                    stage="schema_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=1,
                    column=1,
                    message="Scene file is empty.",
                    suggested_fix="Generate a valid Godot 4 .tscn scene definition.",
                )
            ]

        # Raw text header checks (exact load_steps and format regex)
        header_errors, parsed_format, parsed_load_steps = self._validate_raw_header(tscn_text, file_path)
        errors.extend(header_errors)

        # Parse to SceneAST for structural verification
        try:
            scene_ast = parse_tscn(tscn_text, scene_path=file_path)
        except Exception as ex:
            errors.append(
                DiagnosticError(
                    stage="schema_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=1,
                    column=1,
                    message=f"Failed to parse .tscn scene: {ex}",
                    suggested_fix="Check .tscn syntax against Godot 4 format specification.",
                )
            )
            return errors

        # Verify load_steps formula against actual counts
        actual_total = len(scene_ast.ext_resources) + len(scene_ast.sub_resources)
        expected_load_steps = actual_total + 1 if actual_total > 0 else 0

        if parsed_load_steps is not None and parsed_load_steps != expected_load_steps:
            errors.append(
                DiagnosticError(
                    stage="schema_check",
                    severity="WARNING",
                    file_path=file_path,
                    line=1,
                    column=1,
                    message=(
                        f"load_steps mismatch in header: declared {parsed_load_steps}, "
                        f"expected {expected_load_steps} (ext={len(scene_ast.ext_resources)}, sub={len(scene_ast.sub_resources)})."
                    ),
                    suggested_fix=f"Update header to load_steps={expected_load_steps}.",
                )
            )

        # Run remaining checks on parsed AST
        ast_errors = self.validate_scene_ast(scene_ast, project_root=p_root, file_path=file_path)
        # Avoid duplicate header errors already caught in raw check
        for err in ast_errors:
            if not any(e.message == err.message and e.line == err.line for e in errors):
                errors.append(err)

        return errors

    def validate_scene_file(
        self,
        file_path: Union[str, Path],
        project_root: Optional[Union[str, Path]] = None,
    ) -> List[DiagnosticError]:
        """Validates a single .tscn file on disk."""
        path = Path(file_path)
        p_root = Path(project_root).resolve() if project_root else self.project_root
        if not path.is_file():
            return [
                DiagnosticError(
                    stage="schema_check",
                    severity="ERROR",
                    file_path=str(path),
                    line=1,
                    column=1,
                    message=f"Scene file does not exist: {path}",
                )
            ]
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as ex:
            return [
                DiagnosticError(
                    stage="schema_check",
                    severity="ERROR",
                    file_path=str(path),
                    line=1,
                    column=1,
                    message=f"Could not read scene file: {ex}",
                )
            ]
        rel_path = str(path.relative_to(p_root)) if p_root and path.is_relative_to(p_root) else str(path)
        return self.validate_tscn_text(text, file_path=rel_path, project_root=p_root)

    def validate_project_scenes(
        self,
        project_dir: Union[str, Path],
    ) -> List[DiagnosticError]:
        """Validates all .tscn files in project directory."""
        root = Path(project_dir).resolve()
        errors: List[DiagnosticError] = []
        if not root.is_dir():
            return [
                DiagnosticError(
                    stage="schema_check",
                    severity="ERROR",
                    file_path=str(root),
                    line=1,
                    column=1,
                    message=f"Project directory does not exist: {root}",
                )
            ]

        for p in sorted(root.rglob("*.tscn")):
            if p.is_file():
                errors.extend(self.validate_scene_file(p, project_root=root))

        return errors

    # ------------------------------------------------------------------------
    # Internal Validation Checkers
    # ------------------------------------------------------------------------

    def _validate_raw_header(
        self, tscn_text: str, file_path: str
    ) -> Tuple[List[DiagnosticError], Optional[int], Optional[int]]:
        errors: List[DiagnosticError] = []
        lines = tscn_text.strip().split("\n")

        first_tag_line = 0
        header_line = ""
        for idx, line in enumerate(lines, start=1):
            s = line.strip()
            if s and not s.startswith(";"):
                first_tag_line = idx
                header_line = s
                break

        if not header_line.startswith("[gd_scene"):
            errors.append(
                DiagnosticError(
                    stage="schema_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=first_tag_line or 1,
                    column=1,
                    message="Missing '[gd_scene ...]' scene header tag at top of file.",
                    suggested_fix="Ensure file begins with [gd_scene format=3 ...].",
                )
            )
            return errors, None, None

        # Check format=3
        format_match = re.search(r'\bformat=(\d+)\b', header_line)
        parsed_format = int(format_match.group(1)) if format_match else None
        if parsed_format is None:
            errors.append(
                DiagnosticError(
                    stage="schema_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=first_tag_line,
                    column=1,
                    message="Missing 'format' attribute in [gd_scene] header.",
                    suggested_fix="Add 'format=3' for Godot 4 compatibility.",
                )
            )
        elif parsed_format != 3:
            errors.append(
                DiagnosticError(
                    stage="schema_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=first_tag_line,
                    column=1,
                    message=f"Unsupported scene format '{parsed_format}'; Godot 4 requires format=3.",
                    suggested_fix="Set format=3 in [gd_scene] tag.",
                )
            )

        # Check load_steps attribute
        load_steps_match = re.search(r'\bload_steps=(\d+)\b', header_line)
        parsed_load_steps = int(load_steps_match.group(1)) if load_steps_match else None

        return errors, parsed_format, parsed_load_steps

    def _validate_ast_header(self, scene: SceneAST, file_path: str) -> List[DiagnosticError]:
        errors: List[DiagnosticError] = []
        if getattr(scene, "format_version", 3) != 3:
            errors.append(
                DiagnosticError(
                    stage="schema_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=1,
                    column=1,
                    message=f"SceneAST format_version {scene.format_version} != 3.",
                    suggested_fix="Set format_version=3.",
                )
            )
        return errors

    def _validate_ast_ext_resources(
        self,
        scene: SceneAST,
        file_path: str,
        project_root: Optional[Path],
    ) -> List[DiagnosticError]:
        errors: List[DiagnosticError] = []
        seen_ids: Set[str] = set()

        for idx, ext in enumerate(scene.ext_resources, start=1):
            # Unique ID check
            if not ext.id:
                errors.append(
                    DiagnosticError(
                        stage="schema_check",
                        severity="ERROR",
                        file_path=file_path,
                        line=idx,
                        message="External resource has missing or empty id.",
                    )
                )
            elif ext.id in seen_ids:
                errors.append(
                    DiagnosticError(
                        stage="schema_check",
                        severity="ERROR",
                        file_path=file_path,
                        line=idx,
                        message=f"Duplicate external resource id '{ext.id}'.",
                        suggested_fix="Assign unique IDs to all external resources.",
                    )
                )
            else:
                seen_ids.add(ext.id)

            # Valid resource type
            if ext.type not in GODOT4_RESOURCE_TYPES:
                errors.append(
                    DiagnosticError(
                        stage="schema_check",
                        severity="WARNING",
                        file_path=file_path,
                        line=idx,
                        message=f"External resource '{ext.id}' has unrecognized type '{ext.type}'.",
                    )
                )

            # Path syntax
            if not ext.path.startswith("res://"):
                errors.append(
                    DiagnosticError(
                        stage="schema_check",
                        severity="ERROR",
                        file_path=file_path,
                        line=idx,
                        message=f"External resource path '{ext.path}' does not start with 'res://'.",
                        suggested_fix=f"Prepend 'res://' to path (e.g. res://{ext.path.lstrip('/')}).",
                    )
                )
            elif ".." in ext.path:
                errors.append(
                    DiagnosticError(
                        stage="schema_check",
                        severity="ERROR",
                        file_path=file_path,
                        line=idx,
                        message=f"Path traversal detected in resource path '{ext.path}'.",
                        suggested_fix="Remove '..' relative segments from res:// path.",
                    )
                )
            elif project_root:
                # Disk existence check
                rel_disk = ext.path.replace("res://", "").lstrip("/")
                disk_file = project_root / rel_disk
                if not disk_file.exists():
                    errors.append(
                        DiagnosticError(
                            stage="schema_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=idx,
                            message=f"Referenced external resource does not exist on disk: '{ext.path}'",
                            suggested_fix=f"Generate or place asset at {rel_disk}.",
                        )
                    )

        return errors

    def _validate_ast_sub_resources(
        self,
        scene: SceneAST,
        file_path: str,
    ) -> List[DiagnosticError]:
        errors: List[DiagnosticError] = []
        seen_ids: Set[str] = set()

        for idx, sub in enumerate(scene.sub_resources, start=1):
            if not sub.id:
                errors.append(
                    DiagnosticError(
                        stage="schema_check",
                        severity="ERROR",
                        file_path=file_path,
                        line=idx,
                        message="Sub-resource has missing or empty id.",
                    )
                )
            elif sub.id in seen_ids:
                errors.append(
                    DiagnosticError(
                        stage="schema_check",
                        severity="ERROR",
                        file_path=file_path,
                        line=idx,
                        message=f"Duplicate sub-resource id '{sub.id}'.",
                        suggested_fix="Assign unique IDs to all sub-resources.",
                    )
                )
            else:
                seen_ids.add(sub.id)

            if sub.type not in GODOT4_RESOURCE_TYPES:
                errors.append(
                    DiagnosticError(
                        stage="schema_check",
                        severity="WARNING",
                        file_path=file_path,
                        line=idx,
                        message=f"Sub-resource '{sub.id}' has unrecognized type '{sub.type}'.",
                    )
                )

        return errors

    def _validate_ast_nodes(
        self,
        scene: SceneAST,
        file_path: str,
    ) -> List[DiagnosticError]:
        errors: List[DiagnosticError] = []

        if not scene.nodes:
            errors.append(
                DiagnosticError(
                    stage="schema_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=1,
                    column=1,
                    message="Scene contains no nodes.",
                    suggested_fix="Add at least one root node to the scene.",
                )
            )
            return errors

        # 1. Single Root Node Check
        roots = [n for n in scene.nodes if n.is_root]
        if len(roots) == 0:
            errors.append(
                DiagnosticError(
                    stage="schema_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=1,
                    column=1,
                    message="Scene must contain exactly 1 root node, found 0.",
                    suggested_fix="Declare root node without parent attribute.",
                )
            )
        elif len(roots) > 1:
            errors.append(
                DiagnosticError(
                    stage="schema_check",
                    severity="ERROR",
                    file_path=file_path,
                    line=1,
                    column=1,
                    message=f"Scene must contain exactly 1 root node, found {len(roots)}: {[r.name for r in roots]}.",
                    suggested_fix="Attach child nodes to the single root node using parent='.' or parent path.",
                )
            )

        # 2. Hierarchy, Sibling Uniqueness, Topological Order & Node Types
        declared_nodes_by_path: Set[str] = set()
        declared_node_names: Set[str] = set()
        siblings_by_parent: Dict[str, Set[str]] = {}

        root_name = roots[0].name if roots else None

        for idx, node in enumerate(scene.nodes, start=1):
            # Node Type check
            if not node.type and not node.instance:
                errors.append(
                    DiagnosticError(
                        stage="schema_check",
                        severity="ERROR",
                        file_path=file_path,
                        line=idx,
                        message=f"Node '{node.name}' has neither type nor instance attribute.",
                        suggested_fix="Specify a valid Godot 4 node class (e.g. type='Sprite2D') or instance scene.",
                    )
                )
            elif node.type and node.type not in GODOT4_NODE_CLASSES:
                errors.append(
                    DiagnosticError(
                        stage="schema_check",
                        severity="WARNING",
                        file_path=file_path,
                        line=idx,
                        message=f"Node '{node.name}' has unrecognized Godot 4 type '{node.type}'.",
                    )
                )

            # Hierarchy validation
            if node.is_root:
                declared_nodes_by_path.add(".")
                if root_name:
                    declared_nodes_by_path.add(root_name)
                declared_node_names.add(node.name)
            else:
                parent_ref = node.parent
                if not parent_ref:
                    continue  # already caught as extra root

                # Self-parenting check
                if parent_ref == node.name:
                    errors.append(
                        DiagnosticError(
                            stage="schema_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=idx,
                            message=f"Node '{node.name}' cannot be its own parent (self-parenting).",
                            suggested_fix="Set parent to valid ancestor node or '.'.",
                        )
                    )
                    continue

                # Check if parent exists in declared nodes (Topological ordering & valid parent)
                parent_valid = (
                    parent_ref == "."
                    or parent_ref in declared_nodes_by_path
                    or parent_ref in declared_node_names
                )
                if not parent_valid:
                    errors.append(
                        DiagnosticError(
                            stage="schema_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=idx,
                            message=f"Node '{node.name}' references non-existent parent '{parent_ref}'.",
                            suggested_fix=f"Ensure parent node '{parent_ref}' is defined before child node.",
                        )
                    )

                # Sibling name uniqueness under same parent
                parent_key = parent_ref or "."
                if parent_key not in siblings_by_parent:
                    siblings_by_parent[parent_key] = set()

                if node.name in siblings_by_parent[parent_key]:
                    errors.append(
                        DiagnosticError(
                            stage="schema_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=idx,
                            message=f"Duplicate sibling node name '{node.name}' under parent '{parent_key}'.",
                            suggested_fix=f"Rename node '{node.name}' to a unique sibling name.",
                        )
                    )
                else:
                    siblings_by_parent[parent_key].add(node.name)

                # Register this node's path
                node_path = node.name if parent_ref == "." else f"{parent_ref}/{node.name}"
                declared_nodes_by_path.add(node_path)
                declared_node_names.add(node.name)

        # 3. Cyclic Dependency Graph Detection
        node_parent_map: Dict[str, str] = {}
        for n in scene.nodes:
            if not n.is_root and n.parent:
                node_parent_map[n.name] = n.parent

        for node_name, p_name in node_parent_map.items():
            visited: Set[str] = {node_name}
            curr = p_name
            while curr and curr != ".":
                first_seg = curr.split("/")[0]
                if first_seg in visited:
                    errors.append(
                        DiagnosticError(
                            stage="schema_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=1,
                            message=f"Cyclic node parenting detected involving node '{node_name}' and ancestor '{first_seg}'.",
                            suggested_fix="Break cyclic parent reference chain in scene graph.",
                        )
                    )
                    break
                visited.add(first_seg)
                curr = node_parent_map.get(first_seg)

        return errors

    def _validate_ast_scripts(
        self,
        scene: SceneAST,
        file_path: str,
        project_root: Optional[Path],
    ) -> List[DiagnosticError]:
        errors: List[DiagnosticError] = []
        ext_map: Dict[str, ExtResourceAST] = {r.id: r for r in scene.ext_resources}

        for idx, node in enumerate(scene.nodes, start=1):
            if node.script_id:
                if node.script_id not in ext_map:
                    errors.append(
                        DiagnosticError(
                            stage="schema_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=idx,
                            message=f"Node '{node.name}' references unknown script_id '{node.script_id}'.",
                            suggested_fix=f"Register script as [ext_resource id=\"{node.script_id}\"] in scene header.",
                        )
                    )
                else:
                    ext = ext_map[node.script_id]
                    if ext.type not in ("Script", "GDScript", "CSharpScript"):
                        errors.append(
                            DiagnosticError(
                                stage="schema_check",
                                severity="ERROR",
                                file_path=file_path,
                                line=idx,
                                message=f"Node '{node.name}' script_id references resource of type '{ext.type}', expected 'Script'.",
                                suggested_fix="Ensure external resource has type='Script' or 'GDScript'.",
                            )
                        )
                    if project_root:
                        rel = ext.path.replace("res://", "").lstrip("/")
                        if not (project_root / rel).exists():
                            errors.append(
                                DiagnosticError(
                                    stage="schema_check",
                                    severity="ERROR",
                                    file_path=file_path,
                                    line=idx,
                                    message=f"Attached script file does not exist on disk: '{ext.path}'",
                                    suggested_fix=f"Create script file at {rel}.",
                                )
                            )

            if node.instance:
                if node.instance not in ext_map:
                    errors.append(
                        DiagnosticError(
                            stage="schema_check",
                            severity="ERROR",
                            file_path=file_path,
                            line=idx,
                            message=f"Node '{node.name}' instances unknown resource id '{node.instance}'.",
                            suggested_fix=f"Register subscene as [ext_resource id=\"{node.instance}\"] in scene header.",
                        )
                    )
                else:
                    ext = ext_map[node.instance]
                    if ext.type != "PackedScene":
                        errors.append(
                            DiagnosticError(
                                stage="schema_check",
                                severity="ERROR",
                                file_path=file_path,
                                line=idx,
                                message=f"Node '{node.name}' instances resource of type '{ext.type}', expected 'PackedScene'.",
                            )
                        )

        return errors

    def _validate_ast_connections(
        self,
        scene: SceneAST,
        file_path: str,
    ) -> List[DiagnosticError]:
        errors: List[DiagnosticError] = []
        node_names: Set[str] = {n.name for n in scene.nodes}
        node_names.add(".")  # root alias

        for idx, conn in enumerate(scene.connections, start=1):
            # Check emitter node exists
            from_base = conn.from_node.split("/")[0] if conn.from_node != "." else "."
            if from_base not in node_names:
                errors.append(
                    DiagnosticError(
                        stage="schema_check",
                        severity="ERROR",
                        file_path=file_path,
                        line=idx,
                        message=f"Signal connection 'from' node '{conn.from_node}' does not exist in scene.",
                        suggested_fix="Wire signal from an existing scene node.",
                    )
                )

            # Check receiver node exists
            to_base = conn.to_node.split("/")[0] if conn.to_node != "." else "."
            if to_base not in node_names:
                errors.append(
                    DiagnosticError(
                        stage="schema_check",
                        severity="ERROR",
                        file_path=file_path,
                        line=idx,
                        message=f"Signal connection 'to' node '{conn.to_node}' does not exist in scene.",
                        suggested_fix="Wire signal to an existing scene node.",
                    )
                )

            # Signal name
            if not conn.signal or not conn.signal.strip():
                errors.append(
                    DiagnosticError(
                        stage="schema_check",
                        severity="ERROR",
                        file_path=file_path,
                        line=idx,
                        message="Signal connection has empty signal name.",
                    )
                )

            # Callback method name
            if not conn.method or not conn.method.strip():
                errors.append(
                    DiagnosticError(
                        stage="schema_check",
                        severity="ERROR",
                        file_path=file_path,
                        line=idx,
                        message="Signal connection has empty callback method name.",
                    )
                )

        return errors
