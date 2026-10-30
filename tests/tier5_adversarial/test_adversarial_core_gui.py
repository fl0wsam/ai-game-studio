"""
tests/tier5_adversarial/test_adversarial_core_gui.py - Tier 5 Adversarial Stress & Vulnerability Suite
Empirically stress-tests Core Orchestration and GUI subsystems:
1. EventBus stress: wildcard topics, deep recursion cascading, rapid unsubscriptions,
   subscriber exception isolation, and ReDoS resistance.
2. Blackboard concurrency: parallel asynchronous state writes, high-volume token usage updates,
   concurrent read/write consistency without torn reads, and rapid rollback under contention.
3. Security: rate limiter token exhaustion, oversized token requests, rapid circuit breaker tripping,
   half-open concurrency leaks, and secret masking under corrupted/unusual log records.
4. WebSocket Telemetry: rapid burst connections/disconnections, high-throughput message streaming,
   slow client isolation / head-of-line blocking, and backpressure resilience.
5. REST API boundary & fuzzing: invalid payload injections on /api/direct, path traversal probing
   on /api/project/assets/{id}, illegal output paths on /api/project/export, and verifier fuzzing.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import os
import pathlib
import re
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from studio.core.blackboard import Blackboard
from studio.core.event_bus import EventBus, topic_matches
from studio.core.models import (
    AgentRole,
    AssetItem,
    AssetManifest,
    AssetType,
    EngineTarget,
    GameDesignDocument,
    GuiTelemetryEvent,
    NodeAST,
    ProjectStatus,
    ReviewFeedback,
    SceneAST,
    ScriptSpec,
    StudioProjectState,
    TokenUsageMetrics,
    VerificationReport,
)
from studio.core.security import (
    CircuitBreaker,
    CircuitState,
    CredentialManager,
    SecretMasker,
    SecretMaskingFilter,
    TokenBucketRateLimiter,
)
from studio.engine.ast_models import ExtResourceAST, NodeAST as EngineNodeAST, SceneAST as EngineSceneAST
from studio.engine.hierarchy import CleanHierarchyPackager
from studio.gui.routes import router as api_router
from studio.gui.server import create_app
from studio.gui.websocket_manager import WebSocketTelemetryManager


# ============================================================================
# PART 1: EventBus Adversarial & Stress Testing
# ============================================================================

class TestAdversarialEventBus(unittest.IsolatedAsyncioTestCase):
    """Adversarially probes EventBus pattern matching, cascading dispatch, and concurrency."""

    async def asyncSetUp(self) -> None:
        self.bus = EventBus(telemetry_buffer_size=100)

    async def asyncTearDown(self) -> None:
        await self.bus.close()

    def test_eventbus_wildcard_redos_exponential_backtracking(self) -> None:
        """
        Adversarially stresses the double-wildcard recursive matcher with multiple '**' segments
        against non-matching inputs.
        Verifies execution time remains bounded and checks for combinatorial blowup.
        """
        # Complex pattern with multiple wildcards
        pattern = "prefix.**.mid1.**.mid2.**.target"
        
        # 1. Matching string with many intermediate segments
        matching_topic = "prefix.a.b.c.mid1.d.e.f.mid2.g.h.target"
        t0 = time.perf_counter()
        match_res = topic_matches(pattern, matching_topic)
        elapsed_match = time.perf_counter() - t0
        self.assertTrue(match_res)
        self.assertLess(elapsed_match, 0.2, f"Matching took too long: {elapsed_match:.4f}s")

        # 2. Non-matching string that forces extensive backtracking
        non_matching_topic = "prefix.a.b.c.mid1.d.e.f.mid2.g.h.mismatch"
        t0 = time.perf_counter()
        non_match_res = topic_matches(pattern, non_matching_topic)
        elapsed_non_match = time.perf_counter() - t0
        self.assertFalse(non_match_res)
        self.assertLess(elapsed_non_match, 0.5, f"Non-matching backtracking took too long: {elapsed_non_match:.4f}s")

        # 3. Boundary & edge cases
        self.assertTrue(topic_matches("#", "deeply.nested.topic.structure"))
        self.assertTrue(topic_matches("**", "deeply.nested.topic.structure"))
        self.assertTrue(topic_matches("", ""))
        self.assertTrue(topic_matches("..", ""))
        self.assertTrue(topic_matches("telemetry...event", "telemetry.event"))

    async def test_eventbus_deep_recursion_cascading_events(self) -> None:
        """
        Tests deep cascading event propagation where subscriber N triggers subscriber N+1.
        Verifies that deep asynchronous pub/sub chains do not cause stack overflow,
        RecursionError, or deadlock on EventBus locks.
        """
        cascade_depth = 40
        received_levels: List[int] = []

        def make_handler(level: int):
            async def handler(event_data: Dict[str, Any]) -> None:
                received_levels.append(level)
                if level < cascade_depth:
                    await self.bus.publish(f"cascade.step.{level + 1}", {"level": level + 1})
            return handler

        for i in range(1, cascade_depth + 1):
            self.bus.subscribe(f"cascade.step.{i}", make_handler(i))

        # Trigger the cascade
        await self.bus.publish("cascade.step.1", {"level": 1})

        self.assertEqual(len(received_levels), cascade_depth)
        self.assertEqual(received_levels, list(range(1, cascade_depth + 1)))

    async def test_eventbus_high_throughput_burst_flood(self) -> None:
        """
        Floods 2,000 events across 50 subscribers with overlapping wildcard patterns.
        Verifies zero dropped events, accurate delivery counts, and ring-buffer boundedness.
        """
        num_subscribers = 50
        num_publishers = 10
        events_per_publisher = 200
        total_events = num_publishers * events_per_publisher

        delivery_counter = 0
        counter_lock = asyncio.Lock()

        async def counting_handler(event: Any) -> None:
            nonlocal delivery_counter
            async with counter_lock:
                delivery_counter += 1

        patterns = ["telemetry.*", "agent.**", "pipeline.*.status", "#", "*"]
        sub_ids = []
        for i in range(num_subscribers):
            p = patterns[i % len(patterns)]
            sid = self.bus.subscribe(p, counting_handler)
            sub_ids.append(sid)

        async def publisher(pub_id: int) -> None:
            for j in range(events_per_publisher):
                topic = f"telemetry.event_{pub_id}_{j}"
                telem = GuiTelemetryEvent(
                    event_type="test_event",
                    agent="benchmark",
                    data={"pub_id": pub_id, "idx": j},
                    timestamp=time.time(),
                )
                await self.bus.emit_telemetry(telem)

        publishers = [asyncio.create_task(publisher(k)) for k in range(num_publishers)]
        await asyncio.gather(*publishers)

        # Ensure deliveries occurred across subscribers
        self.assertGreater(delivery_counter, total_events)

        # Buffer must be capped at 100 as configured in asyncSetUp
        history = self.bus.get_telemetry_history(limit=500)
        self.assertEqual(len(history), 100)

    async def test_eventbus_rapid_concurrent_unsubscription_during_flood(self) -> None:
        """
        Subscribes 60 handlers that dynamically unsubscribe themselves and sibling subscribers
        while concurrent publishers flood messages.
        Verifies thread safety: no RuntimeError ("dictionary changed size during iteration")
        and no deadlocks.
        """
        num_subscribers = 60
        sub_ids: List[str] = []
        received_counts = 0
        lock = asyncio.Lock()

        def make_handler(idx: int):
            async def handler(event: Any) -> None:
                nonlocal received_counts
                async with lock:
                    received_counts += 1
                # Rapidly mutate subscriptions dictionary during dispatch
                target_idx = (idx + 1) % num_subscribers
                if target_idx < len(sub_ids):
                    self.bus.unsubscribe(sub_ids[target_idx])
            return handler

        for i in range(num_subscribers):
            sid = self.bus.subscribe("stress.unsubscribe.topic", make_handler(i))
            sub_ids.append(sid)

        async def publisher():
            for k in range(100):
                await self.bus.publish("stress.unsubscribe.topic", {"tick": k})
                if k == 50:
                    self.bus.clear_subscribers("stress.unsubscribe.topic")

        await asyncio.gather(publisher(), publisher())
        self.assertGreater(received_counts, 0)

    async def test_eventbus_subscriber_exception_and_cancellation_isolation(self) -> None:
        """
        Tests that exceptions thrown by crashing or malicious handlers
        (ZeroDivisionError, KeyError, RuntimeError, unhandled async exception)
        do not prevent healthy subscribers on the same topic from executing.
        """
        healthy_results: List[int] = []

        def failing_zero_div(e):
            return 1 / 0

        def failing_key_err(e):
            raise KeyError("missing_key_in_malicious_handler")

        def failing_runtime(e):
            raise RuntimeError("Fatal subscriber crash")

        async def failing_async(e):
            raise ValueError("Async handler failure")

        def healthy_sync(e):
            healthy_results.append(e.get("val", 0))

        async def healthy_async(e):
            healthy_results.append(e.get("val", 0) * 10)

        self.bus.subscribe("isolation.test", failing_zero_div)
        self.bus.subscribe("isolation.test", failing_key_err)
        self.bus.subscribe("isolation.test", failing_runtime)
        self.bus.subscribe("isolation.test", failing_async)
        self.bus.subscribe("isolation.test", healthy_sync)
        self.bus.subscribe("isolation.test", healthy_async)

        for i in range(10):
            await self.bus.publish("isolation.test", {"val": i})

        # Both healthy sync and healthy async should have executed 10 times each
        self.assertEqual(len(healthy_results), 20)
        self.assertEqual(healthy_results.count(5), 1)
        self.assertEqual(healthy_results.count(50), 1)


# ============================================================================
# PART 2: Blackboard Concurrency & Consistency Stress
# ============================================================================

class TestAdversarialBlackboardConcurrency(unittest.IsolatedAsyncioTestCase):
    """Adversarially stresses Blackboard concurrency, state consistency, and rollback."""

    async def asyncSetUp(self) -> None:
        self.bb = Blackboard(
            project_name="AdversarialStudioProject",
            target_engine=EngineTarget.GODOT4.value,
            user_prompt="Stress test project prompt",
            max_revisions=500,
        )

    async def test_blackboard_high_concurrency_parallel_async_state_writes(self) -> None:
        """
        Spawns 50 parallel asynchronous tasks mutating distinct aspects of project state
        simultaneously: scenes, scripts, review feedback, asset items, status, and logs.
        Verifies:
        - All 50 scenes, scripts, and asset items are successfully registered.
        - Revisions monotonically increase without duplicate IDs.
        - State dictionary maintains integrity without corrupted entries.
        """
        num_tasks = 50

        async def worker(task_id: int) -> None:
            # 1. Add Scene
            scene = SceneAST(
                name=f"Level_{task_id}",
                path=f"res://scenes/level_{task_id}.tscn",
                root_node_type="Node2D",
            )
            await self.bb.add_scene(scene)

            # 2. Add Script
            script = ScriptSpec(
                rel_path=f"scripts/actor_{task_id}.gd",
                language="gdscript",
                code=f"extends Node2D\n# Task {task_id}\n",
            )
            await self.bb.add_script(script)

            # 3. Add Asset Item
            asset = AssetItem(
                asset_id=f"sprite_{task_id}",
                asset_type="sprite",
                rel_path=f"assets/sprites/sprite_{task_id}.png",
                file_bytes=b"dummy_png_bytes",
            )
            await self.bb.add_asset_item(asset)

            # 4. Add Review Feedback
            feedback = ReviewFeedback(
                reviewer=AgentRole.VALIDATOR,
                target_role=AgentRole.ENGINEER,
                target_artifact=f"scene_{task_id}",
                approved=True,
                comments=f"Approved worker {task_id}",
            )
            await self.bb.add_review_feedback(feedback)

            # 5. Add Log
            await self.bb.add_log(f"worker_{task_id}", "INFO", f"Worker {task_id} done")

            # 6. Update Status
            await self.bb.set_status(ProjectStatus.IN_PROGRESS, active_phase=f"PHASE_{task_id}")

        tasks = [asyncio.create_task(worker(i)) for i in range(num_tasks)]
        await asyncio.gather(*tasks)

        # Empirical verifications
        state = await self.bb.get_state()
        self.assertEqual(len(state.scenes), num_tasks)
        self.assertEqual(len(state.scripts), num_tasks)
        self.assertIsNotNone(state.asset_manifest)
        self.assertEqual(len(state.asset_manifest.items), num_tasks)
        self.assertEqual(len(state.review_history), num_tasks)

        # Revision history monotonicity
        history = self.bb.get_revision_history()
        rev_ids = [r[0] for r in history]
        self.assertEqual(rev_ids, sorted(rev_ids), "Revision IDs must be strictly sorted")
        self.assertEqual(len(rev_ids), len(set(rev_ids)), "Duplicate revision IDs detected")

    async def test_blackboard_high_volume_token_usage_contention(self) -> None:
        """
        100 concurrent async tasks record token metrics across 3 frontier providers
        (10 updates per task = 1,000 updates).
        Verifies atomic arithmetic accumulation without race conditions or lost updates.
        """
        num_tasks = 100
        updates_per_task = 10
        providers = ["openai", "claude", "gemini"]

        expected_tokens: Dict[str, Dict[str, Any]] = {
            p: {"prompt": 0, "completion": 0, "requests": 0, "cost": 0.0}
            for p in providers
        }

        # Deterministic accounting
        for i in range(num_tasks):
            for j in range(updates_per_task):
                p = providers[(i + j) % len(providers)]
                p_tok = 10 + (i % 5)
                c_tok = 20 + (j % 5)
                cost = 0.0005
                expected_tokens[p]["prompt"] += p_tok
                expected_tokens[p]["completion"] += c_tok
                expected_tokens[p]["requests"] += 1
                expected_tokens[p]["cost"] += cost

        async def worker(task_id: int) -> None:
            for j in range(updates_per_task):
                p = providers[(task_id + j) % len(providers)]
                p_tok = 10 + (task_id % 5)
                c_tok = 20 + (j % 5)
                await self.bb.record_token_metrics(
                    provider=p,
                    prompt_tokens=p_tok,
                    completion_tokens=c_tok,
                    cost_usd=0.0005,
                )

        tasks = [asyncio.create_task(worker(i)) for i in range(num_tasks)]
        await asyncio.gather(*tasks)

        metrics = await self.bb.get_token_metrics()
        for p in providers:
            self.assertIn(p, metrics)
            m = metrics[p]
            exp = expected_tokens[p]
            self.assertEqual(m.request_count, exp["requests"], f"Provider {p} request count mismatch")
            self.assertEqual(m.prompt_tokens, exp["prompt"], f"Provider {p} prompt tokens mismatch")
            self.assertEqual(m.completion_tokens, exp["completion"], f"Provider {p} completion tokens mismatch")
            self.assertEqual(m.total_tokens, exp["prompt"] + exp["completion"])
            self.assertAlmostEqual(m.estimated_cost_usd, exp["cost"], places=4)

    async def test_blackboard_concurrent_read_write_consistency_no_torn_reads(self) -> None:
        """
        Tests high-frequency readers running in parallel with continuous mutating writers.
        Verifies:
        - Snapshots returned by get_state() are isolated deep copies.
        - Mutating a returned snapshot object never corrupts Blackboard's internal state.
        - Readers never observe partially updated or torn state.
        """
        num_writers = 20
        num_readers = 20
        torn_read_detected = False

        async def writer(w_id: int) -> None:
            for i in range(15):
                script = ScriptSpec(
                    rel_path=f"scripts/worker_{w_id}_{i}.gd",
                    language="gdscript",
                    code=f"extends Node\nvar id = {w_id}_{i}\n",
                )
                await self.bb.add_script(script)
                await asyncio.sleep(0.001)

        async def reader(r_id: int) -> None:
            nonlocal torn_read_detected
            for _ in range(15):
                state = await self.bb.get_state()
                scripts = await self.bb.get_scripts()
                export = await self.bb.export_dict()

                # Internal consistency invariant
                if len(scripts) != len(export.get("scripts", {})):
                    torn_read_detected = True

                # Mutate returned state locally to verify isolation
                state.project_name = "CORRUPTED_NAME"
                state.scripts["evil"] = ScriptSpec(rel_path="evil.gd", language="gdscript", code="")

                await asyncio.sleep(0.001)

        writer_tasks = [asyncio.create_task(writer(i)) for i in range(num_writers)]
        reader_tasks = [asyncio.create_task(reader(i)) for i in range(num_readers)]
        await asyncio.gather(*(writer_tasks + reader_tasks))

        self.assertFalse(torn_read_detected, "Torn read detected across concurrent operations")

        # Verify internal project_name was never altered by rogue readers
        clean_state = await self.bb.get_state()
        self.assertEqual(clean_state.project_name, "AdversarialStudioProject")
        self.assertNotIn("evil", clean_state.scripts)

    async def test_blackboard_rapid_rollback_under_high_write_pressure(self) -> None:
        """
        Performs concurrent state writes while tasks simultaneously invoke rollback_to_revision().
        Verifies that rollbacks do not cause deadlocks or corrupt history tracking.
        """
        # Create baseline state
        await self.bb.add_script(
            ScriptSpec(rel_path="scripts/baseline.gd", language="gdscript", code="# baseline")
        )
        history = self.bb.get_revision_history()
        baseline_rev = history[-1][0]

        async def writer(w_id: int) -> None:
            for k in range(10):
                await self.bb.add_script(
                    ScriptSpec(rel_path=f"scripts/temp_{w_id}_{k}.gd", language="gdscript", code="# temp")
                )

        async def rollbacker() -> None:
            await asyncio.sleep(0.005)
            await self.bb.rollback_to_revision(baseline_rev)

        writers = [asyncio.create_task(writer(i)) for i in range(15)]
        rollbackers = [asyncio.create_task(rollbacker()) for _ in range(3)]
        await asyncio.gather(*(writers + rollbackers))

        # Check blackboard remains healthy and responsive
        state = await self.bb.get_state()
        self.assertIsNotNone(state)
        self.assertIn("scripts/baseline.gd", state.scripts)


# ============================================================================
# PART 3: Security & Provider Resilience Stress
# ============================================================================

class TestAdversarialSecurityResilience(unittest.IsolatedAsyncioTestCase):
    """Adversarially probes rate limiters, circuit breakers, and secret masking."""

    async def test_rate_limiter_token_exhaustion_and_burst(self) -> None:
        """
        Exhausts rate limiter tokens in a rapid burst and verifies subsequent acquisitions
        are immediately rejected until capacity replenishes.
        """
        limiter = TokenBucketRateLimiter(requests_per_minute=30, tokens_per_minute=300)
        
        # Acquire all 30 request tokens
        acquired_count = 0
        for _ in range(30):
            res = await limiter.acquire(tokens=10, timeout=0.01)
            if res:
                acquired_count += 1

        self.assertEqual(acquired_count, 30)

        # Next acquisition must fail fast due to exhausted tokens
        exhausted_res = await limiter.acquire(tokens=10, timeout=0.01)
        self.assertFalse(exhausted_res, "Acquisition should fail when token bucket is exhausted")

        # Wait small duration for partial refill (0.1s refilled 0.05 reqs and 0.5 tokens)
        await asyncio.sleep(0.1)
        # Still not enough for 1 full request token
        self.assertFalse(await limiter.acquire(tokens=1, timeout=0.01))

    async def test_rate_limiter_oversized_token_request_starvation(self) -> None:
        """
        Requests tokens greater than total bucket capacity (tokens > token_capacity).
        Confirms the request cannot be satisfied, and empirically demonstrates:
        1. Fast-fail (< 10ms) when calculated wait_time exceeds timeout.
        2. Starvation sleep loop when calculated wait_time <= timeout, causing caller to sleep
           for a request that can never succeed because tokens are clamped to capacity.
        """
        # Case 1: Fast-fail when wait_time (60s) > timeout (0.05s)
        limiter_slow = TokenBucketRateLimiter(requests_per_minute=60, tokens_per_minute=500)
        t0 = time.monotonic()
        res_fast = await limiter_slow.acquire(tokens=1000, timeout=0.05)
        elapsed_fast = time.monotonic() - t0

        self.assertFalse(res_fast, "Requesting tokens > capacity must return False")
        self.assertLess(elapsed_fast, 0.02, f"Expected fast-fail (<20ms), took {elapsed_fast:.4f}s")

        # Case 2: Starvation sleep when wait_time (0.04s) <= timeout (0.10s)
        # Capacity is 60,000; refill rate is 1,000 tok/sec
        limiter_fast = TokenBucketRateLimiter(requests_per_minute=60, tokens_per_minute=60000)
        t1 = time.monotonic()
        # Request 60,040 tokens (40 tokens over capacity). tok_wait = 40 / 1000 = 0.04s.
        res_starve = await limiter_fast.acquire(tokens=60040, timeout=0.09)
        elapsed_starve = time.monotonic() - t1

        self.assertFalse(res_starve, "Requesting tokens > capacity must return False")
        # Empirically proves caller is starved and sleeps repeatedly until timeout
        self.assertGreaterEqual(elapsed_starve, 0.07, f"Expected starvation sleep >=0.07s, took {elapsed_starve:.4f}s")

    async def test_rate_limiter_high_concurrency_race_conditions(self) -> None:
        """
        25 concurrent tasks race to acquire tokens from a tight bucket with capacity=10.
        Verifies internal mutex prevents negative token balances or race corruption.
        """
        limiter = TokenBucketRateLimiter(requests_per_minute=10, tokens_per_minute=100)
        
        successes = 0
        lock = asyncio.Lock()

        async def worker() -> None:
            nonlocal successes
            ok = await limiter.acquire(tokens=10, timeout=0.01)
            if ok:
                async with lock:
                    successes += 1

        tasks = [asyncio.create_task(worker()) for _ in range(25)]
        await asyncio.gather(*tasks)

        # Capacity is 10 requests / 100 tokens, each worker took 10 tokens -> exactly 10 can succeed
        self.assertEqual(successes, 10, f"Expected exactly 10 immediate successes, got {successes}")

    def test_circuit_breaker_rapid_failure_tripping_and_recovery(self) -> None:
        """
        Rapidly records failures to trip the circuit breaker into OPEN state,
        verifies fast-fail, allows recovery timeout to expire, and verifies transition
        to HALF_OPEN and CLOSED upon success.
        """
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=0.05)
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        self.assertTrue(breaker.can_execute())

        # Record 2 failures -> remains CLOSED
        breaker.record_failure()
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        self.assertTrue(breaker.can_execute())

        # 3rd failure trips breaker to OPEN
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.OPEN)
        self.assertFalse(breaker.can_execute())

        # Wait for recovery timeout
        time.sleep(0.06)

        # First call after recovery timeout transitions to HALF_OPEN
        self.assertTrue(breaker.can_execute())
        self.assertEqual(breaker.state, CircuitState.HALF_OPEN)

        # Success in HALF_OPEN resets breaker to CLOSED
        breaker.record_success()
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        self.assertEqual(breaker.failure_count, 0)
        self.assertTrue(breaker.can_execute())

    def test_circuit_breaker_half_open_failure_re_trip(self) -> None:
        """
        Verifies that when a probe fails while in HALF_OPEN state,
        the breaker immediately re-trips back to OPEN state.
        """
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=0.04)
        breaker.record_failure()
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.OPEN)

        time.sleep(0.05)
        self.assertTrue(breaker.can_execute())
        self.assertEqual(breaker.state, CircuitState.HALF_OPEN)

        # Probe fails
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.OPEN)
        self.assertFalse(breaker.can_execute())

    def test_circuit_breaker_half_open_concurrency_leak_probe(self) -> None:
        """
        Probes the concurrency contract of CircuitBreaker.can_execute() in HALF_OPEN state.
        In an unsynchronized implementation, multiple concurrent callers calling can_execute()
        while in HALF_OPEN all return True instead of restricting to a single probe.
        Documents this empirical behavior.
        """
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.04)
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.OPEN)

        time.sleep(0.05)
        # Breaker is eligible for HALF_OPEN
        # Check multiple simultaneous queries
        results = [breaker.can_execute() for _ in range(5)]
        
        # In current implementation, once state changes to HALF_OPEN on first call,
        # subsequent calls while state == HALF_OPEN also return True
        self.assertTrue(results[0])
        # Document whether subsequent calls also returned True
        all_passed = all(results)
        self.assertTrue(all_passed, "Empirical observation: can_execute in HALF_OPEN permits multiple concurrent queries")

    def test_secret_masker_adversarial_corrupted_and_unusual_logs(self) -> None:
        """
        Stresses SecretMasker with unusual, corrupted, and complex log messages:
        - Escaped JSON quotes
        - URL query strings
        - Multiple concatenated secrets across Anthropic, Gemini, OpenAI
        - Non-string records in SecretMaskingFilter (int, None, custom objects)
        - Structured tuple/dict log arguments
        """
        # 1. Escaped JSON quotes
        escaped_json = r'{\"api_key\": \"sk-proj-very_secret_openai_key_12345\"}'
        masked_json = SecretMasker.mask(escaped_json)
        self.assertNotIn("very_secret_openai_key", masked_json)

        # 2. URL query parameters
        url_log = "GET https://api.anthropic.com/v1/messages?key=sk-ant-secret_anthropic_key_98765 HTTP/1.1"
        masked_url = SecretMasker.mask(url_log)
        self.assertNotIn("secret_anthropic_key_98765", masked_url)

        # 3. Multiple mixed provider secrets in single string
        mixed_str = (
            "Claude: sk-ant-claude_token_abc, "
            "Gemini: AIzaSy_gemini_token_xyz, "
            "OpenAI: sk-proj-openai_token_123"
        )
        masked_mixed = SecretMasker.mask(mixed_str)
        self.assertNotIn("claude_token_abc", masked_mixed)
        self.assertNotIn("gemini_token_xyz", masked_mixed)
        self.assertNotIn("openai_token_123", masked_mixed)

        # 4. SecretMaskingFilter handling unusual record.msg types
        filt = SecretMaskingFilter()
        
        # Int record.msg
        rec_int = logging.LogRecord("test", logging.INFO, "test.py", 1, 12345, (), None)
        self.assertTrue(filt.filter(rec_int))
        self.assertEqual(rec_int.msg, 12345)

        # None record.msg
        rec_none = logging.LogRecord("test", logging.INFO, "test.py", 1, None, (), None)
        self.assertTrue(filt.filter(rec_none))
        self.assertIsNone(rec_none.msg)

        # Structured args masking
        rec_args = logging.LogRecord(
            "test", logging.INFO, "test.py", 1, "User token: %s",
            ("sk-proj-secret_token_in_args",), None
        )
        filt.filter(rec_args)
        self.assertNotIn("secret_token_in_args", str(rec_args.args))


# ============================================================================
# PART 4: WebSocket Telemetry Concurrency & Backpressure
# ============================================================================

class MockWebSocketClient:
    """Mock WebSocket for stress-testing WebSocketTelemetryManager directly."""

    def __init__(self, client_id: str, latency: float = 0.0, fail_on_send: Optional[Exception] = None) -> None:
        self.client_id = client_id
        self.latency = latency
        self.fail_on_send = fail_on_send
        self.messages_received: List[Dict[str, Any]] = []
        self.closed = False

    async def accept(self) -> None:
        pass

    async def send_json(self, payload: Dict[str, Any]) -> None:
        if self.fail_on_send:
            raise self.fail_on_send
        if self.latency > 0:
            await asyncio.sleep(self.latency)
        self.messages_received.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


class TestAdversarialWebSocketTelemetry(unittest.IsolatedAsyncioTestCase):
    """Stress tests WebSocket telemetry broadcast, churn, and slow client isolation."""

    async def asyncSetUp(self) -> None:
        self.event_bus = EventBus(telemetry_buffer_size=100)
        self.mgr = WebSocketTelemetryManager(event_bus=self.event_bus, history_limit=50)

    async def asyncTearDown(self) -> None:
        await self.mgr.close_all()
        await self.event_bus.close()

    async def test_websocket_rapid_burst_churn_50_clients(self) -> None:
        """
        Simulates 50 clients rapidly connecting and disconnecting while the manager
        broadcasts 100 high-frequency events.
        Verifies:
        - No unhandled exceptions or deadlocks occur.
        - Disconnected clients are cleanly removed from active_connections.
        """
        clients = [MockWebSocketClient(f"client_{i}") for i in range(50)]

        async def client_lifecycle(c: MockWebSocketClient, delay: float) -> None:
            await self.mgr.connect(c)  # type: ignore
            await asyncio.sleep(delay)
            await self.mgr.disconnect(c)  # type: ignore

        # Stagger client connect/disconnect cycles
        lifecycle_tasks = [
            asyncio.create_task(client_lifecycle(c, (i % 5) * 0.002))
            for i, c in enumerate(clients)
        ]

        async def broadcaster() -> None:
            for k in range(100):
                await self.mgr.broadcast({"event_type": "churn_test", "tick": k})
                await asyncio.sleep(0.0005)

        await asyncio.gather(*(lifecycle_tasks + [broadcaster()]))

        # All clients disconnected
        self.assertEqual(len(self.mgr.active_connections), 0)

    async def test_websocket_slow_client_head_of_line_blocking_empirical(self) -> None:
        """
        Empirically probes head-of-line blocking in sequential WebSocket broadcast.
        Connects 1 slow client (0.02s latency) and 4 fast clients (0.0001s latency).
        Demonstrates that sequential broadcast duration is dominated by the slow client,
        delaying message delivery to fast clients.
        """
        fast_clients = [MockWebSocketClient(f"fast_{i}", latency=0.0001) for i in range(4)]
        slow_client = MockWebSocketClient("slow_0", latency=0.02)

        for c in fast_clients:
            await self.mgr.connect(c)  # type: ignore
        await self.mgr.connect(slow_client)  # type: ignore

        t0 = time.perf_counter()
        # Broadcast 3 events
        for i in range(3):
            await self.mgr.broadcast({"seq": i})
        elapsed = time.perf_counter() - t0

        # With 3 broadcasts and 1 client having 0.02s delay:
        # Sequential iteration requires >= 3 * 0.02s = 0.06s
        self.assertGreaterEqual(elapsed, 0.05, f"Expected sequential blocking >=0.05s, got {elapsed:.4f}s")

        for c in fast_clients:
            self.assertEqual(len(c.messages_received), 3)
        self.assertEqual(len(slow_client.messages_received), 3)

    async def test_websocket_backpressure_and_client_exception_resilience(self) -> None:
        """
        Tests that clients raising WebSocketDisconnect, RuntimeError, or connection dropped
        during broadcast are pruned automatically without interrupting delivery to healthy clients.
        """
        healthy_1 = MockWebSocketClient("healthy_1")
        healthy_2 = MockWebSocketClient("healthy_2")
        failing_disconnect = MockWebSocketClient("fail_disc", fail_on_send=WebSocketDisconnect(code=1006))
        failing_runtime = MockWebSocketClient("fail_runtime", fail_on_send=RuntimeError("Socket closed"))
        failing_generic = MockWebSocketClient("fail_generic", fail_on_send=ConnectionResetError("Peer reset"))

        for c in [healthy_1, failing_disconnect, healthy_2, failing_runtime, failing_generic]:
            await self.mgr.connect(c)  # type: ignore

        self.assertEqual(len(self.mgr.active_connections), 5)

        # Broadcast event
        sent_count = await self.mgr.broadcast({"msg": "resilience_check"})

        # Only the 2 healthy clients should have received the message
        self.assertEqual(sent_count, 2)
        self.assertEqual(len(healthy_1.messages_received), 1)
        self.assertEqual(len(healthy_2.messages_received), 1)

        # Failing clients must have been pruned from active_connections
        self.assertEqual(len(self.mgr.active_connections), 2)
        self.assertIn(healthy_1, self.mgr.active_connections)
        self.assertIn(healthy_2, self.mgr.active_connections)

    async def test_websocket_historical_catchup_under_churn(self) -> None:
        """
        Verifies historical telemetry replay buffer catch-up when a new client connects,
        and tests that a client failing during catch-up does not crash the server.
        """
        # Populate history
        for i in range(25):
            telem = GuiTelemetryEvent(
                event_type="agent_state",
                agent="claude_engineer",
                state="working",
                data={"step": i},
                timestamp=time.time(),
            )
            await self.event_bus.emit_telemetry(telem)

        # Normal client connects -> receives 25 history events
        normal_client = MockWebSocketClient("normal")
        await self.mgr.connect(normal_client)  # type: ignore
        self.assertEqual(len(normal_client.messages_received), 25)

        # Client that fails during catch-up send_json
        broken_client = MockWebSocketClient("broken", fail_on_send=RuntimeError("Client hung up"))
        # Must not raise unhandled exception
        await self.mgr.connect(broken_client)  # type: ignore
        self.assertIn(broken_client, self.mgr.active_connections)


# ============================================================================
# PART 5: REST API Boundary, Fuzzing & Injection Attacks
# ============================================================================

class TestAdversarialRestApiFuzzing(unittest.TestCase):
    """Adversarial security and boundary fuzzing on REST API endpoints."""

    def setUp(self) -> None:
        self.blackboard = Blackboard(project_name="FuzzingProject")
        self.event_bus = EventBus()
        self.app = create_app(blackboard=self.blackboard, event_bus=self.event_bus)
        self.client = TestClient(self.app)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = pathlib.Path(self.temp_dir.name)

    def tearDown(self) -> None:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(self.event_bus.close())
        loop.close()
        self.temp_dir.cleanup()

    def test_rest_direct_fuzzing_and_payload_injections(self) -> None:
        """
        Fuzzes POST /api/direct with:
        - Shell injection and SQL injection syntax in prompt
        - Script tags / XSS injections
        - Extreme iterations boundaries (-100, 0, 11, 999999)
        - Giant 200KB prompt strings
        - Conflict verification when status is already IN_PROGRESS
        """
        # 1. Shell and SQL injection payloads handled purely as text
        injections = [
            "; rm -rf /; echo 'owned'",
            "' OR '1'='1' --",
            "<script>alert(document.domain)</script>",
            "{{ 7 * 7 }} ${7*7}",
            "\x00\x01\x02\x03\xff",
        ]
        for inj in injections:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self.blackboard.set_status(ProjectStatus.PENDING))
            loop.close()
            res = self.client.post("/api/direct", json={"prompt": f"Create game with {inj}", "mock": True})
            self.assertEqual(res.status_code, 202, f"Failed for injection: {inj}")

        # 2. Iterations validation (ge=1, le=10)
        invalid_iterations = [-10, 0, 11, 99999]
        for it in invalid_iterations:
            res = self.client.post("/api/direct", json={"prompt": "valid prompt", "max_iterations": it})
            self.assertEqual(res.status_code, 422, f"Expected 422 for max_iterations={it}")

        # 3. Giant 200KB prompt handled without buffer overflow
        loop = asyncio.new_event_loop()
        loop.run_until_complete(self.blackboard.set_status(ProjectStatus.PENDING))
        loop.close()
        huge_prompt = "A" * 200000
        res_huge = self.client.post("/api/direct", json={"prompt": huge_prompt, "mock": True})
        self.assertEqual(res_huge.status_code, 202)

        # 4. Status conflict: IN_PROGRESS returns 409
        loop = asyncio.new_event_loop()
        loop.run_until_complete(self.blackboard.set_status(ProjectStatus.IN_PROGRESS))
        loop.close()

        res_conflict = self.client.post("/api/direct", json={"prompt": "Another prompt"})
        self.assertEqual(res_conflict.status_code, 409)
        self.assertIn("already running", res_conflict.json()["detail"].lower())

    def test_rest_asset_path_traversal_fuzzing(self) -> None:
        """
        Probes GET /api/project/assets/{id} with directory traversal strings,
        URL encoded slashes, and illegal characters.
        Verifies all are safely rejected (400 or 404), never exposing host files.
        """
        attack_ids = [
            "../../etc/passwd",
            "..%2f..%2fetc%2fpasswd",
            "....//....//etc/shadow",
            "..\\..\\windows\\system32\\cmd.exe",
            "/absolute/root/file.png",
            "asset%00.png",
            "asset/subpath/secret.json",
        ]

        for attack in attack_ids:
            res = self.client.get(f"/api/project/assets/{attack}")
            self.assertIn(
                res.status_code,
                (400, 404),
                f"Path traversal '{attack}' returned unexpected status {res.status_code}",
            )

    def test_rest_export_illegal_output_paths_and_injection(self) -> None:
        """
        Probes POST /api/project/export with illegal output directories:
        - Relative path traversal (../../escaped)
        - Backslash path traversal (..\\..\\escaped)
        - Nested traversal (sub/../../escaped)
        Verifies rejection with 400 Bad Request.
        """
        illegal_paths = [
            "../../escaped_dir",
            "..\\..\\escaped_dir",
            "subdir/../../escaped_dir",
            "./valid/../../escape",
        ]

        for p in illegal_paths:
            res = self.client.post("/api/project/export", json={"output_dir": p})
            self.assertEqual(
                res.status_code,
                400,
                f"Illegal path '{p}' was not rejected with 400 (got {res.status_code})",
            )
            self.assertIn("path traversal", res.json()["detail"].lower())

    def test_rest_verify_endpoint_stage_fuzzing(self) -> None:
        """
        Fuzzes POST /api/project/verify with invalid stage names and out-of-range simulation frames.
        """
        # Invalid stages
        res_stage = self.client.post(
            "/api/project/verify",
            json={"stages": ["invalid_stage_xyz", "rm_rf"]},
        )
        self.assertEqual(res_stage.status_code, 400)
        self.assertIn("invalid verification stage", res_stage.json()["detail"].lower())

        # Negative simulation frames
        res_neg = self.client.post("/api/project/verify", json={"simulation_frames": -50})
        self.assertEqual(res_neg.status_code, 422)

        # Excess simulation frames
        res_huge = self.client.post("/api/project/verify", json={"simulation_frames": 50000})
        self.assertEqual(res_huge.status_code, 422)

    def test_rest_export_manifest_files_created_attribute_resilience(self) -> None:
        """
        Empirically verifies POST /api/project/export behavior with Godot4 target.
        Confirms whether EngineProjectManifest.files_created vs files_written
        is gracefully caught by fallback exception handling.
        """
        # Seed blackboard with GDD and assets
        loop = asyncio.new_event_loop()
        gdd = GameDesignDocument(
            title="Export Test Game",
            high_concept="Test Game High Concept",
            genre="platformer_2d",
        )
        manifest = AssetManifest(project_name="ExportTest")
        loop.run_until_complete(self.blackboard.set_gdd(gdd))
        loop.run_until_complete(self.blackboard.set_asset_manifest(manifest))
        loop.close()

        export_target = str(self.output_dir / "valid_export")
        res = self.client.post(
            "/api/project/export",
            json={"output_dir": export_target, "target_engine": "godot4"},
        )

        # Should succeed via packager fallback or direct export
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], "exported")
        self.assertEqual(data["engine"], "godot4")


if __name__ == "__main__":
    unittest.main()
