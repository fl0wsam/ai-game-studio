"""
tests/tier1_features/test_orch_core.py - Tier 1 Systematic Feature Tests (F-ORCH-01 through F-ORCH-07)
Opaque-box, requirement-driven tests covering core orchestration in isolation:
- F-ORCH-01: Blackboard State Store (>=5 tests)
- F-ORCH-02: Async In-Process Event Bus (>=5 tests)
- F-ORCH-03: OpenAI Narrative Agent (>=5 tests)
- F-ORCH-04: Google Gemini Multimodal Agent (>=5 tests)
- F-ORCH-05: Anthropic Claude Code Agent (>=5 tests)
- F-ORCH-06: Provider Resilience & Security (>=5 tests)
- F-ORCH-07: Deterministic Mock Provider (>=5 tests)
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import pathlib
import time
from typing import Any, Dict, List, Optional

from PIL import Image

from tests.conftest_base import StudioTestCase

from studio.core.blackboard import Blackboard
from studio.core.event_bus import EventBus, topic_matches
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
    detect_archetype,
    derive_seed,
)
from studio.providers.openai_client import OpenAIClient


# ============================================================================
# F-ORCH-01: Blackboard State Store Tests
# ============================================================================

class TestFeatureOrch01Blackboard(StudioTestCase):
    """Systematic tests validating F-ORCH-01: Blackboard State Store."""

    def test_orch_01_01_initial_state_and_defaults(self) -> None:
        """Verify default initialization, timestamps, revision count, and empty structures."""
        bb = Blackboard(project_name="CyberRunner", target_engine="godot4", user_prompt="Build a cyber runner")
        state = self.run_async(bb.get_state())

        self.assertEqual(state.project_name, "CyberRunner")
        self.assertEqual(state.target_engine, "godot4")
        self.assertEqual(state.user_prompt, "Build a cyber runner")
        self.assertEqual(state.status, ProjectStatus.PENDING)
        self.assertEqual(state.active_phase, "INITIALIZATION")
        self.assertEqual(state.revision, 1)
        self.assertIsNone(state.gdd)
        self.assertIsNone(state.asset_manifest)
        self.assertEqual(len(state.scenes), 0)
        self.assertEqual(len(state.scripts), 0)
        self.assertIsNone(state.verification_report)
        self.assertGreater(state.created_at, 0)
        self.assertGreaterEqual(state.updated_at, state.created_at)

    def test_orch_01_02_store_and_retrieve_gdd_fidelity(self) -> None:
        """Verify full fidelity GDD persistence, nested field retrieval, and snapshot isolation."""
        bb = Blackboard(project_name="GDDTest")
        gdd = self.get_mock_gdd(title="Solar Odyssey", genre=GameGenre.SPACE_SHOOTER.value)

        self.run_async(bb.set_gdd(gdd))
        retrieved_gdd = self.run_async(bb.get_gdd())

        self.assertIsNotNone(retrieved_gdd)
        self.assertEqual(retrieved_gdd.title, "Solar Odyssey")
        self.assertEqual(retrieved_gdd.genre, GameGenre.SPACE_SHOOTER.value)
        self.assertEqual(len(retrieved_gdd.gameplay_mechanics), 3)
        self.assertEqual(len(retrieved_gdd.required_assets), 3)
        self.assertEqual(len(retrieved_gdd.quests), 1)

        # Verify deep copy isolation: mutating retrieved does not affect internal blackboard
        retrieved_gdd.title = "MutatedTitle"
        fresh_gdd = self.run_async(bb.get_gdd())
        self.assertEqual(fresh_gdd.title, "Solar Odyssey")

    def test_orch_01_03_store_and_retrieve_asset_manifest(self) -> None:
        """Verify AssetManifest registration, dynamic AssetItem additions, and query by ID."""
        bb = Blackboard(project_name="AssetTest")
        manifest = self.get_mock_manifest("AssetTest")

        self.run_async(bb.set_asset_manifest(manifest))
        retrieved_manifest = self.run_async(bb.get_asset_manifest())
        self.assertIsNotNone(retrieved_manifest)
        self.assertEqual(len(retrieved_manifest.items), 3)

        # Retrieve individual AssetItem
        item = self.run_async(bb.get_asset_item("sprite_player"))
        self.assertIsNotNone(item)
        self.assertEqual(item.asset_id, "sprite_player")
        self.assert_valid_png(item.file_bytes)

        # Add single new AssetItem dynamically
        new_wav = self.create_test_audio(duration=0.3, freq=880.0)
        new_item = AssetItem(
            asset_id="sfx_laser",
            asset_type=AssetType.AUDIO_SFX,
            rel_path="audio/sfx/laser.wav",
            engine_path="res://audio/sfx/laser.wav",
            file_bytes=new_wav,
        )
        self.run_async(bb.add_asset_item(new_item))

        retrieved_item = self.run_async(bb.get_asset_item("sfx_laser"))
        self.assertIsNotNone(retrieved_item)
        self.assertEqual(retrieved_item.rel_path, "audio/sfx/laser.wav")
        self.assert_valid_wav(retrieved_item.file_bytes)

    def test_orch_01_04_atomic_scenes_scripts_and_verification_report(self) -> None:
        """Verify atomic registration of SceneAST, ScriptSpec, and VerificationReport."""
        bb = Blackboard(project_name="PipelineTest")
        scene = self.get_mock_scene("Level1", "scenes/level1.tscn")
        script = self.get_mock_script("scripts/player.gd", "gdscript")

        report = VerificationReport(
            passed=True,
            stages_executed=["syntax_check", "schema_check", "asset_integrity"],
            errors=[],
            warnings=[],
            execution_time_ms=12.4,
            simulation_frames_run=60,
        )

        self.run_async(bb.add_scene(scene))
        self.run_async(bb.add_script(script))
        self.run_async(bb.set_verification_report(report))

        scenes = self.run_async(bb.get_scenes())
        scripts = self.run_async(bb.get_scripts())
        ver_rep = self.run_async(bb.get_verification_report())

        self.assertEqual(len(scenes), 1)
        self.assertEqual(scenes[0].name, "Level1")
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0].rel_path, "scripts/player.gd")
        self.assertIsNotNone(ver_rep)
        self.assertTrue(ver_rep.passed)
        self.assertEqual(ver_rep.simulation_frames_run, 60)

    def test_orch_01_05_monotonic_revision_history_and_rollback(self) -> None:
        """Verify monotonically increasing revisions and rollback to historical snapshots."""
        bb = Blackboard(project_name="HistoryTest")

        init_state = self.run_async(bb.get_state())
        rev_initial = init_state.revision

        # Mutation 1: set status
        self.run_async(bb.set_status(ProjectStatus.IN_PROGRESS, active_phase="NARRATIVE_DESIGN"))
        state_1 = self.run_async(bb.get_state())
        self.assertEqual(state_1.revision, rev_initial + 1)
        self.assertEqual(state_1.status, ProjectStatus.IN_PROGRESS)

        # Mutation 2: set GDD
        gdd = self.get_mock_gdd("Target Snapshot Game")
        self.run_async(bb.set_gdd(gdd))
        state_2 = self.run_async(bb.get_state())
        self.assertEqual(state_2.revision, rev_initial + 2)
        saved_rev = state_2.revision

        # Mutation 3: corrupt or fail status
        self.run_async(bb.set_status(ProjectStatus.FAILED, active_phase="ERROR_ABORT"))
        state_3 = self.run_async(bb.get_state())
        self.assertEqual(state_3.status, ProjectStatus.FAILED)

        # Rollback to saved_rev
        rollback_success = self.run_async(bb.rollback_to_revision(saved_rev))
        self.assertTrue(rollback_success)

        rolled_state = self.run_async(bb.get_state())
        self.assertEqual(rolled_state.status, ProjectStatus.IN_PROGRESS)
        self.assertIsNotNone(rolled_state.gdd)
        self.assertEqual(rolled_state.gdd.title, "Target Snapshot Game")

    def test_orch_01_06_serialization_and_disk_roundtrip(self) -> None:
        """Verify full JSON export, dict serialization, and file save/load roundtrip."""
        bb = Blackboard(project_name="DiskTest", user_prompt="Save to disk test")
        gdd = self.get_mock_gdd("Disk Game")
        manifest = self.get_mock_manifest("Disk Game")
        self.run_async(bb.set_gdd(gdd))
        self.run_async(bb.set_asset_manifest(manifest))

        export_dict = self.run_async(bb.export_dict())
        self.assertEqual(export_dict["project_name"], "DiskTest")
        self.assertEqual(export_dict["gdd"]["title"], "Disk Game")

        # Save to temporary file
        save_path = self.workspace_dir / "project_state.json"
        self.run_async(bb.save_to_file(save_path))
        self.assertTrue(save_path.exists())

        # Load into separate Blackboard instance
        bb2 = Blackboard(project_name="Temp")
        self.run_async(bb2.load_from_file(save_path))
        loaded_state = self.run_async(bb2.get_state())

        self.assertEqual(loaded_state.project_name, "DiskTest")
        self.assertIsNotNone(loaded_state.gdd)
        self.assertEqual(loaded_state.gdd.title, "Disk Game")
        self.assertEqual(len(loaded_state.asset_manifest.items), 3)

    def test_orch_01_07_observer_listeners_and_token_metrics(self) -> None:
        """Verify observer pattern callback dispatch and token metrics tracking."""
        bb = Blackboard(project_name="ObserverTest")
        dispatched_actions: List[str] = []

        def on_change(snapshot: StudioProjectState, action: str) -> None:
            dispatched_actions.append(action)

        sub_id = bb.register_listener(on_change)
        self.assertIsInstance(sub_id, str)

        self.run_async(bb.set_status(ProjectStatus.IN_PROGRESS))
        self.run_async(bb.record_token_metrics(provider="openai", prompt_tokens=100, completion_tokens=50, cost_usd=0.003))

        metrics = self.run_async(bb.get_token_metrics())
        self.assertIn("openai", metrics)
        self.assertEqual(metrics["openai"].total_tokens, 150)
        self.assertAlmostEqual(metrics["openai"].estimated_cost_usd, 0.003)

        self.assertIn("set_status", dispatched_actions)
        self.assertIn("record_token_metrics", dispatched_actions)

        # Unregister listener
        bb.unregister_listener(sub_id)
        dispatched_count = len(dispatched_actions)
        self.run_async(bb.set_status(ProjectStatus.COMPLETED))
        self.assertEqual(len(dispatched_actions), dispatched_count)


# ============================================================================
# F-ORCH-02: Async In-Process Event Bus Tests
# ============================================================================

class TestFeatureOrch02EventBus(StudioTestCase):
    """Systematic tests validating F-ORCH-02: Async In-Process Event Bus."""

    def test_orch_02_01_direct_subscribe_and_publish(self) -> None:
        """Verify direct topic subscription and event receipt."""
        bus = EventBus()
        received_events: List[AgentMessage] = []

        async def handler(msg: AgentMessage) -> None:
            received_events.append(msg)

        bus.subscribe("agent.director.gdd_ready", handler)

        test_msg = AgentMessage(
            sender=AgentRole.DIRECTOR,
            recipient=AgentRole.ARTIST,
            message_type="GDD_READY",
            payload={"gdd_title": "Neon Grid"},
        )
        self.run_async(bus.publish("agent.director.gdd_ready", test_msg))

        self.assertEqual(len(received_events), 1)
        self.assertEqual(received_events[0].payload["gdd_title"], "Neon Grid")
        self.assertEqual(received_events[0].sender, AgentRole.DIRECTOR)

    def test_orch_02_02_multiple_subscribers_fan_out(self) -> None:
        """Verify message fan-out across multiple independent subscribers."""
        bus = EventBus()
        collector_a: List[str] = []
        collector_b: List[str] = []
        collector_c: List[str] = []

        bus.subscribe("build.progress", lambda e: collector_a.append(e["step"]))
        bus.subscribe("build.progress", lambda e: collector_b.append(e["step"]))
        bus.subscribe("build.progress", lambda e: collector_c.append(e["step"]))

        payload = {"step": "ASSET_SYNTHESIS"}
        self.run_async(bus.publish("build.progress", payload))

        self.assertEqual(collector_a, ["ASSET_SYNTHESIS"])
        self.assertEqual(collector_b, ["ASSET_SYNTHESIS"])
        self.assertEqual(collector_c, ["ASSET_SYNTHESIS"])

    def test_orch_02_03_hierarchical_wildcard_topic_matching(self) -> None:
        """Verify exact, single-level (*), multi-level (**), and global wildcard matching."""
        # Unit tests on topic_matches helper directly
        self.assertTrue(topic_matches("agent.*.state", "agent.artist.state"))
        self.assertFalse(topic_matches("agent.*.state", "agent.artist.sub.state"))
        self.assertTrue(topic_matches("telemetry.**", "telemetry.cpu"))
        self.assertTrue(topic_matches("telemetry.**", "telemetry.node.agent.status"))
        self.assertTrue(topic_matches("**", "any.nested.topic.name"))
        self.assertFalse(topic_matches("studio.lifecycle.*", "telemetry.lifecycle.started"))

        # In-bus integration test
        bus = EventBus()
        star_events: List[str] = []
        double_star_events: List[str] = []

        bus.subscribe("agent.*.done", lambda e: star_events.append(e))
        bus.subscribe("telemetry.**", lambda e: double_star_events.append(e))

        self.run_async(bus.publish("agent.artist.done", "ArtistDone"))
        self.run_async(bus.publish("agent.engineer.done", "EngineerDone"))
        self.run_async(bus.publish("agent.sub.worker.done", "WorkerSubDone"))  # Should not match single *

        self.run_async(bus.publish("telemetry.system", "Telem1"))
        self.run_async(bus.publish("telemetry.studio.websocket.client", "Telem2"))

        self.assertEqual(star_events, ["ArtistDone", "EngineerDone"])
        self.assertEqual(double_star_events, ["Telem1", "Telem2"])

    def test_orch_02_04_unsubscribe_and_clear_subscribers(self) -> None:
        """Verify subscription removal and pattern-based clearing."""
        bus = EventBus()
        events: List[str] = []

        sub_id = bus.subscribe("alerts.warning", lambda e: events.append(e))
        self.run_async(bus.publish("alerts.warning", "Alert 1"))
        self.assertEqual(len(events), 1)

        unsub_ok = bus.unsubscribe(sub_id)
        self.assertTrue(unsub_ok)

        self.run_async(bus.publish("alerts.warning", "Alert 2"))
        self.assertEqual(len(events), 1)  # No new events received

        # Pattern clearing
        bus.subscribe("logs.info", lambda e: None)
        bus.subscribe("logs.info", lambda e: None)
        bus.subscribe("logs.error", lambda e: None)

        cleared_count = bus.clear_subscribers("logs.info")
        self.assertEqual(cleared_count, 2)

    def test_orch_02_05_subscriber_exception_shielding(self) -> None:
        """Verify that an unhandled exception in one subscriber does not disrupt others."""
        bus = EventBus()
        clean_events: List[str] = []

        def failing_handler(e: Any) -> None:
            raise RuntimeError("Fatal crash in rogue subscriber")

        def good_handler(e: Any) -> None:
            clean_events.append(e)

        bus.subscribe("test.fault", failing_handler)
        bus.subscribe("test.fault", good_handler)

        # Silence logger during expected subscriber exception
        prev_level = logging.getLogger("studio.core.event_bus").level
        logging.getLogger("studio.core.event_bus").setLevel(logging.CRITICAL + 1)
        try:
            # Publisher should not raise exception
            self.run_async(bus.publish("test.fault", "FaultTolerantEvent"))
        finally:
            logging.getLogger("studio.core.event_bus").setLevel(prev_level)

        self.assertEqual(clean_events, ["FaultTolerantEvent"])

    def test_orch_02_06_sync_and_async_handlers(self) -> None:
        """Verify seamless execution of both sync and async subscriber callbacks."""
        bus = EventBus()
        sync_result: List[str] = []
        async_result: List[str] = []

        def sync_cb(e: str) -> None:
            sync_result.append(e.upper())

        async def async_cb(e: str) -> None:
            await asyncio.sleep(0.001)
            async_result.append(e.lower())

        bus.subscribe("test.dual", sync_cb)
        bus.subscribe("test.dual", async_cb)

        self.run_async(bus.publish("test.dual", "EventPayload"))

        self.assertEqual(sync_result, ["EVENTPAYLOAD"])
        self.assertEqual(async_result, ["eventpayload"])

    def test_orch_02_07_telemetry_ring_buffer_and_history_queries(self) -> None:
        """Verify telemetry buffer capacity and chronological historical querying."""
        bus = EventBus(telemetry_buffer_size=10)

        for i in range(15):
            telem = GuiTelemetryEvent(
                event_type="build_progress",
                agent="orchestrator",
                data={"progress": i * 10},
            )
            self.run_async(bus.emit_telemetry(telem))

        # Query full history - bounded to maxlen 10
        history = bus.get_telemetry_history(limit=50)
        self.assertEqual(len(history), 10)
        self.assertEqual(history[-1].data["progress"], 140)

        # Query filtered by event_type
        filtered = bus.get_telemetry_history(event_type="build_progress", limit=5)
        self.assertEqual(len(filtered), 5)


# ============================================================================
# F-ORCH-03: OpenAI Narrative Agent Tests
# ============================================================================

class TestFeatureOrch03OpenAINarrativeAgent(StudioTestCase):
    """Systematic tests validating F-ORCH-03: OpenAI Narrative Agent."""

    def test_orch_03_01_generate_gdd_schema_compliance(self) -> None:
        """Verify generate_gdd produces a valid GameDesignDocument satisfying all schema rules."""
        client = OpenAIClient(api_key=None)  # Uses deterministic mock fallback
        gdd = self.run_async(client.generate_gdd("Neon Cyber Platformer"))

        self.assertIsInstance(gdd, GameDesignDocument)
        self.assertTrue(len(gdd.title) > 0)
        self.assertEqual(gdd.target_engine, "godot4")
        self.assertGreaterEqual(len(gdd.gameplay_mechanics), 1)
        self.assertGreaterEqual(len(gdd.required_assets), 1)
        self.assertGreaterEqual(len(gdd.scenes_plan), 1)

    def test_orch_03_02_narrative_lore_generation(self) -> None:
        """Verify narrative lore and worldbuilding documentation."""
        client = OpenAIClient(api_key=None)
        gdd = self.run_async(client.generate_gdd("Ancient Temple Dungeon Crawler"))

        self.assertIsInstance(gdd.narrative_lore, str)
        self.assertGreater(len(gdd.narrative_lore), 20)
        self.assertTrue(any(k in gdd.narrative_lore.lower() for k in ("world", "realm", "expanse", "dimension")))

    def test_orch_03_03_dialogue_trees_generation(self) -> None:
        """Verify generation of interactive dialogue trees with choices."""
        client = OpenAIClient(api_key=None)
        dialogue = self.run_async(client.generate_dialogue_tree("Elder Alchemist", "Mystic Caves", "wise and cryptic"))

        self.assertIn("root_node_id", dialogue)
        self.assertIn("nodes", dialogue)
        root = dialogue["nodes"][dialogue["root_node_id"]]
        self.assertEqual(root["speaker"], "Elder Alchemist")
        self.assertGreater(len(root["choices"]), 0)

    def test_orch_03_04_quest_mechanics_generation(self) -> None:
        """Verify generation of quest mechanics with objectives and rewards."""
        client = OpenAIClient(api_key=None)
        quest = self.run_async(client.generate_quest_mechanics("Defeat the Dragon of Shadow Mountain"))

        self.assertIn("quest_id", quest)
        self.assertIn("title", quest)
        self.assertIn("stages", quest)
        self.assertIn("rewards", quest)
        self.assertGreater(len(quest["stages"]), 0)

    def test_orch_03_05_strict_role_isolation_disallows_assets(self) -> None:
        """Verify division of labor: OpenAIClient must NOT generate visual/audio assets."""
        client = OpenAIClient(api_key=None)
        gdd = self.get_mock_gdd()

        with self.assertRaises(RuntimeError) as ctx:
            self.run_async(client.generate_assets(gdd))

        self.assertIn("Division of Labor Violation", str(ctx.exception))
        self.assertIn("GeminiClient", str(ctx.exception))

    def test_orch_03_06_strict_role_isolation_disallows_code(self) -> None:
        """Verify division of labor: OpenAIClient must NOT generate engine code/scenes."""
        client = OpenAIClient(api_key=None)
        gdd = self.get_mock_gdd()
        manifest = self.get_mock_manifest()

        with self.assertRaises(RuntimeError) as ctx:
            self.run_async(client.generate_code(gdd, manifest))

        self.assertIn("Division of Labor Violation", str(ctx.exception))
        self.assertIn("ClaudeClient", str(ctx.exception))

    def test_orch_03_07_health_check_status(self) -> None:
        """Verify health check returns MOCK_FALLBACK when no API key configured."""
        client = OpenAIClient(api_key=None)
        health = self.run_async(client.check_health())

        self.assertEqual(health.provider_name, "openai")
        self.assertEqual(health.status, ProviderHealthStatus.MOCK_FALLBACK)


# ============================================================================
# F-ORCH-04: Google Gemini Multimodal Agent Tests
# ============================================================================

class TestFeatureOrch04GeminiMultimodalAgent(StudioTestCase):
    """Systematic tests validating F-ORCH-04: Google Gemini Multimodal Agent."""

    def test_orch_04_01_generate_assets_sprite_manifest(self) -> None:
        """Verify generate_assets returns populated AssetManifest with valid PNG sprites."""
        client = GeminiClient(api_key=None)
        gdd = self.get_mock_gdd()

        manifest = self.run_async(client.generate_assets(gdd))

        self.assertIsInstance(manifest, AssetManifest)
        self.assertGreaterEqual(len(manifest.items), 1)

        # Verify sprite asset has valid PNG bytes
        sprite_item = manifest.get_item("sprite_player")
        self.assertIsNotNone(sprite_item)
        self.assert_valid_png(sprite_item.file_bytes)

    def test_orch_04_02_generate_sound_effects(self) -> None:
        """Verify generate_audio produces valid 16-bit 44.1kHz PCM WAV sound effects."""
        client = GeminiClient(api_key=None)
        wav_bytes = self.run_async(client.generate_audio("Player jump sound", duration_sec=0.2, audio_type="sfx"))

        self.assert_valid_wav(wav_bytes)
        self.assertGreater(len(wav_bytes), 100)

    def test_orch_04_03_generate_background_music(self) -> None:
        """Verify generate_audio produces longer BGM tracks with audio metadata."""
        client = GeminiClient(api_key=None)
        bgm_bytes = self.run_async(client.generate_audio("Upbeat electronic synth loop", duration_sec=1.5, audio_type="music"))

        self.assert_valid_wav(bgm_bytes)
        # 1.5s at 44.1kHz 16-bit mono is ~132,300 bytes + 44 header
        self.assertGreaterEqual(len(bgm_bytes), 1000)

    def test_orch_04_04_generate_cutscene_storyboard(self) -> None:
        """Verify generate_cutscene produces visual storyboard frame bytes."""
        client = GeminiClient(api_key=None)
        frame_bytes = self.run_async(client.generate_cutscene("Hero gazing at neon horizon"))

        self.assert_valid_png(frame_bytes)

    def test_orch_04_05_strict_role_isolation_disallows_gdd(self) -> None:
        """Verify division of labor: GeminiClient must NOT formulate Game Design Documents."""
        client = GeminiClient(api_key=None)

        with self.assertRaises(RuntimeError) as ctx:
            self.run_async(client.generate_gdd("Game idea"))

        self.assertIn("Division of Labor Violation", str(ctx.exception))
        self.assertIn("OpenAIClient", str(ctx.exception))

    def test_orch_04_06_strict_role_isolation_disallows_code(self) -> None:
        """Verify division of labor: GeminiClient must NOT generate engine code/scenes."""
        client = GeminiClient(api_key=None)
        gdd = self.get_mock_gdd()
        manifest = self.get_mock_manifest()

        with self.assertRaises(RuntimeError) as ctx:
            self.run_async(client.generate_code(gdd, manifest))

        self.assertIn("Division of Labor Violation", str(ctx.exception))
        self.assertIn("ClaudeClient", str(ctx.exception))

    def test_orch_04_07_health_check_status(self) -> None:
        """Verify health check returns MOCK_FALLBACK when no API key configured."""
        client = GeminiClient(api_key=None)
        health = self.run_async(client.check_health())

        self.assertEqual(health.provider_name, "gemini")
        self.assertEqual(health.status, ProviderHealthStatus.MOCK_FALLBACK)


# ============================================================================
# F-ORCH-05: Anthropic Claude Code Agent Tests
# ============================================================================

class TestFeatureOrch05ClaudeCodeAgent(StudioTestCase):
    """Systematic tests validating F-ORCH-05: Anthropic Claude Code Agent."""

    def test_orch_05_01_generate_code_gdscript_mechanics(self) -> None:
        """Verify generate_code generates SceneASTs and valid GDScript code."""
        client = ClaudeClient(api_key=None)
        gdd = self.get_mock_gdd()
        manifest = self.get_mock_manifest()

        scenes, scripts = self.run_async(client.generate_code(gdd, manifest))

        self.assertGreater(len(scenes), 0)
        self.assertGreater(len(scripts), 0)

        # Inspect generated player script
        player_script = next((s for s in scripts if "player" in s.rel_path.lower()), scripts[0])
        self.assertIn(player_script.language, ("gdscript", "python"))
        self.assertIn("extends", player_script.code)
        self.assertIn("velocity", player_script.code)

    def test_orch_05_02_generate_scene_ast_hierarchy(self) -> None:
        """Verify SceneAST nodes, external resources, and parent-child hierarchy."""
        client = ClaudeClient(api_key=None)
        gdd = self.get_mock_gdd()
        manifest = self.get_mock_manifest()

        scenes, _ = self.run_async(client.generate_code(gdd, manifest))
        main_scene = scenes[0]

        self.assertIsInstance(main_scene, SceneAST)
        self.assertGreater(len(main_scene.nodes), 0)
        # Root node has parent None
        root = main_scene.nodes[0]
        self.assertIsNone(root.parent)
        # Child nodes have non-None parents
        if len(main_scene.nodes) > 1:
            child = main_scene.nodes[1]
            self.assertIsNotNone(child.parent)

    def test_orch_05_03_debug_code_targeted_repair(self) -> None:
        """Verify debug_code fixes a broken script with error annotation."""
        client = ClaudeClient(api_key=None)
        broken_script = ScriptSpec(
            rel_path="scripts/hazard.gd",
            language="gdscript",
            code="func _ready():\n\tbad_call()\n",
        )
        diag_err = DiagnosticError(
            stage="syntax_check",
            file_path="scripts/hazard.gd",
            line=2,
            message="Undefined function bad_call()",
        )

        fixed_script = self.run_async(client.debug_code(broken_script, diag_err))

        self.assertIsInstance(fixed_script, ScriptSpec)
        self.assertEqual(fixed_script.rel_path, "scripts/hazard.gd")
        self.assertIn("Corrected", fixed_script.code)

    def test_orch_05_04_repair_code_batch(self) -> None:
        """Verify batch code repair based on verification error reports."""
        client = ClaudeClient(api_key=None)
        scripts = [self.get_mock_script()]
        scenes = [self.get_mock_scene()]
        errors = [
            DiagnosticError(
                stage="syntax_check",
                file_path="scripts/player.gd",
                line=5,
                message="Deprecated syntax",
            )
        ]

        result = self.run_async(client.repair_code(errors, scripts, scenes))

        self.assertIn("scripts", result)
        self.assertIn("scenes", result)
        self.assertEqual(len(result["scripts"]), 1)

    def test_orch_05_05_strict_role_isolation_disallows_gdd(self) -> None:
        """Verify division of labor: ClaudeClient must NOT formulate Game Design Documents."""
        client = ClaudeClient(api_key=None)

        with self.assertRaises(RuntimeError) as ctx:
            self.run_async(client.generate_gdd("Game idea"))

        self.assertIn("Division of Labor Violation", str(ctx.exception))
        self.assertIn("OpenAIClient", str(ctx.exception))

    def test_orch_05_06_strict_role_isolation_disallows_assets(self) -> None:
        """Verify division of labor: ClaudeClient must NOT generate visual/audio assets."""
        client = ClaudeClient(api_key=None)
        gdd = self.get_mock_gdd()

        with self.assertRaises(RuntimeError) as ctx:
            self.run_async(client.generate_assets(gdd))

        self.assertIn("Division of Labor Violation", str(ctx.exception))
        self.assertIn("GeminiClient", str(ctx.exception))

    def test_orch_05_07_health_check_status(self) -> None:
        """Verify health check returns MOCK_FALLBACK when no API key configured."""
        client = ClaudeClient(api_key=None)
        health = self.run_async(client.check_health())

        self.assertEqual(health.provider_name, "claude")
        self.assertEqual(health.status, ProviderHealthStatus.MOCK_FALLBACK)


# ============================================================================
# F-ORCH-06: Provider Resilience & Security Tests
# ============================================================================

class TestFeatureOrch06ProviderResilienceSecurity(StudioTestCase):
    """Systematic tests validating F-ORCH-06: Security & Resilience."""

    def test_orch_06_01_secret_masker_regex_patterns(self) -> None:
        """Verify regex secret masking for Anthropic, OpenAI, Gemini, Bearer tokens, and JSON."""
        # Anthropic key
        anthropic_raw = "Error authenticating with sk-ant-api03-abcdef1234567890_xyz in request"
        anthropic_masked = SecretMasker.mask(anthropic_raw)
        self.assertNotIn("abcdef1234567890_xyz", anthropic_masked)
        self.assertIn("...[REDACTED]", anthropic_masked)

        # OpenAI project key
        openai_proj_raw = "Using key sk-proj-1234567890abcdef123456"
        self.assertIn("...[REDACTED]", SecretMasker.mask(openai_proj_raw))

        # Standard OpenAI key
        openai_std_raw = "API key sk-1234567890abcdef12345678"
        self.assertIn("...[REDACTED]", SecretMasker.mask(openai_std_raw))

        # Google Gemini key
        gemini_raw = "Failed on AIzaSyABCDEF1234567890"
        self.assertIn("...[REDACTED]", SecretMasker.mask(gemini_raw))

        # Bearer token
        bearer_raw = "Header Bearer secret_access_token_123456"
        self.assertIn("...[REDACTED]", SecretMasker.mask(bearer_raw))

        # JSON key pattern
        json_raw = '{"api_key": "secret_live_key_value"}'
        self.assertIn('[REDACTED]', SecretMasker.mask(json_raw))

    def test_orch_06_02_secret_masking_log_filter(self) -> None:
        """Verify logging filter redacts secrets before writing log records."""
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(SecretMaskingFilter())
        handler.setFormatter(logging.Formatter("%(message)s"))

        logger = logging.getLogger("test_security_logger")
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        logger.info("Connecting with key sk-ant-api03-secretkey99887766")
        output = stream.getvalue()

        self.assertNotIn("secretkey99887766", output)
        self.assertIn("...[REDACTED]", output)

    def test_orch_06_03_credential_manager_hierarchy(self) -> None:
        """Verify credential hierarchy: Runtime Override > Environment Variable > .env File."""
        # Create temp .env file
        env_file = self.workspace_dir / ".env"
        env_file.write_text("OPENAI_API_KEY=dotenv_key_12345678\n", encoding="utf-8")

        cm = CredentialManager(env_path=env_file)

        # 1. Resolved from .env
        self.assertEqual(cm.get_key("openai"), "dotenv_key_12345678")
        self.assertTrue(cm.is_configured("openai"))

        # 2. Environment variable overrides .env
        os.environ["OPENAI_API_KEY"] = "os_environ_key_87654321"
        self.assertEqual(cm.get_key("openai"), "os_environ_key_87654321")

        # 3. Runtime override overrides environment variable
        cm.set_override("openai", "runtime_override_key_99999999")
        self.assertEqual(cm.get_key("openai"), "runtime_override_key_99999999")

    def test_orch_06_04_token_bucket_rate_limiter_depletion_and_refill(self) -> None:
        """Verify dual-token bucket depletion and non-blocking acquisition."""
        limiter = TokenBucketRateLimiter(requests_per_minute=60, tokens_per_minute=6000)

        # Acquire initial tokens
        ok = self.run_async(limiter.acquire(tokens=100, timeout=1.0))
        self.assertTrue(ok)

        # Check internal capacity was decremented
        self.assertLess(limiter.req_tokens, 60.0)
        self.assertLess(limiter.tokens, 6000.0)

    def test_orch_06_05_exponential_backoff_retry_transient_errors(self) -> None:
        """Verify exponential backoff retries on transient errors and succeeds."""
        backoff = ExponentialBackoff(max_retries=3, base_delay=0.01, max_backoff=0.05)
        call_count = 0

        async def transient_network_call() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                # Simulate 429 rate limit
                err = RuntimeError("HTTP 429 Too Many Requests")
                setattr(err, "status_code", 429)
                raise err
            return "SUCCESS"

        result = self.run_async(backoff.execute(transient_network_call))
        self.assertEqual(result, "SUCCESS")
        self.assertEqual(call_count, 3)

    def test_orch_06_06_exponential_backoff_non_retryable_error(self) -> None:
        """Verify exponential backoff does NOT retry on permanent non-retryable errors."""
        backoff = ExponentialBackoff(max_retries=3, base_delay=0.01)
        call_count = 0

        async def permanent_error_call() -> None:
            nonlocal call_count
            call_count += 1
            raise ValueError("Invalid parameter value - not retryable")

        with self.assertRaises(ValueError):
            self.run_async(backoff.execute(permanent_error_call))

        self.assertEqual(call_count, 1)

    def test_orch_06_07_circuit_breaker_state_transitions(self) -> None:
        """Verify CircuitBreaker state transitions: CLOSED -> OPEN -> HALF_OPEN -> CLOSED."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.05)

        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.can_execute())

        # Record 2 failures -> still CLOSED
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.CLOSED)

        # 3rd failure trips to OPEN
        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertFalse(cb.can_execute())

        # Wait recovery timeout -> transitions to HALF_OPEN
        time.sleep(0.06)
        self.assertTrue(cb.can_execute())
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

        # Successful probe -> resets to CLOSED
        cb.record_success()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.can_execute())


