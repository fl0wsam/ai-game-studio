"""
studio/engine/archetypes/grid_puzzle.py - Grid Match Puzzle Archetype Template.
Provides scenes and scripts for a Match-3 Grid Puzzle game (PuzzleBoard, Tile, HUD).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from studio.core.models import (
    AssetManifest,
    AssetRequirement,
    AssetType,
    DialogueNode,
    EngineTarget,
    GameDesignDocument,
    GameGenre,
    QuestObjective,
    QuestSpec,
    ScenePlan,
    ScriptSpec,
)
from studio.engine.archetypes.base import BaseArchetypeTemplate
from studio.engine.ast_models import (
    ConnectionAST,
    ExtResourceAST,
    ExtResourceRef,
    NodeAST,
    SceneAST,
    SubResourceAST,
    SubResourceRef,
    Vector2,
    Vector2i,
)


class GridPuzzleTemplate(BaseArchetypeTemplate):
    """Archetype template for a Grid Match-3 Puzzle game."""

    @property
    def genre(self) -> GameGenre:
        return GameGenre.GRID_PUZZLE

    @property
    def display_name(self) -> str:
        return "Grid Match Puzzle"

    @property
    def description(self) -> str:
        return "Tactical match-3 puzzle featuring discrete grid swapping, horizontal/vertical matching, gravity cascade, and move limit."

    def get_asset_requirements(self) -> List[AssetRequirement]:
        return [
            AssetRequirement(asset_id="sprite_player", asset_type=AssetType.SPRITE, description="Tile selection cursor frame", target_rel_path="assets/sprites/sprite_player.png", width=64, height=64),
            AssetRequirement(asset_id="sprite_item", asset_type=AssetType.SPRITE, description="Elemental gem and jewel tiles", target_rel_path="assets/sprites/sprite_item.png", width=60, height=60),
            AssetRequirement(asset_id="sprite_bg", asset_type=AssetType.TEXTURE, description="Mystic glyph board background", target_rel_path="assets/backgrounds/sprite_bg.png", width=1280, height=720),
            AssetRequirement(asset_id="sfx_pickup", asset_type=AssetType.AUDIO_SFX, description="Match-3 crystal chime effect", target_rel_path="audio/sfx/sfx_pickup.wav", duration_sec=0.25),
            AssetRequirement(asset_id="sfx_hit", asset_type=AssetType.AUDIO_SFX, description="Invalid move error thud", target_rel_path="audio/sfx/sfx_hit.wav", duration_sec=0.2),
            AssetRequirement(asset_id="bgm_loop", asset_type=AssetType.AUDIO_MUSIC, description="Calm relaxing puzzle ambiance melody", target_rel_path="audio/music/bgm_loop.wav", duration_sec=50.0),
        ]

    def get_gdd(self, prompt: str = "", seed: int = 42, target_engine: str = "godot4") -> GameDesignDocument:
        title = "Ancient Glyph Puzzle" if "glyph" in prompt.lower() else "Jewel Match Odyssey"
        return GameDesignDocument(
            title=title,
            genre=self.genre.value,
            target_engine=target_engine,
            high_concept="Swap mystical elemental glyphs on an 8x8 grid to form match-3 lines, trigger cascading chains, and reach the score quota.",
            gameplay_mechanics=[
                "8x8 discrete matrix grid management with zero starting 3-in-a-row matches",
                "Adjacent orthogonal tile swapping with invalid swap rollback",
                "Horizontal and vertical match detection scanning 3-in-a-row or greater",
                "Gravity cascade filling empty slots from top with chain reaction triggers",
                "Turn move countdown and target score completion victory condition",
            ],
            narrative_lore="Ancient glyphs guard the sanctuary vault. Aligning the elemental stones unlocks the hidden knowledge.",
            art_style_guide={"palette": "gemstone neon crystal", "sprite_resolution": "64x64 tiles"},
            audio_style_guide={"music_genre": "ambient zen melody", "sfx_style": "glass bell chimes and jewel snaps"},
            quests=[
                QuestSpec(
                    quest_id="quest_reach_target",
                    title="Sanctuary Awakening",
                    description="Attain 1500 points before running out of moves.",
                    objectives=[
                        QuestObjective(objective_id="obj_0", description="Reach 1500 points", target_count=1500),
                    ],
                )
            ],
            dialogue_trees=[
                DialogueNode(node_id="sanctuary_1", speaker="Sanctuary Oracle", text="Align three identical glyphs to channel the elemental energy.")
            ],
            required_assets=self.get_asset_requirements(),
            scenes_plan=[
                ScenePlan(name="PuzzleBoard", path="scenes/puzzle_board.tscn", root_node_type="Node2D", required_assets=["sprite_bg", "sfx_pickup", "bgm_loop"]),
                ScenePlan(name="Tile", path="scenes/tile.tscn", root_node_type="Node2D", required_assets=["sprite_item"]),
            ],
        )

    def get_scenes(
        self,
        gdd: Optional[GameDesignDocument] = None,
        manifest: Optional[AssetManifest] = None,
    ) -> List[SceneAST]:
        scenes: List[SceneAST] = []

        # 1. Tile Scene
        tile_scn = SceneAST(name="Tile", path="scenes/tile.tscn")
        tile_scn.add_ext_resource("Script", "res://scripts/tile.gd", "1_script")
        tile_scn.add_ext_resource("Texture2D", "res://assets/sprites/sprite_item.png", "2_tex")

        tile_node = NodeAST(name="Tile", type="Node2D", script_id="1_script")
        tile_sprite = NodeAST(name="Sprite2D", type="Sprite2D", parent=".", properties={"texture": ExtResourceRef("2_tex")})
        tile_btn = NodeAST(name="Button", type="Button", parent=".", properties={"flat": True, "position": Vector2(-30.0, -30.0), "size": Vector2(60.0, 60.0)})
        tile_scn.nodes.extend([tile_node, tile_sprite, tile_btn])
        scenes.append(tile_scn)

        # 2. PuzzleBoard Scene
        board_scn = SceneAST(name="PuzzleBoard", path="scenes/puzzle_board.tscn")
        board_scn.add_ext_resource("Script", "res://scripts/puzzle_board.gd", "1_script")
        board_scn.add_ext_resource("Texture2D", "res://assets/backgrounds/sprite_bg.png", "2_bg_tex")
        board_scn.add_ext_resource("Texture2D", "res://assets/sprites/sprite_player.png", "3_cursor_tex")
        board_scn.add_ext_resource("AudioStream", "res://audio/sfx/sfx_pickup.wav", "4_match_sfx")
        board_scn.add_ext_resource("AudioStream", "res://audio/music/bgm_loop.wav", "5_bgm")

        board_root = NodeAST(name="PuzzleBoard", type="Node2D", script_id="1_script")
        bg_node = NodeAST(name="Background", type="Sprite2D", parent=".", properties={"position": Vector2(640.0, 360.0), "texture": ExtResourceRef("2_bg_tex")})
        tile_container = NodeAST(name="TileContainer", type="Node2D", parent=".")
        cursor_node = NodeAST(name="Cursor", type="Sprite2D", parent=".", properties={"texture": ExtResourceRef("3_cursor_tex"), "visible": False})
        match_audio = NodeAST(name="MatchAudio", type="AudioStreamPlayer", parent=".", properties={"stream": ExtResourceRef("4_match_sfx")})
        bgm_player = NodeAST(name="BGMPlayer", type="AudioStreamPlayer", parent=".", properties={"autoplay": True, "stream": ExtResourceRef("5_bgm")})

        hud_root = NodeAST(name="HUD", type="CanvasLayer", parent=".")
        score_lbl = NodeAST(name="ScoreLabel", type="Label", parent="HUD", properties={"position": Vector2(20.0, 20.0), "text": "Score: 0"})
        moves_lbl = NodeAST(name="MovesLabel", type="Label", parent="HUD", properties={"position": Vector2(20.0, 50.0), "text": "Moves: 25"})
        target_lbl = NodeAST(name="TargetLabel", type="Label", parent="HUD", properties={"position": Vector2(20.0, 80.0), "text": "Target: 1500"})

        board_scn.nodes.extend([board_root, bg_node, tile_container, cursor_node, match_audio, bgm_player, hud_root, score_lbl, moves_lbl, target_lbl])
        scenes.append(board_scn)

        return scenes

    def get_scripts(
        self,
        gdd: Optional[GameDesignDocument] = None,
        target_engine: str = "godot4",
    ) -> List[ScriptSpec]:
        scripts: List[ScriptSpec] = []

        board_gd = """extends Node2D
