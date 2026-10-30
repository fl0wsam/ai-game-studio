"""
studio/engine/hierarchy.py - Clean Hierarchy Packager & Project Assembly.
Enforces standardized directory layouts, packages assets, scripts, scenes,
and documentation, sanitizes paths against traversal attacks, and verifies integrity.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union
from pydantic import BaseModel, ConfigDict, Field

from studio.core.models import (
    AssetManifest,
    GameDesignDocument,
    ScriptSpec,
)
from studio.engine.ast_models import SceneAST

logger = logging.getLogger("studio.engine.hierarchy")


class PackagedFileMetadata(BaseModel):
    """Metadata and integrity hash for a single packaged file."""
    model_config = ConfigDict(populate_by_name=True)

    rel_path: str
    category: str                     # "asset", "audio", "scene", "script", "doc", "config"
    byte_size: int
    checksum_sha256: str
    mime_type: str = "application/octet-stream"


class HierarchyManifest(BaseModel):
    """Cryptographic manifest of all files packaged in a game engine project."""
    model_config = ConfigDict(populate_by_name=True)

    project_name: str
    target_engine: str
    root_directory: str
    total_files: int
    total_bytes: int
    files: List[PackagedFileMetadata] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)


class HierarchyValidationReport(BaseModel):
    """Integrity report verifying canonical directory compliance and absence of corrupt files."""
    model_config = ConfigDict(populate_by_name=True)

    passed: bool
    missing_directories: List[str] = Field(default_factory=list)
    missing_files: List[str] = Field(default_factory=list)
    stray_root_files: List[str] = Field(default_factory=list)
    empty_files: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class CleanHierarchyPackager:
    """
    F-ENG-03: Clean Hierarchy Packager
    Enforces standardized directory layouts, packages assets, scripts, scenes,
    and documentation, sanitizes paths against traversal attacks, and verifies integrity.
    """

    CANONICAL_DIRECTORIES: List[str] = [
        "assets/sprites",
        "assets/textures",
        "assets/backgrounds",
        "assets/tilesets",
        "assets/ui",
        "audio/sfx",
        "audio/music",
        "scenes",
        "scripts",
        "docs",
    ]

    ALLOWED_ROOT_FILES: Set[str] = {
        "project.godot",
        ".gitignore",
        "README.md",
        "project_manifest.json",
        "main.py",
        "game.py",
        "requirements.txt",
        "index.html",
        "game.js",
        "style.css",
        "package.json",
    }

    def __init__(self, target_engine: str = "godot4", safe_mode: bool = True) -> None:
        self.target_engine = target_engine
        self.safe_mode = safe_mode

    def sanitize_path(self, base_dir: Union[str, Path], user_rel_path: str) -> Path:
        """
        Sanitizes relative paths to prevent directory traversal outside base_dir.
        Raises ValueError if traversal is detected.
        """
        clean_rel = user_rel_path.strip().replace("\\", "/")
        if clean_rel.startswith("res://"):
            clean_rel = clean_rel[6:]

        if clean_rel.startswith("/") or Path(clean_rel).is_absolute():
            raise ValueError(f"Absolute paths not allowed: '{user_rel_path}'")

        parts = Path(clean_rel).parts
        if ".." in parts:
            raise ValueError(f"Path traversal detected in path: '{user_rel_path}'")

        target = (Path(base_dir) / clean_rel).resolve()
        base_resolved = Path(base_dir).resolve()

        if not str(target).startswith(str(base_resolved)):
            raise ValueError(f"Path traversal outside root directory: '{user_rel_path}'")
        return target

    def create_hierarchy(
        self,
        output_dir: Union[str, Path],
        extra_subdirs: Optional[List[str]] = None,
    ) -> List[Path]:
        """Creates all canonical directories idempotently."""
        root = Path(output_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        created = [root]

        dirs_to_create = list(self.CANONICAL_DIRECTORIES)
        if extra_subdirs:
            dirs_to_create.extend(extra_subdirs)

        for rel in dirs_to_create:
            p = root / rel
            p.mkdir(parents=True, exist_ok=True)
            created.append(p)
        return created

    def package_assets(
        self,
        output_dir: Union[str, Path],
        manifest: AssetManifest,
        overwrite: bool = True,
    ) -> List[PackagedFileMetadata]:
        """Writes binary assets from manifest to disk."""
        meta_list: List[PackagedFileMetadata] = []
        root = Path(output_dir).resolve()

        for item in manifest.items:
            rel = item.rel_path
            if not any(rel.startswith(p) for p in ("assets/", "audio/")):
                atype_str = str(item.asset_type).lower()
                if "music" in atype_str or "bgm" in item.asset_id.lower():
                    rel = f"audio/music/{Path(rel).name}"
                elif "audio" in atype_str or "sfx" in atype_str:
                    rel = f"audio/sfx/{Path(rel).name}"
                elif "texture" in atype_str:
                    rel = f"assets/textures/{Path(rel).name}"
                elif "background" in atype_str or "bg" in item.asset_id.lower():
                    rel = f"assets/backgrounds/{Path(rel).name}"
                else:
                    rel = f"assets/sprites/{Path(rel).name}"

            dest = self.sanitize_path(root, rel)
            dest.parent.mkdir(parents=True, exist_ok=True)

            if dest.exists() and not overwrite:
                continue

            raw_bytes = item.file_bytes or b""
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_bytes(raw_bytes)
            tmp.replace(dest)

            checksum = hashlib.sha256(raw_bytes).hexdigest()
            cat = "audio" if rel.startswith("audio/") else "asset"
            meta_list.append(
                PackagedFileMetadata(
                    rel_path=str(dest.relative_to(root)).replace("\\", "/"),
                    category=cat,
                    byte_size=len(raw_bytes),
                    checksum_sha256=checksum,
                    mime_type="audio/wav" if rel.endswith(".wav") else "image/png",
                )
            )
        return meta_list

    def package_scripts(
        self,
        output_dir: Union[str, Path],
        scripts: Union[List[ScriptSpec], Dict[str, ScriptSpec], List[Any]],
        overwrite: bool = True,
    ) -> List[PackagedFileMetadata]:
        """Writes engine scripts to scripts/."""
        meta_list: List[PackagedFileMetadata] = []
        root = Path(output_dir).resolve()
        script_items = list(scripts.values()) if isinstance(scripts, dict) else list(scripts)

        for script in script_items:
            rel = getattr(script, "rel_path", "script.gd")
            if not rel.startswith("scripts/"):
                rel = f"scripts/{rel}"
            dest = self.sanitize_path(root, rel)
            dest.parent.mkdir(parents=True, exist_ok=True)

            if dest.exists() and not overwrite:
                continue

            content = getattr(script, "code", "").encode("utf-8")
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_bytes(content)
            tmp.replace(dest)

            meta_list.append(
                PackagedFileMetadata(
                    rel_path=str(dest.relative_to(root)).replace("\\", "/"),
                    category="script",
                    byte_size=len(content),
                    checksum_sha256=hashlib.sha256(content).hexdigest(),
                    mime_type="text/x-gdscript" if rel.endswith(".gd") else "text/x-python",
                )
            )
        return meta_list

    def package_scenes(
        self,
        output_dir: Union[str, Path],
        scenes: Union[List[SceneAST], Dict[str, SceneAST], List[Any]],
        serializer: Optional[Callable[[Any], str]] = None,
        overwrite: bool = True,
    ) -> List[PackagedFileMetadata]:
        """Serializes and writes scene files to scenes/."""
        meta_list: List[PackagedFileMetadata] = []
        root = Path(output_dir).resolve()
        scene_items = list(scenes.values()) if isinstance(scenes, dict) else list(scenes)

        for scn in scene_items:
            raw_path = getattr(scn, "path", "scenes/main.tscn")
            rel = raw_path[6:] if raw_path.startswith("res://") else raw_path.lstrip("/")
            if not rel.startswith("scenes/"):
                rel = f"scenes/{rel}"

            dest = self.sanitize_path(root, rel)
            dest.parent.mkdir(parents=True, exist_ok=True)

            if dest.exists() and not overwrite:
                continue

            if serializer:
                text = serializer(scn)
            elif hasattr(scn, "to_tscn"):
                text = scn.to_tscn()
            elif isinstance(scn, str):
                text = scn
            else:
                from studio.engine.adapters.godot_adapter import serialize_tscn
                text = serialize_tscn(scn)

            content = text.encode("utf-8")
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_bytes(content)
            tmp.replace(dest)

            meta_list.append(
                PackagedFileMetadata(
                    rel_path=str(dest.relative_to(root)).replace("\\", "/"),
                    category="scene",
                    byte_size=len(content),
                    checksum_sha256=hashlib.sha256(content).hexdigest(),
                    mime_type="text/plain",
                )
            )
        return meta_list

    def package_docs(
        self,
        output_dir: Union[str, Path],
        gdd: GameDesignDocument,
    ) -> List[PackagedFileMetadata]:
        """Generates GDD.md, LORE.md, and CONTROLS.md in docs/."""
        meta_list: List[PackagedFileMetadata] = []
        root = Path(output_dir).resolve()
        docs_dir = root / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)

        # 1. GDD.md
        gdd_content = self._render_gdd_markdown(gdd)
        gdd_path = docs_dir / "GDD.md"
        gdd_path.write_text(gdd_content, encoding="utf-8")
        meta_list.append(self._make_meta(gdd_path, root, "doc"))

        # 2. LORE.md
        lore_content = self._render_lore_markdown(gdd)
        lore_path = docs_dir / "LORE.md"
        lore_path.write_text(lore_content, encoding="utf-8")
        meta_list.append(self._make_meta(lore_path, root, "doc"))

        # 3. CONTROLS.md
        controls_content = self._render_controls_markdown(gdd)
        controls_path = docs_dir / "CONTROLS.md"
        controls_path.write_text(controls_content, encoding="utf-8")
        meta_list.append(self._make_meta(controls_path, root, "doc"))

        return meta_list

    def generate_manifest(
        self,
        output_dir: Union[str, Path],
        project_name: str = "GameProject",
    ) -> HierarchyManifest:
        """Scans the output directory and generates project_manifest.json."""
        root = Path(output_dir).resolve()
        files_meta: List[PackagedFileMetadata] = []
        total_bytes = 0

        for path in root.rglob("*"):
            if path.is_file() and not any(part.startswith(".") for part in path.parts if part != ".gitignore"):
                if path.name == "project_manifest.json":
                    continue
                content = path.read_bytes()
                size = len(content)
                total_bytes += size
                rel = str(path.relative_to(root)).replace("\\", "/")

                cat = "asset"
                if rel.startswith("audio/"):
                    cat = "audio"
                elif rel.startswith("scenes/"):
                    cat = "scene"
                elif rel.startswith("scripts/"):
                    cat = "script"
                elif rel.startswith("docs/"):
                    cat = "doc"
                elif rel in ("project.godot", "requirements.txt", "package.json"):
                    cat = "config"

                files_meta.append(
                    PackagedFileMetadata(
                        rel_path=rel,
                        category=cat,
                        byte_size=size,
                        checksum_sha256=hashlib.sha256(content).hexdigest(),
                    )
                )

        manifest = HierarchyManifest(
            project_name=project_name,
            target_engine=self.target_engine,
            root_directory=str(root),
            total_files=len(files_meta),
            total_bytes=total_bytes,
            files=files_meta,
        )

        manifest_path = root / "project_manifest.json"
        manifest_path.write_text(json.dumps(manifest.model_dump(), indent=2), encoding="utf-8")
        return manifest

    def verify_hierarchy(self, output_dir: Union[str, Path]) -> HierarchyValidationReport:
        """Validates that output_dir conforms to clean hierarchy standards."""
        root = Path(output_dir).resolve()
        missing_dirs = []
        for d in self.CANONICAL_DIRECTORIES:
            if not (root / d).is_dir():
                missing_dirs.append(d)

        empty_files = []
        stray_root_files = []
        for item in root.iterdir():
            if item.is_file():
                if item.name not in self.ALLOWED_ROOT_FILES:
                    stray_root_files.append(item.name)
                if item.stat().st_size == 0:
                    empty_files.append(str(item.relative_to(root)))

        # Also check empty files in subdirs
        for path in root.rglob("*"):
            if path.is_file() and path.stat().st_size == 0:
                rel = str(path.relative_to(root)).replace("\\", "/")
                if rel not in empty_files:
                    empty_files.append(rel)

        passed = len(missing_dirs) == 0 and len(empty_files) == 0
        return HierarchyValidationReport(
            passed=passed,
            missing_directories=missing_dirs,
            stray_root_files=stray_root_files,
            empty_files=empty_files,
            errors=[f"Missing directory: {d}" for d in missing_dirs] + [f"Empty file: {f}" for f in empty_files],
            warnings=[f"Unrecognized root file: {f}" for f in stray_root_files],
        )

    def _make_meta(self, path: Path, root: Path, cat: str) -> PackagedFileMetadata:
        content = path.read_bytes()
        return PackagedFileMetadata(
            rel_path=str(path.relative_to(root)).replace("\\", "/"),
            category=cat,
            byte_size=len(content),
            checksum_sha256=hashlib.sha256(content).hexdigest(),
            mime_type="text/markdown",
        )

    def _render_gdd_markdown(self, gdd: GameDesignDocument) -> str:
        lines = [
            f"# {gdd.title} — Game Design Document",
            "",
            f"**Genre**: {gdd.genre}",
            f"**Target Engine**: {self.target_engine}",
            f"**Version**: {gdd.version}",
            "",
            "## High Concept",
            gdd.high_concept or "N/A",
            "",
            "## Core Mechanics",
        ]
        for m in gdd.gameplay_mechanics:
            lines.append(f"- {m}")
        lines.extend([
            "",
            "## Art Style Guide",
            f"```json\n{json.dumps(gdd.art_style_guide, indent=2)}\n```",
            "",
            "## Audio Style Guide",
            f"```json\n{json.dumps(gdd.audio_style_guide, indent=2)}\n```",
            "",
        ])
        return "\n".join(lines)

    def _render_lore_markdown(self, gdd: GameDesignDocument) -> str:
        lines = [
            f"# {gdd.title} — Lore & World-Building",
            "",
            "## Narrative Overview",
            gdd.narrative_lore or "No additional lore provided.",
            "",
            "## Quests & Objectives",
        ]
        if gdd.quests:
            for q in gdd.quests:
                title = getattr(q, "title", "Quest")
                desc = getattr(q, "description", "")
                lines.append(f"### {title}")
                lines.append(desc)
                lines.append("")
        else:
            lines.append("No explicit quests defined.\n")

        lines.append("## Dialogue & Characters\n")
        if gdd.dialogue_trees:
            for d in gdd.dialogue_trees:
                speaker = getattr(d, "speaker", "NPC")
                text = getattr(d, "text", "")
                lines.append(f"- **{speaker}**: \"{text}\"")
        else:
            lines.append("No branching dialogue trees registered.\n")

        return "\n".join(lines)

    def _render_controls_markdown(self, gdd: GameDesignDocument) -> str:
        return (
            f"# Controls Guide: {gdd.title}\n\n"
            "| Action | Primary Key | Secondary Key |\n"
            "|---|---|---|\n"
            "| Move Left | Left Arrow | A |\n"
            "| Move Right | Right Arrow | D |\n"
            "| Move Up / Jump | Up Arrow | W / Space |\n"
            "| Move Down | Down Arrow | S |\n"
            "| Attack / Fire / Interact | Space / J | E / Enter |\n"
            "| Pause / Menu | Escape | P |\n"
        )
