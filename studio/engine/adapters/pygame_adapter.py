"""
studio/engine/adapters/pygame_adapter.py - Pygame / Python Export Adapter.
Exports standalone runnable Python games with Pygame and an embedded pure-Python headless mock shim.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from studio.core.models import (
    AssetManifest,
    EngineTarget,
    GameDesignDocument,
    ScriptSpec,
)
from studio.engine.adapters.base import BaseEngineAdapter, EngineProjectManifest
from studio.engine.ast_models import SceneAST

# ============================================================================
# Pure-Python Headless Shim Source Code
# ============================================================================

ENGINE_SHIM_CODE = '''"""
game/engine_shim.py - Pure-Python Headless Pygame Shim.
Provides seamless fallback for environments where pygame is not installed or X11 is absent.
"""
import sys
import time

QUIT = 256
KEYDOWN = 768
KEYUP = 769

K_LEFT = 1073741904
K_RIGHT = 1073741903
K_UP = 1073741906
K_DOWN = 1073741905
K_SPACE = 32
K_a = 97
K_d = 100
K_w = 119
K_s = 115
K_e = 101
K_r = 114
K_j = 106

class Event:
    def __init__(self, type_id=0, **kwargs):
        self.type = type_id
        for k, v in kwargs.items():
            setattr(self, k, v)

class Rect:
    def __init__(self, left, top, width, height):
        self.x = int(left)
        self.y = int(top)
        self.width = int(width)
        self.height = int(height)

    @property
    def left(self): return self.x
    @left.setter
    def left(self, val): self.x = int(val)

    @property
    def right(self): return self.x + self.width
    @right.setter
    def right(self, val): self.x = int(val) - self.width

    @property
    def top(self): return self.y
    @top.setter
    def top(self, val): self.y = int(val)

    @property
    def bottom(self): return self.y + self.height
    @bottom.setter
    def bottom(self, val): self.y = int(val) - self.height

    @property
    def centerx(self): return self.x + self.width // 2
    @centerx.setter
    def centerx(self, val): self.x = int(val) - self.width // 2

    @property
    def centery(self): return self.y + self.height // 2
    @centery.setter
    def centery(self, val): self.y = int(val) - self.height // 2

    @property
    def center(self): return (self.centerx, self.centery)
    @center.setter
    def center(self, val): self.centerx, self.centery = val

    def colliderect(self, other):
        return not (
            self.right <= other.left or
            self.left >= other.right or
            self.bottom <= other.top or
            self.top >= other.bottom
        )

class Surface:
    def __init__(self, size=(100, 100)):
        self.size = size
        self.width, self.height = size

    def fill(self, color): pass
    def get_width(self): return self.width
    def get_height(self): return self.height
    def get_rect(self, **kwargs):
        r = Rect(0, 0, self.width, self.height)
        for k, v in kwargs.items():
            setattr(r, k, v)
        return r
    def blit(self, source, dest, area=None): pass

class Clock:
    def __init__(self):
        self._last = time.time()
    def tick(self, framerate=60):
        now = time.time()
        elapsed = now - self._last
        self._last = now
        target_delta = 1.0 / max(framerate, 1)
        if elapsed < target_delta:
            time.sleep(target_delta - elapsed)
        return int(elapsed * 1000)

class Display:
    def __init__(self):
        self._mode = Surface((1280, 720))
    def set_mode(self, size, flags=0):
        self._mode = Surface(size)
        return self._mode
    def set_caption(self, title): pass
    def flip(self): pass
    def update(self): pass

class EventModule:
    def get(self): return []
    def pump(self): pass

class KeyPressed:
    def __init__(self, pressed=None):
        self._pressed = pressed or {}
    def __getitem__(self, key):
        return self._pressed.get(key, False)
    def __contains__(self, key):
        return key in self._pressed

class KeyModule:
    def __init__(self):
        self._pressed = {}
    def get_pressed(self):
        return KeyPressed(self._pressed)

class DrawModule:
    def rect(self, surface, color, rect, width=0, border_radius=0): pass
    def circle(self, surface, color, center, radius, width=0): pass
    def line(self, surface, color, start_pos, end_pos, width=1): pass
    def polygon(self, surface, color, points, width=0): pass

class ImageModule:
    def load(self, filename):
        return Surface((64, 64))

class MixerSound:
    def play(self): pass
    def stop(self): pass

class MixerModule:
    def init(self): pass
    def Sound(self, filename): return MixerSound()
class TimeModule:
    Clock = Clock
    def get_ticks(self): return int(time.time() * 1000)
    def wait(self, ms): time.sleep(ms / 1000.0)
    def delay(self, ms): time.sleep(ms / 1000.0)

class PygameShim:
    Clock = Clock
    Rect = Rect
    Surface = Surface
    QUIT = QUIT
    KEYDOWN = KEYDOWN
    KEYUP = KEYUP
    K_LEFT = K_LEFT
    K_RIGHT = K_RIGHT
    K_UP = K_UP
    K_DOWN = K_DOWN
    K_SPACE = K_SPACE
    K_a = K_a
    K_d = K_d
    K_w = K_w
    K_s = K_s
    K_e = K_e
    K_r = K_r
    K_j = K_j

    def __init__(self):
        self.display = Display()
        self.event = EventModule()
        self.key = KeyModule()
        self.draw = DrawModule()
        self.image = ImageModule()
        self.mixer = MixerModule()
        self.time = TimeModule()

    def init(self): return (6, 0)
    def quit(self): pass

pygame = PygameShim()
'''


class PygameAdapter(BaseEngineAdapter):
    """Pygame adapter exporting standalone Python 2D arcade games."""

    @property
    def target(self) -> EngineTarget:
        return EngineTarget.PYGAME

    @property
    def display_name(self) -> str:
        return "Pygame (Python 2D)"

    @property
    def file_extensions(self) -> List[str]:
        return [".py"]

    def export_project(
        self,
        gdd: GameDesignDocument,
        manifest: AssetManifest,
        scenes: Union[List[SceneAST], Dict[str, SceneAST], List[Any], Dict[str, Any]],
        scripts: Union[List[ScriptSpec], Dict[str, ScriptSpec], List[Any], Dict[str, Any]],
        output_dir: Union[str, Path],
        **kwargs: Any,
    ) -> EngineProjectManifest:
        out_path = Path(output_dir).resolve()
        out_path.mkdir(parents=True, exist_ok=True)
        files_created: List[str] = []

        # 1. Structure
        for d in ("game", "game/entities", "game/scenes", "assets/sprites", "audio/sfx", "audio/music", "docs"):
            (out_path / d).mkdir(parents=True, exist_ok=True)

        # 2. Embed Headless Engine Shim
        shim_file = out_path / "game" / "engine_shim.py"
        shim_file.write_text(ENGINE_SHIM_CODE, encoding="utf-8")
        files_created.append("game/engine_shim.py")

        (out_path / "game" / "__init__.py").write_text("", encoding="utf-8")
        (out_path / "game" / "entities" / "__init__.py").write_text("", encoding="utf-8")
        (out_path / "game" / "scenes" / "__init__.py").write_text("", encoding="utf-8")
        files_created.extend(["game/__init__.py", "game/entities/__init__.py", "game/scenes/__init__.py"])

        config_content = (
            "# game/config.py - Display and gameplay configuration\n"
            "WINDOW_WIDTH = 1280\n"
            "WINDOW_HEIGHT = 720\n"
            "FPS = 60\n"
            f'TITLE = "{gdd.title}"\n'
        )
        (out_path / "game" / "config.py").write_text(config_content, encoding="utf-8")
        files_created.append("game/config.py")

        # 3. Assets & Documentation
        asset_files = self.write_asset_files(manifest, out_path)
        files_created.extend(asset_files)
        doc_files = self.write_documentation(gdd, out_path)
        files_created.extend(doc_files)

        # 4. Generate standalone main.py
        genre_lower = str(gdd.genre).lower()
        main_py_content = self._build_main_py(gdd, genre_lower)
        (out_path / "main.py").write_text(main_py_content, encoding="utf-8")
        files_created.append("main.py")

        # 5. Requirements & README
        reqs = "pygame>=2.5.0\npillow>=10.0.0\n"
        (out_path / "requirements.txt").write_text(reqs, encoding="utf-8")
        files_created.append("requirements.txt")

        readme = (
            f"# {gdd.title} (Pygame Edition)\n\n"
            "## Running Locally\n"
            "```bash\n"
            "pip install -r requirements.txt\n"
            "python3 main.py\n"
            "```\n\n"
            "## Running Headless Simulation\n"
            "```bash\n"
            "python3 main.py --headless --frames 60\n"
            "```\n"
        )
        (out_path / "README.md").write_text(readme, encoding="utf-8")
        files_created.append("README.md")

        return EngineProjectManifest(
            target=self.target,
            project_name=gdd.title,
            output_dir=str(out_path),
            entry_file="main.py",
            files_created=files_created,
            assets_linked=len(asset_files),
            scenes_created=1,
            scripts_created=1,
            readme_path="README.md",
            run_command=self.get_run_command(out_path),
            metadata={"genre": gdd.genre},
        )

    def get_run_command(self, project_dir: Union[str, Path]) -> List[str]:
        return ["python3", "main.py"]

    def _build_main_py(self, gdd: GameDesignDocument, genre: str) -> str:
        return f'''#!/usr/bin/env python3
"""
main.py - Standalone entrypoint for {gdd.title}.
Generated autonomously by AI Game Studio (Pygame adapter).
Supports interactive desktop execution and headless test simulation.
"""
import argparse
import sys
import random

try:
    import pygame
except ImportError:
    from game.engine_shim import pygame


def main():
    parser = argparse.ArgumentParser(description="{gdd.title}")
    parser.add_argument("--headless", action="store_true", help="Run without display window")
    parser.add_argument("--frames", type=int, default=60, help="Frames to simulate in headless mode")
    args = parser.parse_args()

    pygame.init()
    screen_size = (1280, 720)
    screen = pygame.display.set_mode(screen_size)
    pygame.display.set_caption("{gdd.title}")
    clock = pygame.time.Clock()

    player_rect = pygame.Rect(100, 500, 32, 48)
    vel_x = 0.0
    vel_y = 0.0
    speed = 5.0
    jump_velocity = -14.0
    gravity = 0.7
    on_ground = False
    score = 0
    hp = 3

    platforms = [
        pygame.Rect(0, 680, 1280, 40),
        pygame.Rect(350, 520, 250, 24),
        pygame.Rect(750, 400, 280, 24),
    ]
    coins = [
        pygame.Rect(450, 480, 16, 16),
        pygame.Rect(850, 360, 16, 16),
    ]

    running = True
    frame_count = 0

    while running:
        frame_count += 1
        if args.headless and frame_count >= args.frames:
            break

        clock.tick(60)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        keys = pygame.key.get_pressed()
        vel_x = 0.0
        if keys[pygame.K_LEFT] or keys[pygame.K_a]:
            vel_x = -speed
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
            vel_x = speed
        if (keys[pygame.K_SPACE] or keys[pygame.K_UP]) and on_ground:
            vel_y = jump_velocity
            on_ground = False

        vel_y += gravity
        player_rect.x += int(vel_x)

        # Collision X
        for plat in platforms:
            if player_rect.colliderect(plat):
                if vel_x > 0:
                    player_rect.right = plat.left
                elif vel_x < 0:
                    player_rect.left = plat.right

        player_rect.y += int(vel_y)
        on_ground = False

        # Collision Y
        for plat in platforms:
            if player_rect.colliderect(plat):
                if vel_y > 0:
                    player_rect.bottom = plat.top
                    vel_y = 0
                    on_ground = True
                elif vel_y < 0:
                    player_rect.top = plat.bottom
                    vel_y = 0

        # Coins
        for c in list(coins):
            if player_rect.colliderect(c):
                coins.remove(c)
                score += 100

        if not args.headless:
            screen.fill((20, 24, 40))
            for plat in platforms:
                pygame.draw.rect(screen, (80, 120, 180), plat)
            for c in coins:
                pygame.draw.circle(screen, (255, 215, 0), c.center, 8)
            pygame.draw.rect(screen, (0, 230, 255), player_rect)
            pygame.display.flip()

    pygame.quit()
    if args.headless:
        print(f"[SIMULATION] Completed {{frame_count}} frames successfully. Final score: {{score}}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
