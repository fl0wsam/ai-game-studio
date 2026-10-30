"""
studio/engine/hub.py - Pluggable Engine Adapter Hub.
Coordinates export pipelines across Godot 4, Pygame, Web HTML5, and Unity.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from studio.core.models import (
    AssetManifest,
    EngineTarget,
    GameDesignDocument,
    ScriptSpec,
)
from studio.engine.adapters.base import BaseEngineAdapter, EngineProjectManifest
from studio.engine.ast_models import SceneAST

logger = logging.getLogger("studio.engine.hub")


class EngineHub:
    """
    Central hub managing registered engine adapters and dispatching export jobs.
    """

    def __init__(self, register_defaults: bool = True) -> None:
        self._adapters: Dict[EngineTarget, BaseEngineAdapter] = {}
        if register_defaults:
            self.register_builtin_adapters()

    def register_adapter(self, adapter: BaseEngineAdapter, override: bool = False) -> None:
        """Registers an engine adapter instance."""
        target = adapter.target
        if target in self._adapters and not override:
            raise ValueError(f"Adapter for engine '{target.value}' already registered. Use override=True.")
        self._adapters[target] = adapter
        logger.info(f"Registered engine adapter '{adapter.display_name}' for target '{target.value}'")

    def unregister_adapter(self, target: Union[EngineTarget, str]) -> Optional[BaseEngineAdapter]:
        """Unregisters an adapter by target."""
        resolved = self.resolve_target(target)
        return self._adapters.pop(resolved, None)

    def get_adapter(self, target: Union[EngineTarget, str]) -> BaseEngineAdapter:
        """Retrieves adapter for target or raises KeyError."""
        resolved = self.resolve_target(target)
        if resolved not in self._adapters:
            available = [t.value for t in self._adapters.keys()]
            raise KeyError(f"No adapter registered for engine '{resolved.value}'. Available: {available}")
        return self._adapters[resolved]

    def has_adapter(self, target: Union[EngineTarget, str]) -> bool:
        """Checks if adapter is registered for target."""
        try:
            resolved = self.resolve_target(target)
            return resolved in self._adapters
        except Exception:
            return False

    def list_supported_engines(self) -> List[Dict[str, Any]]:
        """Returns metadata for all registered engine targets."""
        res: List[Dict[str, Any]] = []
        for target, adapter in self._adapters.items():
            prereqs = adapter.validate_prerequisites()
            res.append({
                "target": target.value,
                "display_name": adapter.display_name,
                "file_extensions": adapter.file_extensions,
                "supported_archetypes": adapter.supported_archetypes,
                "prerequisites": prereqs,
            })
        return res

    def resolve_target(self, target: Optional[Union[EngineTarget, str]]) -> EngineTarget:
        """Normalizes string or enum to valid EngineTarget with fallback."""
        if target is None:
            return EngineTarget.GODOT4
        if isinstance(target, EngineTarget):
            return target

        normalized = target.strip().lower()
        mapping = {
            "godot": EngineTarget.GODOT4,
            "godot4": EngineTarget.GODOT4,
            "pygame": EngineTarget.PYGAME,
            "python": EngineTarget.PYGAME,
            "web": EngineTarget.WEB_HTML5,
            "html5": EngineTarget.WEB_HTML5,
            "web_html5": EngineTarget.WEB_HTML5,
            "phaser": EngineTarget.WEB_HTML5,
            "canvas": EngineTarget.WEB_HTML5,
            "unity": EngineTarget.UNITY,
        }
        if normalized in mapping:
            return mapping[normalized]
        try:
            return EngineTarget(normalized)
        except ValueError:
            logger.warning(f"Unknown engine target '{target}', defaulting to '{EngineTarget.GODOT4.value}'")
            return EngineTarget.GODOT4

    def export(
        self,
        target: Union[EngineTarget, str],
        gdd: GameDesignDocument,
        manifest: AssetManifest,
        scenes: Union[List[SceneAST], Dict[str, SceneAST], List[Any], Dict[str, Any]],
        scripts: Union[List[ScriptSpec], Dict[str, ScriptSpec], List[Any], Dict[str, Any]],
        output_dir: Union[str, Path],
        **kwargs: Any,
    ) -> EngineProjectManifest:
        """Exports project using the target engine adapter."""
        adapter = self.get_adapter(target)
        out_path = Path(output_dir).resolve()
        out_path.mkdir(parents=True, exist_ok=True)
        return adapter.export_project(gdd, manifest, scenes, scripts, out_path, **kwargs)

    def export_multi(
        self,
        targets: List[Union[EngineTarget, str]],
        gdd: GameDesignDocument,
        manifest: AssetManifest,
        scenes: Union[List[SceneAST], Dict[str, SceneAST], List[Any], Dict[str, Any]],
        scripts: Union[List[ScriptSpec], Dict[str, ScriptSpec], List[Any], Dict[str, Any]],
        base_output_dir: Union[str, Path],
        **kwargs: Any,
    ) -> Dict[str, EngineProjectManifest]:
        """Exports project across multiple engines under subdirectories."""
        base_path = Path(base_output_dir).resolve()
        results: Dict[str, EngineProjectManifest] = {}
        for t in targets:
            resolved = self.resolve_target(t)
            target_dir = base_path / resolved.value
            results[resolved.value] = self.export(
                resolved, gdd, manifest, scenes, scripts, target_dir, **kwargs
            )
        return results

    def register_builtin_adapters(self) -> None:
        """Discovers and registers all built-in adapters."""
        try:
            from studio.engine.adapters.godot_adapter import GodotAdapter
            self.register_adapter(GodotAdapter(), override=True)
        except Exception as e:
            logger.warning(f"Could not load GodotAdapter: {e}")

        try:
            from studio.engine.adapters.pygame_adapter import PygameAdapter
            self.register_adapter(PygameAdapter(), override=True)
        except Exception as e:
            logger.warning(f"Could not load PygameAdapter: {e}")

        try:
            from studio.engine.adapters.web_adapter import WebAdapter
            self.register_adapter(WebAdapter(), override=True)
        except Exception as e:
            logger.warning(f"Could not load WebAdapter: {e}")

        try:
            from studio.engine.adapters.unity_adapter import UnityAdapter
            self.register_adapter(UnityAdapter(), override=True)
        except Exception as e:
            logger.warning(f"Could not load UnityAdapter: {e}")


# Global Singleton Helper
_DEFAULT_HUB: Optional[EngineHub] = None

def get_engine_hub() -> EngineHub:
    """Returns global singleton EngineHub instance."""
    global _DEFAULT_HUB
    if _DEFAULT_HUB is None:
        _DEFAULT_HUB = EngineHub(register_defaults=True)
    return _DEFAULT_HUB
