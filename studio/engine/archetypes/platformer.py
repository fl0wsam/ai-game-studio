"""
studio/engine/archetypes/platformer.py - 2D Platformer Archetype Template.
Provides scenes and scripts for a 2D Platformer (Player, MainLevel, Coin, Hazard, HUD).
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
)


class PlatformerTemplate(BaseArchetypeTemplate):
    """Archetype template for a 2D Platformer game."""

    @property
    def genre(self) -> GameGenre:
        return GameGenre.PLATFORMER_2D

    @property
    def display_name(self) -> str:
        return "2D Platformer"

    @property
    def description(self) -> str:
        return "Classic 2D side-scrolling platformer featuring running, jumping, collectible pickups, hazard spikes, and HUD."

    def get_asset_requirements(self) -> List[AssetRequirement]:
        return [
            AssetRequirement(asset_id="sprite_player", asset_type=AssetType.SPRITE, description="Hero runner sprite", target_rel_path="assets/sprites/sprite_player.png", width=32, height=48),
            AssetRequirement(asset_id="sprite_enemy", asset_type=AssetType.SPRITE, description="Hazard spike or enemy", target_rel_path="assets/sprites/sprite_enemy.png", width=32, height=32),
            AssetRequirement(asset_id="sprite_item", asset_type=AssetType.SPRITE, description="Golden coin pickup", target_rel_path="assets/sprites/sprite_item.png", width=16, height=16),
            AssetRequirement(asset_id="sprite_tile", asset_type=AssetType.SPRITE, description="Platform ledge texture", target_rel_path="assets/sprites/sprite_tile.png", width=64, height=32),
            AssetRequirement(asset_id="sprite_bg", asset_type=AssetType.TEXTURE, description="Parallax sky background", target_rel_path="assets/backgrounds/sprite_bg.png", width=1280, height=720),
            AssetRequirement(asset_id="sfx_jump", asset_type=AssetType.AUDIO_SFX, description="Jump sound effect", target_rel_path="audio/sfx/sfx_jump.wav", duration_sec=0.2),
            AssetRequirement(asset_id="sfx_pickup", asset_type=AssetType.AUDIO_SFX, description="Coin pickup sound chime", target_rel_path="audio/sfx/sfx_pickup.wav", duration_sec=0.25),
            AssetRequirement(asset_id="sfx_hit", asset_type=AssetType.AUDIO_SFX, description="Hazard impact hurt sound", target_rel_path="audio/sfx/sfx_hit.wav", duration_sec=0.3),
            AssetRequirement(asset_id="bgm_loop", asset_type=AssetType.AUDIO_MUSIC, description="Upbeat chiptune level music", target_rel_path="audio/music/bgm_loop.wav", duration_sec=30.0),
        ]

    def get_gdd(self, prompt: str = "", seed: int = 42, target_engine: str = "godot4") -> GameDesignDocument:
        title = "Neon Cyber Runner" if "cyber" in prompt.lower() else "Super Pixel Leap"
        return GameDesignDocument(
            title=title,
            genre=self.genre.value,
            target_engine=target_engine,
            high_concept="Fast-paced 2D platformer leaping over perilous hazards to collect energy coins and reach the portal.",
            gameplay_mechanics=[
                "Smooth horizontal running and deceleration",
                "Physics-based jumping with variable jump velocity and floor detection",
                "Collectible coins increasing player score",
                "Hazard spikes causing damage and temporary invulnerability flash",
                "Follow-camera with horizontal and vertical smoothing",
            ],
            narrative_lore="In a vibrant retro-digital realm, a nimble protagonist must traverse fractured platforms to restore core energy.",
            art_style_guide={"palette": "16-bit cyber arcade", "sprite_resolution": "32x32"},
            audio_style_guide={"music_genre": "chiptune synthwave", "sfx_style": "8-bit crisp retro"},
            quests=[
                QuestSpec(
                    quest_id="quest_gather_coins",
                    title="Energy Restoration",
                    description="Collect 5 energy coins and avoid hazard spikes.",
                    objectives=[
                        QuestObjective(objective_id="obj_0", description="Collect 5 coins", target_count=5),
                        QuestObjective(objective_id="obj_1", description="Survive hazards", target_count=1),
                    ],
                )
            ],
            dialogue_trees=[
                DialogueNode(node_id="guide_1", speaker="System Terminal", text="Warning: High-voltage hazards ahead. Press Jump to clear gaps.")
            ],
            required_assets=self.get_asset_requirements(),
            scenes_plan=[
                ScenePlan(name="MainLevel", path="scenes/main_level.tscn", root_node_type="Node2D", required_assets=["sprite_bg", "bgm_loop"]),
                ScenePlan(name="Player", path="scenes/player.tscn", root_node_type="CharacterBody2D", required_assets=["sprite_player", "sfx_jump", "sfx_hit"]),
                ScenePlan(name="Coin", path="scenes/coin.tscn", root_node_type="Area2D", required_assets=["sprite_item", "sfx_pickup"]),
                ScenePlan(name="Hazard", path="scenes/hazard.tscn", root_node_type="Area2D", required_assets=["sprite_enemy"]),
                ScenePlan(name="HUD", path="scenes/hud.tscn", root_node_type="CanvasLayer"),
            ],
        )

    def get_scenes(
        self,
        gdd: Optional[GameDesignDocument] = None,
        manifest: Optional[AssetManifest] = None,
    ) -> List[SceneAST]:
        scenes: List[SceneAST] = []

        # 1. Player Scene
        player_scn = SceneAST(name="Player", path="scenes/player.tscn")
        player_scn.add_ext_resource("Script", "res://scripts/player.gd", "1_script")
        player_scn.add_ext_resource("Texture2D", "res://assets/sprites/sprite_player.png", "2_tex")
        player_scn.add_ext_resource("AudioStream", "res://audio/sfx/sfx_jump.wav", "3_jump_sfx")
        player_scn.add_ext_resource("AudioStream", "res://audio/sfx/sfx_hit.wav", "4_hit_sfx")
        player_scn.add_sub_resource("CapsuleShape2D", "CapsuleShape2D_player", {"height": 48.0, "radius": 14.0})

        player_node = NodeAST(
            name="Player",
            type="CharacterBody2D",
            script_id="1_script",
            groups=["player"],
            properties={"collision_layer": 2, "collision_mask": 1},
        )
        sprite_node = NodeAST(
            name="Sprite2D",
            type="Sprite2D",
            parent=".",
            properties={"texture": ExtResourceRef("2_tex")},
        )
        col_node = NodeAST(
            name="CollisionShape2D",
            type="CollisionShape2D",
            parent=".",
            properties={"shape": SubResourceRef("CapsuleShape2D_player")},
        )
        cam_node = NodeAST(
            name="Camera2D",
            type="Camera2D",
            parent=".",
            properties={"position_smoothing_enabled": True, "position_smoothing_speed": 8.0},
        )
        jump_audio = NodeAST(
            name="JumpAudio",
            type="AudioStreamPlayer2D",
            parent=".",
            properties={"stream": ExtResourceRef("3_jump_sfx"), "bus": "&\"SFX\""},
        )
        hurt_audio = NodeAST(
            name="HurtAudio",
            type="AudioStreamPlayer2D",
            parent=".",
            properties={"stream": ExtResourceRef("4_hit_sfx"), "bus": "&\"SFX\""},
        )
        player_scn.nodes.extend([player_node, sprite_node, col_node, cam_node, jump_audio, hurt_audio])
        scenes.append(player_scn)

        # 2. Coin Scene
        coin_scn = SceneAST(name="Coin", path="scenes/coin.tscn")
        coin_scn.add_ext_resource("Script", "res://scripts/coin.gd", "1_script")
        coin_scn.add_ext_resource("Texture2D", "res://assets/sprites/sprite_item.png", "2_tex")
        coin_scn.add_ext_resource("AudioStream", "res://audio/sfx/sfx_pickup.wav", "3_sfx")
        coin_scn.add_sub_resource("CircleShape2D", "CircleShape2D_coin", {"radius": 12.0})

        coin_node = NodeAST(
            name="Coin",
            type="Area2D",
            script_id="1_script",
            groups=["pickups"],
            properties={"collision_layer": 4, "collision_mask": 2},
        )
        coin_sprite = NodeAST(name="Sprite2D", type="Sprite2D", parent=".", properties={"texture": ExtResourceRef("2_tex")})
        coin_shape = NodeAST(name="CollisionShape2D", type="CollisionShape2D", parent=".", properties={"shape": SubResourceRef("CircleShape2D_coin")})
        coin_audio = NodeAST(name="PickupAudio", type="AudioStreamPlayer2D", parent=".", properties={"stream": ExtResourceRef("3_sfx")})
        coin_scn.nodes.extend([coin_node, coin_sprite, coin_shape, coin_audio])
        coin_scn.add_connection("body_entered", ".", ".", "_on_body_entered")
        scenes.append(coin_scn)

        # 3. Hazard Scene
        hazard_scn = SceneAST(name="Hazard", path="scenes/hazard.tscn")
        hazard_scn.add_ext_resource("Script", "res://scripts/hazard.gd", "1_script")
        hazard_scn.add_ext_resource("Texture2D", "res://assets/sprites/sprite_enemy.png", "2_tex")
        hazard_scn.add_sub_resource("RectangleShape2D", "RectangleShape2D_hazard", {"size": Vector2(32.0, 24.0)})

        hazard_node = NodeAST(
            name="Hazard",
            type="Area2D",
            script_id="1_script",
            groups=["hazards"],
            properties={"collision_layer": 8, "collision_mask": 2},
        )
        hazard_sprite = NodeAST(name="Sprite2D", type="Sprite2D", parent=".", properties={"texture": ExtResourceRef("2_tex")})
        hazard_shape = NodeAST(name="CollisionShape2D", type="CollisionShape2D", parent=".", properties={"shape": SubResourceRef("RectangleShape2D_hazard")})
        hazard_scn.nodes.extend([hazard_node, hazard_sprite, hazard_shape])
        hazard_scn.add_connection("body_entered", ".", ".", "_on_body_entered")
        scenes.append(hazard_scn)

        # 4. HUD Scene
        hud_scn = SceneAST(name="HUD", path="scenes/hud.tscn")
        hud_scn.add_ext_resource("Script", "res://scripts/hud.gd", "1_script")
        hud_node = NodeAST(name="HUD", type="CanvasLayer", script_id="1_script")
        margin_node = NodeAST(name="MarginContainer", type="MarginContainer", parent=".", properties={"anchors_preset": 15})
        box_node = NodeAST(name="HBoxContainer", type="HBoxContainer", parent="MarginContainer")
        score_lbl = NodeAST(name="ScoreLabel", type="Label", parent="MarginContainer/HBoxContainer", properties={"text": "Score: 0"})
        hp_lbl = NodeAST(name="HealthLabel", type="Label", parent="MarginContainer/HBoxContainer", properties={"text": "HP: 3"})
        hud_scn.nodes.extend([hud_node, margin_node, box_node, score_lbl, hp_lbl])
        scenes.append(hud_scn)

        # 5. MainLevel Scene
        main_scn = SceneAST(name="MainLevel", path="scenes/main_level.tscn")
        main_scn.add_ext_resource("Texture2D", "res://assets/backgrounds/sprite_bg.png", "1_bg_tex")
        main_scn.add_ext_resource("PackedScene", "res://scenes/player.tscn", "2_player_scn")
        main_scn.add_ext_resource("PackedScene", "res://scenes/coin.tscn", "3_coin_scn")
        main_scn.add_ext_resource("PackedScene", "res://scenes/hazard.tscn", "4_hazard_scn")
        main_scn.add_ext_resource("PackedScene", "res://scenes/hud.tscn", "5_hud_scn")
        main_scn.add_ext_resource("AudioStream", "res://audio/music/bgm_loop.wav", "6_bgm")
        main_scn.add_sub_resource("RectangleShape2D", "RectangleShape2D_floor", {"size": Vector2(1280.0, 48.0)})
        main_scn.add_sub_resource("RectangleShape2D", "RectangleShape2D_plat", {"size": Vector2(300.0, 32.0)})

        level_root = NodeAST(name="MainLevel", type="Node2D")
        bg_node = NodeAST(name="Background", type="Sprite2D", parent=".", properties={"position": Vector2(640.0, 360.0), "texture": ExtResourceRef("1_bg_tex")})
        platforms = NodeAST(name="Platforms", type="StaticBody2D", parent=".", properties={"collision_layer": 1, "collision_mask": 0})
        floor_col = NodeAST(name="FloorShape", type="CollisionShape2D", parent="Platforms", properties={"position": Vector2(640.0, 696.0), "shape": SubResourceRef("RectangleShape2D_floor")})
        plat_col = NodeAST(name="Platform1", type="CollisionShape2D", parent="Platforms", properties={"position": Vector2(400.0, 500.0), "shape": SubResourceRef("RectangleShape2D_plat")})
        player_inst = NodeAST(name="Player", parent=".", instance="2_player_scn", properties={"position": Vector2(100.0, 600.0)})
        coins_container = NodeAST(name="CoinContainer", type="Node2D", parent=".")
        coin1 = NodeAST(name="Coin1", parent="CoinContainer", instance="3_coin_scn", properties={"position": Vector2(400.0, 450.0)})
        hazard1 = NodeAST(name="Hazard1", parent=".", instance="4_hazard_scn", properties={"position": Vector2(600.0, 660.0)})
        hud_inst = NodeAST(name="HUD", parent=".", instance="5_hud_scn")
        bgm_player = NodeAST(name="BGMPlayer", type="AudioStreamPlayer", parent=".", properties={"autoplay": True, "stream": ExtResourceRef("6_bgm")})

        main_scn.nodes.extend([level_root, bg_node, platforms, floor_col, plat_col, player_inst, coins_container, coin1, hazard1, hud_inst, bgm_player])
        scenes.append(main_scn)

        return scenes

    def get_scripts(
        self,
        gdd: Optional[GameDesignDocument] = None,
        target_engine: str = "godot4",
    ) -> List[ScriptSpec]:
        scripts: List[ScriptSpec] = []

        # GDScript versions
        player_gd = """extends CharacterBody2D
