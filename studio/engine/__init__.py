"""
studio/engine - Modular Game Engine Project Pipeline.
Provides SceneAST representation, Dynamic Asset Linker, Clean Hierarchy Packager,
EngineHub, Archetype Templates, and Multi-Engine Export Adapters.
"""
from studio.engine.adapters.base import (
    BaseEngineAdapter,
    EngineExportResult,
    EngineProjectManifest,
)
from studio.engine.adapters.godot_adapter import (
    GodotAdapter,
    GodotProjectConfig,
    generate_project_godot,
    parse_tscn,
    serialize_tscn,
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

__all__ = [
    # AST
    "Vector2",
    "Vector2i",
    "Vector3",
    "Color",
    "Rect2",
    "ExtResourceRef",
    "SubResourceRef",
    "StringNameRef",
    "NodePathRef",
    "generate_godot_uid",
    "ExtResourceAST",
    "SubResourceAST",
    "NodeAST",
    "ConnectionAST",
    "SceneAST",
    # Linker
    "DynamicAssetLinker",
    "AssetBinding",
    # Hierarchy
    "CleanHierarchyPackager",
    "HierarchyManifest",
    "HierarchyValidationReport",
    "PackagedFileMetadata",
    # Hub
    "EngineHub",
    "get_engine_hub",
    # Adapters
    "BaseEngineAdapter",
    "EngineProjectManifest",
    "EngineExportResult",
    "GodotAdapter",
    "PygameAdapter",
    "WebAdapter",
    "UnityAdapter",
    "serialize_tscn",
    "parse_tscn",
    "generate_project_godot",
    "GodotProjectConfig",
    # Archetypes
    "BaseArchetypeTemplate",
    "ArchetypeRegistry",
    "get_archetype",
    "PlatformerTemplate",
    "TopDownRPGTemplate",
    "SpaceShooterTemplate",
    "GridPuzzleTemplate",
]
