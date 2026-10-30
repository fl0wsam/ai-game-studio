"""
studio/providers/base.py - Abstract Provider Client Interface & Resilience Harness
Defines the base contract for all specialized AI clients (OpenAI, Gemini, Claude, Mock).
Enforces role boundaries, rate limiting, circuit breaking, exponential backoff, and metrics.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class ProviderHealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    MOCK_FALLBACK = "mock_fallback"


class ProviderHealth(BaseModel):
    provider_name: str
    status: ProviderHealthStatus
    latency_ms: float = 0.0
    message: str = "OK"
    timestamp: float = Field(default_factory=time.time)


class ProviderMetrics(BaseModel):
    provider_name: str
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    tokens_prompt: int = 0
    tokens_completion: int = 0
    total_latency_ms: float = 0.0
    rate_limit_hits: int = 0
    circuit_breaker_trips: int = 0

    @property
    def avg_latency_ms(self) -> float:
        if self.successful_requests == 0:
            return 0.0
        return self.total_latency_ms / self.successful_requests


class ProviderResponse(BaseModel):
    provider_name: str
    model_name: str
    content: Any
    tokens_prompt: int = 0
    tokens_completion: int = 0
    latency_ms: float = 0.0
    is_mock: bool = False
    raw_response: Optional[Dict[str, Any]] = None


class BaseProviderClient(ABC):
    """
    Abstract base class for all AI studio providers (OpenAI, Gemini, Claude, Mock).
    Enforces specialized division of labor and wraps executions with rate limiting,
    circuit breakers, exponential backoff, and metrics instrumentation.
    """

    def __init__(
        self,
        provider_name: str,
        model_name: str,
        api_key: Optional[str] = None,
        rate_limiter: Optional[Any] = None,       # TokenBucketRateLimiter
        circuit_breaker: Optional[Any] = None,    # CircuitBreaker
        backoff_handler: Optional[Any] = None,    # ExponentialBackoff
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.api_key = api_key
        self.rate_limiter = rate_limiter
        self.circuit_breaker = circuit_breaker
        self.backoff_handler = backoff_handler
        self.metrics = ProviderMetrics(provider_name=provider_name)

    # -----------------------------------------------------------------------
    # Core Domain Methods (Enforcing Division of Labor)
    # -----------------------------------------------------------------------

    @abstractmethod
    async def generate_gdd(
        self,
        prompt: str,
        context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        """
        Generate a structured GameDesignDocument from a game premise.
        Specialized Provider: OpenAIClient.
        """
        pass

    @abstractmethod
    async def generate_assets(
        self,
        gdd: Any,  # GameDesignDocument
        context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:  # AssetManifest
        """
        Generate visual and audio assets based on GDD requirements.
        Specialized Provider: GeminiClient.
        """
        pass

    @abstractmethod
    async def generate_code(
        self,
        gdd: Any,       # GameDesignDocument
        manifest: Any,  # AssetManifest
        context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Tuple[List[Any], List[Any]]:  # (List[SceneAST], List[ScriptSpec])
        """
        Generate engine scripts, mechanics, and scene hierarchy trees.
        Specialized Provider: ClaudeClient.
        """
        pass

    @abstractmethod
    async def check_health(self) -> ProviderHealth:
        """
        Probe provider availability, credential status, and API ping.
        """
        pass

    # -----------------------------------------------------------------------
    # Primitive Generation Methods
    # -----------------------------------------------------------------------

    async def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Generate raw text or code output."""
        raise NotImplementedError(f"{self.provider_name} does not implement generate_text")

    async def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
    ) -> T:
        """Generate structured output adhering to a Pydantic model schema."""
        raise NotImplementedError(f"{self.provider_name} does not implement generate_structured")

    async def generate_image(
        self,
        prompt: str,
        width: int = 512,
        height: int = 512,
        format: str = "PNG",
    ) -> bytes:
        """Generate binary image bytes (PNG/JPG)."""
        raise NotImplementedError(f"{self.provider_name} does not implement generate_image")

    async def generate_audio(
        self,
        prompt: str,
        duration_sec: float = 2.0,
        audio_type: str = "sfx",
    ) -> bytes:
        """Generate binary audio bytes (WAV)."""
        raise NotImplementedError(f"{self.provider_name} does not implement generate_audio")

    # -----------------------------------------------------------------------
    # Resilience Execution Wrapper
    # -----------------------------------------------------------------------

    async def execute_with_resilience(
        self,
        coro_func: Any,
        *args: Any,
        estimated_tokens: int = 500,
        **kwargs: Any,
    ) -> Any:
        """
        Executes a network coroutine through RateLimiter, CircuitBreaker,
        and ExponentialBackoff while capturing telemetry metrics.
        """
        start_time = time.perf_counter()
        self.metrics.total_requests += 1

        # 1. Rate limiter check
        if self.rate_limiter:
            acquired = await self.rate_limiter.acquire(tokens=estimated_tokens, timeout=30.0)
            if not acquired:
                self.metrics.rate_limit_hits += 1
                raise RuntimeError(f"Rate limit exceeded for provider {self.provider_name}")

        # 2. Circuit breaker check
        if self.circuit_breaker and not self.circuit_breaker.can_execute():
            self.metrics.circuit_breaker_trips += 1
            raise RuntimeError(f"Circuit breaker is OPEN for provider {self.provider_name}")

        # 3. Execution with exponential backoff
        try:
            if self.backoff_handler:
                result = await self.backoff_handler.execute(coro_func, *args, **kwargs)
            else:
                result = await coro_func(*args, **kwargs)

            latency_ms = (time.perf_counter() - start_time) * 1000.0
            self.metrics.successful_requests += 1
            self.metrics.total_latency_ms += latency_ms

            if self.circuit_breaker:
                self.circuit_breaker.record_success()

            return result

        except Exception as exc:
            self.metrics.failed_requests += 1
            if self.circuit_breaker:
                self.circuit_breaker.record_failure()
            raise exc