class_name PlatformerPlayer

signal score_changed(new_score: int)
signal health_changed(current_hp: int)
signal player_died

@export var speed: float = 300.0
@export var jump_velocity: float = -420.0
@export var gravity: float = 980.0
@export var max_health: int = 3

var current_health: int
var score: int = 0
var is_invulnerable: bool = false

@onready var sprite: Sprite2D = $Sprite2D
@onready var jump_sfx: AudioStreamPlayer2D = $JumpAudio
@onready var hurt_sfx: AudioStreamPlayer2D = $HurtAudio

func _ready() -> void:
\tcurrent_health = max_health
\thealth_changed.emit(current_health)
\tscore_changed.emit(score)

func _physics_process(delta: float) -> void:
\tif not is_on_floor():
\t\tvelocity.y += gravity * delta

\tif Input.is_action_just_pressed("ui_accept") and is_on_floor():
\t\tvelocity.y = jump_velocity
\t\tif jump_sfx and jump_sfx.stream:
\t\t\tjump_sfx.play()

\tvar direction := Input.get_axis("ui_left", "ui_right")
\tif direction != 0.0:
\t\tvelocity.x = direction * speed
\t\tif sprite:
\t\t\tsprite.flip_h = direction < 0.0
\telse:
\t\tvelocity.x = move_toward(velocity.x, 0.0, speed * delta * 6.0)

