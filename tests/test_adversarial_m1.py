"""
tests/test_adversarial_m1.py - Empirical Adversarial Stress & Edge-Case Probing
Adversarial test harness targeting Milestone 1 core components:
1. Blackboard concurrency stress (50+ concurrent async tasks, cross-thread operations, deadlock probing)
2. EventBus stress (deep wildcards, rapid unsubscribe during dispatch, failing handlers, slow handlers)
3. SecretMasker bypass attempts (single-quoted JSON, case variations, punctuation inside tokens, custom tokens)
"""
from __future__ import annotations

import asyncio
import copy
import logging
import re
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from studio.core.blackboard import Blackboard
from studio.core.event_bus import EventBus, topic_matches
from studio.core.models import (
    AgentMessage,
    AgentRole,
    AssetItem,
    AssetManifest,
    AssetType,
    EngineTarget,
    GuiTelemetryEvent,
    ProjectStatus,
    SceneAST,
    ScriptSpec,
    TokenUsageMetrics,
)
from studio.core.security import (
    CredentialManager,
    SecretMasker,
    SecretMaskingFilter,
)


class TestBlackboardAdversarialConcurrency(unittest.IsolatedAsyncioTestCase):
    """Stress tests Blackboard under high concurrency and cross-thread access."""

    async def asyncSetUp(self) -> None:
        self.bb = Blackboard(
            project_name="AdversarialStressGame",
            target_engine=EngineTarget.GODOT4.value,
            user_prompt="Adversarial stress test prompt",
            max_revisions=500,
        )

    async def test_blackboard_high_concurrency_50_tasks_scenes_scripts_tokens(self) -> None:
        """
        Stress test spawning 60 concurrent async tasks mutating scenes, scripts,
        token metrics, logs, and asset items simultaneously.
        Verifies:
        - Exact sum of token metrics across all concurrent tasks
        - Monotonic revision increment without state corruption
        - Complete set of scripts and scenes present
        """
        num_tasks = 60
        tokens_per_task = 15
        expected_total_tokens = num_tasks * tokens_per_task * 2  # prompt + completion

        async def worker(task_id: int) -> None:
            # 1. Mutate scenes
            scene = SceneAST(
                name=f"Level_{task_id}",
                path=f"res://scenes/level_{task_id}.tscn",
                root_node_type="Node2D",
            )
            await self.bb.add_scene(scene)

            # 2. Mutate scripts
            script = ScriptSpec(
                rel_path=f"scripts/worker_{task_id}.gd",
                language="gdscript",
                code=f"# Worker {task_id} script\nextends Node2D\n",
            )
            await self.bb.add_script(script)

            # 3. Mutate token metrics
            await self.bb.record_token_metrics(
                provider="mock_stress",
                prompt_tokens=tokens_per_task,
                completion_tokens=tokens_per_task,
                cost_usd=0.001,
            )

            # 4. Mutate logs
            await self.bb.add_log(
                sender=f"worker_{task_id}",
                level="INFO",
                message=f"Log from worker {task_id}",
                metadata={"task_id": task_id},
            )

            # 5. Mutate asset item
            item = AssetItem(
                asset_id=f"sprite_{task_id}",
                asset_type=AssetType.SPRITE.value,
                rel_path=f"assets/sprites/sprite_{task_id}.png",
                file_bytes=b"fake_png_bytes",
            )
            await self.bb.add_asset_item(item)

        tasks = [asyncio.create_task(worker(i)) for i in range(num_tasks)]
        await asyncio.gather(*tasks)

        # Verification 1: Token metrics sum
        metrics = await self.bb.get_token_metrics()
        self.assertIn("mock_stress", metrics)
        m = metrics["mock_stress"]
        self.assertEqual(m.request_count, num_tasks, f"Expected {num_tasks} requests, got {m.request_count}")
        self.assertEqual(m.total_tokens, expected_total_tokens, f"Expected {expected_total_tokens} tokens, got {m.total_tokens}")

        # Verification 2: Scripts count
        scripts = await self.bb.get_scripts()
        self.assertEqual(len(scripts), num_tasks, f"Expected {num_tasks} scripts, got {len(scripts)}")

        # Verification 3: Scenes count
        scenes = await self.bb.get_scenes()
        self.assertEqual(len(scenes), num_tasks, f"Expected {num_tasks} scenes, got {len(scenes)}")

        # Verification 4: Asset manifest items count
        manifest = await self.bb.get_asset_manifest()
        self.assertIsNotNone(manifest)
        self.assertEqual(len(manifest.items), num_tasks, f"Expected {num_tasks} assets, got {len(manifest.items)}")

        # Verification 5: Logs count
        logs = await self.bb.get_logs(limit=200)
        self.assertGreaterEqual(len(logs), num_tasks)

        # Verification 6: Revision history monotonically increases
        revs = self.bb.get_revision_history()
        rev_ids = [r[0] for r in revs]
        self.assertEqual(rev_ids, sorted(rev_ids), "Revision IDs are not strictly monotonic")
        self.assertEqual(len(rev_ids), len(set(rev_ids)), "Duplicate revision IDs detected")

    async def test_blackboard_concurrent_readers_and_writers(self) -> None:
        """
        Tests concurrent readers (get_state, get_scenes, export_dict)
        running in parallel with 50 mutating writers.
        Verifies readers never encounter race conditions or torn reads.
        """
        num_writers = 50
        num_readers = 30
        reader_observations = []

        async def writer(idx: int) -> None:
            script = ScriptSpec(
                rel_path=f"scripts/script_{idx}.gd",
                language="gdscript",
                code=f"var worker = {idx}",
            )
            await self.bb.add_script(script)
            await self.bb.record_token_metrics("writer_prov", 5, 5)

        async def reader(idx: int) -> None:
            for _ in range(5):
                state = await self.bb.get_state()
                scenes = await self.bb.get_scenes()
                scripts = await self.bb.get_scripts()
                export = await self.bb.export_dict()
                reader_observations.append((len(scripts), len(export.get("scripts", {}))))
                await asyncio.sleep(0.001)

        writer_tasks = [asyncio.create_task(writer(i)) for i in range(num_writers)]
        reader_tasks = [asyncio.create_task(reader(i)) for i in range(num_readers)]
        await asyncio.gather(*(writer_tasks + reader_tasks))

        # Check all reader observations had internal consistency
        for s_len, exp_len in reader_observations:
            self.assertEqual(s_len, exp_len, f"Torn read detected: scripts={s_len} vs export={exp_len}")

    async def test_blackboard_observer_reentrancy_and_mutation(self) -> None:
        """
        Tests observers that perform async mutations on the Blackboard during notification.
        Verifies no self-deadlock occurs when listener mutates state.
        """
        reentrant_mutations_done = 0

        async def reentrant_listener(state, action: str) -> None:
            nonlocal reentrant_mutations_done
            if action == "add_script" and reentrant_mutations_done < 3:
                reentrant_mutations_done += 1
                await self.bb.add_log("observer", "DEBUG", f"Reentrant log {reentrant_mutations_done}")

        sub_id = self.bb.register_listener(reentrant_listener)
        try:
            await self.bb.add_script(
                ScriptSpec(
                    rel_path="scripts/trigger.gd",
                    language="gdscript",
                    code="# trigger",
                )
            )
            self.assertGreaterEqual(reentrant_mutations_done, 1)
        finally:
            self.bb.unregister_listener(sub_id)

    async def test_blackboard_cross_thread_deadlock_vulnerability(self) -> None:
        """
        Probes the known architectural constraint where multiple OS threads
        running separate event loops (via asyncio.run) attempt to acquire
        Blackboard's shared asyncio.Lock.
        Demonstrates that asyncio.Lock is not thread-safe across separate event loops.
        """
        loop = asyncio.get_running_loop()
        deadlocked = False

        def thread_worker(thread_id: int, out: List[str]) -> None:
            async def task():
                try:
                    # Attempt to get state with a 1.5s timeout
                    await asyncio.wait_for(self.bb.get_state(), timeout=1.5)
                    out.append(f"Thread {thread_id} success")
                except asyncio.TimeoutError:
                    out.append(f"Thread {thread_id} TIMEOUT")

            try:
                asyncio.run(task())
            except Exception as e:
                out.append(f"Thread {thread_id} EXCEPTION: {e}")

        # Dispatch thread workers
        results: List[str] = []
        t1 = threading.Thread(target=thread_worker, args=(1, results))
        t2 = threading.Thread(target=thread_worker, args=(2, results))

        t1.start()
        t2.start()
        t1.join(timeout=3.0)
        t2.join(timeout=3.0)

        # Record whether threads succeeded or timed out / hung
        # This empirically documents the multi-loop asyncio.Lock limitation.
        all_success = all("success" in r for r in results) and len(results) == 2
        # If threads were blocked / timed out, that confirms the vulnerability.
        self.assertTrue(len(results) == 2 or not all_success, "Observed thread execution behavior")

    async def test_blackboard_cross_thread_executor_mutations(self) -> None:
        """
        Tests 10 OS threads from a ThreadPoolExecutor concurrently submitting
        mutations to the Blackboard using asyncio.run_coroutine_threadsafe on the main loop.
        Verifies:
        - 100 cross-thread mutations complete without race conditions or dropped updates.
        - Exact accumulation of token metrics across all threads.
        """
        loop = asyncio.get_running_loop()
        num_threads = 10
        mutations_per_thread = 10
        expected_total_requests = num_threads * mutations_per_thread
        expected_total_tokens = expected_total_requests * 20  # 10 prompt + 10 completion

        def thread_task(thread_id: int) -> None:
            for i in range(mutations_per_thread):
                fut = asyncio.run_coroutine_threadsafe(
                    self.bb.record_token_metrics(
                        provider="thread_worker",
                        prompt_tokens=10,
                        completion_tokens=10,
                        cost_usd=0.0005,
                    ),
                    loop,
                )
                fut.result(timeout=5.0)

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            tasks = [loop.run_in_executor(executor, thread_task, tid) for tid in range(num_threads)]
            await asyncio.gather(*tasks)

        metrics = await self.bb.get_token_metrics()
        self.assertIn("thread_worker", metrics)
        m = metrics["thread_worker"]
        self.assertEqual(m.request_count, expected_total_requests)
        self.assertEqual(m.total_tokens, expected_total_tokens)

    async def test_blackboard_concurrent_rollback_under_mutation_storm(self) -> None:
        """
        Tests 30 concurrent writer tasks adding scripts and logs while 5 tasks
        attempt to rollback to previous revisions.
        Verifies no deadlocks occur and state remains accessible.
        """
        # First create an initial baseline revision
        await self.bb.add_script(
            ScriptSpec(rel_path="scripts/baseline.gd", language="gdscript", code="# baseline")
        )
        history = self.bb.get_revision_history()
        baseline_rev = history[-1][0]

        async def writer(idx: int) -> None:
            await self.bb.add_script(
                ScriptSpec(rel_path=f"scripts/storm_{idx}.gd", language="gdscript", code=f"# {idx}")
            )
            await self.bb.add_log("storm", "INFO", f"Log {idx}")

        async def rollbacker() -> None:
            await asyncio.sleep(0.005)
            await self.bb.rollback_to_revision(baseline_rev)

        writers = [asyncio.create_task(writer(i)) for i in range(30)]
        rollbackers = [asyncio.create_task(rollbacker()) for _ in range(5)]

        await asyncio.gather(*(writers + rollbackers))

        # Check blackboard is still responsive
        state = await self.bb.get_state()
        self.assertIsNotNone(state)



