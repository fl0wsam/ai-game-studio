"""
studio/engine/adapters/base.py - Base Engine Adapter & Project Manifest Models.
Defines EngineProjectManifest and the BaseEngineAdapter abstract base class.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field

from studio.core.models import (
    AssetManifest,
    EngineTarget,
    GameDesignDocument,
    GameGenre,
    ScriptSpec,
)
from studio.engine.ast_models import SceneAST


class EngineProjectManifest(BaseModel):
    """
    Standardized manifest returned upon exporting a game project to disk.
    Fully describes the output location, entry file, linked assets, and runtime instructions.
    """
    model_config = ConfigDict(populate_by_name=True)

    target: EngineTarget
    project_name: str
    output_dir: str
    entry_file: str                       # e.g. "main.py", "index.html", "project.godot"
    files_created: List[str] = Field(default_factory=list)
    assets_linked: int = 0
    scenes_created: int = 0
    scripts_created: int = 0
    readme_path: Optional[str] = None
    run_command: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    exported_at: float = Field(default_factory=time.time)


# Alias for backwards and cross-spec compatibility
EngineExportResult = EngineProjectManifest


class BaseEngineAdapter(ABC):
    """
    Abstract interface that all engine export adapters must implement.
    Guarantees uniform export lifecycle, asset staging, documentation generation,
    and prerequisite reporting.
    """

    @property
    @abstractmethod
    def target(self) -> EngineTarget:
        """The canonical EngineTarget enum for this adapter."""
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable engine title (e.g. 'Godot 4 (GL Compatibility)')."""
        pass

    @property
    def file_extensions(self) -> List[str]:
        """List of primary code/scene file extensions produced by this adapter."""
        return []

    @property
    def supported_archetypes(self) -> List[str]:
        """Game archetypes supported by this adapter."""
        return [
            GameGenre.PLATFORMER_2D.value,
            GameGenre.TOP_DOWN_RPG.value,
            GameGenre.SPACE_SHOOTER.value,
            GameGenre.GRID_PUZZLE.value,
        ]

    @abstractmethod
    def export_project(
        self,
        gdd: GameDesignDocument,
        manifest: AssetManifest,
        scenes: Union[List[SceneAST], Dict[str, SceneAST], List[Any], Dict[str, Any]],
        scripts: Union[List[ScriptSpec], Dict[str, ScriptSpec], List[Any], Dict[str, Any]],
        output_dir: Union[str, Path],
        **kwargs: Any,
    ) -> EngineProjectManifest:
        """
        Exports a complete, structured game engine project to disk.
        """
        pass

    def validate_prerequisites(self) -> Dict[str, Any]:
        """
        Validates whether host environment meets runtime/build prerequisites.
        Returns a dict: {'available': bool, 'details': str, 'install_hint': str}
        """
        return {"available": True, "details": "Ready for execution", "install_hint": ""}

    def get_run_command(self, project_dir: Union[str, Path]) -> List[str]:
        """Returns the CLI command vector to launch the exported game."""
        return []

    # ------------------------------------------------------------------------
    # Shared Helper Utilities
    # ------------------------------------------------------------------------

    def write_asset_files(
        self,
        manifest: AssetManifest,
        output_dir: Path,
        asset_subpath: str = "assets",
        audio_subpath: str = "audio",
    ) -> List[str]:
        """
        Materializes in-memory asset bytes from AssetManifest onto disk.
        Safely handles sprites, textures, backgrounds, SFX, and music.
        """
        created: List[str] = []
        for item in manifest.items:
            rel = item.rel_path.lstrip("/")
            target_path = output_dir / rel
            target_path.parent.mkdir(parents=True, exist_ok=True)

            if item.file_bytes:
                target_path.write_bytes(item.file_bytes)
            else:
                target_path.write_text(f"# Placeholder for asset {item.asset_id}\n", encoding="utf-8")
            created.append(rel)
        return created

    def write_documentation(self, gdd: GameDesignDocument, output_dir: Path) -> List[str]:
        """Writes standardized docs/GDD.md and docs/CONTROLS.md."""
        docs_dir = output_dir / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        created: List[str] = []

        gdd_content = (
            f"# {gdd.title} — Game Design Document\n\n"
            f"**Genre**: {gdd.genre}\n"
            f"**Target Engine**: {self.display_name}\n"
            f"**High Concept**: {gdd.high_concept}\n\n"
            "## Core Mechanics\n"
            + "\n".join(f"- {m}" for m in gdd.gameplay_mechanics)
            + "\n\n## World Lore & Narrative\n"
            + f"{gdd.narrative_lore}\n"
        )
        (docs_dir / "GDD.md").write_text(gdd_content, encoding="utf-8")
        created.append("docs/GDD.md")

        controls_content = (
            f"# Controls Guide: {gdd.title}\n\n"
            "| Action | Primary Key | Secondary Key |\n"
            "|---|---|---|\n"
            "| Move Left | Left Arrow | A |\n"
            "| Move Right | Right Arrow | D |\n"
            "| Jump / Action | Space | W / Up Arrow |\n"
            "| Attack / Fire | J / Left Click | Enter |\n"
            "| Restart | R | Tap Screen |\n"
        )
        (docs_dir / "CONTROLS.md").write_text(controls_content, encoding="utf-8")
        created.append("docs/CONTROLS.md")

        return created