\tmove_and_slide()

func add_score(amount: int) -> void:
\tscore += amount
\tscore_changed.emit(score)

func take_damage(amount: int = 1) -> void:
\tif is_invulnerable:
\t\treturn
\tcurrent_health = max(0, current_health - amount)
\thealth_changed.emit(current_health)
\tif hurt_sfx and hurt_sfx.stream:
\t\thurt_sfx.play()

\tif current_health <= 0:
\t\tplayer_died.emit()
\t\tqueue_free()
\telse:
\t\t_trigger_invulnerability()

func _trigger_invulnerability() -> void:
\tis_invulnerable = true
\tvar tween = create_tween().set_loops(4)
\ttween.tween_property(sprite, "modulate:a", 0.3, 0.1)
\ttween.tween_property(sprite, "modulate:a", 1.0, 0.1)
\tawait tween.finished
\tis_invulnerable = false
"""
        scripts.append(ScriptSpec(rel_path="scripts/player.gd", language="gdscript", code=player_gd))

        coin_gd = """extends Area2D
class_name CoinPickup

@export var value: int = 100
@onready var sfx: AudioStreamPlayer2D = $PickupAudio

func _on_body_entered(body: Node2D) -> void:
\tif body.has_method("add_score"):
\t\tbody.add_score(value)
\tif sfx and sfx.stream:
\t\tsfx.play()
\thide()
\tset_deferred("monitoring", false)
\tawait get_tree().create_timer(0.3).timeout
\tqueue_free()
"""
        scripts.append(ScriptSpec(rel_path="scripts/coin.gd", language="gdscript", code=coin_gd))

        hazard_gd = """extends Area2D
