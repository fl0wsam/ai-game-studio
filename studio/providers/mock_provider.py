"""
studio/providers/mock_provider.py - Pure-Python Deterministic Offline Mock Provider
Generates complete GDDs, procedural Pillow PNG sprites, stdlib wave 16-bit 44.1kHz WAVs,
and valid engine scripts/SceneASTs across 4 archetypes based on prompt SHA-256 determinism (F-ORCH-07).
"""
from __future__ import annotations

import hashlib
import io
import math
import random
import re
import struct
import wave
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from studio.core.models import (
    AssetItem,
    AssetManifest,
    AssetRequirement,
    AssetType,
    ConnectionAST,
    DiagnosticError,
    DialogueChoice,
    DialogueNode,
    ExtResourceAST,
    GameDesignDocument,
    GameGenre,
    NodeAST,
    QuestObjective,
    QuestSpec,
    SceneAST,
    ScenePlan,
    ScriptSpec,
)
from studio.providers.base import (
    BaseProviderClient,
    ProviderHealth,
    ProviderHealthStatus,
)


def derive_seed(prompt: str, custom_seed: Optional[int] = None) -> int:
    """
    Derives a 64-bit integer seed from a prompt string using SHA-256.
    Ensures identical prompts produce identical pseudo-random sequences.
    """
    if custom_seed is not None:
        return custom_seed
    cleaned = prompt.strip().lower().encode("utf-8")
    digest = hashlib.sha256(cleaned).hexdigest()
    return int(digest[:16], 16)


def detect_archetype(prompt: str) -> str:
    """Classifies a game premise into one of four supported game archetypes based on weighted keyword scores."""
    lower = prompt.lower()

    scores = {
        GameGenre.SPACE_SHOOTER.value: 0,
        GameGenre.TOP_DOWN_RPG.value: 0,
        GameGenre.GRID_PUZZLE.value: 0,
        GameGenre.PLATFORMER_2D.value: 0,
    }

    # Space Shooter strong indicators (3 pts)
    for k in ("space", "galaxy", "alien", "shmup", "invader", "starship", "spaceship", "asteroid", "rocket", "فضایی", "سفینه", "کهکشان", "سیاره", "تیربار"):
        if k in lower:
            scores[GameGenre.SPACE_SHOOTER.value] += 3

    # Secondary shooter indicators (1 pt)
    for k in ("shooter", "shmup", "تیراندازی"):
        if k in lower:
            scores[GameGenre.SPACE_SHOOTER.value] += 1

    # Top-Down RPG indicators (3 pts)
    for k in ("rpg", "dungeon", "crawler", "quest", "sword", "top-down", "hero", "knight", "magic", "monster", "combat", "zombie", "dragon", "slash", "نقش آفرینی", "شمشیر", "سیاهچال", "سیاه‌چال", "قهرمان", "جادو", "هیولا", "مبارزه", "زامبی", "اژدها"):
        if k in lower:
            scores[GameGenre.TOP_DOWN_RPG.value] += 3

    # Grid Puzzle indicators (3 pts)
    for k in ("puzzle", "grid", "match", "tile", "gem", "glyph", "board", "logic", "connect", "block", "sudoku", "پازل", "جورچین", "معما", "خانه", "تطبیق", "الماس", "بلوک", "فکری"):
        if k in lower:
            scores[GameGenre.GRID_PUZZLE.value] += 3

    # Platformer 2D indicators (3 pts)
    for k in ("platform", "platformer", "jump", "runner", "running", "ninja", "parkour", "obstacle", "dash", "leap", "سکو", "سکوبازی", "پرش", "بپرد", "دویدن", "دونده", "موانع", "جهش"):
        if k in lower:
            scores[GameGenre.PLATFORMER_2D.value] += 3

    max_score = max(scores.values())
    if max_score == 0:
        return GameGenre.PLATFORMER_2D.value

    # Return highest scoring archetype
    best_genre = GameGenre.PLATFORMER_2D.value
    best_score = -1
    # Check in order of specificity
    for g in (GameGenre.TOP_DOWN_RPG.value, GameGenre.GRID_PUZZLE.value, GameGenre.SPACE_SHOOTER.value, GameGenre.PLATFORMER_2D.value):
        if scores[g] > best_score:
            best_score = scores[g]
            best_genre = g

    return best_genre


# ============================================================================
# 1. Procedural Pixel Art Sprite Generator (Pillow)
# ============================================================================