class_name PuzzleBoard

signal score_updated(new_score: int)
signal moves_updated(moves_left: int)
signal game_won
signal game_over

const GRID_WIDTH: int = 8
const GRID_HEIGHT: int = 8
const TILE_SIZE: int = 64
const NUM_TYPES: int = 5

@export var max_moves: int = 25
@export var target_score: int = 1500

var grid: Array = []
var selected_pos: Vector2i = Vector2i(-1, -1)
var score: int = 0
var moves_left: int = 25
var is_busy: bool = false

@onready var tile_container: Node2D = $TileContainer
@onready var match_sfx: AudioStreamPlayer = $MatchAudio

func _ready() -> void:
\tmoves_left = max_moves
\tinitialize_board()

func initialize_board() -> void:
\tgrid.clear()
\tfor x in range(GRID_WIDTH):
\t\tvar col = []
\t\tfor y in range(GRID_HEIGHT):
\t\t\tvar tile_type = randi() % NUM_TYPES
\t\t\twhile (x >= 2 and col[y-1] == tile_type and col[y-2] == tile_type) or (y >= 2 and grid[x-1][y] == tile_type and grid[x-2][y] == tile_type):
\t\t\t\ttile_type = randi() % NUM_TYPES
\t\t\tcol.append(tile_type)
\t\tgrid.append(col)