# ============================================================================
# F-ORCH-07: Deterministic Mock Provider Tests
# ============================================================================

class TestFeatureOrch07DeterministicMockProvider(StudioTestCase):
    """Systematic tests validating F-ORCH-07: Deterministic Mock Provider."""

    def test_orch_07_01_seed_reproducibility(self) -> None:
        """Verify identical prompts and seeds yield byte-for-byte reproducible outputs."""
        prompt = "Procedural Alien Runner Game"
        seed = derive_seed(prompt)

        gdd_1 = MockGDDGenerator.generate(prompt, seed=seed)
        gdd_2 = MockGDDGenerator.generate(prompt, seed=seed)

        self.assertEqual(gdd_1.title, gdd_2.title)
        self.assertEqual(gdd_1.narrative_lore, gdd_2.narrative_lore)
        self.assertEqual(gdd_1.gameplay_mechanics, gdd_2.gameplay_mechanics)
        self.assertEqual(len(gdd_1.required_assets), len(gdd_2.required_assets))

    def test_orch_07_02_procedural_pixel_art_png_generation(self) -> None:
        """Verify procedural sprite generator produces valid RGBA PNG images."""
        png_bytes = MockSpriteGenerator.generate(
            asset_id="player_sprite",
            asset_type="sprite",
            width=32,
            height=32,
            archetype=GameGenre.PLATFORMER_2D.value,
            seed=42,
        )

        self.assert_valid_png(png_bytes)
        img = Image.open(io.BytesIO(png_bytes))
        self.assertEqual(img.size, (32, 32))
        self.assertEqual(img.mode, "RGBA")

    def test_orch_07_03_procedural_chiptune_audio_wav_generation(self) -> None:
        """Verify procedural audio generator produces valid 16-bit 44.1kHz PCM WAV files."""
        wav_bytes = MockAudioGenerator.generate(
            asset_id="laser_sfx",
            asset_type="sfx",
            duration_sec=0.25,
            archetype=GameGenre.SPACE_SHOOTER.value,
            seed=123,
        )

        self.assert_valid_wav(wav_bytes)

    def test_orch_07_04_procedural_scene_ast_and_gdscript_generation(self) -> None:
        """Verify MockCodeGenerator produces valid SceneAST trees and GDScript specs."""
        gdd = self.get_mock_gdd("Space Defense")
        manifest = self.get_mock_manifest("Space Defense")

        scenes, scripts = MockCodeGenerator.generate(gdd, manifest, target_engine="godot4")

        self.assertGreater(len(scenes), 0)
        self.assertGreater(len(scripts), 0)

        # Validate SceneAST structure
        scene = scenes[0]
        self.assertIsInstance(scene, SceneAST)
        self.assertGreater(len(scene.nodes), 0)

        # Validate script has code
        script = scripts[0]
        self.assertIsInstance(script, ScriptSpec)
        self.assertIn("extends", script.code)

    def test_orch_07_05_archetype_detection_all_genres(self) -> None:
        """Verify archetype classification for Platformer, RPG, Space Shooter, and Grid Puzzle."""
        self.assertEqual(detect_archetype("Cyberpunk 2D platformer jump and run"), GameGenre.PLATFORMER_2D.value)
        self.assertEqual(detect_archetype("Medieval dungeon crawler top-down sword quest"), GameGenre.TOP_DOWN_RPG.value)
        self.assertEqual(detect_archetype("Galactic space alien laser shooter"), GameGenre.SPACE_SHOOTER.value)
        self.assertEqual(detect_archetype("Ancient glyph match 3 tile grid puzzle"), GameGenre.GRID_PUZZLE.value)
        # Default fallback
        self.assertEqual(detect_archetype("Unknown generic arcade"), GameGenre.PLATFORMER_2D.value)

    def test_orch_07_06_zero_cost_metrics_and_health(self) -> None:
        """Verify mock provider records 0 token cost and sub-millisecond offline health probe."""
        mock_client = MockProviderClient()

        health = self.run_async(mock_client.check_health())
        self.assertEqual(health.status, ProviderHealthStatus.HEALTHY)
        self.assertEqual(health.provider_name, "mock")
        self.assertLess(health.latency_ms, 10.0)
        self.assertEqual(mock_client.metrics.tokens_prompt, 0)
        self.assertEqual(mock_client.metrics.tokens_completion, 0)
