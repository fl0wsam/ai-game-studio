"""
studio/verification/asset_verifier.py - Asset Integrity & Binary Format Header Verifier.
Implements F-VERIF-03: Discovers asset references from scenes, configs, manifests, and scripts;
verifies physical presence and non-zero sizes; checks binary magic byte signatures (PNG, JPEG,
WAV, OGG, MP3, SVG, JSON); flags path traversals and orphaned assets.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from studio.core.models import (
    AssetItem,
    AssetManifest,
    DiagnosticError,
    GameDesignDocument,
)


# ============================================================================
# Asset Reference & Verification Data Models
# ============================================================================

@dataclass
class DiscoveredAssetRef:
    """A discovered asset reference in a project source file."""
    target_path: str               # Raw reference: "res://assets/sprites/player.png"
    resolved_rel_path: str         # Normalized relative disk path: "assets/sprites/player.png"
    referring_file: str            # File containing reference: "scenes/main_level.tscn"
    line_number: Optional[int] = None  # 1-indexed line number
    resource_id: Optional[str] = None  # ExtResource id if applicable
    expected_type: Optional[str] = None  # Texture2D, AudioStream, etc.
    context_snippet: Optional[str] = None


@dataclass
class AssetVerificationResult:
    """Summary of asset verification across project."""
    passed: bool
    total_references_scanned: int
    unique_assets_referenced: int
    files_on_disk_scanned: int
    errors: List[DiagnosticError] = field(default_factory=list)
    warnings: List[DiagnosticError] = field(default_factory=list)
    orphaned_files: List[str] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    corrupted_files: List[str] = field(default_factory=list)


# ============================================================================
# Magic Byte & Header Validators
# ============================================================================

def validate_magic_bytes(data: bytes, extension: str) -> Optional[str]:
    """
    Validates binary header signatures for standard game engine asset formats.
    Returns None if valid, or a descriptive error message if invalid.
    """
    ext = extension.lower().lstrip(".")

    if ext == "png":
        if len(data) < 24:
            return "PNG file is truncated (less than 24 bytes)."
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "Missing PNG magic header signature (\\x89PNG\\r\\n\\x1a\\n)."
        if data[12:16] != b"IHDR":
            return "PNG missing IHDR header chunk at offset 12."
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        if width <= 0 or height <= 0:
            return f"Invalid PNG dimensions: {width}x{height}."
        return None

    elif ext in ("jpg", "jpeg"):
        if len(data) < 4:
            return "JPEG file is truncated."
        if not data.startswith(b"\xff\xd8\xff"):
            return "Missing JPEG SOI magic signature (\\xff\\xd8\\xff)."
        return None

    elif ext == "wav":
        if len(data) < 12:
            return "WAV file is truncated (less than 12 bytes)."
        if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            return "Missing WAV RIFF/WAVE container signature."
        return None

    elif ext == "ogg":
        if len(data) < 4 or data[:4] != b"OggS":
            return "Missing OGG 'OggS' container signature."
        return None

    elif ext == "mp3":
        if len(data) < 4:
            return "MP3 file is truncated."
        is_id3 = data.startswith(b"ID3")
        is_sync = (data[0] == 0xFF) and ((data[1] & 0xE0) == 0xE0)
        if not (is_id3 or is_sync):
            return "Missing MP3 ID3 header or MPEG sync frame."
        return None

    elif ext == "svg":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return "SVG is not valid UTF-8 text."
        lower = text.lower()
        if "<svg" not in lower or "</svg>" not in lower:
            return "SVG missing <svg> or </svg> XML tags."
        return None

    elif ext == "json":
        try:
            text = data.decode("utf-8")
            json.loads(text)
        except UnicodeDecodeError:
            return "JSON file is not valid UTF-8 text."
        except json.JSONDecodeError as ex:
            return f"Malformed JSON content: {ex}"
        return None

    return None


# ============================================================================
# Asset Verifier Implementation
# ============================================================================

class AssetVerifier:
    """
    Validates physical presence, non-zero size, format headers, and broken links
    for game assets referenced across scenes, scripts, manifests, and configs.
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        manifest: Optional[AssetManifest] = None,
        gdd: Optional[GameDesignDocument] = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.manifest = manifest
        self.gdd = gdd

    # ------------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------------

    def discover_references(self) -> List[DiscoveredAssetRef]:
        """Scans project directory and manifests for all asset references."""
        refs: List[DiscoveredAssetRef] = []
        if not self.project_dir.is_dir():
            return refs

        # 1. Discover references in .tscn scene files
        for p in sorted(self.project_dir.rglob("*.tscn")):
            if p.is_file():
                refs.extend(self._discover_from_tscn(p))

        # 2. Discover references in project.godot
        godot_cfg = self.project_dir / "project.godot"
        if godot_cfg.is_file():
            refs.extend(self._discover_from_godot_config(godot_cfg))

        # 3. Discover references in scripts (.gd, .py, .js, .cs)
        for ext in ("*.gd", "*.py", "*.js", "*.cs"):
            for p in sorted(self.project_dir.rglob(ext)):
                if p.is_file():
                    refs.extend(self._discover_from_script(p))

        # 4. Discover references in project_manifest.json on disk
        manifest_file = self.project_dir / "project_manifest.json"
        if manifest_file.is_file():
            refs.extend(self._discover_from_manifest_file(manifest_file))

        # 5. In-memory AssetManifest
        if self.manifest:
            for item in self.manifest.items:
                r_path = item.rel_path or item.engine_path.replace("res://", "").lstrip("/")
                refs.append(
                    DiscoveredAssetRef(
                        target_path=item.engine_path,
                        resolved_rel_path=r_path,
                        referring_file="AssetManifest",
                        line_number=None,
                        resource_id=item.asset_id,
                        expected_type=item.asset_type.value if hasattr(item.asset_type, "value") else str(item.asset_type),
                    )
                )

        # 6. In-memory GDD
        if self.gdd:
            for req in self.gdd.required_assets:
                r_path = req.get_rel_path()
                refs.append(
                    DiscoveredAssetRef(
                        target_path=f"res://{r_path}",
                        resolved_rel_path=r_path,
                        referring_file="GameDesignDocument",
                        line_number=None,
                        resource_id=req.asset_id,
                        expected_type=str(req.asset_type),
                    )
                )

        return refs

    def verify_all(self, fail_on_orphan: bool = False) -> AssetVerificationResult:
        """Executes full asset verification pipeline."""
        refs = self.discover_references()
        errors: List[DiagnosticError] = []
        warnings: List[DiagnosticError] = []

        missing_set: Set[str] = set()
        corrupted_set: Set[str] = set()
        referenced_disk_paths: Set[str] = set()

        # Step 1: Forward check for every discovered reference
        for ref in refs:
            # Security / Path Traversal Check
            if ".." in ref.target_path or ".." in ref.resolved_rel_path:
                errors.append(
                    DiagnosticError(
                        stage="asset_integrity",
                        severity="ERROR",
                        file_path=ref.referring_file,
                        line=ref.line_number,
                        message=f"Security violation: path traversal in asset reference '{ref.target_path}'.",
                        suggested_fix="Remove relative '..' components from asset path.",
                    )
                )
                continue

            disk_path = (self.project_dir / ref.resolved_rel_path).resolve()
            # Ensure within sandbox
            try:
                disk_path.relative_to(self.project_dir)
            except ValueError:
                errors.append(
                    DiagnosticError(
                        stage="asset_integrity",
                        severity="ERROR",
                        file_path=ref.referring_file,
                        line=ref.line_number,
                        message=f"Security violation: asset path '{ref.target_path}' resolves outside project root.",
                        suggested_fix="Keep all asset references inside project directory.",
                    )
                )
                continue

            rel_str = str(disk_path.relative_to(self.project_dir))
            referenced_disk_paths.add(rel_str)

            # Check existence
            if not disk_path.exists():
                missing_set.add(rel_str)
                errors.append(
                    DiagnosticError(
                        stage="asset_integrity",
                        severity="ERROR",
                        file_path=ref.referring_file,
                        line=ref.line_number,
                        message=f"Missing referenced asset file: '{ref.target_path}' (expected at {rel_str}).",
                        suggested_fix=f"Generate asset or place file at {rel_str}.",
                    )
                )
                continue

            # Check non-zero size
            file_size = disk_path.stat().st_size
            if file_size == 0:
                corrupted_set.add(rel_str)
                errors.append(
                    DiagnosticError(
                        stage="asset_integrity",
                        severity="ERROR",
                        file_path=ref.referring_file,
                        line=ref.line_number,
                        message=f"Asset file is empty (0 bytes): '{rel_str}'.",
                        suggested_fix=f"Replace empty file {rel_str} with valid asset content.",
                    )
                )
                continue

            # Check magic bytes & format header
            ext = disk_path.suffix.lower()
            try:
                with open(disk_path, "rb") as f:
                    header_bytes = f.read(1024)
                magic_err = validate_magic_bytes(header_bytes, ext)
                if magic_err:
                    corrupted_set.add(rel_str)
                    errors.append(
                        DiagnosticError(
                            stage="asset_integrity",
                            severity="ERROR",
                            file_path=ref.referring_file,
                            line=ref.line_number,
                            message=f"Asset corrupted or invalid format in '{rel_str}': {magic_err}",
                            suggested_fix=f"Ensure file {rel_str} is a valid {ext} asset.",
                        )
                    )
            except Exception as ex:
                corrupted_set.add(rel_str)
                errors.append(
                    DiagnosticError(
                        stage="asset_integrity",
                        severity="ERROR",
                        file_path=ref.referring_file,
                        line=ref.line_number,
                        message=f"Failed reading asset file '{rel_str}': {ex}",
                    )
                )

        # Step 2: Reverse check (Orphan detection)
        # Scan disk files under assets/ and audio/
        orphaned: List[str] = []
        scanned_disk_count = 0

        for base_dir in ("assets", "audio"):
            dir_path = self.project_dir / base_dir
            if dir_path.is_dir():
                for p in dir_path.rglob("*"):
                    if p.is_file():
                        scanned_disk_count += 1
                        rel = str(p.relative_to(self.project_dir))
                        if rel not in referenced_disk_paths:
                            orphaned.append(rel)
                            severity = "ERROR" if fail_on_orphan else "WARNING"
                            diag = DiagnosticError(
                                stage="asset_integrity",
                                severity=severity,
                                file_path=rel,
                                line=1,
                                message=f"Orphaned asset file not referenced by any scene, script, or manifest: '{rel}'.",
                                suggested_fix="Reference asset in scene/manifest or delete unused file.",
                            )
                            if fail_on_orphan:
                                errors.append(diag)
                            else:
                                warnings.append(diag)

        unique_refs = len({r.resolved_rel_path for r in refs})
        passed = len(errors) == 0

        return AssetVerificationResult(
            passed=passed,
            total_references_scanned=len(refs),
            unique_assets_referenced=unique_refs,
            files_on_disk_scanned=scanned_disk_count,
            errors=errors,
            warnings=warnings,
            orphaned_files=orphaned,
            missing_files=sorted(list(missing_set)),
            corrupted_files=sorted(list(corrupted_set)),
        )

    def verify_file(self, file_path: Union[str, Path]) -> List[DiagnosticError]:
        """Validates a single asset file on disk (size + magic bytes)."""
        p = Path(file_path)
        if not p.is_file():
            return [
                DiagnosticError(
                    stage="asset_integrity",
                    severity="ERROR",
                    file_path=str(p),
                    line=1,
                    message=f"Asset file does not exist: {p}",
                )
            ]
        if p.stat().st_size == 0:
            return [
                DiagnosticError(
                    stage="asset_integrity",
                    severity="ERROR",
                    file_path=str(p),
                    line=1,
                    message=f"Asset file is empty (0 bytes): {p}",
                )
            ]
        try:
            with open(p, "rb") as f:
                data = f.read(1024)
            err = validate_magic_bytes(data, p.suffix.lower())
            if err:
                return [
                    DiagnosticError(
                        stage="asset_integrity",
                        severity="ERROR",
                        file_path=str(p),
                        line=1,
                        message=f"Asset format error in {p.name}: {err}",
                    )
                ]
        except Exception as ex:
            return [
                DiagnosticError(
                    stage="asset_integrity",
                    severity="ERROR",
                    file_path=str(p),
                    line=1,
                    message=f"Error reading asset {p}: {ex}",
                )
            ]
        return []

    def verify_project(self) -> List[DiagnosticError]:
        """Returns list of DiagnosticError from full project asset verification."""
        res = self.verify_all()
        return res.errors + res.warnings

    # ------------------------------------------------------------------------
    # Internal Parsers
    # ------------------------------------------------------------------------

    def _discover_from_tscn(self, file_path: Path) -> List[DiscoveredAssetRef]:
        refs: List[DiscoveredAssetRef] = []
        rel_referring = str(file_path.relative_to(self.project_dir))
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return refs

        ext_res_pattern = re.compile(
            r'\[ext_resource\s+type="([^"]+)"\s+(?:uid="[^"]*"\s+)?path="([^"]+)"\s+id="([^"]+)"\]'
        )
        inline_path_pattern = re.compile(
            r'"(res://(?:assets|audio)/[^"]+)"'
        )

        for line_num, line in enumerate(lines, start=1):
            s = line.strip()
            # Ext resource tag
            m = ext_res_pattern.search(s)
            if m:
                res_type, res_path, res_id = m.group(1), m.group(2), m.group(3)
                norm_rel = res_path.replace("res://", "").lstrip("/")
                refs.append(
                    DiscoveredAssetRef(
                        target_path=res_path,
                        resolved_rel_path=norm_rel,
                        referring_file=rel_referring,
                        line_number=line_num,
                        resource_id=res_id,
                        expected_type=res_type,
                        context_snippet=s,
                    )
                )
                continue

            # Inline res:// paths
            for im in inline_path_pattern.finditer(s):
                res_path = im.group(1)
                norm_rel = res_path.replace("res://", "").lstrip("/")
                refs.append(
                    DiscoveredAssetRef(
                        target_path=res_path,
                        resolved_rel_path=norm_rel,
                        referring_file=rel_referring,
                        line_number=line_num,
                        context_snippet=s,
                    )
                )

        return refs

    def _discover_from_godot_config(self, file_path: Path) -> List[DiscoveredAssetRef]:
        refs: List[DiscoveredAssetRef] = []
        rel_referring = str(file_path.relative_to(self.project_dir))
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return refs

        path_pattern = re.compile(r'=\s*"(res://[^"]+)"')
        for line_num, line in enumerate(lines, start=1):
            m = path_pattern.search(line)
            if m:
                target = m.group(1)
                resolved = target.replace("res://", "").lstrip("/")
                refs.append(
                    DiscoveredAssetRef(
                        target_path=target,
                        resolved_rel_path=resolved,
                        referring_file=rel_referring,
                        line_number=line_num,
                        context_snippet=line.strip(),
                    )
                )
        return refs

    def _discover_from_script(self, file_path: Path) -> List[DiscoveredAssetRef]:
        refs: List[DiscoveredAssetRef] = []
        rel_referring = str(file_path.relative_to(self.project_dir))
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return refs

        patterns = [
            # GDScript preload / load
            re.compile(r'(?:preload|load)\(\s*"(res://[^"]+)"\s*\)'),
            # Pygame image/sound load
            re.compile(r'pygame\.(?:image|mixer)\.(?:load|Sound)\(\s*["\']([^"\']+)["\']\s*\)'),
            # Generic res:// literals
            re.compile(r'"(res://(?:assets|audio)/[^"]+)"'),
        ]

        for line_num, line in enumerate(lines, start=1):
            s = line.strip()
            if s.startswith("#") or s.startswith("//"):
                continue
            for pat in patterns:
                for match in pat.finditer(s):
                    raw_target = match.group(1)
                    resolved = raw_target.replace("res://", "").lstrip("/")
                    refs.append(
                        DiscoveredAssetRef(
                            target_path=raw_target,
                            resolved_rel_path=resolved,
                            referring_file=rel_referring,
                            line_number=line_num,
                            context_snippet=s,
                        )
                    )
        return refs

    def _discover_from_manifest_file(self, file_path: Path) -> List[DiscoveredAssetRef]:
        refs: List[DiscoveredAssetRef] = []
        rel_referring = str(file_path.relative_to(self.project_dir))
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return refs

        items = data.get("items", []) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict):
                r_path = item.get("rel_path") or item.get("path")
                engine_path = item.get("engine_path") or f"res://{r_path}"
                if r_path:
                    refs.append(
                        DiscoveredAssetRef(
                            target_path=engine_path,
                            resolved_rel_path=r_path.replace("res://", "").lstrip("/"),
                            referring_file=rel_referring,
                            resource_id=item.get("asset_id"),
                            expected_type=item.get("asset_type"),
                        )
                    )
        return refs


# Alias for compatibility
AssetIntegrityVerifier = AssetVerifier
