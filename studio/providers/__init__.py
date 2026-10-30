"""
studio/providers - Specialized AI frontier clients and deterministic mock provider.
Strictly divides labor across OpenAI, Gemini, and Claude.
"""

from studio.providers.base import (
    BaseProviderClient,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderMetrics,
    ProviderResponse,
)
from studio.providers.claude_client import ClaudeClient
from studio.providers.gemini_client import GeminiClient
from studio.providers.mock_provider import (
    MockAudioGenerator,
    MockCodeGenerator,
    MockGDDGenerator,
    MockProviderClient,
    MockSpriteGenerator,
    derive_seed,
    detect_archetype,
)
from studio.providers.openai_client import OpenAIClient

__all__ = [
    "BaseProviderClient",
    "ProviderHealth",
    "ProviderHealthStatus",
    "ProviderMetrics",
    "ProviderResponse",
    "OpenAIClient",
    "GeminiClient",
    "ClaudeClient",
    "MockProviderClient",
    "MockGDDGenerator",
    "MockSpriteGenerator",
    "MockAudioGenerator",
    "MockCodeGenerator",
    "derive_seed",
    "detect_archetype",
]
