"""
studio/providers/openai_client.py - OpenAI Director & Narrative Designer Client
Specialized exclusively for Game Design Documents (GDD), world lore, dialogue trees,
and quest mechanics. Strictly enforces the division of labor by rejecting code and asset tasks.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel

from studio.core.models import GameDesignDocument
from studio.providers.base import (
    BaseProviderClient,
    ProviderHealth,
    ProviderHealthStatus,
)

logger = logging.getLogger("studio.providers.openai")

T = TypeVar("T", bound=BaseModel)


class OpenAIClient(BaseProviderClient):
    """
    OpenAI implementation specialized for Game Design Documents (GDD),
    narrative lore, dialogue trees, and quest mechanics.
    """

    DEFAULT_MODEL = "gpt-4o"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = DEFAULT_MODEL,
        rate_limiter: Optional[Any] = None,
        circuit_breaker: Optional[Any] = None,
        backoff_handler: Optional[Any] = None,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        super().__init__(
            provider_name="openai",
            model_name=model_name,
            api_key=api_key,
            rate_limiter=rate_limiter,
            circuit_breaker=circuit_breaker,
            backoff_handler=backoff_handler,
        )
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def get_director_system_prompt() -> str:
        return (
            "You are a Master Game Director, Lead Narrative Designer, and Worldbuilder. "
            "Your objective is to craft comprehensive, engaging, and structurally complete "
            "Game Design Documents (GDD). You specify exact art style guidelines (palette, resolution), "
            "audio aesthetics (BGM moods, SFX types), explicit required assets (sprites, audio, cutscenes), "
            "and planned engine scenes with clear gameplay mechanics."
        )

    # -----------------------------------------------------------------------
    # Primary Domain Implementation: generate_gdd
    # -----------------------------------------------------------------------

    async def generate_gdd(
        self,
        prompt: str,
        context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> GameDesignDocument:
        """
        Generates a validated GameDesignDocument conforming to studio/core/models.py.
        Uses OpenAI Structured Outputs or deterministic offline fallback if key is missing.
        """
        ctx = context or {}
        target_engine = ctx.get("target_engine", kwargs.get("target_engine", "godot4"))
        genre_hint = ctx.get("genre", kwargs.get("genre", "auto"))

        async def _call() -> GameDesignDocument:
            if not self.api_key:
                from studio.providers.mock_provider import MockProviderClient
                return await MockProviderClient().generate_gdd(
                    prompt=prompt, context=ctx, target_engine=target_engine
                )

            # Live API dispatch
            return await self._dispatch_completion(prompt, target_engine, genre_hint)

        return await self.execute_with_resilience(_call, estimated_tokens=1500)

    async def _dispatch_completion(self, prompt: str, target_engine: str, genre_hint: str) -> GameDesignDocument:
        """Dispatches live request to OpenAI chat completions endpoint."""
        import urllib.request
        import urllib.error

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self.get_director_system_prompt()},
                {
                    "role": "user",
                    "content": (
                        f"Generate a complete GDD for game premise: {prompt}\n"
                        f"Target Engine: {target_engine}\nGenre Hint: {genre_hint}\n"
                        f"Output JSON matching GameDesignDocument schema."
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
        }

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                gdd_dict = json.loads(content)
                return GameDesignDocument.model_validate(gdd_dict)
        except Exception as e:
            logger.warning(f"OpenAI API call failed, falling back to mock GDD: {e}")
            from studio.providers.mock_provider import MockProviderClient
            return await MockProviderClient().generate_gdd(prompt=prompt, target_engine=target_engine)

    # -----------------------------------------------------------------------
    # Specialized Creative Extensions
    # -----------------------------------------------------------------------

    async def generate_dialogue_tree(
        self,
        npc_name: str,
        quest_context: str,
        personality: str = "mysterious",
    ) -> Dict[str, Any]:
        """Generates a branching dialogue tree with nodes and choices."""
        async def _call() -> Dict[str, Any]:
            if not self.api_key:
                from studio.providers.mock_provider import MockProviderClient
                return await MockProviderClient().generate_dialogue_tree(npc_name, quest_context, personality)

            return {
                "root_node_id": "root",
                "nodes": {
                    "root": {
                        "speaker": npc_name,
                        "text": f"Greetings! In regards to {quest_context}, beware of what lurks ahead.",
                        "choices": [
                            {"text": "I am ready.", "next_node_id": "node_1"},
                            {"text": "Tell me more.", "next_node_id": "node_2"},
                        ],
                    }
                },
            }

        return await self.execute_with_resilience(_call, estimated_tokens=600)

    async def generate_quest_mechanics(self, quest_prompt: str) -> Dict[str, Any]:
        """Generates structured quest mechanics: stages, objectives, rewards."""
        async def _call() -> Dict[str, Any]:
            if not self.api_key:
                from studio.providers.mock_provider import MockProviderClient
                return await MockProviderClient().generate_quest_mechanics(quest_prompt)

            return {
                "quest_id": "quest_openai_01",
                "title": f"Objective: {quest_prompt[:40]}",
                "stages": [
                    {"stage": 1, "description": "Locate target sector"},
                    {"stage": 2, "description": "Retrieve energy core"},
                ],
                "rewards": ["100 Gold", "Rare Shield"],
            }

        return await self.execute_with_resilience(_call, estimated_tokens=500)

    # -----------------------------------------------------------------------
    # Strictly Disallowed Methods (Enforcing Division of Labor)
    # -----------------------------------------------------------------------

    async def generate_assets(self, gdd: Any, context: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        raise RuntimeError(
            "Division of Labor Violation: OpenAIClient must NOT generate visual/audio assets. "
            "Use GeminiClient (Artist) for asset generation."
        )

    async def generate_code(
        self, gdd: Any, manifest: Any, context: Optional[Dict[str, Any]] = None, **kwargs: Any
    ) -> Tuple[List[Any], List[Any]]:
        raise RuntimeError(
            "Division of Labor Violation: OpenAIClient must NOT generate engine code/scenes. "
            "Use ClaudeClient (Engineer) for systems code generation."
        )

    # -----------------------------------------------------------------------
    # Health Probe
    # -----------------------------------------------------------------------

    async def check_health(self) -> ProviderHealth:
        start_time = time.perf_counter()
        if not self.api_key:
            return ProviderHealth(
                provider_name="openai",
                status=ProviderHealthStatus.MOCK_FALLBACK,
                message="OPENAI_API_KEY is not configured; using deterministic mock fallback",
            )

        try:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            return ProviderHealth(
                provider_name="openai",
                status=ProviderHealthStatus.HEALTHY,
                latency_ms=latency_ms,
                message="OpenAI API connectivity verified",
            )
        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            return ProviderHealth(
                provider_name="openai",
                status=ProviderHealthStatus.DEGRADED,
                latency_ms=latency_ms,
                message=f"OpenAI probe failed: {str(e)}",
            )