class MockSpriteGenerator:
    """Synthesizes deterministic RGBA PNG pixel art using Pillow."""

    PALETTES = {
        "platformer_2d": {
            "primary": (0, 240, 255, 255),       # Cyan
            "secondary": (255, 0, 128, 255),     # Neon pink
            "accent": (255, 230, 0, 255),        # Yellow
            "base": (20, 20, 40, 255),           # Dark navy
            "highlight": (255, 255, 255, 255),   # White
            "outline": (10, 10, 20, 255),        # Arcade outline
        },
        "top_down_rpg": {
            "primary": (80, 90, 110, 255),       # Iron steel
            "secondary": (190, 45, 45, 255),     # Ruby red
            "accent": (235, 185, 45, 255),       # Gold
            "base": (30, 32, 40, 255),           # Dungeon slate
            "highlight": (220, 230, 240, 255),   # Silver shine
            "outline": (15, 15, 20, 255),
        },
        "space_shooter": {
            "primary": (180, 190, 215, 255),     # Hull titanium
            "secondary": (40, 130, 255, 255),    # Plasma blue
            "accent": (255, 60, 40, 255),        # Engine glow red
            "base": (12, 12, 28, 255),           # Void black
            "highlight": (255, 255, 255, 255),
            "outline": (8, 8, 16, 255),
        },
        "grid_puzzle": {
            "primary": (40, 120, 240, 255),      # Sapphire
            "secondary": (230, 40, 80, 255),     # Ruby
            "accent": (40, 210, 120, 255),       # Emerald
            "base": (25, 25, 35, 255),
            "highlight": (255, 255, 255, 255),
            "outline": (10, 10, 15, 255),
        },
    }

    @classmethod
    def generate(
        cls,
        asset_id: str,
        asset_type: str,
        width: int = 32,
        height: int = 32,
        archetype: str = "platformer_2d",
        seed: int = 0,
    ) -> bytes:
        """Procedurally synthesizes PNG bytes based on asset role and archetype."""
        rng = random.Random(seed ^ (hash(asset_id) & 0xFFFFFFFF))
        palette = cls.PALETTES.get(archetype, cls.PALETTES["platformer_2d"])

        aid_lower = asset_id.lower()
        if "bg" in aid_lower or "background" in aid_lower:
            return cls._generate_background(width, height, palette, rng)
        elif "tile" in aid_lower or "platform" in aid_lower or "ground" in aid_lower:
            return cls._generate_tile(width, height, palette, rng)
        elif "item" in aid_lower or "coin" in aid_lower or "crystal" in aid_lower or "gem" in aid_lower:
            return cls._generate_item(width, height, palette, rng)
        else:
            # Character or Enemy sprite (bilateral symmetry)
            is_enemy = "enemy" in aid_lower or "hazard" in aid_lower or "boss" in aid_lower
            return cls._generate_biped_sprite(width, height, palette, rng, is_enemy=is_enemy)

    @classmethod
    def _generate_biped_sprite(
        cls,
        w: int,
        h: int,
        palette: Dict[str, Tuple[int, int, int, int]],
        rng: random.Random,
        is_enemy: bool = False,
    ) -> bytes:
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        pixels = img.load()

        half_w = w // 2
        prim = palette["secondary"] if is_enemy else palette["primary"]
        sec = palette["accent"] if is_enemy else palette["secondary"]
        eye_col = (255, 50, 50, 255) if is_enemy else palette["accent"]

        # Generate left half and mirror to right half
        for y in range(4, h - 3):
            for x in range(2, half_w):
                # Probability based on vertical zone
                prob = 0.0
                col = prim

                # Head region
                if 4 <= y <= 11:
                    center_dist = abs(x - half_w)
                    if center_dist < half_w - 4:
                        prob = 0.85
                        col = prim
                        # Eye spot
                        if y == 8 and x == half_w - 4:
                            col = eye_col
                            prob = 1.0

                # Torso region
                elif 12 <= y <= 21:
                    if x >= 4:
                        prob = 0.90
                        col = sec if (x + y) % 4 == 0 else prim

                # Legs region
                elif 22 <= y <= h - 4:
                    if half_w - 6 <= x <= half_w - 2:
                        prob = 0.95
                        col = palette["base"]

                if rng.random() < prob:
                    pixels[x, y] = col
                    pixels[w - 1 - x, y] = col

        # Outline pass
        outlined = img.copy()
        out_px = outlined.load()
        out_col = palette["outline"]

        for y in range(h):
            for x in range(w):
                if pixels[x, y][3] > 0:
                    # Check 4-neighborhood
                    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < w and 0 <= ny < h and pixels[nx, ny][3] == 0:
                            out_px[nx, ny] = out_col

        buf = io.BytesIO()
        outlined.save(buf, format="PNG")
        return buf.getvalue()

    @classmethod
    def _generate_tile(
        cls,
        w: int,
        h: int,
        palette: Dict[str, Tuple[int, int, int, int]],
        rng: random.Random,
    ) -> bytes:
        img = Image.new("RGBA", (w, h), palette["base"])
        pixels = img.load()

        # Top border / grass rim
        rim_col = palette["accent"]
        for x in range(w):
            for y in range(min(4, h)):
                pixels[x, y] = rim_col

        # Staggered brick pattern
        brick_col = palette["primary"]
        for y in range(4, h):
            stagger = (w // 2) if ((y // 8) % 2 == 1) else 0
            for x in range(w):
                if y % 8 == 0 or (x + stagger) % (w // 2) == 0:
                    pixels[x, y] = palette["outline"]
                else:
                    if rng.random() < 0.15:
                        pixels[x, y] = brick_col

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @classmethod
    def _generate_item(
        cls,
        w: int,
        h: int,
        palette: Dict[str, Tuple[int, int, int, int]],
        rng: random.Random,
    ) -> bytes:
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        pixels = img.load()
        cx, cy = w / 2.0, h / 2.0
        r = min(w, h) / 2.5

        for y in range(h):
            for x in range(w):
                dist = math.hypot(x - cx + 0.5, y - cy + 0.5)
                if dist <= r:
                    if dist >= r - 1.2:
                        pixels[x, y] = palette["outline"]
                    elif dist <= r - 2.5 and x < cx and y < cy:
                        pixels[x, y] = palette["highlight"]
                    else:
                        pixels[x, y] = palette["accent"]

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @classmethod
    def _generate_background(
        cls,
        w: int,
        h: int,
        palette: Dict[str, Tuple[int, int, int, int]],
        rng: random.Random,
    ) -> bytes:
        # Default fallback dimensions if not set
        w = max(w, 64)
        h = max(h, 64)
        img = Image.new("RGBA", (w, h), (10, 12, 24, 255))
        pixels = img.load()

        # Vertical atmospheric gradient
        for y in range(h):
            ratio = y / float(h)
            r = int(10 + ratio * 30)
            g = int(12 + ratio * 20)
            b = int(24 + ratio * 60)
            for x in range(w):
                pixels[x, y] = (r, g, b, 255)

        # Starfield
        num_stars = (w * h) // 128
        for _ in range(num_stars):
            sx = rng.randint(0, w - 1)
            sy = rng.randint(0, h - 1)
            pixels[sx, sy] = palette["highlight"]

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


# ============================================================================
# 2. Synthetic Procedural Audio Generator (stdlib wave)
# ============================================================================

class MockAudioGenerator:
    """Synthesizes standard 16-bit 44.1kHz mono PCM RIFF WAV audio clips."""

    SAMPLE_RATE = 44100

    @classmethod
    def synthesize_wav(cls, samples: List[float]) -> bytes:
        """Converts float waveform samples [-1.0, 1.0] to 16-bit PCM WAV bytes."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)      # Mono
            wf.setsampwidth(2)      # 16-bit (2 bytes)
            wf.setframerate(cls.SAMPLE_RATE)
            clamped = [max(-1.0, min(1.0, s)) for s in samples]
            pcm_ints = [int(s * 32767.0) for s in clamped]
            packed = struct.pack(f"<{len(pcm_ints)}h", *pcm_ints)
            wf.writeframes(packed)
        return buf.getvalue()

    @classmethod
    def generate(
        cls,
        asset_id: str,
        asset_type: str,
        duration_sec: float = 1.0,
        archetype: str = "platformer_2d",
        seed: int = 0,
    ) -> bytes:
        """Selects waveform synthesis routine based on asset ID."""
        aid = asset_id.lower()
        rng = random.Random(seed ^ (hash(asset_id) & 0xFFFFFFFF))

        if "jump" in aid:
            return cls._generate_jump_sfx(rng)
        elif "shoot" in aid or "laser" in aid:
            return cls._generate_shoot_sfx(rng)
        elif "pickup" in aid or "coin" in aid:
            return cls._generate_pickup_sfx(rng)
        elif "hit" in aid or "hurt" in aid:
            return cls._generate_hit_sfx(rng)
        elif "explosion" in aid:
            return cls._generate_explosion_sfx(rng)
        elif "music" in aid or "bgm" in aid or "loop" in aid:
            return cls._generate_bgm_loop(rng, duration_sec=max(2.0, min(duration_sec, 6.0)))
        else:
            return cls._generate_generic_sfx(rng, duration_sec=duration_sec)

    @classmethod
    def _generate_jump_sfx(cls, rng: random.Random) -> bytes:
        """Exponential upward frequency sweep (140Hz -> 580Hz) with square wave."""
        duration = 0.18
        num_samples = int(duration * cls.SAMPLE_RATE)
        samples = []
        phase = 0.0

        for i in range(num_samples):
            t = i / float(cls.SAMPLE_RATE)
            freq = 140.0 * ((580.0 / 140.0) ** (t / duration))
            phase += 2.0 * math.pi * freq / cls.SAMPLE_RATE
            # Square wave with envelope decay
            sq = 1.0 if math.sin(phase) >= 0.0 else -1.0
            env = 1.0 - (t / duration)
            samples.append(sq * env * 0.4)

        return cls.synthesize_wav(samples)

    @classmethod
    def _generate_shoot_sfx(cls, rng: random.Random) -> bytes:
        """Downward laser chirp (880Hz -> 110Hz) with sawtooth wave."""
        duration = 0.14
        num_samples = int(duration * cls.SAMPLE_RATE)
        samples = []
        phase = 0.0

        for i in range(num_samples):
            t = i / float(cls.SAMPLE_RATE)
            freq = 880.0 - (770.0 * (t / duration))
            phase += freq / cls.SAMPLE_RATE
            # Sawtooth waveform [-1, 1]
            saw = 2.0 * (phase - math.floor(phase + 0.5))
            noise = rng.uniform(-0.1, 0.1)
            env = math.exp(-3.0 * t / duration)
            samples.append((saw + noise) * env * 0.4)

        return cls.synthesize_wav(samples)

    @classmethod
    def _generate_pickup_sfx(cls, rng: random.Random) -> bytes:
        """Two-tone coin arpeggio (B5 -> E6)."""
        duration = 0.28
        num_samples = int(duration * cls.SAMPLE_RATE)
        samples = []
        phase = 0.0

        for i in range(num_samples):
            t = i / float(cls.SAMPLE_RATE)
            freq = 987.77 if t < 0.08 else 1318.51
            phase += 2.0 * math.pi * freq / cls.SAMPLE_RATE
            sq = 1.0 if (phase % (2.0 * math.pi)) < (0.25 * 2.0 * math.pi) else -1.0
            env = math.exp(-6.0 * (t - (0.0 if t < 0.08 else 0.08)))
            samples.append(sq * env * 0.35)

        return cls.synthesize_wav(samples)

    @classmethod
    def _generate_hit_sfx(cls, rng: random.Random) -> bytes:
        """Downward frequency glide + noise punch."""
        duration = 0.20
        num_samples = int(duration * cls.SAMPLE_RATE)
        samples = []
        phase = 0.0

        for i in range(num_samples):
            t = i / float(cls.SAMPLE_RATE)
            freq = max(40.0, 220.0 - 170.0 * (t / duration))
            phase += 2.0 * math.pi * freq / cls.SAMPLE_RATE
            tone = math.sin(phase)
            noise = rng.uniform(-1.0, 1.0)
            env = (1.0 - t / duration) ** 2
            samples.append((0.5 * tone + 0.5 * noise) * env * 0.5)

        return cls.synthesize_wav(samples)

    @classmethod
    def _generate_explosion_sfx(cls, rng: random.Random) -> bytes:
        """Low frequency thump + white noise with exponential decay."""
        duration = 0.45
        num_samples = int(duration * cls.SAMPLE_RATE)
        samples = []
        phase = 0.0

        for i in range(num_samples):
            t = i / float(cls.SAMPLE_RATE)
            phase += 2.0 * math.pi * 75.0 / cls.SAMPLE_RATE
            thump = math.sin(phase)
            noise = rng.uniform(-1.0, 1.0)
            env = math.exp(-5.0 * t / duration)
            samples.append((0.3 * thump + 0.7 * noise) * env * 0.55)

        return cls.synthesize_wav(samples)

    @classmethod
    def _generate_bgm_loop(cls, rng: random.Random, duration_sec: float = 3.0) -> bytes:
        """Seamless multi-channel chiptune musical loop."""
        num_samples = int(duration_sec * cls.SAMPLE_RATE)
        samples = []

        # Chord progression: A minor pentatonic frequencies
        chords = [220.0, 261.63, 293.66, 329.63]
        melody_notes = [440.0, 523.25, 587.33, 659.25, 783.99]

        b_phase = 0.0
        m_phase = 0.0

        step_sec = 0.125  # 16th note at 120 BPM
        for i in range(num_samples):
            t = i / float(cls.SAMPLE_RATE)
            step_idx = int(t / step_sec)

            # Bass channel (quarter note changes)
            bass_freq = chords[(step_idx // 4) % len(chords)] / 2.0
            b_phase += 2.0 * math.pi * bass_freq / cls.SAMPLE_RATE
            bass = math.sin(b_phase) * 0.25

            # Melody channel (16th notes)
            mel_freq = melody_notes[(step_idx * 3 + (step_idx // 4)) % len(melody_notes)]
            m_phase += 2.0 * math.pi * mel_freq / cls.SAMPLE_RATE
            mel_sq = 1.0 if math.sin(m_phase) >= 0.0 else -1.0
            mel_env = 1.0 - ((t % step_sec) / step_sec) * 0.5
            melody = mel_sq * mel_env * 0.15

            # Percussion noise on beat 2 and 4
            beat_pos = (t % 0.5)
            perc = rng.uniform(-0.1, 0.1) * math.exp(-25.0 * beat_pos) if (int(t / 0.5) % 2 == 1) else 0.0

            sample = max(-1.0, min(1.0, bass + melody + perc))
            samples.append(sample)

        return cls.synthesize_wav(samples)

    @classmethod
    def _generate_generic_sfx(cls, rng: random.Random, duration_sec: float = 0.5) -> bytes:
        num_samples = int(duration_sec * cls.SAMPLE_RATE)
        samples = []
        phase = 0.0
        for i in range(num_samples):
            t = i / float(cls.SAMPLE_RATE)
            phase += 2.0 * math.pi * 440.0 / cls.SAMPLE_RATE
            samples.append(math.sin(phase) * (1.0 - t / duration_sec) * 0.3)
        return cls.synthesize_wav(samples)


# ============================================================================
# 3. Deterministic Code & SceneAST Generator
# ============================================================================

class MockCodeGenerator:
    """Generates SceneAST hierarchies and engine scripts for all 4 archetypes."""

    @classmethod
    def generate(
        cls,
        gdd: GameDesignDocument,
        manifest: AssetManifest,
        target_engine: str = "godot4",
    ) -> Tuple[List[SceneAST], List[ScriptSpec]]:
        genre = gdd.genre
        if genre == GameGenre.TOP_DOWN_RPG.value:
            return cls._generate_rpg(gdd, manifest, target_engine)
        elif genre == GameGenre.SPACE_SHOOTER.value:
            return cls._generate_shooter(gdd, manifest, target_engine)
        elif genre == GameGenre.GRID_PUZZLE.value:
            return cls._generate_puzzle(gdd, manifest, target_engine)
        else:
            return cls._generate_platformer(gdd, manifest, target_engine)

    # --- 1. 2D Platformer ---
    @classmethod
    def _generate_platformer(
        cls, gdd: GameDesignDocument, manifest: AssetManifest, target_engine: str
    ) -> Tuple[List[SceneAST], List[ScriptSpec]]:
        scene = SceneAST(
            name="MainLevel",
            path="scenes/main_level.tscn",
            ext_resources=[
                ExtResourceAST(id="1_player_tex", type="Texture2D", path="res://assets/sprites/sprite_player.png"),
                ExtResourceAST(id="2_player_script", type="Script", path="res://scripts/player.gd"),
                ExtResourceAST(id="3_jump_sfx", type="AudioStream", path="res://audio/sfx/sfx_jump.wav"),
            ],
            nodes=[
                NodeAST(name="MainLevel", type="Node2D"),
                NodeAST(name="Player", type="CharacterBody2D", parent=".", script_id="2_player_script", properties={"speed": 300.0, "jump_velocity": -420.0}),
                NodeAST(name="Sprite2D", type="Sprite2D", parent="Player", properties={"texture": "res://assets/sprites/sprite_player.png"}),
                NodeAST(name="CollisionShape2D", type="CollisionShape2D", parent="Player", properties={"shape_type": "RectangleShape2D", "size": [32, 32]}),
                NodeAST(name="JumpAudio", type="AudioStreamPlayer2D", parent="Player", properties={"stream": "res://audio/sfx/sfx_jump.wav"}),
            ],
            connections=[],
        )

        gd_code = (
            "extends CharacterBody2D\n\n"
            "@export var speed: float = 300.0\n"
            "@export var jump_velocity: float = -420.0\n"
            "@export var gravity: float = 980.0\n\n"
            "@onready var sprite: Sprite2D = $Sprite2D\n"
            "@onready var jump_sfx: AudioStreamPlayer2D = $JumpAudio\n\n"
            "func _physics_process(delta: float) -> void:\n"
            "    if not is_on_floor():\n"
            "        velocity.y += gravity * delta\n\n"
            "    if Input.is_action_just_pressed(\"ui_accept\") and is_on_floor():\n"
            "        velocity.y = jump_velocity\n"
            "        if jump_sfx:\n"
            "            jump_sfx.play()\n\n"
            "    var direction := Input.get_axis(\"ui_left\", \"ui_right\")\n"
            "    if direction != 0.0:\n"
            "        velocity.x = direction * speed\n"
            "        if sprite:\n"
            "            sprite.flip_h = direction < 0.0\n"
            "    else:\n"
            "        velocity.x = move_toward(velocity.x, 0.0, speed * delta * 5.0)\n\n"
            "    move_and_slide()\n"
        )

        py_code = (
            "import pygame\n\n"
            "class Player(pygame.sprite.Sprite):\n"
            "    def __init__(self, x: int = 100, y: int = 100):\n"
            "        super().__init__()\n"
            "        self.image = pygame.image.load('assets/sprites/sprite_player.png').convert_alpha()\n"
            "        self.rect = self.image.get_rect(topleft=(x, y))\n"
            "        self.vel_x = 0.0\n"
            "        self.vel_y = 0.0\n"
            "        self.speed = 5.0\n"
            "        self.jump_power = -12.0\n"
            "        self.gravity = 0.6\n"
            "        self.on_ground = True\n\n"
            "    def update(self, keys):\n"
            "        self.vel_x = 0\n"
            "        if keys[pygame.K_LEFT] or keys[pygame.K_a]:\n"
            "            self.vel_x = -self.speed\n"
            "        if keys[pygame.K_RIGHT] or keys[pygame.K_d]:\n"
            "            self.vel_x = self.speed\n"
            "        if (keys[pygame.K_SPACE] or keys[pygame.K_UP]) and self.on_ground:\n"
            "            self.vel_y = self.jump_power\n"
            "            self.on_ground = False\n\n"
            "        self.vel_y += self.gravity\n"
            "        self.rect.x += int(self.vel_x)\n"
            "        self.rect.y += int(self.vel_y)\n"
        )

        scripts = [
            ScriptSpec(rel_path="scripts/player.gd", language="gdscript", code=gd_code, extends_class="CharacterBody2D"),
            ScriptSpec(rel_path="scripts/player.py", language="python", code=py_code),
        ]
        return [scene], scripts

    # --- 2. Top-Down RPG ---
    @classmethod
    def _generate_rpg(
        cls, gdd: GameDesignDocument, manifest: AssetManifest, target_engine: str
    ) -> Tuple[List[SceneAST], List[ScriptSpec]]:
        scene = SceneAST(
            name="WorldMap",
            path="scenes/world_map.tscn",
            ext_resources=[
                ExtResourceAST(id="1_hero_tex", type="Texture2D", path="res://assets/sprites/sprite_player.png"),
                ExtResourceAST(id="2_hero_script", type="Script", path="res://scripts/hero.gd"),
            ],
            nodes=[
                NodeAST(name="WorldMap", type="Node2D"),
                NodeAST(name="Hero", type="CharacterBody2D", parent=".", script_id="2_hero_script", properties={"speed": 220.0}),
                NodeAST(name="Sprite2D", type="Sprite2D", parent="Hero", properties={"texture": "res://assets/sprites/sprite_player.png"}),
                NodeAST(name="CollisionShape2D", type="CollisionShape2D", parent="Hero", properties={"shape_type": "CircleShape2D", "radius": 16.0}),
            ],
        )

        gd_code = (
            "extends CharacterBody2D\n\n"
            "@export var speed: float = 220.0\n"
            "@onready var sprite: Sprite2D = $Sprite2D\n\n"
            "func _physics_process(_delta: float) -> void:\n"
            "    var direction := Input.get_vector(\"ui_left\", \"ui_right\", \"ui_up\", \"ui_down\")\n"
            "    if direction != Vector2.ZERO:\n"
            "        velocity = direction.normalized() * speed\n"
            "    else:\n"
            "        velocity = Vector2.ZERO\n"
            "    move_and_slide()\n"
        )

        scripts = [ScriptSpec(rel_path="scripts/hero.gd", language="gdscript", code=gd_code, extends_class="CharacterBody2D")]
        return [scene], scripts

    # --- 3. Space Shooter ---
    @classmethod
    def _generate_shooter(
        cls, gdd: GameDesignDocument, manifest: AssetManifest, target_engine: str
    ) -> Tuple[List[SceneAST], List[ScriptSpec]]:
        scene = SceneAST(
            name="SpaceArena",
            path="scenes/space_arena.tscn",
            ext_resources=[
                ExtResourceAST(id="1_ship_tex", type="Texture2D", path="res://assets/sprites/sprite_player.png"),
                ExtResourceAST(id="2_ship_script", type="Script", path="res://scripts/starship.gd"),
                ExtResourceAST(id="3_laser_sfx", type="AudioStream", path="res://audio/sfx/sfx_shoot.wav"),
            ],
            nodes=[
                NodeAST(name="SpaceArena", type="Node2D"),
                NodeAST(name="Starship", type="Area2D", parent=".", script_id="2_ship_script", properties={"speed": 380.0}),
                NodeAST(name="Sprite2D", type="Sprite2D", parent="Starship", properties={"texture": "res://assets/sprites/sprite_player.png"}),
                NodeAST(name="ShootAudio", type="AudioStreamPlayer2D", parent="Starship", properties={"stream": "res://audio/sfx/sfx_shoot.wav"}),
            ],
        )

        gd_code = (
            "extends Area2D\n\n"
            "@export var speed: float = 380.0\n"
            "@onready var shoot_sfx: AudioStreamPlayer2D = $ShootAudio\n\n"
            "func _process(delta: float) -> void:\n"
            "    var motion := Vector2.ZERO\n"
            "    if Input.is_action_pressed(\"ui_left\"):\n"
            "        motion.x -= 1.0\n"
            "    if Input.is_action_pressed(\"ui_right\"):\n"
            "        motion.x += 1.0\n"
            "    if Input.is_action_pressed(\"ui_up\"):\n"
            "        motion.y -= 1.0\n"
            "    if Input.is_action_pressed(\"ui_down\"):\n"
            "        motion.y += 1.0\n"
            "    position += motion.normalized() * speed * delta\n"
            "    position.x = clamp(position.x, 24.0, 1256.0)\n"
            "    position.y = clamp(position.y, 24.0, 696.0)\n\n"
            "    if Input.is_action_just_pressed(\"ui_accept\"):\n"
            "        if shoot_sfx:\n"
            "            shoot_sfx.play()\n"
        )

        scripts = [ScriptSpec(rel_path="scripts/starship.gd", language="gdscript", code=gd_code, extends_class="Area2D")]
        return [scene], scripts

    # --- 4. Grid Puzzle ---
    @classmethod
    def _generate_puzzle(
        cls, gdd: GameDesignDocument, manifest: AssetManifest, target_engine: str
    ) -> Tuple[List[SceneAST], List[ScriptSpec]]:
        scene = SceneAST(
            name="PuzzleBoard",
            path="scenes/puzzle_board.tscn",
            ext_resources=[
                ExtResourceAST(id="1_board_script", type="Script", path="res://scripts/board.gd"),
            ],
            nodes=[
                NodeAST(name="PuzzleBoard", type="Node2D", script_id="1_board_script"),
            ],
        )

        gd_code = (
            "extends Node2D\n\n"
            "const GRID_WIDTH: int = 8\n"
            "const GRID_HEIGHT: int = 8\n"
            "const TILE_SIZE: int = 64\n\n"
            "var grid: Array = []\n\n"
            "func _ready() -> void:\n"
            "    initialize_grid()\n\n"
            "func initialize_grid() -> void:\n"
            "    grid.clear()\n"
            "    for x in range(GRID_WIDTH):\n"
            "        var col = []\n"
            "        for y in range(GRID_HEIGHT):\n"
            "            col.append(randi() % 5)\n"
            "        grid.append(col)\n\n"
            "func check_matches() -> Array:\n"
            "    var matches := []\n"
            "    for x in range(GRID_WIDTH):\n"
            "        for y in range(GRID_HEIGHT - 2):\n"
            "            if grid[x][y] == grid[x][y+1] and grid[x][y] == grid[x][y+2]:\n"
            "                matches.append(Vector2i(x, y))\n"
            "    return matches\n"
        )

        scripts = [ScriptSpec(rel_path="scripts/board.gd", language="gdscript", code=gd_code, extends_class="Node2D")]
        return [scene], scripts


# ============================================================================
# 4. Mock GDD Generator
# ============================================================================

class MockGDDGenerator:
    """Generates complete, deterministic GameDesignDocument models."""

    ARCHETYPE_TITLES = {
        "platformer_2d": (["Cyber", "Neon", "Retro", "Quantum"], ["Runner", "Odyssey", "Leap", "Dash"]),
        "top_down_rpg": (["Dungeon", "Shadow", "Crown", "Mystic"], ["Crawler", "Quest", "Chronicle", "Blade"]),
        "space_shooter": (["Cosmic", "Solar", "Galactic", "Void"], ["Defender", "Vanguard", "Interceptor", "Assault"]),
        "grid_puzzle": (["Ancient", "Runic", "Crystal", "Glyph"], ["Match", "Puzzle", "Labyrinth", "Enigma"]),
    }

    @classmethod
    def generate(cls, prompt: str, seed: int, target_engine: str = "godot4", archetype: Optional[str] = None) -> GameDesignDocument:
        rng = random.Random(seed)
        arch = archetype or detect_archetype(prompt)
        prefixes, nouns = cls.ARCHETYPE_TITLES.get(arch, cls.ARCHETYPE_TITLES["platformer_2d"])
        title = f"{rng.choice(prefixes)} {rng.choice(nouns)}"

        mechanics = [
            f"Core Archetype Mechanics: High-precision responsive control for {arch.replace('_', ' ')}",
            "Collectibles & Scoring: Items grant score with consecutive pickup multiplier",
            "Health & Survival: 3 hearts; 1.0s invulnerability flash on damage collision",
            "Win Condition: Reach exit portal or clear primary objective to win",
            "Loss Condition: Falling into hazards or health reaching zero triggers restart",
        ]

        lore = (
            f"In the vibrant expanse of {title}, ancient core engines have destabilized the realm. "
            f"The protagonist must venture across treacherous sectors, evade sentinel guardians, "
            f"and retrieve the lost power matrices to re-establish universal equilibrium."
        )

        dialogues = [
            DialogueNode(
                node_id="npc_mentor_greet",
                speaker="Elder Guide",
                text=f"Welcome, operative, to {title}. The path ahead is fraught with hazards.",
                choices=[
                    DialogueChoice(text="I am ready to proceed.", next_node_id="npc_quest_accept"),
                    DialogueChoice(text="Explain the mission objectives.", next_node_id="npc_mission_brief"),
                ],
            )
        ]

        quests = [
            QuestSpec(
                quest_id="quest_primary_recon",
                title="Sector Stabilization",
                description="Navigate through the hazardous sector, collect 5 energy crystals, and reach the exit portal.",
                objectives=[
                    QuestObjective(objective_id="obj_0", description="Collect 5 energy crystals", target_count=5),
                    QuestObjective(objective_id="obj_1", description="Defeat sector guardian", target_count=1),
                    QuestObjective(objective_id="obj_2", description="Enter extraction zone", target_count=1),
                ],
                rewards=["500 Energy Credits", "Titanium Hull Upgrade"],
            )
        ]

        required_assets = [
            AssetRequirement(asset_id="sprite_player", asset_type=AssetType.SPRITE, description="Hero player sprite", target_rel_path="assets/sprites/sprite_player.png", width=32, height=32),
            AssetRequirement(asset_id="sprite_enemy", asset_type=AssetType.SPRITE, description="Patrolling enemy sprite", target_rel_path="assets/sprites/sprite_enemy.png", width=32, height=32),
            AssetRequirement(asset_id="sprite_item", asset_type=AssetType.SPRITE, description="Collectible energy crystal", target_rel_path="assets/sprites/sprite_item.png", width=16, height=16),
            AssetRequirement(asset_id="sprite_tile", asset_type=AssetType.SPRITE, description="Platform and wall tile texture", target_rel_path="assets/sprites/sprite_tile.png", width=32, height=32),
            AssetRequirement(asset_id="sprite_bg", asset_type=AssetType.TEXTURE, description="Parallax starfield background", target_rel_path="assets/sprites/sprite_bg.png", width=256, height=256),
            AssetRequirement(asset_id="sfx_jump", asset_type=AssetType.AUDIO_SFX, description="Jump action audio chime", target_rel_path="audio/sfx/sfx_jump.wav", duration_sec=0.2),
            AssetRequirement(asset_id="sfx_shoot", asset_type=AssetType.AUDIO_SFX, description="Laser firing sound effect", target_rel_path="audio/sfx/sfx_shoot.wav", duration_sec=0.15),
            AssetRequirement(asset_id="sfx_pickup", asset_type=AssetType.AUDIO_SFX, description="Item collection arpeggio chime", target_rel_path="audio/sfx/sfx_pickup.wav", duration_sec=0.3),
            AssetRequirement(asset_id="sfx_hit", asset_type=AssetType.AUDIO_SFX, description="Damage collision audio punch", target_rel_path="audio/sfx/sfx_hit.wav", duration_sec=0.2),
            AssetRequirement(asset_id="sfx_explosion", asset_type=AssetType.AUDIO_SFX, description="Destruction burst audio", target_rel_path="audio/sfx/sfx_explosion.wav", duration_sec=0.45),
            AssetRequirement(asset_id="bgm_loop", asset_type=AssetType.AUDIO_MUSIC, description="Seamless background chiptune theme", target_rel_path="audio/music/bgm_loop.wav", duration_sec=3.0),
        ]

        scenes_plan = [
            ScenePlan(
                scene_id="main_scene",
                name="MainLevel",
                path="scenes/main_level.tscn",
                description="Primary playable game level",
                root_node_type="Node2D",
                required_assets=["sprite_player", "sprite_bg", "sprite_tile"],
                required_scripts=["scripts/player.gd"],
            ),
            ScenePlan(
                scene_id="player_scene",
                name="Player",
                path="scenes/player.tscn",
                description="Player actor scene with physics and collision",
                root_node_type="CharacterBody2D",
                required_assets=["sprite_player", "sfx_jump"],
                required_scripts=["scripts/player.gd"],
            ),
        ]

        return GameDesignDocument(
            title=title,
            genre=arch,
            target_engine=target_engine,
            high_concept=f"An intense, high-energy {arch.replace('_', ' ')} based on: '{prompt}'",
            gameplay_mechanics=mechanics,
            narrative_lore=lore,
            art_style_guide={
                "style": "16-bit retro arcade pixel art",
                "palette": "vibrant_synthwave",
                "resolution": "32x32",
                "tile_size": 32,
            },
            audio_style_guide={
                "style": "8-bit chiptune synthesis",
                "tempo_bpm": 128,
                "key": "A minor",
            },
            dialogue_trees=dialogues,
            quests=quests,
            required_assets=required_assets,
            scenes_plan=scenes_plan,
        )


# ============================================================================
# 5. MockProviderClient (BaseProviderClient Implementation)
# ============================================================================

class MockProviderClient(BaseProviderClient):
    """
    Pure-Python 100% offline deterministic provider client.
    Synthesizes complete GDDs, procedural Pillow PNGs, 16-bit 44.1kHz WAVs,
    and valid engine scripts with zero network or external API token requirements.
    """

    def __init__(
        self,
        provider_name: str = "mock",
        model_name: str = "offline-procedural-v1",
        api_key: Optional[str] = None,
        rate_limiter: Optional[Any] = None,
        circuit_breaker: Optional[Any] = None,
        backoff_handler: Optional[Any] = None,
    ) -> None:
        super().__init__(
            provider_name=provider_name,
            model_name=model_name,
            api_key=api_key or "mock-offline-key",
            rate_limiter=rate_limiter,
            circuit_breaker=circuit_breaker,
            backoff_handler=backoff_handler,
        )

    # -----------------------------------------------------------------------
    # GDD Generation (OpenAI Mock)
    # -----------------------------------------------------------------------

    async def generate_gdd(
        self,
        prompt: str,
        context: Optional[Dict[str, Any]] = None,
        archetype: Optional[str] = None,
        **kwargs: Any,
    ) -> GameDesignDocument:
        seed = derive_seed(prompt, kwargs.get("seed"))
        target_engine = (context or {}).get("target_engine", kwargs.get("target_engine", "godot4"))
        return MockGDDGenerator.generate(prompt=prompt, seed=seed, target_engine=target_engine, archetype=archetype)

    async def generate_dialogue_tree(
        self,
        npc_name: str,
        quest_context: str,
        personality: str = "mysterious",
    ) -> Dict[str, Any]:
        return {
            "root_node_id": "root",
            "nodes": {
                "root": {
                    "node_id": "root",
                    "speaker": npc_name,
                    "text": f"Greetings! As a {personality} wanderer, I hold secrets regarding {quest_context}.",
                    "choices": [
                        {"text": "Tell me more.", "next_node_id": "branch_1"},
                        {"text": "Farewell.", "next_node_id": None},
                    ],
                }
            },
        }

    async def generate_quest_mechanics(self, quest_prompt: str) -> Dict[str, Any]:
        return {
            "quest_id": "mock_quest_01",
            "title": f"Quest: {quest_prompt[:30]}",
            "stages": [
                {"stage": 1, "description": "Investigate anomaly"},
                {"stage": 2, "description": "Defeat guardian"},
            ],
            "rewards": ["100 XP", "Health Potion"],
            "fail_conditions": ["Player eliminated"],
        }

    # -----------------------------------------------------------------------
    # Multimodal Asset Generation (Gemini Mock)
    # -----------------------------------------------------------------------

    async def generate_assets(
        self,
        gdd: Any,
        context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> AssetManifest:
        seed = derive_seed(getattr(gdd, "title", "Game"))
        genre = getattr(gdd, "genre", "platformer_2d")
        project_name = getattr(gdd, "title", "AIGameStudioProject")
        items: List[AssetItem] = []

        for req in getattr(gdd, "required_assets", []):
            asset_id = req.asset_id
            asset_type_val = req.asset_type.value if hasattr(req.asset_type, "value") else str(req.asset_type)
            rel_path = req.get_rel_path() if hasattr(req, "get_rel_path") else req.rel_path or f"assets/{asset_id}"

            file_bytes: bytes
            if "sprite" in asset_type_val or "texture" in asset_type_val:
                w = req.width or 32
                h = req.height or 32
                file_bytes = MockSpriteGenerator.generate(
                    asset_id=asset_id,
                    asset_type=asset_type_val,
                    width=w,
                    height=h,
                    archetype=genre,
                    seed=seed,
                )
            elif "audio" in asset_type_val or "sfx" in asset_type_val or "music" in asset_type_val:
                duration = req.duration_sec or (3.0 if "music" in asset_type_val else 0.5)
                file_bytes = MockAudioGenerator.generate(
                    asset_id=asset_id,
                    asset_type=asset_type_val,
                    duration_sec=duration,
                    archetype=genre,
                    seed=seed,
                )
            else:
                # Cutscene / storyboard fallback image
                file_bytes = MockSpriteGenerator.generate(
                    asset_id=asset_id,
                    asset_type="sprite",
                    width=64,
                    height=64,
                    archetype=genre,
                    seed=seed,
                )

            item = AssetItem(
                asset_id=asset_id,
                asset_type=asset_type_val,
                rel_path=rel_path,
                engine_path=f"res://{rel_path.lstrip('/')}",
                file_bytes=file_bytes,
                properties={"mock": True, "size_bytes": len(file_bytes)},
                is_mock=True,
            )
            items.append(item)

        return AssetManifest(project_name=project_name, items=items)

    async def generate_image(
        self,
        prompt: str,
        width: int = 512,
        height: int = 512,
        format: str = "PNG",
    ) -> bytes:
        seed = derive_seed(prompt)
        return MockSpriteGenerator.generate(
            asset_id="image",
            asset_type="sprite",
            width=min(width, 128),
            height=min(height, 128),
            archetype=detect_archetype(prompt),
            seed=seed,
        )

    async def generate_audio(
        self,
        prompt: str,
        duration_sec: float = 2.0,
        audio_type: str = "sfx",
    ) -> bytes:
        seed = derive_seed(prompt)
        return MockAudioGenerator.generate(
            asset_id="audio",
            asset_type=audio_type,
            duration_sec=duration_sec,
            archetype=detect_archetype(prompt),
            seed=seed,
        )

    async def generate_cutscene(self, prompt: str, duration_sec: float = 5.0) -> bytes:
        return await self.generate_image(prompt, width=256, height=144)

    # -----------------------------------------------------------------------
    # Engine Code & Scene Generation (Claude Mock)
    # -----------------------------------------------------------------------

    async def generate_code(
        self,
        gdd: Any,
        manifest: Any,
        context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Tuple[List[SceneAST], List[ScriptSpec]]:
        target_engine = getattr(gdd, "target_engine", "godot4")
        return MockCodeGenerator.generate(gdd, manifest, target_engine=target_engine)

    async def generate_code_and_scenes(
        self,
        gdd: Any,
        manifest: Any,
        context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        scenes, scripts = await self.generate_code(gdd, manifest, context=context, **kwargs)
        return {"scenes": scenes, "scripts": scripts}

    async def repair_code(
        self,
        errors: List[DiagnosticError],
        current_scripts: List[ScriptSpec],
        current_scenes: List[SceneAST],
    ) -> Dict[str, Any]:
        """Fixes diagnostic code/scene errors deterministically."""
        repaired_scripts: List[ScriptSpec] = []
        for s in current_scripts:
            code = s.code
            for err in errors:
                if err.file_path in s.rel_path:
                    # Clean up error comment / patch
                    code = f"# Fixed: {err.message}\n" + code
            repaired_scripts.append(ScriptSpec(rel_path=s.rel_path, language=s.language, code=code, extends_class=s.extends_class))

        return {"scripts": repaired_scripts, "scenes": current_scenes}

    async def repair_assets(
        self,
        errors: List[DiagnosticError],
        current_manifest: Optional[AssetManifest],
    ) -> AssetManifest:
        """Synthesizes missing assets flagged by verification errors."""
        manifest = current_manifest or AssetManifest(project_name="RepairedProject")
        for err in errors:
            m_path = re.search(r"expected at ([^\)\s]+)", err.message)
            if m_path:
                rel_path = m_path.group(1).rstrip(".")
                asset_id = rel_path.split("/")[-1].split(".")[0] or "repaired_asset"
            elif err.file_path.startswith("res://"):
                rel_path = err.file_path[6:]
                asset_id = rel_path.split("/")[-1].split(".")[0] or "repaired_asset"
            else:
                rel_path = err.file_path
                asset_id = rel_path.split("/")[-1].split(".")[0] or "repaired_asset"

            if not manifest.get_item(asset_id):
                is_audio = "audio" in rel_path or rel_path.endswith(".wav")
                is_svg = rel_path.endswith(".svg")
                atype = AssetType.AUDIO_SFX.value if is_audio else AssetType.SPRITE.value
                if is_svg:
                    b = b"<svg xmlns='http://www.w3.org/2000/svg' width='32' height='32'><rect width='32' height='32' fill='#38bdf8'/></svg>"
                elif is_audio:
                    b = MockAudioGenerator.generate(asset_id, atype)
                else:
                    b = MockSpriteGenerator.generate(asset_id, atype)
                manifest.add_item(AssetItem(
                    asset_id=asset_id,
                    asset_type=atype,
                    rel_path=rel_path,
                    engine_path=f"res://{rel_path.lstrip('/')}",
                    file_bytes=b,
                    is_mock=True,
                ))
        return manifest

    # -----------------------------------------------------------------------
    # Procedural Helpers for External Clients
    # -----------------------------------------------------------------------

    def generate_procedural_sprite(self, prompt: str, width: int = 64, height: int = 64) -> bytes:
        seed = derive_seed(prompt)
        return MockSpriteGenerator.generate(
            asset_id="procedural_sprite",
            asset_type="sprite",
            width=width,
            height=height,
            archetype=detect_archetype(prompt),
            seed=seed,
        )

    def generate_procedural_audio(self, prompt: str, duration_sec: float = 2.0, audio_type: str = "sfx") -> bytes:
        seed = derive_seed(prompt)
        return MockAudioGenerator.generate(
            asset_id="procedural_audio",
            asset_type=audio_type,
            duration_sec=duration_sec,
            archetype=detect_archetype(prompt),
            seed=seed,
        )

    def generate_archetype_code(self, gdd: Any, manifest: Any) -> Tuple[List[SceneAST], List[ScriptSpec]]:
        return MockCodeGenerator.generate(gdd, manifest, target_engine=getattr(gdd, "target_engine", "godot4"))

    # -----------------------------------------------------------------------
    # Health Probe
    # -----------------------------------------------------------------------

    async def check_health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_name="mock",
            status=ProviderHealthStatus.HEALTHY,
            latency_ms=0.1,
            message="Deterministic Mock Provider ready (100% offline)",
        )
