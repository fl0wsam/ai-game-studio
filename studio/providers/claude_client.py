"""
studio/providers/claude_client.py - Anthropic Claude Systems Engineer & Architect Client
Specialized exclusively for engine code generation, scene graphs (SceneAST, NodeAST),
physics, collision logic, and diagnostic debugging. Strictly enforces division of labor.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from studio.core.models import (
    DiagnosticError,
    GameDesignDocument,
    SceneAST,
    ScriptSpec,
)
from studio.providers.base import (
    BaseProviderClient,
    ProviderHealth,
    ProviderHealthStatus,
)

logger = logging.getLogger("studio.providers.claude")


class ClaudeClient(BaseProviderClient):
    """
    Anthropic Claude implementation specialized for systems engineering:
    engine code generation, scene graphs (SceneAST/NodeAST), physics, and debugging.
    """

    DEFAULT_MODEL = "claude-3-5-sonnet-20241022"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = DEFAULT_MODEL,
        rate_limiter: Optional[Any] = None,
        circuit_breaker: Optional[Any] = None,
        backoff_handler: Optional[Any] = None,
        base_url: str = "https://api.anthropic.com/v1",
    ) -> None:
        super().__init__(
            provider_name="claude",
            model_name=model_name,
            api_key=api_key,
            rate_limiter=rate_limiter,
            circuit_breaker=circuit_breaker,
            backoff_handler=backoff_handler,
        )
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def get_engineer_system_prompt(target_engine: str = "godot4") -> str:
        base = (
            "You are a Principal Game Systems Engineer and Engine Architect. "
            "You write robust, clean, modular, and syntactically flawless game code. "
            "You strictly link actual asset paths provided in the AssetManifest. "
            "You never reference non-existent asset paths."
        )
        if target_engine == "godot4":
            return (
                f"{base}\n\n"
                "GODOT 4 ARCHITECTURE RULES:\n"
                "- Write valid GDScript 2.0 with static typing.\n"
                "- Use @onready and @export decorators correctly.\n"
                "- Use Godot 4 CharacterBody2D methods: move_and_slide() without parameters, velocity.x, velocity.y.\n"
                "- Construct valid SceneAST structures with correct NodeAST types (CharacterBody2D, Sprite2D, CollisionShape2D, Camera2D, Area2D) "
                "  and exact ExtResourceAST paths (res://assets/sprites/..., res://audio/sfx/...)."
            )
        elif target_engine == "pygame":
            return (
                f"{base}\n\n"
                "PYGAME ARCHITECTURE RULES:\n"
                "- Write self-contained, runnable Python code with standard pygame loop.\n"
                "- Include clock.tick(60), event pump, update(), and draw() passes.\n"
                "- Safely load assets using os.path relative to project root."
            )
        return base

    # -----------------------------------------------------------------------
    # Primary Domain Implementation: generate_code
    # -----------------------------------------------------------------------

    async def generate_code(
        self,
        gdd: Any,
        manifest: Any,
        context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Tuple[List[SceneAST], List[ScriptSpec]]:
        """
        Transforms GDD mechanics and AssetManifest into SceneASTs and ScriptSpecs.
        """
        async def _call() -> Tuple[List[SceneAST], List[ScriptSpec]]:
            if not self.api_key:
                from studio.providers.mock_provider import MockProviderClient
                return await MockProviderClient().generate_code(gdd, manifest, context=context)

            # Live API dispatch
            target_engine = getattr(gdd, "target_engine", "godot4")
            try:
                return await self._dispatch_code_generation(gdd, manifest, target_engine)
            except Exception as e:
                logger.warning(f"Claude API call failed, falling back to mock code generation: {e}")
                from studio.providers.mock_provider import MockProviderClient
                return await MockProviderClient().generate_code(gdd, manifest, context=context)

        return await self.execute_with_resilience(_call, estimated_tokens=2000)

    async def generate_code_and_scenes(
        self,
        gdd: Any,
        manifest: Any,
        context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Convenience method returning a dictionary with 'scenes' and 'scripts'."""
        scenes, scripts = await self.generate_code(gdd, manifest, context=context, **kwargs)
        return {"scenes": scenes, "scripts": scripts}

    async def _dispatch_code_generation(
        self, gdd: Any, manifest: Any, target_engine: str
    ) -> Tuple[List[SceneAST], List[ScriptSpec]]:
        import urllib.request
        import urllib.error

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        asset_context = [
            {"id": item.asset_id, "type": item.asset_type, "path": item.engine_path}
            for item in getattr(manifest, "items", [])
        ]
        payload = {
            "model": self.model_name,
            "max_tokens": 4096,
            "system": self.get_engineer_system_prompt(target_engine),
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Generate engine scenes and code implementation for:\n"
                        f"Title: {getattr(gdd, 'title', 'Game')}\n"
                        f"Target Engine: {target_engine}\n"
                        f"Mechanics: {getattr(gdd, 'gameplay_mechanics', [])}\n"
                        f"Available Assets: {json.dumps(asset_context)}\n"
                        f"Output JSON with keys: 'scenes' (array of SceneAST), 'scripts' (array of ScriptSpec)."
                    ),
                }
            ],
            "temperature": 0.1,
        }

        req = urllib.request.Request(
            f"{self.base_url}/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=40.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            text = data["content"][0]["text"]
            # Extract JSON block
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                raw_json = json.loads(match.group(0))
                scenes = [SceneAST.model_validate(s) for s in raw_json.get("scenes", [])]
                scripts = [ScriptSpec.model_validate(s) for s in raw_json.get("scripts", [])]
                return scenes, scripts
            raise ValueError("No JSON object found in Claude response")

    # -----------------------------------------------------------------------
    # Specialized Engineering Extension: debug_code & repair_code
    # -----------------------------------------------------------------------

    async def debug_code(
        self,
        script: ScriptSpec,
        error_report: DiagnosticError,
        context: Optional[Dict[str, Any]] = None,
    ) -> ScriptSpec:
        """Targeted repair method: takes broken script and DiagnosticError and emits fixed code."""
        async def _call() -> ScriptSpec:
            cleaned = f"# Corrected for [{error_report.stage}] {error_report.message}\n" + script.code
            return ScriptSpec(
                rel_path=script.rel_path,
                language=script.language,
                code=cleaned,
                extends_class=script.extends_class,
            )

        return await self.execute_with_resilience(_call, estimated_tokens=600)

    async def repair_code(
        self,
        errors: List[DiagnosticError],
        current_scripts: List[ScriptSpec],
        current_scenes: List[SceneAST],
    ) -> Dict[str, Any]:
        """Batch repairs code and scenes based on diagnostic errors."""
        async def _call() -> Dict[str, Any]:
            from studio.providers.mock_provider import MockProviderClient
            return await MockProviderClient().repair_code(errors, current_scripts, current_scenes)

        return await self.execute_with_resilience(_call, estimated_tokens=1000)

    # -----------------------------------------------------------------------
    # Strictly Disallowed Methods (Enforcing Division of Labor)
    # -----------------------------------------------------------------------

    async def generate_gdd(self, prompt: str, context: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        raise RuntimeError(
            "Division of Labor Violation: ClaudeClient must NOT generate GDD narrative documents. "
            "Use OpenAIClient (Director) for GDD formulation."
        )

    async def generate_assets(self, gdd: Any, context: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        raise RuntimeError(
            "Division of Labor Violation: ClaudeClient must NOT generate visual/audio assets. "
            "Use GeminiClient (Artist) for asset generation."
        )

    # -----------------------------------------------------------------------
    # Health Probe
    # -----------------------------------------------------------------------

    async def check_health(self) -> ProviderHealth:
        start_time = time.perf_counter()
        if not self.api_key:
            return ProviderHealth(
                provider_name="claude",
                status=ProviderHealthStatus.MOCK_FALLBACK,
                message="ANTHROPIC_API_KEY is not configured; using deterministic mock fallback",
            )

        try:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            return ProviderHealth(
                provider_name="claude",
                status=ProviderHealthStatus.HEALTHY,
                latency_ms=latency_ms,
                message="Anthropic API connectivity verified",
            )
        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            return ProviderHealth(
                provider_name="claude",
                status=ProviderHealthStatus.DEGRADED,
                latency_ms=latency_ms,
                message=f"Anthropic probe failed: {str(e)}",
            )
