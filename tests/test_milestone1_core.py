"""
tests/test_milestone1_core.py - Comprehensive Unit and Integration Test Suite for Milestone 1
Covers F-ORCH-01 through F-ORCH-07:
- F-ORCH-01: Blackboard State Store & Canonical Pydantic Models
- F-ORCH-02: Async In-Process Event Bus & Telemetry Buffer
- F-ORCH-03: OpenAI Creative Director & Narrative Designer Client
- F-ORCH-04: Google Gemini Multimodal Artist & Composer Client
- F-ORCH-05: Anthropic Claude Systems Engineer & Architect Client
- F-ORCH-06: Security Subsystem (CredentialManager, SecretMasker, RateLimiter, Backoff, CircuitBreaker)
- F-ORCH-07: Deterministic Offline Mock Provider (Pillow PNGs, stdlib WAVs, ASTs, Determinism)
- Studio Orchestrator FSM & Review Feedback Loop
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import time
import unittest
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional
from PIL import Image

from studio.core.models import (
    AgentMessage,
    AgentRole,
    AssetItem,
    AssetManifest,
    AssetRequirement,
    AssetType,
    DiagnosticError,
    DialogueChoice,
    DialogueNode,
    EngineTarget,
    ExtResourceAST,
    GameDesignDocument,
    GameGenre,
    GuiTelemetryEvent,
    NodeAST,
    ProjectStatus,
    QuestObjective,
    QuestSpec,
    ReviewFeedback,
    SceneAST,
    ScenePlan,
    ScriptSpec,
    StudioProjectState,
    TokenUsageMetrics,
    VerificationReport,
)
from studio.core.blackboard import Blackboard
from studio.core.event_bus import EventBus, topic_matches
from studio.core.orchestrator import OrchestrationPhase, StudioOrchestrator
from studio.core.security import (
    CircuitBreaker,
    CircuitState,
    CredentialManager,
    ExponentialBackoff,
    SecretMasker,
    SecretMaskingFilter,
    TokenBucketRateLimiter,
)
from studio.providers.base import (
    BaseProviderClient,
    ProviderHealth,
    ProviderHealthStatus,
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


# ============================================================================
# F-ORCH-01: Blackboard State Store & Models Tests
# ============================================================================

class TestBlackboardAndModels(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bb = Blackboard(
            project_name="TestCyberGame",
            target_engine=EngineTarget.GODOT4.value,
            user_prompt="A cyberpunk endless runner",
            max_revisions=10,
        )

    async def test_blackboard_initial_state(self) -> None:
        state = await self.bb.get_state()
        self.assertEqual(state.project_name, "TestCyberGame")
        self.assertEqual(state.target_engine, "godot4")
        self.assertEqual(state.status, ProjectStatus.PENDING)
        self.assertEqual(state.active_phase, "INITIALIZATION")
        self.assertEqual(state.revision, 1)
        self.assertIsNone(state.gdd)
        self.assertIsNone(state.asset_manifest)
        self.assertEqual(len(state.scenes), 0)
        self.assertEqual(len(state.scripts), 0)

    async def test_set_and_get_gdd(self) -> None:
        gdd = GameDesignDocument(
            title="Neon Runner",
            genre=GameGenre.PLATFORMER_2D.value,
            target_engine=EngineTarget.GODOT4.value,
            high_concept="Fast neon runner",
            gameplay_mechanics=["Wall slide", "Double jump"],
            narrative_lore="Ancient cyber lore",
        )
        await self.bb.set_gdd(gdd)
        retrieved = await self.bb.get_gdd()
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.title, "Neon Runner")
        self.assertEqual(retrieved.gameplay_mechanics, ["Wall slide", "Double jump"])

    async def test_snapshot_immutability(self) -> None:
        gdd = GameDesignDocument(
            title="Immutable Test",
            high_concept="Test concept",
        )
        await self.bb.set_gdd(gdd)
        snapshot = await self.bb.get_state()
        snapshot.project_name = "TAMPERED_NAME"
        snapshot.gdd.title = "TAMPERED_TITLE"

        actual = await self.bb.get_state()
        self.assertEqual(actual.project_name, "TestCyberGame")
        self.assertEqual(actual.gdd.title, "Immutable Test")

    async def test_asset_manifest_and_items(self) -> None:
        item1 = AssetItem(
            asset_id="sprite_player",
            asset_type=AssetType.SPRITE,
            rel_path="assets/sprites/player.png",
            file_bytes=b"\x89PNG\r\n\x1a\nTESTBYTES",
        )
        await self.bb.add_asset_item(item1)

        manifest = await self.bb.get_asset_manifest()
        self.assertIsNotNone(manifest)
        self.assertEqual(len(manifest.items), 1)

        retrieved_item = await self.bb.get_asset_item("sprite_player")
        self.assertIsNotNone(retrieved_item)
        self.assertEqual(retrieved_item.rel_path, "assets/sprites/player.png")
        self.assertEqual(retrieved_item.file_bytes, b"\x89PNG\r\n\x1a\nTESTBYTES")
        self.assertIsNotNone(retrieved_item.checksum_sha256)

    async def test_scenes_and_scripts_crud(self) -> None:
        scene = SceneAST(
            name="MainLevel",
            path="scenes/main.tscn",
            nodes=[NodeAST(name="MainLevel", type="Node2D")],
        )
        script = ScriptSpec(
            rel_path="scripts/player.gd",
            language="gdscript",
            code="extends CharacterBody2D\n",
        )
        await self.bb.add_scene(scene)
        await self.bb.add_script(script)

        scenes = await self.bb.get_scenes()
        scripts = await self.bb.get_scripts()
        self.assertEqual(len(scenes), 1)
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scenes[0].name, "MainLevel")
        self.assertEqual(scripts[0].rel_path, "scripts/player.gd")

    async def test_token_metrics_accumulation(self) -> None:
        await self.bb.record_token_metrics("openai", prompt_tokens=100, completion_tokens=50, cost_usd=0.002)
        await self.bb.record_token_metrics("openai", prompt_tokens=200, completion_tokens=100, cost_usd=0.004)
        await self.bb.record_token_metrics("claude", prompt_tokens=300, completion_tokens=150, cost_usd=0.005)

        metrics = await self.bb.get_token_metrics()
        self.assertEqual(metrics["openai"].prompt_tokens, 300)
        self.assertEqual(metrics["openai"].completion_tokens, 150)
        self.assertEqual(metrics["openai"].total_tokens, 450)
        self.assertAlmostEqual(metrics["openai"].estimated_cost_usd, 0.006)
        self.assertEqual(metrics["claude"].total_tokens, 450)

    async def test_revision_history_and_rollback(self) -> None:
        await self.bb.set_status(ProjectStatus.IN_PROGRESS, active_phase="NARRATIVE")
        rev_after_narrative = (await self.bb.get_state()).revision

        await self.bb.set_status(ProjectStatus.FAILED, active_phase="FAILED")
        self.assertEqual((await self.bb.get_state()).status, ProjectStatus.FAILED)

        # Rollback
        success = await self.bb.rollback_to_revision(rev_after_narrative)
        self.assertTrue(success)
        restored = await self.bb.get_state()
        self.assertEqual(restored.status, ProjectStatus.IN_PROGRESS)
        self.assertEqual(restored.active_phase, "NARRATIVE")

    async def test_json_export_and_load_roundtrip(self) -> None:
        item = AssetItem(
            asset_id="test_icon",
            asset_type=AssetType.SPRITE,
            rel_path="assets/icon.png",
            file_bytes=b"MY_SECRET_PNG_BINARY_DATA",
        )
        await self.bb.add_asset_item(item)
        exported_json = await self.bb.export_json()
        self.assertIn("test_icon", exported_json)

        new_bb = Blackboard(project_name="SecondProject")
        data = json.loads(exported_json)
        await new_bb.load_dict(data)

        restored_item = await new_bb.get_asset_item("test_icon")
        self.assertIsNotNone(restored_item)
        self.assertEqual(restored_item.file_bytes, b"MY_SECRET_PNG_BINARY_DATA")

    async def test_asset_manifest_queries(self) -> None:
        item_sprite = AssetItem(
            asset_id="s1", asset_type=AssetType.SPRITE, rel_path="assets/s1.png", file_bytes=b"SPRITE"
        )
        item_audio = AssetItem(
            asset_id="a1", asset_type=AssetType.AUDIO_SFX, rel_path="audio/a1.wav", file_bytes=b"AUDIO"
        )
        await self.bb.add_asset_item(item_sprite)
        await self.bb.add_asset_item(item_audio)

        manifest = await self.bb.get_asset_manifest()
        sprites = manifest.get_by_type(AssetType.SPRITE)
        audios = manifest.get_by_type("audio_sfx")
        self.assertEqual(len(sprites), 1)
        self.assertEqual(len(audios), 1)
        self.assertIsNone(manifest.get_item("non_existent_asset"))

    async def test_save_and_load_file(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "state.json"
            await self.bb.set_status(ProjectStatus.IN_PROGRESS, active_phase="TEST_SAVE")
            await self.bb.save_to_file(file_path)
            self.assertTrue(file_path.exists())

            new_bb = Blackboard(project_name="LoadedFromFile")
            await new_bb.load_from_file(file_path)
            state = await new_bb.get_state()
            self.assertEqual(state.status, ProjectStatus.IN_PROGRESS)
            self.assertEqual(state.active_phase, "TEST_SAVE")

    async def test_concurrency_race_condition(self) -> None:
        """Launches 30 concurrent async tasks updating blackboard state to test thread & async safety."""
        async def updater(idx: int) -> None:
            await self.bb.add_log("system", "INFO", f"Log from worker {idx}")
            await self.bb.record_token_metrics("mock", prompt_tokens=10, completion_tokens=10)

        tasks = [updater(i) for i in range(30)]
        await asyncio.gather(*tasks)

        metrics = await self.bb.get_token_metrics()
        self.assertEqual(metrics["mock"].request_count, 30)
        self.assertEqual(metrics["mock"].total_tokens, 600)
        logs = await self.bb.get_logs(limit=50)
        self.assertGreaterEqual(len(logs), 30)


# ============================================================================
# F-ORCH-02: Async In-Process Event Bus Tests
# ============================================================================

class TestEventBus(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bus = EventBus(telemetry_buffer_size=50)

    async def asyncTearDown(self) -> None:
        await self.bus.close()

    def test_topic_pattern_matching_rules(self) -> None:
        self.assertTrue(topic_matches("agent.*.state", "agent.openai.state"))
        self.assertFalse(topic_matches("agent.*.state", "agent.openai.sub.state"))
        self.assertTrue(topic_matches("telemetry.**", "telemetry.agent.state.active"))
        self.assertTrue(topic_matches("telemetry.**", "telemetry"))
        self.assertTrue(topic_matches("**", "any.depth.event"))
        self.assertTrue(topic_matches("*", "single"))
        self.assertFalse(topic_matches("a.b.c", "a.b.d"))

    async def test_exact_topic_delivery(self) -> None:
        delivered = []
        async def handler(evt: Any) -> None:
            delivered.append(evt)

        sub_id = self.bus.subscribe("agent.claude.done", handler)
        msg = AgentMessage(
            sender=AgentRole.ENGINEER,
            recipient=AgentRole.ORCHESTRATOR,
            message_type="CODE_READY",
            payload={"files": 2},
        )
        await self.bus.publish("agent.claude.done", msg)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0].payload["files"], 2)

        # Unsubscribe and verify no more delivery
        self.bus.unsubscribe(sub_id)
        await self.bus.publish("agent.claude.done", msg)
        self.assertEqual(len(delivered), 1)

    async def test_single_and_multi_level_wildcards(self) -> None:
        wildcard_received = []
        deep_received = []

        self.bus.subscribe("agent.*.state", lambda e: wildcard_received.append(e))
        self.bus.subscribe("telemetry.**", lambda e: deep_received.append(e))

        await self.bus.publish("agent.gemini.state", {"agent": "gemini", "active": True})
        await self.bus.publish("agent.claude.state", {"agent": "claude", "active": True})
        await self.bus.publish("agent.sub.nested.state", {"agent": "ignored"})
        await self.bus.publish("telemetry.build.progress.step1", {"progress": 25})

        self.assertEqual(len(wildcard_received), 2)
        self.assertEqual(len(deep_received), 1)

    async def test_subscriber_exception_isolation(self) -> None:
        """Confirms one subscriber throwing an exception does not break other subscribers."""
        survivor_called = []

        def failing_handler(evt: Any) -> None:
            raise RuntimeError("Fatal subscriber crash!")

        def healthy_handler(evt: Any) -> None:
            survivor_called.append(evt)

        self.bus.subscribe("test.crash", failing_handler)
        self.bus.subscribe("test.crash", healthy_handler)

        await self.bus.publish("test.crash", {"key": "val"})
        self.assertEqual(len(survivor_called), 1)

    async def test_telemetry_history_buffer_and_filtering(self) -> None:
        t0 = time.time()
        telem1 = GuiTelemetryEvent(event_type="agent_state", agent="openai_director", state="active")
        telem2 = GuiTelemetryEvent(event_type="build_progress", state="active", data={"pct": 50})
        telem3 = GuiTelemetryEvent(event_type="agent_state", agent="claude_engineer", state="complete")

        await self.bus.emit_telemetry(telem1)
        await self.bus.emit_telemetry(telem2)
        await self.bus.emit_telemetry(telem3)

        all_history = self.bus.get_telemetry_history(since_timestamp=t0 - 1.0)
        self.assertEqual(len(all_history), 3)

        agent_state_only = self.bus.get_telemetry_history(event_type="agent_state")
        self.assertEqual(len(agent_state_only), 2)

        claude_only = self.bus.get_telemetry_history(agent="claude_engineer")
        self.assertEqual(len(claude_only), 1)
        self.assertEqual(claude_only[0].state, "complete")

    async def test_event_bus_clear_and_sync_publish(self) -> None:
        received = []
        self.bus.subscribe("sync.test", lambda e: received.append(e))
        self.bus.publish_sync("sync.test", {"sync": True})
        await asyncio.sleep(0.01)
        self.assertEqual(len(received), 1)

        count = self.bus.clear_subscribers("sync.test")
        self.assertEqual(count, 1)
        self.bus.publish_sync("sync.test", {"sync": False})
        await asyncio.sleep(0.01)
        self.assertEqual(len(received), 1)


# ============================================================================
# F-ORCH-03: OpenAI Creative Director & Narrative Designer Tests
# ============================================================================

class TestOpenAIClient(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = OpenAIClient()  # without API key -> uses deterministic offline fallback

    async def test_generate_gdd(self) -> None:
        gdd = await self.client.generate_gdd("A top down dungeon crawler RPG with sword mechanics")
        self.assertIsInstance(gdd, GameDesignDocument)
        self.assertIsNotNone(gdd.title)
        self.assertEqual(gdd.genre, GameGenre.TOP_DOWN_RPG.value)
        self.assertGreaterEqual(len(gdd.gameplay_mechanics), 3)
        self.assertGreaterEqual(len(gdd.required_assets), 5)
        self.assertGreaterEqual(len(gdd.scenes_plan), 1)

    async def test_generate_dialogue_tree_and_quests(self) -> None:
        dialogue = await self.client.generate_dialogue_tree(
            npc_name="Blacksmith", quest_context="repairing the ancient broadsword"
        )
        self.assertIn("root_node_id", dialogue)
        self.assertIn("nodes", dialogue)

        quests = await self.client.generate_quest_mechanics("Find 3 power gems in dark catacombs")
        self.assertIn("quest_id", quests)
        self.assertIn("stages", quests)

    async def test_division_of_labor_enforcement(self) -> None:
        """Calling generate_assets or generate_code on OpenAIClient must raise RuntimeError."""
        with self.assertRaises(RuntimeError) as ctx1:
            await self.client.generate_assets(None)
        self.assertIn("Division of Labor Violation", str(ctx1.exception))

        with self.assertRaises(RuntimeError) as ctx2:
            await self.client.generate_code(None, None)
        self.assertIn("Division of Labor Violation", str(ctx2.exception))

    async def test_health_check_offline(self) -> None:
        health = await self.client.check_health()
        self.assertEqual(health.status, ProviderHealthStatus.MOCK_FALLBACK)
        self.assertEqual(health.provider_name, "openai")


# ============================================================================
# F-ORCH-04: Google Gemini Multimodal Artist & Composer Tests
# ============================================================================

class TestGeminiClient(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = GeminiClient()

    async def test_generate_assets_from_gdd(self) -> None:
        gdd = GameDesignDocument(
            title="Space Odyssey",
            genre=GameGenre.SPACE_SHOOTER.value,
            high_concept="Space blaster",
            required_assets=[
                AssetRequirement(asset_id="sprite_ship", asset_type=AssetType.SPRITE, target_rel_path="assets/sprites/ship.png", width=32, height=32),
                AssetRequirement(asset_id="sfx_laser", asset_type=AssetType.AUDIO_SFX, target_rel_path="audio/sfx/laser.wav", duration_sec=0.2),
            ],
        )
        manifest = await self.client.generate_assets(gdd)
        self.assertIsInstance(manifest, AssetManifest)
        self.assertEqual(len(manifest.items), 2)

        ship = manifest.get_item("sprite_ship")
        self.assertIsNotNone(ship)
        self.assertTrue(ship.file_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

        laser = manifest.get_item("sfx_laser")
        self.assertIsNotNone(laser)
        self.assertTrue(laser.file_bytes.startswith(b"RIFF"))

    async def test_generate_image_and_audio_primitives(self) -> None:
        img_bytes = await self.client.generate_image("A futuristic coin item", width=32, height=32)
        self.assertTrue(img_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        img = Image.open(io.BytesIO(img_bytes))
        self.assertEqual(img.size, (32, 32))

        wav_bytes = await self.client.generate_audio("Laser sound effect", duration_sec=0.3, audio_type="sfx")
        self.assertTrue(wav_bytes.startswith(b"RIFF"))
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 44100)

    async def test_repair_assets(self) -> None:
        errors = [
            DiagnosticError(stage="asset_integrity", file_path="assets/sprites/missing_gem.png", message="File missing"),
        ]
        repaired = await self.client.repair_assets(errors, current_manifest=None)
        item = repaired.get_item("missing_gem")
        self.assertIsNotNone(item)
        self.assertTrue(item.file_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

    async def test_division_of_labor_enforcement(self) -> None:
        with self.assertRaises(RuntimeError) as ctx1:
            await self.client.generate_gdd("prompt")
        self.assertIn("Division of Labor Violation", str(ctx1.exception))

        with self.assertRaises(RuntimeError) as ctx2:
            await self.client.generate_code(None, None)
        self.assertIn("Division of Labor Violation", str(ctx2.exception))


# ============================================================================
# F-ORCH-05: Anthropic Claude Systems Engineer & Architect Tests
# ============================================================================

class TestClaudeClient(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = ClaudeClient()

    async def test_generate_code_and_scenes(self) -> None:
        gdd = GameDesignDocument(
            title="Retro Platformer",
            genre=GameGenre.PLATFORMER_2D.value,
            target_engine=EngineTarget.GODOT4.value,
            high_concept="2D Jumper",
            gameplay_mechanics=["Run", "Jump"],
        )
        manifest = AssetManifest(project_name="Retro Platformer")

        scenes, scripts = await self.client.generate_code(gdd, manifest)
        self.assertGreaterEqual(len(scenes), 1)
        self.assertGreaterEqual(len(scripts), 1)

        scene = scenes[0]
        self.assertIsInstance(scene, SceneAST)
        self.assertIn("scenes/", scene.path)
        self.assertGreaterEqual(len(scene.nodes), 1)

        script = scripts[0]
        self.assertIsInstance(script, ScriptSpec)
        self.assertIn("extends", script.code)

    async def test_debug_code(self) -> None:
        broken_script = ScriptSpec(
            rel_path="scripts/player.gd",
            language="gdscript",
            code="func _ready():\n    pass\n",
        )
        diag = DiagnosticError(stage="syntax_check", file_path="scripts/player.gd", message="Missing type annotation")
        fixed = await self.client.debug_code(broken_script, diag)
        self.assertIn("Corrected", fixed.code)

    async def test_division_of_labor_enforcement(self) -> None:
        with self.assertRaises(RuntimeError) as ctx1:
            await self.client.generate_gdd("prompt")
        self.assertIn("Division of Labor Violation", str(ctx1.exception))

        with self.assertRaises(RuntimeError) as ctx2:
            await self.client.generate_assets(None)
        self.assertIn("Division of Labor Violation", str(ctx2.exception))


# ============================================================================
# F-ORCH-06: Security & Resilience Subsystem Tests
# ============================================================================

class TestSecuritySubsystem(unittest.IsolatedAsyncioTestCase):
    def test_credential_manager_hierarchy(self) -> None:
        cm = CredentialManager()
        cm.set_override("openai", "sk-proj-OVERRIDEKEY123456789")
        self.assertEqual(cm.get_key("openai"), "sk-proj-OVERRIDEKEY123456789")
        self.assertTrue(cm.is_configured("openai"))

        # Fallback to os.environ
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-ENVKEY123456789"
        try:
            self.assertEqual(cm.get_key("anthropic"), "sk-ant-ENVKEY123456789")
            self.assertTrue(cm.is_configured("anthropic"))
        finally:
            del os.environ["ANTHROPIC_API_KEY"]

    def test_credential_manager_dotenv(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("CUSTOM_API_KEY=Custom_DOTENV_KEY_12345\n", encoding="utf-8")
            cm = CredentialManager(env_path=env_file)
            self.assertEqual(cm.get_key("custom"), "Custom_DOTENV_KEY_12345")
            self.assertTrue(cm.is_configured("custom"))

    def test_secret_masker_patterns(self) -> None:
        raw_text = (
            "OpenAI: sk-proj-1234567890abcdef12345678\n"
            "Anthropic: sk-ant-api03-abcdefghijklmnop12345\n"
            "Gemini: AIzaSyB1234567890abcdef\n"
            "Bearer: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9\n"
            'JSON: {"api_key": "super_secret_token_123"}'
        )
        masked = SecretMasker.mask(raw_text)
        self.assertNotIn("sk-proj-1234567890abcdef12345678", masked)
        self.assertIn("sk-proj-...[REDACTED]", masked)
        self.assertNotIn("sk-ant-api03-abcdefghijklmnop12345", masked)
        self.assertIn("sk-ant-...[REDACTED]", masked)
        self.assertNotIn("AIzaSyB1234567890abcdef", masked)
        self.assertIn("AIzaSy...[REDACTED]", masked)
        self.assertIn("[REDACTED]", masked)

    def test_secret_masking_filter(self) -> None:
        filt = SecretMaskingFilter()
        rec = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg="Sending key sk-proj-SECRETKEY9999 to provider", args=(), exc_info=None
        )
        filt.filter(rec)
        self.assertNotIn("sk-proj-SECRETKEY9999", rec.msg)
        self.assertIn("[REDACTED]", rec.msg)

    async def test_token_bucket_rate_limiter(self) -> None:
        # 60 RPM = 1 per sec capacity 2
        limiter = TokenBucketRateLimiter(requests_per_minute=120, tokens_per_minute=2000)
        # First acquire should pass immediately
        ok1 = await limiter.acquire(tokens=1000, timeout=1.0)
        self.assertTrue(ok1)

        # Second acquire of 1000 should pass
        ok2 = await limiter.acquire(tokens=1000, timeout=1.0)
        self.assertTrue(ok2)

        # Third acquire immediately requires waiting; with timeout=0.01 it will fail
        ok3 = await limiter.acquire(tokens=1000, timeout=0.01)
        self.assertFalse(ok3)

    async def test_circuit_breaker_state_transitions(self) -> None:
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.2)
        self.assertTrue(cb.can_execute())
        self.assertEqual(cb.state, CircuitState.CLOSED)

        # Record 2 failures -> stays closed
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.CLOSED)

        # 3rd failure trips breaker
        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertFalse(cb.can_execute())

        # Wait for recovery timeout -> transitions to HALF_OPEN
        await asyncio.sleep(0.25)
        self.assertTrue(cb.can_execute())
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

        # Success resets to CLOSED
        cb.record_success()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.can_execute())

    async def test_exponential_backoff_retry(self) -> None:
        backoff = ExponentialBackoff(max_retries=3, base_delay=0.01, max_backoff=0.05)
        call_count = 0

        async def retryable_call() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("Rate limit exceeded 429")
            return "SUCCESS"

        res = await backoff.execute(retryable_call)
        self.assertEqual(res, "SUCCESS")
        self.assertEqual(call_count, 3)

        # Non-retryable error fails immediately
        async def non_retryable_call() -> None:
            raise ValueError("Invalid argument")

        with self.assertRaises(ValueError):
            await backoff.execute(non_retryable_call)


# ============================================================================
# F-ORCH-07: Deterministic Mock Provider Tests
# ============================================================================

class TestDeterministicMockProvider(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.mock = MockProviderClient()

    def test_seed_determinism(self) -> None:
        s1 = derive_seed("Cyberpunk Neon Ninja")
        s2 = derive_seed("Cyberpunk Neon Ninja")
        s3 = derive_seed("  cyberpunk neon ninja  ")
        self.assertEqual(s1, s2)
        self.assertEqual(s1, s3)

        s_other = derive_seed("Medieval Dungeon Quest")
        self.assertNotEqual(s1, s_other)

    def test_archetype_detection(self) -> None:
        self.assertEqual(detect_archetype("dungeon crawler sword game"), GameGenre.TOP_DOWN_RPG.value)
        self.assertEqual(detect_archetype("alien space shooter galaxy"), GameGenre.SPACE_SHOOTER.value)
        self.assertEqual(detect_archetype("match-3 gem puzzle grid"), GameGenre.GRID_PUZZLE.value)
        self.assertEqual(detect_archetype("endless runner platformer"), GameGenre.PLATFORMER_2D.value)

    async def test_mock_sprite_png_validity(self) -> None:
        png_bytes = MockSpriteGenerator.generate("sprite_player", "sprite", width=32, height=32, seed=12345)
        self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

        img = Image.open(io.BytesIO(png_bytes))
        self.assertEqual(img.size, (32, 32))
        self.assertEqual(img.mode, "RGBA")

        # Bilateral symmetry verification for player sprite
        pixels = img.load()
        for y in range(4, 28):
            for x in range(2, 16):
                # Alpha channels should match across mirror axis
                self.assertEqual(pixels[x, y][3], pixels[31 - x, y][3])

    async def test_mock_audio_wav_validity(self) -> None:
        wav_bytes = MockAudioGenerator.generate("sfx_jump", "audio_sfx", duration_sec=0.2, seed=12345)
        self.assertTrue(wav_bytes.startswith(b"RIFF"))

        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)  # 16-bit
            self.assertEqual(wf.getframerate(), 44100)
            self.assertGreater(wf.getnframes(), 1000)

    async def test_byte_for_byte_full_determinism(self) -> None:
        prompt = "Cybernetic Space Invader"
        gdd1 = await self.mock.generate_gdd(prompt)
        m1 = await self.mock.generate_assets(gdd1)

        gdd2 = await self.mock.generate_gdd(prompt)
        m2 = await self.mock.generate_assets(gdd2)

        self.assertEqual(len(m1.items), len(m2.items))
        for item1, item2 in zip(m1.items, m2.items):
            self.assertEqual(item1.asset_id, item2.asset_id)
            self.assertEqual(item1.file_bytes, item2.file_bytes)
            self.assertEqual(item1.checksum_sha256, item2.checksum_sha256)

    async def test_all_four_archetypes_generation(self) -> None:
        archetypes = [
            GameGenre.PLATFORMER_2D.value,
            GameGenre.TOP_DOWN_RPG.value,
            GameGenre.SPACE_SHOOTER.value,
            GameGenre.GRID_PUZZLE.value,
        ]
        for arch in archetypes:
            gdd = await self.mock.generate_gdd(f"Archetype test for {arch}", archetype=arch)
            manifest = await self.mock.generate_assets(gdd)
            scenes, scripts = await self.mock.generate_code(gdd, manifest)

            self.assertGreaterEqual(len(scenes), 1)
            self.assertGreaterEqual(len(scripts), 1)
            self.assertIn(".tscn", scenes[0].path)

    async def test_mock_provider_cutscene_generation(self) -> None:
        cutscene_bytes = await self.mock.generate_cutscene("Epic battle intro cutscene")
        self.assertTrue(cutscene_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        img = Image.open(io.BytesIO(cutscene_bytes))
        self.assertGreater(img.size[0], 0)
        self.assertGreater(img.size[1], 0)


# ============================================================================
# Studio Orchestrator & Review Loop Tests
# ============================================================================

class TestStudioOrchestrator(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bb = Blackboard(project_name="OrchestratorE2ETest")
        self.bus = EventBus()
        self.openai = OpenAIClient()
        self.gemini = GeminiClient()
        self.claude = ClaudeClient()
        self.orchestrator = StudioOrchestrator(
            blackboard=self.bb,
            event_bus=self.bus,
            openai_client=self.openai,
            gemini_client=self.gemini,
            claude_client=self.claude,
        )

    async def asyncTearDown(self) -> None:
        await self.bus.close()

    async def test_full_pipeline_execution_success(self) -> None:
        state = await self.orchestrator.run_pipeline(
            prompt="A 2D neon platformer with laser obstacles",
            target_engine=EngineTarget.GODOT4.value,
            project_name="NeonPlatformerTest",
        )
        self.assertEqual(state.status, ProjectStatus.COMPLETED)
        self.assertEqual(self.orchestrator.current_phase, OrchestrationPhase.COMPLETED)
        self.assertIsNotNone(state.gdd)
        self.assertIsNotNone(state.asset_manifest)
        self.assertGreaterEqual(len(state.asset_manifest.items), 5)
        self.assertGreaterEqual(len(state.scenes), 1)
        self.assertGreaterEqual(len(state.scripts), 1)
        self.assertIsNotNone(state.verification_report)
        self.assertTrue(state.verification_report.passed)

    async def test_review_feedback_loop_trigger(self) -> None:
        """Simulates verification failure on first check, triggering the review loop and passing on repair."""
        call_count = 0

        class MockFailingVerifier:
            async def verify_project(self, st: StudioProjectState) -> VerificationReport:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # Report 1 code error and 1 asset error
                    return VerificationReport(
                        passed=False,
                        errors=[
                            DiagnosticError(stage="syntax_check", file_path="scripts/player.gd", message="Expected identifier"),
                            DiagnosticError(stage="asset_integrity", file_path="assets/sprites/extra_coin.png", message="Missing asset"),
                        ],
                    )
                return VerificationReport(passed=True, stages_executed=["syntax_check", "asset_integrity"])

        orch = StudioOrchestrator(
            blackboard=self.bb,
            event_bus=self.bus,
            openai_client=self.openai,
            gemini_client=self.gemini,
            claude_client=self.claude,
            verification_pipeline=MockFailingVerifier(),
            max_iterations=2,
        )

        state = await orch.run_pipeline(
            prompt="A test game that triggers review loop",
            target_engine=EngineTarget.GODOT4.value,
        )
        self.assertEqual(state.status, ProjectStatus.COMPLETED)
        self.assertEqual(orch.iteration_count, 1)
        self.assertGreaterEqual(len(state.review_history), 2)  # Claude review + Gemini review

    async def test_orchestrator_max_iterations_exceeded(self) -> None:
        """Simulates permanent verification failure exceeding max_iterations."""
        class AlwaysFailingVerifier:
            async def verify_project(self, st: StudioProjectState) -> VerificationReport:
                return VerificationReport(
                    passed=False,
                    errors=[DiagnosticError(stage="simulation", file_path="game.gd", message="Crash on frame 0")],
                )

        orch = StudioOrchestrator(
            blackboard=self.bb,
            event_bus=self.bus,
            openai_client=self.openai,
            gemini_client=self.gemini,
            claude_client=self.claude,
            verification_pipeline=AlwaysFailingVerifier(),
            max_iterations=1,
        )

        state = await orch.run_pipeline(
            prompt="Game that always crashes",
            target_engine=EngineTarget.GODOT4.value,
        )
        self.assertEqual(state.status, ProjectStatus.FAILED)
        self.assertEqual(orch.current_phase, OrchestrationPhase.FAILED)
        self.assertFalse(state.verification_report.passed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
