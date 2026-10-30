"""
studio/core/security.py - Credential Management, Secret Masking & Provider Resilience
Enforces API key isolation, regex log sanitization, token bucket rate limiting,
exponential backoff with full jitter, and circuit breaking (F-ORCH-06).
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("studio.core.security")


# ============================================================================
# Credential Manager
# ============================================================================

class CredentialManager:
    """
    Secure environment credential resolver supporting:
    1. Runtime user-provided overrides (UI / Directing Panel)
    2. OS Environment variables
    3. Root-level .env file
    """

    KEY_NAMES = {
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
    }

    def __init__(self, env_path: Optional[Union[str, Path]] = None) -> None:
        self._runtime_overrides: Dict[str, str] = {}
        self._dotenv_values: Dict[str, str] = {}
        if env_path:
            p = Path(env_path)
            if p.is_file():
                self._load_dotenv(p)

    def _load_dotenv(self, path: Path) -> None:
        """Parses a simple KEY=VALUE .env file without external dependencies."""
        try:
            content = path.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    self._dotenv_values[key] = val
        except Exception as e:
            logger.warning(f"Failed to load .env from {path}: {e}")

    def set_override(self, provider: str, key_value: str) -> None:
        """Inject runtime key override."""
        self._runtime_overrides[provider.lower()] = key_value.strip()

    def get_key(self, provider: str) -> Optional[str]:
        """Resolve key following hierarchy: Runtime Override -> os.environ -> .env"""
        prov = provider.lower()
        if prov in self._runtime_overrides and self._runtime_overrides[prov]:
            return self._runtime_overrides[prov]

        env_var = self.KEY_NAMES.get(prov, f"{prov.upper()}_API_KEY")
        val = os.environ.get(env_var)
        if val and val.strip():
            return val.strip()

        return self._dotenv_values.get(env_var)

    def is_configured(self, provider: str) -> bool:
        """Checks if a valid-length API key is configured for the provider."""
        key = self.get_key(provider)
        return bool(key and len(key) >= 8)

    def has_all_frontier_keys(self) -> bool:
        """Returns True if all three frontier providers (Claude, Gemini, OpenAI) have keys."""
        return all(self.is_configured(p) for p in ("anthropic", "gemini", "openai"))


# ============================================================================
# Secret Masker & Logging Filter
# ============================================================================

class SecretMasker:
    """
    Regex-based redactor preventing raw API keys from leaking into
    console logs, file outputs, WebSocket telemetry, or exception traces.
    """

    PATTERNS: List[Tuple[re.Pattern, str]] = [
        # Anthropic: sk-ant-...
        (re.compile(r"sk-ant-[a-zA-Z0-9_\-]+"), r"sk-ant-...[REDACTED]"),
        # OpenAI Project: sk-proj-...
        (re.compile(r"sk-proj-[a-zA-Z0-9_\-]+"), r"sk-proj-...[REDACTED]"),
        # OpenAI Standard: sk-... (excluding sk-ant and sk-proj)
        (re.compile(r"sk-(?!ant|proj)[a-zA-Z0-9_\-]+"), r"sk-...[REDACTED]"),
        # Google Gemini: AIzaSy...
        (re.compile(r"AIzaSy[a-zA-Z0-9_\-]+"), r"AIzaSy...[REDACTED]"),
        # Bearer tokens
        (re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]+"), r"Bearer ...[REDACTED]"),
        # JSON key patterns: "api_key": "..."
        (re.compile(r'("(?:api_key|apiKey|token|secret)":\s*")([^"]+)(")'), r'\1[REDACTED]\3'),
    ]

    @classmethod
    def mask(cls, text: str) -> str:
        if not text:
            return text
        result = text
        for pattern, replacement in cls.PATTERNS:
            result = pattern.sub(replacement, result)
        return result


class SecretMaskingFilter(logging.Filter):
    """Logging filter that sanitizes all log records before output."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = SecretMasker.mask(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: SecretMasker.mask(str(v)) for k, v in record.args.items()}
            elif isinstance(record.args, (list, tuple)):
                record.args = tuple(SecretMasker.mask(str(v)) for v in record.args)
        return True


# ============================================================================
# Token Bucket Rate Limiter
# ============================================================================

