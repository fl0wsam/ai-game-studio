"""
studio/engine/archetypes - Game Archetype Presets & Template Generators.
"""
from studio.engine.archetypes.base import (
    ArchetypeRegistry,
    BaseArchetypeTemplate,
    get_archetype,
)
from studio.engine.archetypes.grid_puzzle import GridPuzzleTemplate
from studio.engine.archetypes.platformer import PlatformerTemplate
from studio.engine.archetypes.space_shooter import SpaceShooterTemplate
from studio.engine.archetypes.top_down_rpg import TopDownRPGTemplate

__all__ = [
    "BaseArchetypeTemplate",
    "ArchetypeRegistry",
    "get_archetype",
    "PlatformerTemplate",
    "TopDownRPGTemplate",
    "SpaceShooterTemplate",
    "GridPuzzleTemplate",
]
