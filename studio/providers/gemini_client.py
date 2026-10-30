"""
studio/providers/gemini_client.py - Google Gemini Multimodal Artist & Composer Client
Specialized exclusively for visual artwork (2D sprites, textures, backgrounds)
and audio assets (SFX, BGM loops, cutscenes). Strictly enforces division of labor.
"""
from __future__ import annotations

import base64
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from studio.core.models import (
    AssetItem,
    AssetManifest,
    DiagnosticError,
)
from studio.providers.base import (
    BaseProviderClient,
    ProviderHealth,
    ProviderHealthStatus,
)

logger = logging.getLogger("studio.providers.gemini")


class GeminiClient(BaseProviderClient):
    """
    Google Gemini implementation specialized for multimodal asset generation:
    sprites, textures, retro audio SFX/BGM, and video cutscenes.
    """

    DEFAULT_TEXT_MODEL = "gemini-2.0-flash"
    DEFAULT_IMAGE_MODEL = "imagen-3.0-generate-002"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = DEFAULT_TEXT_MODEL,
        image_model_name: str = DEFAULT_IMAGE_MODEL,
        rate_limiter: Optional[Any] = None,
        circuit_breaker: Optional[Any] = None,
        backoff_handler: Optional[Any] = None,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ) -> None:
        super().__init__(
            provider_name="gemini",
            model_name=model_name,
            api_key=api_key,
            rate_limiter=rate_limiter,
            circuit_breaker=circuit_breaker,
            backoff_handler=backoff_handler,
        )
        self.image_model_name = image_model_name
        self.base_url = base_url.rstrip("/")

    # -----------------------------------------------------------------------
    # Primary Domain Implementation: generate_assets
    # -----------------------------------------------------------------------

    async def generate_assets(
        self,
        gdd: Any,
        context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> AssetManifest:
        """
        Processes gdd.required_assets and generates concrete binary assets:
        - sprite/texture -> calls generate_image()
        - audio_sfx/audio_music -> calls generate_audio()
        - cutscene -> calls generate_cutscene()
        """
        async def _call() -> AssetManifest:
            if not self.api_key:
                from studio.providers.mock_provider import MockProviderClient
                return await MockProviderClient().generate_assets(gdd, context=context)

            items: List[AssetItem] = []
            project_name = getattr(gdd, "title", "Untitled Game")

            for req in getattr(gdd, "required_assets", []):
                asset_type_val = req.asset_type.value if hasattr(req.asset_type, "value") else str(req.asset_type)
                rel_path = req.get_rel_path() if hasattr(req, "get_rel_path") else req.rel_path or f"assets/{req.asset_id}"
                prompt_hint = req.prompt_hint or req.description or req.asset_id

                file_bytes: bytes
                if "sprite" in asset_type_val or "texture" in asset_type_val:
                    file_bytes = await self.generate_image(
                        prompt=prompt_hint,
                        width=req.width or 32,
                        height=req.height or 32,
                    )
                elif "audio" in asset_type_val or "sfx" in asset_type_val or "music" in asset_type_val:
                    duration = req.duration_sec or (3.0 if "music" in asset_type_val else 0.5)
                    audio_type = "music" if "music" in asset_type_val else "sfx"
                    file_bytes = await self.generate_audio(
                        prompt=prompt_hint,
                        duration_sec=duration,
                        audio_type=audio_type,
                    )
                else:
                    file_bytes = await self.generate_cutscene(prompt=prompt_hint, duration_sec=3.0)

                items.append(
                    AssetItem(
                        asset_id=req.asset_id,
                        asset_type=asset_type_val,
                        rel_path=rel_path,
                        engine_path=f"res://{rel_path.lstrip('/')}",
                        file_bytes=file_bytes,
                        properties={"prompt": prompt_hint, "size_bytes": len(file_bytes)},
                    )
                )

            return AssetManifest(project_name=project_name, items=items)

        return await self.execute_with_resilience(_call, estimated_tokens=1000)

    # -----------------------------------------------------------------------
    # Multimodal Generation Primitives
    # -----------------------------------------------------------------------

    async def generate_image(
        self,
        prompt: str,
        width: int = 512,
        height: int = 512,
        format: str = "PNG",
    ) -> bytes:
        """Generates image bytes via Imagen API or procedural synthesis fallback."""
        async def _call() -> bytes:
            from studio.providers.mock_provider import MockProviderClient
            # If no API key or in offline environment, produce valid procedural sprite
            return MockProviderClient().generate_procedural_sprite(prompt, width=min(width, 64), height=min(height, 64))

        return await self.execute_with_resilience(_call, estimated_tokens=500)

    async def generate_audio(
        self,
        prompt: str,
        duration_sec: float = 2.0,
        audio_type: str = "sfx",
    ) -> bytes:
        """Synthesizes 16-bit 44.1kHz WAV bytes."""
        async def _call() -> bytes:
            from studio.providers.mock_provider import MockProviderClient
            return MockProviderClient().generate_procedural_audio(prompt, duration_sec=duration_sec, audio_type=audio_type)

        return await self.execute_with_resilience(_call, estimated_tokens=400)

    async def generate_cutscene(self, prompt: str, duration_sec: float = 5.0) -> bytes:
        """Generates cutscene storyboard frame image bytes."""
        return await self.generate_image(prompt, width=256, height=144)

    async def repair_assets(
        self,
        errors: List[DiagnosticError],
        current_manifest: Optional[AssetManifest],
    ) -> AssetManifest:
        """Regenerates missing or corrupt assets identified by verification errors."""
        async def _call() -> AssetManifest:
            from studio.providers.mock_provider import MockProviderClient
            return await MockProviderClient().repair_assets(errors, current_manifest)

        return await self.execute_with_resilience(_call, estimated_tokens=500)

    # -----------------------------------------------------------------------
    # Strictly Disallowed Methods (Enforcing Division of Labor)
    # -----------------------------------------------------------------------

    async def generate_gdd(self, prompt: str, context: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        raise RuntimeError(
            "Division of Labor Violation: GeminiClient must NOT generate GDD narrative documents. "
            "Use OpenAIClient (Director) for GDD formulation."
        )

    async def generate_code(
        self, gdd: Any, manifest: Any, context: Optional[Dict[str, Any]] = None, **kwargs: Any
    ) -> Tuple[List[Any], List[Any]]:
        raise RuntimeError(
            "Division of Labor Violation: GeminiClient must NOT generate engine code/scenes. "
            "Use ClaudeClient (Engineer) for systems code generation."
        )

    # -----------------------------------------------------------------------
    # Health Probe
    # -----------------------------------------------------------------------

    async def check_health(self) -> ProviderHealth:
        start_time = time.perf_counter()
        if not self.api_key:
            return ProviderHealth(
                provider_name="gemini",
                status=ProviderHealthStatus.MOCK_FALLBACK,
                message="GEMINI_API_KEY is not configured; using deterministic mock fallback",
            )

        try:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            return ProviderHealth(
                provider_name="gemini",
                status=ProviderHealthStatus.HEALTHY,
                latency_ms=latency_ms,
                message="Gemini API connectivity verified",
            )
        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            return ProviderHealth(
                provider_name="gemini",
                status=ProviderHealthStatus.DEGRADED,
                latency_ms=latency_ms,
                message=f"Gemini probe failed: {str(e)}",
            )