class TokenBucketRateLimiter:
    """
    Asyncio-safe dual-token bucket enforcing:
    - Requests Per Minute (RPM)
    - Tokens Per Minute (TPM)
    Replenishes continuously based on fractional elapsed time.
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        tokens_per_minute: int = 50000,
    ) -> None:
        self.rpm = requests_per_minute
        self.tpm = tokens_per_minute

        self.req_capacity = float(requests_per_minute)
        self.req_tokens = float(requests_per_minute)
        self.req_refill_rate = float(requests_per_minute) / 60.0

        self.token_capacity = float(tokens_per_minute)
        self.tokens = float(tokens_per_minute)
        self.token_refill_rate = float(tokens_per_minute) / 60.0

        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.last_refill = now

        self.req_tokens = min(self.req_capacity, self.req_tokens + elapsed * self.req_refill_rate)
        self.tokens = min(self.token_capacity, self.tokens + elapsed * self.token_refill_rate)

    async def acquire(self, tokens: int = 1, timeout: float = 30.0) -> bool:
        """
        Asynchronously acquires 1 request token and specified estimated LLM tokens.
        Sleeps non-blockingly until tokens replenish or timeout expires.
        """
        deadline = time.monotonic() + timeout
        while True:
            async with self._lock:
                self._refill()
                if self.req_tokens >= 1.0 and self.tokens >= tokens:
                    self.req_tokens -= 1.0
                    self.tokens -= tokens
                    return True

                req_wait = (
                    (1.0 - self.req_tokens) / self.req_refill_rate
                    if self.req_tokens < 1.0
                    else 0.0
                )
                tok_wait = (
                    (tokens - self.tokens) / self.token_refill_rate
                    if self.tokens < tokens
                    else 0.0
                )
                wait_time = max(req_wait, tok_wait)

            if time.monotonic() + wait_time > deadline:
                return False

            await asyncio.sleep(min(wait_time, 0.5))


# ============================================================================
# Exponential Backoff with Full Jitter
# ============================================================================

class ExponentialBackoff:
    """
    Implements Full Jitter exponential backoff:
    sleep = uniform(0.1, min(max_backoff, base_delay * 2^attempt))
    Properly detects retryable errors (HTTP 429, 500, 502, 503, 504, ConnectionDrop).
    """

    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        max_retries: int = 5,
        base_delay: float = 1.0,
        max_backoff: float = 30.0,
    ) -> None:
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_backoff = max_backoff

    def is_retryable(self, exc: Exception) -> bool:
        status = getattr(exc, "status_code", getattr(exc, "status", None))
        if status in self.RETRYABLE_STATUS_CODES:
            return True
        exc_str = str(exc).lower()
        if "rate limit" in exc_str or "429" in exc_str or "quota" in exc_str:
            return True
        if "timeout" in exc_str or "connection reset" in exc_str or "temporarily unavailable" in exc_str:
            return True
        return False

    async def execute(self, coro_func: Any, *args: Any, **kwargs: Any) -> Any:
        attempt = 0
        while True:
            try:
                return await coro_func(*args, **kwargs)
            except Exception as exc:
                attempt += 1
                if attempt > self.max_retries or not self.is_retryable(exc):
                    raise exc

                capped_delay = min(self.max_backoff, self.base_delay * (2 ** (attempt - 1)))
                delay = random.uniform(0.1, max(0.1, capped_delay))

                retry_after = getattr(exc, "retry_after", None)
                if retry_after is not None:
                    try:
                        delay = max(delay, float(retry_after))
                    except (ValueError, TypeError):
                        pass

                await asyncio.sleep(delay)


# ============================================================================
# Circuit Breaker
# ============================================================================

class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    Tri-state circuit breaker protecting the application from cascading provider failures.
    - CLOSED: Normal operation.
    - OPEN: Tripped after failure_threshold consecutive errors; fast-fails for recovery_timeout.
    - HALF_OPEN: Probes provider with a single request.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_state_change = time.monotonic()
        self._lock = asyncio.Lock()

    def can_execute(self) -> bool:
        now = time.monotonic()
        if self.state == CircuitState.CLOSED:
            return True
        elif self.state == CircuitState.OPEN:
            if now - self.last_state_change >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.last_state_change = now
                return True
            return False
        elif self.state == CircuitState.HALF_OPEN:
            return True
        return False

    def record_success(self) -> None:
        self.failure_count = 0
        self.state = CircuitState.CLOSED
        self.last_state_change = time.monotonic()

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.last_state_change = time.monotonic()