class_name HazardSpike

@export var damage: int = 1

func _on_body_entered(body: Node2D) -> void:
\tif body.has_method("take_damage"):
\t\tbody.take_damage(damage)
"""
        scripts.append(ScriptSpec(rel_path="scripts/hazard.gd", language="gdscript", code=hazard_gd))

        hud_gd = """extends CanvasLayer
class_name GameHUD

@onready var score_label: Label = $MarginContainer/HBoxContainer/ScoreLabel
@onready var hp_label: Label = $MarginContainer/HBoxContainer/HealthLabel

func update_score(new_score: int) -> void:
\tif score_label:
\t\tscore_label.text = "Score: " + str(new_score)

func update_health(new_hp: int) -> void:
\tif hp_label:
\t\thp_label.text = "HP: " + str(new_hp)
"""
        scripts.append(ScriptSpec(rel_path="scripts/hud.gd", language="gdscript", code=hud_gd))

        if target_engine in ("pygame", "python"):
            player_py = """# scripts/player.py - Player Controller for Pygame
class Player:
    def __init__(self, x=100, y=500):
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0
        self.speed = 5.0
        self.jump_velocity = -14.0
        self.gravity = 0.7
        self.on_ground = False
"""
            py_scripts = [
                ScriptSpec(rel_path="scripts/player.py", language="python", code=player_py),
                ScriptSpec(rel_path="scripts/platformer_game.py", language="python", code=platformer_py),
            ]
            return py_scripts

        return scripts
