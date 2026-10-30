"""
studio/engine/archetypes/base.py - Base Game Archetype Template & Registry.
Defines BaseArchetypeTemplate abstract class and central ArchetypeRegistry.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type, Union

from studio.core.models import (
    AssetManifest,
    AssetRequirement,
    GameDesignDocument,
    GameGenre,
    ScriptSpec,
)
from studio.engine.ast_models import SceneAST

logger = logging.getLogger("studio.engine.archetypes")


class BaseArchetypeTemplate(ABC):
    """
    Abstract blueprint for a game genre archetype.
    Generates canonical GDD specifications, SceneAST hierarchies,
    multi-language engine scripts, and asset requirements.
    """

    @property
    @abstractmethod
    def genre(self) -> GameGenre:
        """GameGenre enum corresponding to this archetype."""
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable archetype name."""
        pass

    @property
    def description(self) -> str:
        return ""

    @abstractmethod
    def get_gdd(self, prompt: str = "", seed: int = 42, target_engine: str = "godot4") -> GameDesignDocument:
        """Constructs a production-ready GameDesignDocument for this archetype."""
        pass

    @abstractmethod
    def get_scenes(
        self,
        gdd: Optional[GameDesignDocument] = None,
        manifest: Optional[AssetManifest] = None,
    ) -> List[SceneAST]:
        """Synthesizes all SceneAST scene graphs required by this archetype."""
        pass

    @abstractmethod
    def get_scripts(
        self,
        gdd: Optional[GameDesignDocument] = None,
        target_engine: str = "godot4",
    ) -> List[ScriptSpec]:
        """Synthesizes all engine scripts required by this archetype."""
        pass

    @abstractmethod
    def get_asset_requirements(self) -> List[AssetRequirement]:
        """Declares all audio and visual assets required by this archetype."""
        pass

    def get_input_actions(self) -> Dict[str, List[str]]:
        """Standard input bindings for this archetype."""
        return {
            "ui_left": ["Left", "A"],
            "ui_right": ["Right", "D"],
            "ui_up": ["Up", "W"],
            "ui_down": ["Down", "S"],
            "ui_accept": ["Space", "Enter"],
        }


class ArchetypeRegistry:
    """
    Central registry of archetype templates mapped to GameGenre values.
    Provides lookup with graceful fallback to default 2D Platformer.
    """

    _registry: Dict[str, BaseArchetypeTemplate] = {}

    @classmethod
    def register(cls, template: BaseArchetypeTemplate) -> None:
        key = template.genre.value.lower()
        cls._registry[key] = template
        logger.info(f"Registered archetype template '{template.display_name}' for genre '{key}'")

    @classmethod
    def get(cls, genre: Union[str, GameGenre, None]) -> BaseArchetypeTemplate:
        if not cls._registry:
            cls._initialize_defaults()

        if genre is None:
            return cls._registry[GameGenre.PLATFORMER_2D.value.lower()]

        raw = genre.value if isinstance(genre, GameGenre) else str(genre)
        norm = raw.strip().lower()

        alias_map = {
            "platformer": GameGenre.PLATFORMER_2D.value.lower(),
            "platformer_2d": GameGenre.PLATFORMER_2D.value.lower(),
            "2d_platformer": GameGenre.PLATFORMER_2D.value.lower(),
            "rpg": GameGenre.TOP_DOWN_RPG.value.lower(),
            "top_down_rpg": GameGenre.TOP_DOWN_RPG.value.lower(),
            "topdown_rpg": GameGenre.TOP_DOWN_RPG.value.lower(),
            "shooter": GameGenre.SPACE_SHOOTER.value.lower(),
            "space_shooter": GameGenre.SPACE_SHOOTER.value.lower(),
            "shmup": GameGenre.SPACE_SHOOTER.value.lower(),
            "puzzle": GameGenre.GRID_PUZZLE.value.lower(),
            "grid_puzzle": GameGenre.GRID_PUZZLE.value.lower(),
            "match3": GameGenre.GRID_PUZZLE.value.lower(),
        }

        lookup_key = alias_map.get(norm, norm)
        if lookup_key in cls._registry:
            return cls._registry[lookup_key]

        logger.warning(f"Unknown genre '{genre}', falling back to '{GameGenre.PLATFORMER_2D.value}'")
        return cls._registry[GameGenre.PLATFORMER_2D.value.lower()]

    @classmethod
    def list_all(cls) -> List[BaseArchetypeTemplate]:
        if not cls._registry:
            cls._initialize_defaults()
        return list(cls._registry.values())

    @classmethod
    def _initialize_defaults(cls) -> None:
        from studio.engine.archetypes.platformer import PlatformerTemplate
        from studio.engine.archetypes.top_down_rpg import TopDownRPGTemplate
        from studio.engine.archetypes.space_shooter import SpaceShooterTemplate
        from studio.engine.archetypes.grid_puzzle import GridPuzzleTemplate

        cls.register(PlatformerTemplate())
        cls.register(TopDownRPGTemplate())
        cls.register(SpaceShooterTemplate())
        cls.register(GridPuzzleTemplate())


def get_archetype(genre: Union[str, GameGenre, None]) -> BaseArchetypeTemplate:
    """Convenience getter for ArchetypeRegistry.get()."""
    return ArchetypeRegistry.get(genre)