func select_tile(pos: Vector2i) -> void:
\tif is_busy or moves_left <= 0:
\t\treturn

\tif selected_pos == Vector2i(-1, -1):
\t\tselected_pos = pos
\telse:
\t\tvar dist = abs(pos.x - selected_pos.x) + abs(pos.y - selected_pos.y)
\t\tif dist == 1:
\t\t\tswap_tiles(selected_pos, pos)
\t\tselected_pos = Vector2i(-1, -1)

func swap_tiles(p1: Vector2i, p2: Vector2i) -> void:
\tis_busy = true
\tvar temp = grid[p1.x][p1.y]
\tgrid[p1.x][p1.y] = grid[p2.x][p2.y]
\tgrid[p2.x][p2.y] = temp

\tvar matches = find_matches()
\tif matches.is_empty():
\t\tgrid[p2.x][p2.y] = grid[p1.x][p1.y]
\t\tgrid[p1.x][p1.y] = temp
\t\tis_busy = false
\telse:
\t\tmoves_left -= 1
\t\tmoves_updated.emit(moves_left)
\t\tawait resolve_matches(matches)
\t\tis_busy = false
\t\tif score >= target_score:
\t\t\tgame_won.emit()
\t\telif moves_left <= 0:
\t\t\tgame_over.emit()

func find_matches() -> Array[Vector2i]:
\tvar matched: Dictionary = {}
\tfor y in range(GRID_HEIGHT):
\t\tfor x in range(GRID_WIDTH - 2):
\t\t\tvar t = grid[x][y]
\t\t\tif t >= 0 and t == grid[x+1][y] and t == grid[x+2][y]:
\t\t\t\tmatched[Vector2i(x, y)] = true
\t\t\t\tmatched[Vector2i(x+1, y)] = true
\t\t\t\tmatched[Vector2i(x+2, y)] = true

