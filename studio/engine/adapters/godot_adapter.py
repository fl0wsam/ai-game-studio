"""
studio/engine/adapters/godot_adapter.py - Deterministic Godot 4 Serializer & Config Generator.
Serializes SceneAST models to 100% compliant .tscn text, parses .tscn back to SceneAST,
generates canonical project.godot configuration files, and implements GodotAdapter.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from studio.core.models import (
    AssetManifest,
    EngineTarget,
    GameDesignDocument,
    ScriptSpec,
)
from studio.engine.adapters.base import BaseEngineAdapter, EngineProjectManifest
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


# ============================================================================
# 1. Godot Variant Value Serializer
# ============================================================================

def format_godot_value(val: Any) -> str:
    """Formats a Python object into Godot 4 Variant text format."""
    if val is None:
        return "null"
    elif isinstance(val, bool):
        return "true" if val else "false"
    elif isinstance(val, int):
        return str(val)
    elif isinstance(val, float):
        if val.is_integer():
            return f"{val:.1f}"
        return str(val)
    elif isinstance(val, (ExtResourceRef, SubResourceRef, StringNameRef, NodePathRef, Vector2, Vector2i, Vector3, Color, Rect2)):
        return val.to_godot()
    elif isinstance(val, str):
        # Already formatted Godot expressions
        if val.startswith(('ExtResource("', "SubResource(", 'NodePath("', '&"', "Vector2(", "Vector2i(", "Vector3(", "Color(", "Rect2(")):
            return val
        escaped = val.replace('"', '\\"')
        return f'"{escaped}"'
    elif isinstance(val, (list, tuple)):
        # Check if list represents a 2D vector [x, y]
        if len(val) == 2 and all(isinstance(x, (int, float)) for x in val):
            return f"Vector2({val[0]}, {val[1]})"
        # Check if list represents a 3D vector [x, y, z]
        if len(val) == 3 and all(isinstance(x, (int, float)) for x in val):
            return f"Vector3({val[0]}, {val[1]}, {val[2]})"
        items_str = ", ".join(format_godot_value(x) for x in val)
        return f"[{items_str}]"
    elif isinstance(val, dict):
        entries = [f"{format_godot_value(k)}: {format_godot_value(v)}" for k, v in val.items()]
        return "{" + ", ".join(entries) + "}"
    return f'"{str(val)}"'


# ============================================================================
# 2. Deterministic .tscn Serializer
# ============================================================================

def _is_root_node(n: Any) -> bool:
    """Helper to detect if a node is the root across ast_models and core.models."""
    if hasattr(n, "is_root"):
        try:
            return bool(n.is_root)
        except Exception:
            pass
    p = getattr(n, "parent", None)
    return p is None or p == ""


def sort_nodes_topologically(nodes: List[Any]) -> List[Any]:
    """
    Sorts nodes so every parent node appears before its descendants.
    Preserves document structure required by Godot engine loader.
    """
    if not nodes:
        return []

    root = next((n for n in nodes if _is_root_node(n)), nodes[0])
    ordered: List[Any] = [root]
    added_names = {root.name}

    remaining = [n for n in nodes if n is not root]
    while remaining:
        progress = False
        next_remaining = []
        for n in remaining:
            parent_key = getattr(n, "parent", None)
            if parent_key == "." or parent_key in added_names:
                ordered.append(n)
                added_names.add(n.name)
                progress = True
            else:
                next_remaining.append(n)
        if not progress:
            ordered.extend(next_remaining)
            break
        remaining = next_remaining

    return ordered


def serialize_tscn(scene: Union[SceneAST, Any]) -> str:
    """
    Deterministically serializes a SceneAST instance into valid Godot 4 .tscn text.
    Strictly calculates load_steps, formats external & sub resources, orders nodes,
    and formats signal connections.
    """
    lines: List[str] = []

    ext_resources = getattr(scene, "ext_resources", [])
    sub_resources = getattr(scene, "sub_resources", [])
    nodes = getattr(scene, "nodes", [])
    connections = getattr(scene, "connections", [])
    format_version = getattr(scene, "format_version", 3)
    scene_path = getattr(scene, "path", "scenes/main.tscn")
    scene_uid = getattr(scene, "uid", None)

    # 1. Header Line
    total_res = len(ext_resources) + len(sub_resources)
    load_steps_part = f"load_steps={total_res + 1} " if total_res > 0 else ""
    uid_val = scene_uid or generate_godot_uid(scene_path)
    lines.append(f"[gd_scene {load_steps_part}format={format_version} uid=\"{uid_val}\"]")
    lines.append("")

    # 2. External Resources (Sorted by ID)
    if ext_resources:
        sorted_ext = sorted(ext_resources, key=lambda r: str(r.id))
        for ext in sorted_ext:
            ext_uid = getattr(ext, "uid", None) or generate_godot_uid(ext.path)
            lines.append(f'[ext_resource type="{ext.type}" uid="{ext_uid}" path="{ext.path}" id="{ext.id}"]')
        lines.append("")

    # 3. Sub-Resources (Sorted by ID)
    if sub_resources:
        sorted_sub = sorted(sub_resources, key=lambda s: str(s.id))
        for sub in sorted_sub:
            lines.append(f'[sub_resource type="{sub.type}" id="{sub.id}"]')
            for prop_key, prop_val in sorted(sub.properties.items()):
                lines.append(f"{prop_key} = {format_godot_value(prop_val)}")
            lines.append("")

    # 4. Node Tree (Topologically Ordered)
    ordered_nodes = sort_nodes_topologically(nodes)
    for node in ordered_nodes:
        tag_parts = [f'[node name="{node.name}"']
        if getattr(node, "type", None):
            tag_parts.append(f'type="{node.type}"')
        if not _is_root_node(node):
            tag_parts.append(f'parent="{node.parent}"')
        if getattr(node, "instance", None):
            tag_parts.append(f'instance=ExtResource("{node.instance}")')
        groups = getattr(node, "groups", [])
        if groups:
            groups_str = ", ".join(f'"{g}"' for g in sorted(groups))
            tag_parts.append(f'groups=[{groups_str}]')

        lines.append(" ".join(tag_parts) + "]")

        # Attached Script
        script_id = getattr(node, "script_id", None)
        if script_id:
            lines.append(f'script = ExtResource("{script_id}")')

        # Unique Name In Owner
        if getattr(node, "unique_name_in_owner", False):
            lines.append("unique_name_in_owner = true")

        # Sorted Properties
        for prop_name, prop_val in sorted(node.properties.items()):
            if prop_name == "script" and script_id:
                continue
            lines.append(f"{prop_name} = {format_godot_value(prop_val)}")

        lines.append("")

    # 5. Signal Connections
    if connections:
        def conn_key(c: Any) -> Tuple[str, str, str]:
            from_n = getattr(c, "from_node", getattr(c, "from", "."))
            to_n = getattr(c, "to_node", getattr(c, "to", "."))
            sig = getattr(c, "signal", "")
            return (str(from_n), str(sig), str(to_n))

        sorted_conns = sorted(connections, key=conn_key)
        for conn in sorted_conns:
            from_n = getattr(conn, "from_node", getattr(conn, "from", "."))
            to_n = getattr(conn, "to_node", getattr(conn, "to", "."))
            sig = getattr(conn, "signal", "")
            method = getattr(conn, "method", "")
            lines.append(f'[connection signal="{sig}" from="{from_n}" to="{to_n}" method="{method}"]')
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ============================================================================
# 3. Godot 4 .tscn Parser (Deserializer)
# ============================================================================

def parse_tscn(tscn_text: str, scene_path: str = "scenes/parsed_scene.tscn") -> SceneAST:
    """
    Parses a Godot 4 text .tscn file back into a SceneAST dataclass.
    Enables round-trip verification and automated scene parsing.
    """
    scene = SceneAST(name="ParsedScene", path=scene_path)
    lines = tscn_text.strip().split("\n")

    current_section: Optional[str] = None
    current_node: Optional[NodeAST] = None
    current_sub: Optional[SubResourceAST] = None

    tag_regex = re.compile(r"^\[([a-zA-Z_]+)\s*(.*)\]$")
    attr_regex = re.compile(r'([a-zA-Z0-9_/]+)=(?:ExtResource\("([^"]+)"\)|"([^"]*)"|([^ \]]+))')

    for line in lines:
        line_str = line.strip()
        if not line_str or line_str.startswith(";"):
            continue

        match = tag_regex.match(line_str)
        if match:
            tag_type = match.group(1)
            raw_attrs = match.group(2)
            attrs: Dict[str, str] = {}
            for am in attr_regex.finditer(raw_attrs):
                k = am.group(1)
                v = am.group(2) or am.group(3) or am.group(4)
                attrs[k] = v

            current_section = tag_type

            if tag_type == "gd_scene":
                if "format" in attrs:
                    scene.format_version = int(attrs["format"])
                if "uid" in attrs:
                    scene.uid = attrs["uid"]

            elif tag_type == "ext_resource":
                ext = ExtResourceAST(
                    id=attrs.get("id", ""),
                    type=attrs.get("type", "Resource"),
                    path=attrs.get("path", ""),
                    uid=attrs.get("uid"),
                )
                scene.ext_resources.append(ext)

            elif tag_type == "sub_resource":
                sub = SubResourceAST(
                    id=attrs.get("id", ""),
                    type=attrs.get("type", "Resource"),
                    properties={},
                )
                scene.sub_resources.append(sub)
                current_sub = sub
                current_node = None

            elif tag_type == "node":
                node = NodeAST(
                    name=attrs.get("name", "Node"),
                    type=attrs.get("type"),
                    parent=attrs.get("parent"),
                    instance=attrs.get("instance"),
                )
                scene.nodes.append(node)
                current_node = node
                current_sub = None
                if node.is_root and scene.name == "ParsedScene":
                    scene.name = node.name

            elif tag_type == "connection":
                conn = ConnectionAST(
                    signal=attrs.get("signal", ""),
                    from_node=attrs.get("from", "."),
                    to_node=attrs.get("to", "."),
                    method=attrs.get("method", ""),
                )
                scene.connections.append(conn)
                current_node = None
                current_sub = None

        elif "=" in line_str:
            key, raw_val = line_str.split("=", 1)
            key = key.strip()
            raw_val = raw_val.strip()

            parsed_val: Any = raw_val
            if raw_val == "true":
                parsed_val = True
            elif raw_val == "false":
                parsed_val = False
            elif raw_val == "null":
                parsed_val = None
            elif raw_val.startswith('"') and raw_val.endswith('"'):
                parsed_val = raw_val[1:-1]
            elif raw_val.startswith('ExtResource("') and raw_val.endswith('")'):
                parsed_val = ExtResourceRef(raw_val[13:-2])
            elif raw_val.startswith('SubResource("') and raw_val.endswith('")'):
                parsed_val = SubResourceRef(raw_val[13:-2])
            elif raw_val.startswith('&"') and raw_val.endswith('"'):
                parsed_val = StringNameRef(raw_val[2:-1])
            elif re.match(r"^-?\d+$", raw_val):
                parsed_val = int(raw_val)
            elif re.match(r"^-?\d+\.\d+$", raw_val):
                parsed_val = float(raw_val)

            if current_section == "node" and current_node:
                if key == "script" and isinstance(parsed_val, ExtResourceRef):
                    current_node.script_id = parsed_val.id
                elif key == "unique_name_in_owner":
                    current_node.unique_name_in_owner = bool(parsed_val)
                else:
                    current_node.properties[key] = parsed_val
            elif current_section == "sub_resource" and current_sub:
                current_sub.properties[key] = parsed_val

    return scene


# ============================================================================
# 4. Canonical project.godot Generator
# ============================================================================

@dataclass
class GodotProjectConfig:
    """Project-level configuration properties for project.godot."""
    project_name: str = "AIGameStudioProject"
    main_scene_path: str = "res://scenes/main_level.tscn"
    viewport_width: int = 1280
    viewport_height: int = 720
    pixel_art_mode: bool = True
    default_gravity: float = 980.0
    autoloads: Dict[str, str] = field(default_factory=dict)
    custom_inputs: Dict[str, List[int]] = field(default_factory=dict)
    icon_path: str = "res://assets/sprites/icon.svg"


def generate_project_godot(config: GodotProjectConfig) -> str:
    """
    Generates a production-ready Godot 4 project.godot configuration file.
    Includes viewport scaling, GL Compatibility rendering, pixel texture filtering,
    standard input bindings, and autoloads.
    """
    lines: List[str] = [
        "; Engine configuration file.",
        "; Generated autonomously by AI Game Studio (Godot 4.x).",
        "",
        "config_version=5",
        "",
        "[application]",
        "",
        f'config/name="{config.project_name}"',
        f'run/main_scene="{config.main_scene_path}"',
        'config/features=PackedStringArray("4.3", "GL Compatibility")',
        f'config/icon="{config.icon_path}"',
        "",
    ]

    # Autoloads
    if config.autoloads:
        lines.append("[autoload]")
        lines.append("")
        for name, path in sorted(config.autoloads.items()):
            prefix = "*" if not path.startswith("*") else ""
            lines.append(f'{name}="{prefix}{path}"')
        lines.append("")

    # Display / Viewport
    lines.extend([
        "[display]",
        "",
        f"window/size/viewport_width={config.viewport_width}",
        f"window/size/viewport_height={config.viewport_height}",
        "window/size/resizable=true",
        'window/stretch/mode="canvas_items"',
        'window/stretch/aspect="keep"',
        "",
    ])

    # Standard Input Actions
    lines.extend([
        "[input]",
        "",
        'ui_left={"deadzone": 0.5, "events": []}',
        'ui_right={"deadzone": 0.5, "events": []}',
        'ui_up={"deadzone": 0.5, "events": []}',
        'ui_down={"deadzone": 0.5, "events": []}',
        'jump={"deadzone": 0.5, "events": []}',
        'attack={"deadzone": 0.5, "events": []}',
        "",
    ])

    # Physics
    lines.extend([
        "[physics]",
        "",
        f"2d/default_gravity={config.default_gravity:.1f}",
        "",
    ])

    # Rendering (GL Compatibility for universal CI/headless execution)
    filter_val = 0 if config.pixel_art_mode else 1
    lines.extend([
        "[rendering]",
        "",
        'renderer/rendering_method="gl_compatibility"',
        'renderer/rendering_method.mobile="gl_compatibility"',
        f"textures/canvas_textures/default_texture_filter={filter_val}",
        "",
    ])

    return "\n".join(lines)


# ============================================================================
# 5. GodotAdapter Implementation
# ============================================================================

class GodotAdapter(BaseEngineAdapter):
    """Godot 4 export adapter generating project.godot, .tscn scenes, and .gd scripts."""

    @property
    def target(self) -> EngineTarget:
        return EngineTarget.GODOT4

    @property
    def display_name(self) -> str:
        return "Godot 4 (GL Compatibility)"

    @property
    def file_extensions(self) -> List[str]:
        return [".tscn", ".gd", ".godot"]

    def export_project(
        self,
        gdd: GameDesignDocument,
        manifest: AssetManifest,
        scenes: Union[List[SceneAST], Dict[str, SceneAST], List[Any], Dict[str, Any]],
        scripts: Union[List[ScriptSpec], Dict[str, ScriptSpec], List[Any], Dict[str, Any]],
        output_dir: Union[str, Path],
        **kwargs: Any,
    ) -> EngineProjectManifest:
        out_path = Path(output_dir).resolve()
        out_path.mkdir(parents=True, exist_ok=True)
        files_created: List[str] = []

        # 1. Canonical Folders
        for sub in ("assets/sprites", "assets/textures", "assets/backgrounds", "audio/sfx", "audio/music", "scenes", "scripts", "docs"):
            (out_path / sub).mkdir(parents=True, exist_ok=True)

        # 2. Materialize Assets
        asset_files = self.write_asset_files(manifest, out_path)
        files_created.extend(asset_files)

        # 3. Write Documentation
        doc_files = self.write_documentation(gdd, out_path)
        files_created.extend(doc_files)

        # 4. Serialize Scenes
        scene_list: List[Any] = list(scenes.values()) if isinstance(scenes, dict) else list(scenes)
        main_scene_path = "res://scenes/main_level.tscn"
        for idx, scn in enumerate(scene_list):
            raw_path = getattr(scn, "path", f"scenes/scene_{idx}.tscn")
            rel_scene = raw_path[6:] if raw_path.startswith("res://") else raw_path.lstrip("/")
            if not rel_scene.startswith("scenes/"):
                rel_scene = f"scenes/{rel_scene}"
            target_scene_file = out_path / rel_scene
            target_scene_file.parent.mkdir(parents=True, exist_ok=True)

            tscn_content = serialize_tscn(scn)
            target_scene_file.write_text(tscn_content, encoding="utf-8")
            files_created.append(rel_scene)
            if idx == 0:
                main_scene_path = f"res://{rel_scene}"

        # 5. Write Scripts
        script_list: List[Any] = list(scripts.values()) if isinstance(scripts, dict) else list(scripts)
        for sc in script_list:
            raw_rel = getattr(sc, "rel_path", "scripts/script.gd")
            rel_script = raw_rel.lstrip("/")
            if not rel_script.startswith("scripts/"):
                rel_script = f"scripts/{rel_script}"
            target_script_file = out_path / rel_script
            target_script_file.parent.mkdir(parents=True, exist_ok=True)
            target_script_file.write_text(getattr(sc, "code", ""), encoding="utf-8")
            files_created.append(rel_script)

        # 6. Generate project.godot
        cfg = GodotProjectConfig(
            project_name=gdd.title,
            main_scene_path=main_scene_path,
        )
        pg_content = generate_project_godot(cfg)
        (out_path / "project.godot").write_text(pg_content, encoding="utf-8")
        files_created.append("project.godot")

        # 7. Generate .gitignore
        gitignore_content = ".godot/\n*.translation\n*.import\n"
        (out_path / ".gitignore").write_text(gitignore_content, encoding="utf-8")
        files_created.append(".gitignore")

        return EngineProjectManifest(
            target=self.target,
            project_name=gdd.title,
            output_dir=str(out_path),
            entry_file="project.godot",
            files_created=files_created,
            assets_linked=len(asset_files),
            scenes_created=len(scene_list),
            scripts_created=len(script_list),
            readme_path="docs/GDD.md",
            run_command=self.get_run_command(out_path),
            metadata={"main_scene": main_scene_path},
        )

    def get_run_command(self, project_dir: Union[str, Path]) -> List[str]:
        return ["godot", "--path", str(project_dir)]
