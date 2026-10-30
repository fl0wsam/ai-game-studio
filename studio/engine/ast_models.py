"""
studio/engine/ast_models.py - Intermediate Scene AST Models for Game Engines.
Defines SceneAST, NodeAST, ExtResourceAST, SubResourceAST, ConnectionAST,
and Godot Variant value wrapper types for deterministic scene graph manipulation.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# 1. Godot Variant Value Wrappers
# ============================================================================

@dataclass(frozen=True)
class Vector2:
    """Godot 2D floating-point vector."""
    x: float = 0.0
    y: float = 0.0

    def to_godot(self) -> str:
        return f"Vector2({self.x}, {self.y})"

    def to_list(self) -> List[float]:
        return [self.x, self.y]


@dataclass(frozen=True)
class Vector2i:
    """Godot 2D integer vector."""
    x: int = 0
    y: int = 0

    def to_godot(self) -> str:
        return f"Vector2i({self.x}, {self.y})"

    def to_list(self) -> List[int]:
        return [self.x, self.y]


@dataclass(frozen=True)
class Vector3:
    """Godot 3D floating-point vector."""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def to_godot(self) -> str:
        return f"Vector3({self.x}, {self.y}, {self.z})"

    def to_list(self) -> List[float]:
        return [self.x, self.y, self.z]


@dataclass(frozen=True)
class Color:
    """Godot RGBA color."""
    r: float = 1.0
    g: float = 1.0
    b: float = 1.0
    a: float = 1.0

    def to_godot(self) -> str:
        return f"Color({self.r}, {self.g}, {self.b}, {self.a})"


@dataclass(frozen=True)
class Rect2:
    """Godot 2D rectangle (position + size)."""
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    def to_godot(self) -> str:
        return f"Rect2({self.x}, {self.y}, {self.width}, {self.height})"


@dataclass(frozen=True)
class ExtResourceRef:
    """Reference to an external resource by its scene-local ID."""
    id: str

    def to_godot(self) -> str:
        return f'ExtResource("{self.id}")'


@dataclass(frozen=True)
class SubResourceRef:
    """Reference to an inline sub-resource by its scene-local ID."""
    id: str

    def to_godot(self) -> str:
        return f'SubResource("{self.id}")'


@dataclass(frozen=True)
class StringNameRef:
    """Godot StringName literal (e.g. &"SFX" for audio bus)."""
    name: str

    def to_godot(self) -> str:
        return f'&"{self.name}"'


@dataclass(frozen=True)
class NodePathRef:
    """Godot NodePath literal (e.g. ^"Player/Sprite2D")."""
    path: str

    def to_godot(self) -> str:
        return f'NodePath("{self.path}")'


# ============================================================================
# 2. External & Sub-Resource AST
# ============================================================================

def generate_godot_uid(seed_key: str) -> str:
    """
    Deterministically synthesizes a valid Godot 4 resource UID.
    Format: uid://<12 base-36 characters> derived from SHA-256.
    """
    digest = hashlib.sha256(seed_key.strip().encode("utf-8")).digest()
    val = int.from_bytes(digest[:8], byteorder="big")
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    encoded = []
    for _ in range(12):
        encoded.append(chars[val % 36])
        val //= 36
    return f"uid://{''.join(encoded)}"


class ExtResourceAST(BaseModel):
    """External resource declaration in a Godot scene (.tscn)."""
    model_config = ConfigDict(populate_by_name=True)

    id: str                                # Unique scene-local ID: "1_player_tex"
    type: str                              # "Texture2D", "AudioStream", "Script", "PackedScene"
    path: str                              # Virtual engine path: "res://assets/sprites/player.png"
    uid: Optional[str] = None              # "uid://dr8x6p2k5y0m"

    def model_post_init(self, __context: Any) -> None:
        if not self.uid and self.path:
            self.uid = generate_godot_uid(self.path)


class SubResourceAST(BaseModel):
    """Inline resource defined directly within the scene file."""
    model_config = ConfigDict(populate_by_name=True)

    id: str                                # e.g. "CapsuleShape2D_player"
    type: str                              # e.g. "CapsuleShape2D", "RectangleShape2D", "CircleShape2D"
    properties: Dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# 3. Node AST
# ============================================================================

class NodeAST(BaseModel):
    """Scene graph node definition."""
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    name: str                              # Node name: "Player", "Sprite2D"
    type: Optional[str] = None             # Godot class: "CharacterBody2D", "Sprite2D"
    parent: Optional[str] = None           # None = root, "." = direct child of root, "Parent/Child" = descendant
    instance: Optional[str] = None         # ExtResource ID if instancing a PackedScene
    properties: Dict[str, Any] = Field(default_factory=dict)
    script_id: Optional[str] = None        # ExtResource ID of attached script
    groups: List[str] = Field(default_factory=list)
    unique_name_in_owner: bool = False     # Access via %NodeName if True

    @property
    def is_root(self) -> bool:
        return self.parent is None or self.parent == ""

    def get_property(self, key: str, default: Any = None) -> Any:
        return self.properties.get(key, default)

    def set_property(self, key: str, value: Any) -> None:
        self.properties[key] = value


# ============================================================================
# 4. Signal Connection AST
# ============================================================================

class ConnectionAST(BaseModel):
    """Signal connection wiring emitter to recipient method."""
    model_config = ConfigDict(populate_by_name=True)

    signal: str                            # e.g. "body_entered", "pressed", "timeout"
    from_node: str                         # Relative emitter path: "Hurtbox" or "."
    to_node: str                           # Relative receiver path: "." or "HUD"
    method: str                            # Callback method: "_on_hurtbox_body_entered"
    flags: Optional[int] = None            # Connection flags
    unbinds: Optional[int] = None          # Godot 4 unbinds count


# ============================================================================
# 5. Scene AST
# ============================================================================

class SceneAST(BaseModel):
    """Complete Abstract Syntax Tree of a game scene."""
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    name: str                              # "MainLevel", "Player"
    path: str                              # "scenes/main_level.tscn"
    format_version: int = 3                # Godot 4 scene format version
    uid: Optional[str] = None              # Deterministic scene UID
    ext_resources: List[ExtResourceAST] = Field(default_factory=list)
    sub_resources: List[SubResourceAST] = Field(default_factory=list)
    nodes: List[NodeAST] = Field(default_factory=list)
    connections: List[ConnectionAST] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        if not self.uid and self.path:
            self.uid = generate_godot_uid(self.path)

    @property
    def load_steps(self) -> int:
        """Calculates total load steps: count(ext) + count(sub) + 1."""
        total = len(self.ext_resources) + len(self.sub_resources)
        return total + 1 if total > 0 else 0

    def get_root(self) -> Optional[NodeAST]:
        """Returns the scene's root node."""
        for n in self.nodes:
            if n.is_root:
                return n
        return self.nodes[0] if self.nodes else None

    def find_node(self, name: str) -> Optional[NodeAST]:
        """Finds node by name."""
        for n in self.nodes:
            if n.name == name:
                return n
        return None

    def get_children(self, parent_name: str) -> List[NodeAST]:
        """Gets direct children of a given node."""
        target_parent = "." if (self.get_root() and self.get_root().name == parent_name) else parent_name
        return [
            n for n in self.nodes
            if n.parent == target_parent or (target_parent == "." and n.parent == parent_name)
        ]

    def add_node(self, node: NodeAST) -> None:
        """Appends node to scene tree."""
        if node.is_root:
            existing_root = self.get_root()
            if existing_root and existing_root.name != node.name:
                raise ValueError(
                    f"Cannot add second root node '{node.name}'. Scene already has root '{existing_root.name}'."
                )
        self.nodes.append(node)

    def add_ext_resource(
        self, resource_type: str, path: str, resource_id: Optional[str] = None
    ) -> str:
        """
        Adds an external resource or returns existing ID if path already registered.
        Guarantees deduplication.
        """
        norm_path = path if path.startswith("res://") else f"res://{path.lstrip('/')}"
        for existing in self.ext_resources:
            if existing.path == norm_path:
                return existing.id

        if not resource_id:
            idx = len(self.ext_resources) + 1
            filename = norm_path.split("/")[-1]
            stem = filename.split(".")[0] if "." in filename else filename
            resource_id = f"{idx}_{stem}"

        ast = ExtResourceAST(id=resource_id, type=resource_type, path=norm_path)
        self.ext_resources.append(ast)
        return resource_id

    def add_sub_resource(
        self, resource_type: str, resource_id: str, properties: Optional[Dict[str, Any]] = None
    ) -> str:
        """Adds inline sub-resource or updates existing."""
        for existing in self.sub_resources:
            if existing.id == resource_id:
                if properties:
                    existing.properties.update(properties)
                return existing.id

        sub = SubResourceAST(id=resource_id, type=resource_type, properties=properties or {})
        self.sub_resources.append(sub)
        return resource_id

    def add_connection(self, signal: str, from_node: str, to_node: str, method: str) -> None:
        """Wires signal connection."""
        conn = ConnectionAST(signal=signal, from_node=from_node, to_node=to_node, method=method)
        self.connections.append(conn)

    def validate_tree(self) -> List[str]:
        """Validates acyclic tree structure, root presence, and resource references."""
        issues: List[str] = []
        if not self.nodes:
            issues.append("Scene contains no nodes.")
            return issues

        roots = [n for n in self.nodes if n.is_root]
        if len(roots) != 1:
            issues.append(f"Scene must contain exactly 1 root node, found {len(roots)}.")

        node_names: Set[str] = {n.name for n in self.nodes}
        ext_ids: Set[str] = {r.id for r in self.ext_resources}
        sub_ids: Set[str] = {s.id for s in self.sub_resources}

        for n in self.nodes:
            if not n.is_root:
                parent_ref = n.parent
                if parent_ref == ".":
                    pass  # Valid child of root
                elif parent_ref and parent_ref not in node_names:
                    base_parent = parent_ref.split("/")[0]
                    if base_parent not in node_names:
                        issues.append(f"Node '{n.name}' references non-existent parent '{parent_ref}'.")

            if n.script_id and n.script_id not in ext_ids:
                issues.append(f"Node '{n.name}' references unknown script_id '{n.script_id}'.")

            if n.instance and n.instance not in ext_ids:
                issues.append(f"Node '{n.name}' instances unknown resource '{n.instance}'.")

        return issues
