"""
studio/core/blackboard.py - Centralized Thread & Async-Safe State Repository
Authoritative project state store enforcing atomic mutations, revision history tracking,
observer notifications, and isolated snapshots.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

from studio.core.models import (
    AssetItem,
    AssetManifest,
    GameDesignDocument,
    ProjectStatus,
    ReviewFeedback,
    SceneAST,
    ScriptSpec,
    StudioProjectState,
    TokenUsageMetrics,
    VerificationReport,
)

logger = logging.getLogger("studio.core.blackboard")

StateChangeCallback = Callable[[StudioProjectState, str], Union[Awaitable[None], None]]


class Blackboard:
    """
    Central thread-safe and async-safe project state store (F-ORCH-01).
    Enforces atomic mutations, revision history tracking, and change observer dispatch.
    """

    def __init__(
        self,
        project_name: str = "UntitledGame",
        target_engine: str = "godot4",
        user_prompt: str = "",
        max_revisions: int = 50,
    ) -> None:
        self._async_lock = asyncio.Lock()
        self._thread_lock = threading.RLock()
        self._max_revisions = max_revisions

        self._state = StudioProjectState(
            project_name=project_name,
            target_engine=target_engine,
            user_prompt=user_prompt,
            status=ProjectStatus.PENDING,
            active_phase="INITIALIZATION",
        )

        # Revision history: List of (revision_id, timestamp, action_label, snapshot)
        self._history: List[Tuple[int, float, str, StudioProjectState]] = []
        self._listeners: Dict[str, StateChangeCallback] = {}
        self._save_revision_unlocked("Initial project creation")

    async def reset(
        self,
        project_name: str = "UntitledGame",
        target_engine: str = "godot4",
        user_prompt: str = "",
    ) -> None:
        """Atomically resets the blackboard state for a new autonomous game build."""
        async with self._async_lock:
            with self._thread_lock:
                self._state = StudioProjectState(
                    project_name=project_name,
                    target_engine=target_engine,
                    user_prompt=user_prompt,
                    status=ProjectStatus.IN_PROGRESS,
                    active_phase="INITIALIZATION",
                )
                self._save_revision_unlocked("Project reset for fresh build")
        await self._notify_listeners("reset")

    # ------------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------------

    def _save_revision_unlocked(self, action: str) -> None:
        """Internal revision archiver (must be called with lock held)."""
        self._state.revision += 1
        self._state.updated_at = time.time()
        snapshot = self._state.model_copy(deep=True)
        self._history.append((self._state.revision, self._state.updated_at, action, snapshot))
        if len(self._history) > self._max_revisions:
            self._history.pop(0)

    async def _notify_listeners(self, action: str) -> None:
        """Dispatches state change to registered listeners."""
        snapshot = await self.get_state()
        for sub_id, callback in list(self._listeners.items()):
            try:
                res = callback(snapshot, action)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error(
                    f"Error in blackboard listener '{sub_id}' on action '{action}': {e}",
                    exc_info=True,
                )

    # ------------------------------------------------------------------------
    # Listener Management (Observer Pattern)
    # ------------------------------------------------------------------------

    def register_listener(self, callback: StateChangeCallback) -> str:
        """Registers a callback invoked upon state mutations. Returns subscription ID."""
        sub_id = str(uuid.uuid4())
        with self._thread_lock:
            self._listeners[sub_id] = callback
        return sub_id

    def unregister_listener(self, sub_id: str) -> bool:
        """Removes a registered callback."""
        with self._thread_lock:
            return self._listeners.pop(sub_id, None) is not None

    # ------------------------------------------------------------------------
    # Read Operations (Returning Detached Snapshots)
    # ------------------------------------------------------------------------

    async def get_state(self) -> StudioProjectState:
        """Returns an isolated, deep copy snapshot of the current state."""
        async with self._async_lock:
            with self._thread_lock:
                return self._state.model_copy(deep=True)

    async def get_gdd(self) -> Optional[GameDesignDocument]:
        async with self._async_lock:
            with self._thread_lock:
                return self._state.gdd.model_copy(deep=True) if self._state.gdd else None

    async def get_asset_manifest(self) -> Optional[AssetManifest]:
        async with self._async_lock:
            with self._thread_lock:
                return (
                    self._state.asset_manifest.model_copy(deep=True)
                    if self._state.asset_manifest
                    else None
                )

    async def get_asset_item(self, asset_id: str) -> Optional[AssetItem]:
        async with self._async_lock:
            with self._thread_lock:
                if not self._state.asset_manifest:
                    return None
                item = self._state.asset_manifest.get_item(asset_id)
                return item.model_copy(deep=True) if item else None

    async def get_scenes(self) -> List[SceneAST]:
        async with self._async_lock:
            with self._thread_lock:
                return [s.model_copy(deep=True) for s in self._state.scenes.values()]

    async def get_scene(self, scene_path: str) -> Optional[SceneAST]:
        async with self._async_lock:
            with self._thread_lock:
                s = self._state.scenes.get(scene_path)
                return s.model_copy(deep=True) if s else None

    async def get_scripts(self) -> List[ScriptSpec]:
        async with self._async_lock:
            with self._thread_lock:
                return [s.model_copy(deep=True) for s in self._state.scripts.values()]

    async def get_script(self, rel_path: str) -> Optional[ScriptSpec]:
        async with self._async_lock:
            with self._thread_lock:
                s = self._state.scripts.get(rel_path)
                return s.model_copy(deep=True) if s else None

    async def get_verification_report(self) -> Optional[VerificationReport]:
        async with self._async_lock:
            with self._thread_lock:
                return (
                    self._state.verification_report.model_copy(deep=True)
                    if self._state.verification_report
                    else None
                )

    async def get_token_metrics(self) -> Dict[str, TokenUsageMetrics]:
        async with self._async_lock:
            with self._thread_lock:
                return {k: v.model_copy(deep=True) for k, v in self._state.token_metrics.items()}

    async def get_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        async with self._async_lock:
            with self._thread_lock:
                return copy.deepcopy(self._state.logs[-limit:])

    # ------------------------------------------------------------------------
    # Mutation Operations
    # ------------------------------------------------------------------------

    async def set_status(self, status: ProjectStatus, active_phase: Optional[str] = None) -> None:
        async with self._async_lock:
            with self._thread_lock:
                self._state.status = status
                if active_phase:
                    self._state.active_phase = active_phase
                self._save_revision_unlocked(f"Status update: {status.value}")
        await self._notify_listeners("set_status")

    async def set_gdd(self, gdd: GameDesignDocument) -> None:
        async with self._async_lock:
            with self._thread_lock:
                self._state.gdd = gdd.model_copy(deep=True)
                self._save_revision_unlocked("Set Game Design Document")
        await self._notify_listeners("set_gdd")

    async def set_asset_manifest(self, manifest: AssetManifest) -> None:
        async with self._async_lock:
            with self._thread_lock:
                self._state.asset_manifest = manifest.model_copy(deep=True)
                self._save_revision_unlocked("Set Asset Manifest")
        await self._notify_listeners("set_asset_manifest")

    async def add_asset_item(self, item: AssetItem) -> None:
        async with self._async_lock:
            with self._thread_lock:
                if self._state.asset_manifest is None:
                    self._state.asset_manifest = AssetManifest(project_name=self._state.project_name)
                self._state.asset_manifest.add_item(item.model_copy(deep=True))
                self._save_revision_unlocked(f"Add AssetItem: {item.asset_id}")
        await self._notify_listeners("add_asset_item")

    async def add_scene(self, scene: SceneAST) -> None:
        async with self._async_lock:
            with self._thread_lock:
                self._state.scenes[scene.path] = scene.model_copy(deep=True)
                self._save_revision_unlocked(f"Add/Update Scene: {scene.path}")
        await self._notify_listeners("add_scene")

    async def add_script(self, script: ScriptSpec) -> None:
        async with self._async_lock:
            with self._thread_lock:
                self._state.scripts[script.rel_path] = script.model_copy(deep=True)
                self._save_revision_unlocked(f"Add/Update Script: {script.rel_path}")
        await self._notify_listeners("add_script")

    async def set_verification_report(self, report: VerificationReport) -> None:
        async with self._async_lock:
            with self._thread_lock:
                self._state.verification_report = report.model_copy(deep=True)
                self._save_revision_unlocked("Set Verification Report")
        await self._notify_listeners("set_verification_report")

    async def add_review_feedback(self, feedback: ReviewFeedback) -> None:
        async with self._async_lock:
            with self._thread_lock:
                self._state.review_history.append(feedback.model_copy(deep=True))
                self._save_revision_unlocked(f"Add ReviewFeedback: {feedback.target_artifact}")
        await self._notify_listeners("add_review_feedback")

    async def record_token_metrics(
        self,
        provider: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float = 0.0,
    ) -> None:
        async with self._async_lock:
            with self._thread_lock:
                if provider not in self._state.token_metrics:
                    self._state.token_metrics[provider] = TokenUsageMetrics(provider=provider)
                m = self._state.token_metrics[provider]
                m.prompt_tokens += prompt_tokens
                m.completion_tokens += completion_tokens
                m.total_tokens += (prompt_tokens + completion_tokens)
                m.estimated_cost_usd += cost_usd
                m.request_count += 1
                self._save_revision_unlocked(f"Token update: {provider} (+{prompt_tokens + completion_tokens} tokens)")
        await self._notify_listeners("record_token_metrics")

    async def add_log(
        self,
        sender: str,
        level: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        async with self._async_lock:
            with self._thread_lock:
                entry = {
                    "timestamp": time.time(),
                    "sender": sender,
                    "level": level.upper(),
                    "message": message,
                    "metadata": metadata or {},
                }
                self._state.logs.append(entry)
                if len(self._state.logs) > 1000:
                    self._state.logs.pop(0)
        # Notify listeners of logs without incrementing full revision counter
        await self._notify_listeners("add_log")

    # ------------------------------------------------------------------------
    # Revisions & Rollback
    # ------------------------------------------------------------------------

    async def rollback_to_revision(self, revision_id: int) -> bool:
        """Rolls back state to a specific historical revision ID."""
        async with self._async_lock:
            with self._thread_lock:
                for rev, _, _, snapshot in self._history:
                    if rev == revision_id:
                        self._state = snapshot.model_copy(deep=True)
                        self._save_revision_unlocked(f"Rollback to revision {revision_id}")
                        break
                else:
                    return False
        await self._notify_listeners("rollback")
        return True

    def get_revision_history(self) -> List[Tuple[int, float, str]]:
        """Returns list of (revision_id, timestamp, action_label)."""
        with self._thread_lock:
            return [(rev, ts, action) for rev, ts, action, _ in self._history]

    # ------------------------------------------------------------------------
    # Serialization, Import & Export
    # ------------------------------------------------------------------------

    async def export_json(self, indent: int = 2) -> str:
        """Dumps current project state to a JSON string."""
        snapshot = await self.get_state()
        return snapshot.model_dump_json(indent=indent)

    async def export_dict(self) -> Dict[str, Any]:
        """Dumps current project state to a raw Python dictionary."""
        snapshot = await self.get_state()
        return snapshot.model_dump()

    async def load_dict(self, data: Dict[str, Any]) -> None:
        """Restores state from a dictionary."""
        loaded = StudioProjectState.model_validate(data)
        async with self._async_lock:
            with self._thread_lock:
                self._state = loaded
                self._save_revision_unlocked("Loaded state from dictionary")
        await self._notify_listeners("load_dict")

    async def save_to_file(self, file_path: Union[str, Path]) -> None:
        """Serializes blackboard state directly to disk."""
        content = await self.export_json()
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    async def load_from_file(self, file_path: Union[str, Path]) -> None:
        """Loads blackboard state from a JSON file on disk."""
        p = Path(file_path)
        content = p.read_text(encoding="utf-8")
        data = json.loads(content)
        await self.load_dict(data)