\tfor x in range(GRID_WIDTH):
\t\tfor y in range(GRID_HEIGHT - 2):
\t\t\tvar t = grid[x][y]
\t\t\tif t >= 0 and t == grid[x][y+1] and t == grid[x][y+2]:
\t\t\t\tmatched[Vector2i(x, y)] = true
\t\t\t\tmatched[Vector2i(x, y+1)] = true
\t\t\t\tmatched[Vector2i(x, y+2)] = true

\tvar res: Array[Vector2i] = []
\tfor k in matched.keys():
\t\tres.append(k)
\treturn res

func check_matches() -> Array[Vector2i]:
\treturn find_matches()

func resolve_matches(matches: Array[Vector2i]) -> void:
\tscore += len(matches) * 10
\tscore_updated.emit(score)
\tif match_sfx and match_sfx.stream:
\t\tmatch_sfx.play()

\tfor pos in matches:
\t\tgrid[pos.x][pos.y] = -1

\tfor x in range(GRID_WIDTH):
\t\tvar empty_slots = 0
\t\tfor y in range(GRID_HEIGHT - 1, -1, -1):
\t\t\tif grid[x][y] == -1:
\t\t\t\tempty_slots += 1
\t\t\telif empty_slots > 0:
\t\t\t\tgrid[x][y + empty_slots] = grid[x][y]
\t\t\t\tgrid[x][y] = -1
\t\tfor y in range(empty_slots):
\t\t\tgrid[x][y] = randi() % NUM_TYPES

\tawait get_tree().create_timer(0.1).timeout
\tvar next_matches = find_matches()
\tif not next_matches.is_empty():
\t\tawait resolve_matches(next_matches)
"""
        scripts.append(ScriptSpec(rel_path="scripts/puzzle_board.gd", language="gdscript", code=board_gd))

        tile_gd = """extends Node2D
class_name GridTile

var tile_type: int = 0
var grid_x: int = 0
var grid_y: int = 0

signal clicked(x: int, y: int)

func _ready() -> void:
\t$Button.pressed.connect(func(): clicked.emit(grid_x, grid_y))
"""
        scripts.append(ScriptSpec(rel_path="scripts/tile.gd", language="gdscript", code=tile_gd))

        # Python / Pygame standalone
        puzzle_py = """# Standalone Python / Pygame Grid Match Puzzle implementation
import sys
import random
try:
    import pygame
except ImportError:
    from game.engine_shim import pygame

def create_board(w=8, h=8, types=5):
    board = []
    for x in range(w):
        col = []
        for y in range(h):
            t = random.randint(0, types - 1)
            while (x >= 2 and col[y-1] == t and col[y-2] == t) or \\
                  (y >= 2 and board[x-1][y] == t and board[x-2][y] == t):
                t = random.randint(0, types - 1)
            col.append(t)
        board.append(col)
    return board

def run_game(headless: bool = False, frames: int = 60) -> int:
    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    clock = pygame.time.Clock()
    board = create_board()
    score = 0
    moves = 25

    colors = [
        (255, 80, 80),
        (80, 255, 80),
        (80, 160, 255),
        (255, 220, 80),
        (220, 80, 255),
    ]

    running = True
    frame_count = 0
    while running and (not headless or frame_count < frames):
        frame_count += 1
        clock.tick(60)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        if not headless:
            screen.fill((20, 20, 35))
            for x in range(8):
                for y in range(8):
                    t = board[x][y]
                    color = colors[t % len(colors)]
                    rect = pygame.Rect(380 + x * 64, 100 + y * 64, 58, 58)
                    pygame.draw.rect(screen, color, rect, border_radius=6)
            pygame.display.flip()

    pygame.quit()
    return score

if __name__ == "__main__":
    run_game()
"""
        if target_engine in ("pygame", "python"):
            return [
                ScriptSpec(rel_path="scripts/puzzle_board.py", language="python", code=puzzle_py),
                ScriptSpec(rel_path="scripts/grid_puzzle_game.py", language="python", code=puzzle_py),
            ]

        return scripts
