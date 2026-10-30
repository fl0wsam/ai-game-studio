"""
tests/conftest_base.py - Common Test Fixtures, Utilities, and Base Test Case
Provides sandboxed temporary directories, procedural PNG/WAV synthesis,
asynchronous runner helpers, and domain model factory builders.
"""
from __future__ import annotations

import asyncio
import io
import math
import os
import pathlib
import struct
import tempfile
import unittest
import wave
from typing import Any, Awaitable, Dict, List, Optional, Tuple, TypeVar

from PIL import Image

from studio.core.models import (
    AgentMessage,
    AgentRole,
    AssetItem,
    AssetManifest,
    AssetRequirement,
    AssetType,
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

T = TypeVar("T")


class StudioTestCase(unittest.TestCase):
    """
    Standard base test case for AI Game Studio testing.
    Provides automated temp workspace sandboxing, procedural media generation,
    and async event loop execution.
    """

    def setUp(self) -> None:
        super().setUp()
        self._temp_dir = tempfile.TemporaryDirectory()
        self.workspace_dir = pathlib.Path(self._temp_dir.name)
        # Snapshot environment to prevent leakage
        self._orig_env = dict(os.environ)

    def tearDown(self) -> None:
        try:
            self._temp_dir.cleanup()
        except Exception:
            pass
        # Restore environment
        os.environ.clear()
        os.environ.update(self._orig_env)
        super().tearDown()

    def run_async(self, coro: Awaitable[T]) -> T:
        """Helper to run async coroutines synchronously within standard unittest."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            try:
                # Cancel pending tasks if any
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            finally:
                loop.close()
                asyncio.set_event_loop(None)

    # ------------------------------------------------------------------------
    # Procedural Media Generation Helpers
    # ------------------------------------------------------------------------

    def create_dummy_png(
        self,
        path: pathlib.Path,
        width: int = 32,
        height: int = 32,
        color: Tuple[int, int, int, int] = (255, 0, 0, 255),
    ) -> pathlib.Path:
        """Procedurally generates a valid PNG image and writes it to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGBA", (width, height), color)
        img.save(path, format="PNG")
        return path

    def create_test_image(
        self,
        width: int = 32,
        height: int = 32,
        color: Tuple[int, int, int, int] = (0, 128, 255, 255),
    ) -> bytes:
        """Procedurally generates a valid PNG image and returns raw bytes in-memory."""
        buf = io.BytesIO()
        img = Image.new("RGBA", (width, height), color)
        img.save(buf, format="PNG")
        return buf.getvalue()

    def create_dummy_wav(
        self,
        path: pathlib.Path,
        duration: float = 0.1,
        freq: float = 440.0,
        sample_rate: int = 44100,
    ) -> pathlib.Path:
        """Procedurally generates a valid 16-bit 44.1kHz mono PCM WAV file on disk."""
        raw_bytes = self.create_test_audio(duration=duration, freq=freq, sample_rate=sample_rate)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw_bytes)
        return path

    def create_test_audio(
        self,
        duration: float = 0.1,
        freq: float = 440.0,
        sample_rate: int = 44100,
    ) -> bytes:
        """Procedurally generates a valid 16-bit 44.1kHz mono PCM WAV file in memory."""
        n_samples = max(1, int(duration * sample_rate))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            raw_data = bytearray()
            for i in range(n_samples):
                # Generate clean sine wave
                t = float(i) / sample_rate
                val = int(16000.0 * math.sin(2.0 * math.pi * freq * t))
                val = max(-32767, min(32767, val))
                raw_data.extend(struct.pack("<h", val))
            wf.writeframes(raw_data)
        return buf.getvalue()

    def create_dummy_tscn(
        self,
        path: pathlib.Path,
        name: str = "Main",
        node_type: str = "Node2D",
    ) -> pathlib.Path:
        """Generates a basic valid Godot 4 .tscn text scene file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        content = f'[gd_scene format=3]\n\n[node name="{name}" type="{node_type}"]\n'
        path.write_text(content, encoding="utf-8")
        return path

    # ------------------------------------------------------------------------
    # Media Integrity Assertion Helpers
    # ------------------------------------------------------------------------

    def assert_valid_png(self, data_or_path: Any) -> None:
        """Asserts that the provided bytes or file path is a valid PNG image."""
        if isinstance(data_or_path, (str, pathlib.Path)):
            p = pathlib.Path(data_or_path)
            self.assertTrue(p.exists(), f"PNG file does not exist: {p}")
            data = p.read_bytes()
        else:
            data = data_or_path

        self.assertIsInstance(data, bytes, "PNG data must be bytes")
        self.assertGreaterEqual(len(data), 8, "PNG file is too small")
        # Magic bytes check: \x89PNG\r\n\x1a\n
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n", "Invalid PNG header magic bytes")
        # Pillow integrity check
        img = Image.open(io.BytesIO(data))
        self.assertEqual(img.format, "PNG", f"Expected PNG format, got {img.format}")
        self.assertGreater(img.width, 0)
        self.assertGreater(img.height, 0)

    def assert_valid_wav(self, data_or_path: Any) -> None:
        """Asserts that the provided bytes or file path is a valid PCM WAV audio."""
        if isinstance(data_or_path, (str, pathlib.Path)):
            p = pathlib.Path(data_or_path)
            self.assertTrue(p.exists(), f"WAV file does not exist: {p}")
            data = p.read_bytes()
        else:
            data = data_or_path

        self.assertIsInstance(data, bytes, "WAV data must be bytes")
        self.assertGreaterEqual(len(data), 44, "WAV file is smaller than canonical 44-byte header")
        self.assertEqual(data[:4], b"RIFF", "Missing RIFF chunk header")
        self.assertEqual(data[8:12], b"WAVE", "Missing WAVE format marker")
        with wave.open(io.BytesIO(data), "rb") as wf:
            self.assertIn(wf.getnchannels(), (1, 2))
            self.assertIn(wf.getsampwidth(), (1, 2, 4))
            self.assertGreater(wf.getframerate(), 0)
            self.assertGreaterEqual(wf.getnframes(), 0)

    # ------------------------------------------------------------------------
    # Mock Model Builders
    # ------------------------------------------------------------------------

    def get_mock_gdd(
        self,
        title: str = "Neon Cyber Runner",
        genre: str = GameGenre.PLATFORMER_2D.value,
        target_engine: str = EngineTarget.GODOT4.value,
    ) -> GameDesignDocument:
        """Builds a fully populated, valid GameDesignDocument."""
        return GameDesignDocument(
            title=title,
            genre=genre,
            target_engine=target_engine,
            high_concept="A high-speed cybernetic platformer navigating neon rooftop hazards.",
            gameplay_mechanics=[
                "Wall jump mechanics with variable jump height",
                "Dash ability with 0.5s cooldown",
                "Coin pickups with combo score multiplier",
            ],
            narrative_lore="In Neo-Veridia, rogue runners transport encrypted data cores across city rooftops.",
            art_style_guide={
                "palette": "Neon Cyan, Magenta, Deep Violet, Charcoal",
                "resolution": "32x32 pixel sprites",
                "visual_theme": "Cyberpunk synthwave",
            },
            audio_style_guide={
                "tempo_bpm": 130,
                "mood": "Uptempo synthwave",
                "sound_palette": "16-bit retro FM synth",
            },
            dialogue_trees=[
                DialogueNode(
                    node_id="intro_01",
                    speaker="Handler Zero",
                    text="Rooftops are swarming with drones. Move fast!",
                    choices=[
                        DialogueChoice(text="On my way.", next_node_id="run_start"),
                        DialogueChoice(text="Give me tactical intel.", next_node_id="intel_brief"),
                    ],
                )
            ],
            quests=[
                QuestSpec(
                    quest_id="quest_rooftop_dash",
                    title="Rooftop Infiltration",
                    description="Escape the patrol drones and reach Sector 4.",
                    objectives=[
                        QuestObjective(objective_id="obj_collect", description="Collect 10 data cores", target_count=10),
                        QuestObjective(objective_id="obj_reach", description="Reach Sector 4 gateway", target_count=1),
                    ],
                    rewards=["Cyber Dash Upgrade", "500 Credits"],
                )
            ],
            required_assets=[
                AssetRequirement(
                    asset_id="sprite_player",
                    asset_type=AssetType.SPRITE,
                    description="Player cyber-runner sprite",
                    prompt_hint="Cyberpunk runner in neon jacket running animation",
                    width=32,
                    height=32,
                ),
                AssetRequirement(
                    asset_id="sfx_jump",
                    asset_type=AssetType.AUDIO_SFX,
                    description="Retro chiptune jump sound effect",
                    prompt_hint="Sharp 8-bit upward pitch jump chime",
                    duration_sec=0.4,
                ),
                AssetRequirement(
                    asset_id="bgm_theme",
                    asset_type=AssetType.AUDIO_MUSIC,
                    description="Synthwave rooftop background music loop",
                    prompt_hint="130 BPM energetic synthesizer bassline loop",
                    duration_sec=8.0,
                ),
            ],
            scenes_plan=[
                ScenePlan(
                    scene_id="main_level",
                    name="MainLevel",
                    path="scenes/main_level.tscn",
                    description="Main rooftop level layout",
                    root_node_type="Node2D",
                    required_assets=["sprite_player", "sfx_jump", "bgm_theme"],
                    script_name="scripts/main_level.gd",
                )
            ],
        )

    def get_mock_manifest(self, project_name: str = "Neon Cyber Runner") -> AssetManifest:
        """Builds a valid AssetManifest populated with dummy PNG and WAV bytes."""
        sprite_bytes = self.create_test_image(32, 32, (0, 255, 200, 255))
        sfx_bytes = self.create_test_audio(duration=0.2, freq=600.0)
        bgm_bytes = self.create_test_audio(duration=0.5, freq=220.0)

        items = [
            AssetItem(
                asset_id="sprite_player",
                asset_type=AssetType.SPRITE,
                rel_path="assets/sprites/player.png",
                engine_path="res://assets/sprites/player.png",
                file_bytes=sprite_bytes,
                properties={"width": 32, "height": 32},
                is_mock=True,
            ),
            AssetItem(
                asset_id="sfx_jump",
                asset_type=AssetType.AUDIO_SFX,
                rel_path="audio/sfx/jump.wav",
                engine_path="res://audio/sfx/jump.wav",
                file_bytes=sfx_bytes,
                properties={"duration_sec": 0.2, "sample_rate": 44100},
                is_mock=True,
            ),
            AssetItem(
                asset_id="bgm_theme",
                asset_type=AssetType.AUDIO_MUSIC,
                rel_path="audio/music/theme.wav",
                engine_path="res://audio/music/theme.wav",
                file_bytes=bgm_bytes,
                properties={"duration_sec": 0.5, "sample_rate": 44100},
                is_mock=True,
            ),
        ]

        return AssetManifest(
            project_name=project_name,
            items=items,
            generator_agent=AgentRole.ARTIST,
        )

    def get_mock_scene(
        self,
        name: str = "MainLevel",
        path: str = "scenes/main_level.tscn",
    ) -> SceneAST:
        """Builds a valid SceneAST with root node and child nodes."""
        return SceneAST(
            name=name,
            path=path,
            ext_resources=[
                ExtResourceAST(id="1_tex", type="Texture2D", path="res://assets/sprites/player.png"),
                ExtResourceAST(id="2_sfx", type="AudioStream", path="res://audio/sfx/jump.wav"),
            ],
            nodes=[
                NodeAST(name=name, type="Node2D", parent=None),
                NodeAST(
                    name="Player",
                    type="CharacterBody2D",
                    parent=".",
                    properties={"motion_mode": 0},
                ),
                NodeAST(
                    name="Sprite2D",
                    type="Sprite2D",
                    parent="Player",
                    properties={"texture": 'ExtResource("1_tex")'},
                ),
            ],
            connections=[],
        )

    def get_mock_script(
        self,
        rel_path: str = "scripts/player.gd",
        language: str = "gdscript",
    ) -> ScriptSpec:
        """Builds a valid ScriptSpec."""
        code = (
            "extends CharacterBody2D\n\n"
            "const SPEED = 300.0\n"
            "const JUMP_VELOCITY = -400.0\n\n"
            "func _physics_process(delta: float) -> void:\n"
            "\tvelocity.x = SPEED\n"
            "\tmove_and_slide()\n"
        )
        return ScriptSpec(
            rel_path=rel_path,
            language=language,
            code=code,
            description="Player movement physics script",
        )