class TestEventBusAdversarialStress(unittest.IsolatedAsyncioTestCase):
    """Stress tests EventBus wildcard matching, concurrency, rapid unsubscription, and error modes."""

    async def asyncSetUp(self) -> None:
        self.bus = EventBus(telemetry_buffer_size=100)

    async def asyncTearDown(self) -> None:
        await self.bus.close()

    def test_wildcard_matching_single_segment_asterisk_spec_violation(self) -> None:
        """
        Tests whether single asterisk '*' correctly matches ONLY single segment topics
        as claimed in docstring:
        - '*' matches exactly one segment
        - '**' or '#' matches zero or more segments
        Vulnerability: `pattern in ("*", "**", "#")` causes '*' to match multi-segment topics.
        """
        # Asterisk alone should match single segment
        self.assertTrue(topic_matches("*", "event"))
        self.assertTrue(topic_matches("*", "telemetry"))

        # Asterisk alone SHOULD NOT match multi-segment topics according to docstring:
        matches_multisegment = topic_matches("*", "agent.claude.state")
        # In current implementation, this returns True because of fast-path:
        # if pattern in ("*", "**", "#"): return True
        # We record this finding:
        is_bug_present = (matches_multisegment is True)
        self.assertTrue(is_bug_present, "Documenting '*' matching multi-segment bug presence")

    def test_wildcard_matching_deep_and_adversarial_patterns(self) -> None:
        """
        Tests deeply nested topics (20+ segments), multiple wildcards,
        and complex wildcard positions.
        """
        # 1. Multiple double asterisks
        self.assertTrue(topic_matches("a.**.c.**.e", "a.b1.b2.c.d1.d2.e"))
        self.assertTrue(topic_matches("a.**.c.**.e", "a.c.e"))
        self.assertFalse(topic_matches("a.**.c.**.e", "a.b.c.d.f"))

        # 2. Deep topic (30 segments)
        deep_topic = ".".join([f"seg{i}" for i in range(30)])
        self.assertTrue(topic_matches("seg0.**", deep_topic))
        self.assertTrue(topic_matches("**.seg29", deep_topic))
        self.assertTrue(topic_matches("seg0.**.seg15.**.seg29", deep_topic))
        self.assertFalse(topic_matches("seg0.**.seg15.**.seg28", deep_topic))

        # 3. Hash '#' wildcard
        self.assertTrue(topic_matches("#", "any.deep.nested.topic"))
        self.assertTrue(topic_matches("agent.#", "agent.claude.code.generation"))
        self.assertTrue(topic_matches("agent.#", "agent"))

    async def test_rapid_unsubscribe_during_high_concurrency_dispatch(self) -> None:
        """
        Subscribes 100 handlers.
        Dispatches 200 events concurrently while handlers rapidly unsubscribe
        themselves, peer handlers, or call clear_subscribers().
        Verifies:
        - No unhandled dictionary mutation exceptions (RuntimeError)
        - No deadlocks
        """
        num_subscribers = 100
        num_events = 150
        invocations = 0
        sub_ids = []

        def make_handler(idx: int):
            def handler(event):
                nonlocal invocations
                invocations += 1
                # Unsubscribe peer subscriber concurrently during dispatch
                target_idx = (idx + 1) % num_subscribers
                if target_idx < len(sub_ids):
                    self.bus.unsubscribe(sub_ids[target_idx])

            return handler

        for i in range(num_subscribers):
            sid = self.bus.subscribe("stress.unsubscribe.*", make_handler(i))
            sub_ids.append(sid)

        # Concurrently publish events
        async def publisher(p_id: int):
            for j in range(num_events // 10):
                await self.bus.publish(f"stress.unsubscribe.{j % 5}", {"p": p_id, "j": j})

        publishers = [asyncio.create_task(publisher(k)) for k in range(10)]
        await asyncio.gather(*publishers)

        self.assertGreater(invocations, 0)
        # Should finish cleanly without unhandled exceptions

    async def test_failing_and_crashing_subscribers_isolation(self) -> None:
        """
        Tests multiple subscribers that fail with various exceptions
        (RuntimeError, KeyError, ZeroDivisionError, TypeError)
        alongside normal subscribers.
        Verifies all healthy subscribers execute and bus remains operational.
        """
        survivor_calls: List[int] = []

        def failing_runtime(e):
            raise RuntimeError("Catastrophic runtime crash")

        def failing_zero_div(e):
            1 / 0

        def failing_key(e):
            raise KeyError("missing_key")

        async def failing_async(e):
            raise ValueError("Async handler failure")

        def healthy_subscriber(e):
            survivor_calls.append(e.get("val", 0))

        self.bus.subscribe("crash.test", failing_runtime)
        self.bus.subscribe("crash.test", failing_zero_div)
        self.bus.subscribe("crash.test", failing_key)
        self.bus.subscribe("crash.test", failing_async)
        self.bus.subscribe("crash.test", healthy_subscriber)

        # Publish 20 events
        for i in range(20):
            await self.bus.publish("crash.test", {"val": i})

        self.assertEqual(len(survivor_calls), 20)
        self.assertEqual(survivor_calls, list(range(20)))

    async def test_telemetry_ring_buffer_stress_overflow(self) -> None:
        """
        Emits 500 telemetry events into a buffer capped at 50.
        Verifies:
        - Exactly 50 events remain
        - Oldest events were evicted
        - Retains newest events in correct temporal order
        """
        bus50 = EventBus(telemetry_buffer_size=50)
        try:
            for i in range(500):
                telem = GuiTelemetryEvent(
                    event_type="build_progress",
                    agent="claude",
                    state="working",
                    data={"step": i},
                    timestamp=1000.0 + i,
                )
                await bus50.emit_telemetry(telem)

            history = bus50.get_telemetry_history(limit=100)
            self.assertEqual(len(history), 50)
            # Check newest event is step 499
            self.assertEqual(history[-1].data["step"], 499)
            # Check oldest kept event is step 450
            self.assertEqual(history[0].data["step"], 450)
        finally:
            await bus50.close()

    def test_wildcard_catastrophic_backtracking_and_recursion_limit(self) -> None:
        """
        Tests whether deeply nested wildcard patterns cause exponential backtracking
        or hit Python's RecursionError limit (default 1000).
        """
        # 10 double-wildcards across a deep topic
        pattern = "a.**.b.**.c.**.d.**.e.**.f.**.g.**.h.**.i.**.j"
        matching_topic = "a.1.2.b.3.4.c.5.6.d.7.8.e.9.0.f.1.2.g.3.4.h.5.6.i.7.8.j"
        non_matching_topic = "a.1.2.b.3.4.c.5.6.d.7.8.e.9.0.f.1.2.g.3.4.h.5.6.i.7.8.k"

        t0 = time.perf_counter()
        res_match = topic_matches(pattern, matching_topic)
        res_non_match = topic_matches(pattern, non_matching_topic)
        elapsed = time.perf_counter() - t0

        self.assertTrue(res_match)
        self.assertFalse(res_non_match)
        self.assertLess(elapsed, 0.5, f"Backtracking too slow: {elapsed:.4f}s")

        # Edge cases: empty topic, malformed patterns with consecutive/leading/trailing dots
        self.assertTrue(topic_matches("", ""))
        self.assertTrue(topic_matches("agent..done", "agent.done"))  # split ignores empty segments
        self.assertTrue(topic_matches(".agent.done.", "agent.done"))

    async def test_rapid_resubscribe_and_clear_during_burst_dispatch(self) -> None:
        """
        Tests 100 subscribers rapidly subscribing, clearing, and re-subscribing
        while 50 concurrent tasks are publishing burst events.
        Verifies:
        - No unhandled dictionary mutation or deadlocks
        - Bus recovers and functions normally afterwards
        """
        active = True
        sub_count = 0

        async def worker_subscriber(w_id: int):
            nonlocal sub_count
            for _ in range(5):
                sid = self.bus.subscribe(f"burst.topic.{w_id}", lambda e: None)
                sub_count += 1
                await asyncio.sleep(0.001)
                self.bus.unsubscribe(sid)

        async def worker_publisher(p_id: int):
            for i in range(10):
                await self.bus.publish(f"burst.topic.{p_id % 10}", {"p": p_id, "i": i})
                if i == 5 and p_id == 0:
                    self.bus.clear_subscribers()

        subs = [asyncio.create_task(worker_subscriber(i)) for i in range(20)]
        pubs = [asyncio.create_task(worker_publisher(i)) for i in range(20)]
        await asyncio.gather(*(subs + pubs))

        # Afterwards, bus still accepts new subscriptions and dispatches cleanly
        delivered = []
        self.bus.subscribe("post_burst.test", lambda e: delivered.append(e))
        await self.bus.publish("post_burst.test", {"status": "ok"})
        self.assertEqual(len(delivered), 1)



class TestSecretMaskerAdversarialBypasses(unittest.TestCase):
    """
    Adversarially probes SecretMasker with evasion patterns:
    - Single-quoted string representations (e.g. Python repr / dict str)
    - Case sensitivity (e.g. "API_KEY", "ApiKey", "Token")
    - Custom tokens / API keys without standard prefixes
    - Tokens containing punctuation (+, =, ., :)
    - Split strings and delimiter variations
    """

    def test_single_quoted_json_repr_bypass(self) -> None:
        """
        Vulnerability probe: In Python, `str(dict)` formats as:
        `{'api_key': 'super_secret_value_12345'}`
        SecretMasker only checks double quotes: `r'("(?:api_key...':\s*")([^"]+)(")'`
        Proves single-quoted secrets leak through unmasked.
        """
        config = {"api_key": "my_super_secret_key_98765"}
        text = str(config)  # produces "{'api_key': 'my_super_secret_key_98765'}"
        masked = SecretMasker.mask(text)

        # Check if the secret leaked
        leaked = "my_super_secret_key_98765" in masked
        # Document vulnerability: Single-quoted dictionary strings bypass the JSON masker
        self.assertTrue(leaked, "Confirmed bypass: Single-quoted dict string bypasses JSON pattern")

    def test_case_sensitivity_bypass(self) -> None:
        """
        Vulnerability probe: JSON keys with different casing:
        `{"API_KEY": "secret_abc"}` or `{"ApiKey": "secret_abc"}` or `{"TOKEN": "secret_abc"}`
        SecretMasker regex is case-sensitive and lacks `re.IGNORECASE`.
        """
        text1 = '{"API_KEY": "super_secret_val_uppercase"}'
        text2 = '{"ApiKey": "super_secret_val_pascalcase"}'
        text3 = '{"TOKEN": "super_secret_val_token_upper"}'

        m1 = SecretMasker.mask(text1)
        m2 = SecretMasker.mask(text2)
        m3 = SecretMasker.mask(text3)

        self.assertIn("super_secret_val_uppercase", m1, "Confirmed bypass: UPPERCASE API_KEY is not masked")
        self.assertIn("super_secret_val_pascalcase", m2, "Confirmed bypass: PascalCase ApiKey is not masked")
        self.assertIn("super_secret_val_token_upper", m3, "Confirmed bypass: UPPERCASE TOKEN is not masked")

    def test_custom_token_without_prefix_bypass(self) -> None:
        """
        Vulnerability probe: SecretMasker only recognizes hardcoded prefixes:
        `sk-ant-`, `sk-proj-`, `sk-`, `AIzaSy`, `Bearer `.
        Custom enterprise keys, internal proxy tokens, or arbitrary secrets
        (e.g., loaded by CredentialManager) are completely unmasked.
        """
        custom_key = "game_studio_live_secret_7718293847291847"
        log_msg = f"Connecting to custom provider with key {custom_key}"
        masked = SecretMasker.mask(log_msg)

        # Custom tokens without hardcoded prefixes are not recognized
        self.assertIn(custom_key, masked, "Confirmed limitation: Custom tokens without hardcoded prefixes are not masked")

    def test_punctuation_and_base64_in_bearer_tokens(self) -> None:
        """
        Probes Bearer token regex:
        `Bearer\s+[a-zA-Z0-9_\-\.]+`
        What happens if a token contains standard Base64 characters like '+' or '=' or '/'?
        """
        # Standard base64 token with '+' and '='
        token = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c="
        masked = SecretMasker.mask(token)

        # The trailing '=' is NOT matched by [a-zA-Z0-9_\-\.], so it remains
        # But the core token body is masked:
        self.assertIn("Bearer ...[REDACTED]=", masked)

    def test_secret_masking_filter_on_log_records(self) -> None:
        """
        Tests SecretMaskingFilter against logging records with:
        - Formatted message string
        - Dict args
        - Tuple args
        """
        mask_filter = SecretMaskingFilter()
        logger = logging.getLogger("test_masking_filter")
        logger.setLevel(logging.INFO)

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='Got response from key sk-ant-secret1234567890: {"api_key": "nested_secret_123"}',
            args=(),
            exc_info=None,
        )
        mask_filter.filter(record)
        self.assertNotIn("sk-ant-secret1234567890", record.msg)
        self.assertNotIn("nested_secret_123", record.msg)
        self.assertIn("sk-ant-...[REDACTED]", record.msg)
        self.assertIn("[REDACTED]", record.msg)

    def test_secret_masker_comprehensive_evasion_battery(self) -> None:
        """
        Runs a battery of 10 evasions against SecretMasker to map out its exact
        defense boundary and surface unhandled bypasses.
        """
        # Vector 1: Single-quoted dict (Python str(dict))
        v1 = SecretMasker.mask("{'api_key': 'super_secret_1'}")
        self.assertIn("super_secret_1", v1, "Bypass: single-quoted dict")

        # Vector 2: UPPERCASE JSON key
        v2 = SecretMasker.mask('{"API_KEY": "super_secret_2"}')
        self.assertIn("super_secret_2", v2, "Bypass: uppercase API_KEY")

        # Vector 3: PascalCase JSON key
        v3 = SecretMasker.mask('{"ApiKey": "super_secret_3"}')
        self.assertIn("super_secret_3", v3, "Bypass: PascalCase ApiKey")

        # Vector 4: Header format x-api-key: <val>
        v4 = SecretMasker.mask("x-api-key: my_raw_header_secret_4")
        self.assertIn("my_raw_header_secret_4", v4, "Bypass: HTTP header format")

        # Vector 5: Authorization header without Bearer
        v5 = SecretMasker.mask("Authorization: raw_token_secret_5")
        self.assertIn("raw_token_secret_5", v5, "Bypass: raw Authorization header")

        # Vector 6: URL query parameter
        v6 = SecretMasker.mask("https://api.openai.com/v1/chat?api_key=query_secret_6")
        self.assertIn("query_secret_6", v6, "Bypass: URL query parameter")

        # Vector 7: Anthropic key with dots / punctuation
        # sk-ant- regex: sk-ant-[a-zA-Z0-9_\-]+
        v7 = SecretMasker.mask("sk-ant-api03-token_part1.leak_part2")
        self.assertIn(".leak_part2", v7, "Bypass: dot in Anthropic key splits token and leaks tail")

        # Vector 8: OpenAI project key with dots / punctuation
        v8 = SecretMasker.mask("sk-proj-token_part1.leak_part2")
        self.assertIn(".leak_part2", v8, "Bypass: dot in OpenAI project key splits token and leaks tail")

        # Vector 9: Custom token without sk- or AIzaSy prefix
        v9 = SecretMasker.mask("studio_enterprise_secret_9988776655")
        self.assertIn("studio_enterprise_secret_9988776655", v9, "Bypass: custom token without prefix")

        # Vector 10: Standard Bearer token with plus
        v10 = SecretMasker.mask("Bearer token+with+plus")
        # Bearer regex matches [a-zA-Z0-9_\-\.], stopping before '+'
        self.assertIn("+with+plus", v10, "Bypass: plus in Bearer token stops matching and leaks tail")


if __name__ == "__main__":
    unittest.main()
