"""
studio/engine/archetypes/space_shooter.py - Space Shooter Archetype Template.
Provides scenes and scripts for a Space Shooter (Starship, SpaceArena, Laser, Asteroid, HUD).
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


class SpaceShooterTemplate(BaseArchetypeTemplate):
    """Archetype template for a Space Shooter (Shmup) arcade game."""

    @property
    def genre(self) -> GameGenre:
        return GameGenre.SPACE_SHOOTER

    @property
    def display_name(self) -> str:
        return "Space Shooter"

    @property
    def description(self) -> str:
        return "Vertical scrolling arcade shmup with ship thrusters, rapid laser fire, asteroid hazards, and score tracking."

    def get_asset_requirements(self) -> List[AssetRequirement]:
        return [
            AssetRequirement(asset_id="sprite_player", asset_type=AssetType.SPRITE, description="Starship player fighter", target_rel_path="assets/sprites/sprite_player.png", width=36, height=36),
            AssetRequirement(asset_id="sprite_enemy", asset_type=AssetType.SPRITE, description="Incoming space asteroid hazard", target_rel_path="assets/sprites/sprite_enemy.png", width=32, height=32),
            AssetRequirement(asset_id="sprite_item", asset_type=AssetType.SPRITE, description="High-energy laser projectile", target_rel_path="assets/sprites/sprite_item.png", width=8, height=20),
            AssetRequirement(asset_id="sprite_bg", asset_type=AssetType.TEXTURE, description="Deep space starfield texture", target_rel_path="assets/backgrounds/sprite_bg.png", width=1280, height=720),
            AssetRequirement(asset_id="sfx_shoot", asset_type=AssetType.AUDIO_SFX, description="Pulse laser fire sound effect", target_rel_path="audio/sfx/sfx_shoot.wav", duration_sec=0.15),
            AssetRequirement(asset_id="sfx_hit", asset_type=AssetType.AUDIO_SFX, description="Hull hit warning chime", target_rel_path="audio/sfx/sfx_hit.wav", duration_sec=0.2),
            AssetRequirement(asset_id="sfx_explosion", asset_type=AssetType.AUDIO_SFX, description="Hazard detonation explosion", target_rel_path="audio/sfx/sfx_explosion.wav", duration_sec=0.5),
            AssetRequirement(asset_id="bgm_loop", asset_type=AssetType.AUDIO_MUSIC, description="Pumping electro-synth space battle soundtrack", target_rel_path="audio/music/bgm_loop.wav", duration_sec=40.0),
        ]

    def get_gdd(self, prompt: str = "", seed: int = 42, target_engine: str = "godot4") -> GameDesignDocument:
        title = "Retro Cosmic Defender" if "cosmic" in prompt.lower() else "Starship Assault"
        return GameDesignDocument(
            title=title,
            genre=self.genre.value,
            target_engine=target_engine,
            high_concept="Pilot an advanced starfighter through asteroid fields, blasting enemy hazards with high-energy plasma lasers.",
            gameplay_mechanics=[
                "Screen-bounded omni-directional starship movement",
                "Continuous projectile firing with muzzle cooldown",
                "Dynamic hazard spawning and continuous downward trajectory",
                "Hitbox collision between laser projectiles and asteroid obstacles",
                "Persistent score multiplier and hull integrity HUD",
            ],
            narrative_lore="Deep within Sector 7, an asteroid cluster threatens the orbital gateway. One starfighter stands as the defense line.",
            art_style_guide={"palette": "neon vector cosmic dark", "sprite_resolution": "32x32 spaceships"},
            audio_style_guide={"music_genre": "synthwave arcade drive", "sfx_style": "laser zaps and crunchy explosions"},
            quests=[
                QuestSpec(
                    quest_id="quest_destroy_asteroids",
                    title="Sector Defense",
                    description="Destroy 10 asteroid hazards and safeguard the flagship.",
                    objectives=[
                        QuestObjective(objective_id="obj_0", description="Destroy 10 asteroids", target_count=10),
                    ],
                )
            ],
            dialogue_trees=[
                DialogueNode(node_id="fleet_cmd_1", speaker="Fleet Command", text="All fighters, deploy! Clear out the asteroid wave immediately.")
            ],
            required_assets=self.get_asset_requirements(),
            scenes_plan=[
                ScenePlan(name="SpaceArena", path="scenes/space_arena.tscn", root_node_type="Node2D", required_assets=["sprite_bg", "bgm_loop"]),
                ScenePlan(name="Starship", path="scenes/starship.tscn", root_node_type="Area2D", required_assets=["sprite_player", "sfx_shoot", "sfx_explosion"]),
                ScenePlan(name="Laser", path="scenes/laser.tscn", root_node_type="Area2D", required_assets=["sprite_item"]),
                ScenePlan(name="Asteroid", path="scenes/asteroid.tscn", root_node_type="Area2D", required_assets=["sprite_enemy"]),
                ScenePlan(name="HUD", path="scenes/hud.tscn", root_node_type="CanvasLayer"),
            ],
        )

    def get_scenes(
        self,
        gdd: Optional[GameDesignDocument] = None,
        manifest: Optional[AssetManifest] = None,
    ) -> List[SceneAST]:
        scenes: List[SceneAST] = []

        # 1. Starship Scene
        ship_scn = SceneAST(name="Starship", path="scenes/starship.tscn")
        ship_scn.add_ext_resource("Script", "res://scripts/starship.gd", "1_script")
        ship_scn.add_ext_resource("Texture2D", "res://assets/sprites/sprite_player.png", "2_tex")
        ship_scn.add_ext_resource("AudioStream", "res://audio/sfx/sfx_shoot.wav", "3_shoot_sfx")
        ship_scn.add_ext_resource("AudioStream", "res://audio/sfx/sfx_explosion.wav", "4_boom_sfx")
        ship_scn.add_sub_resource("CircleShape2D", "CircleShape2D_ship", {"radius": 18.0})

        ship_node = NodeAST(name="Starship", type="Area2D", script_id="1_script", groups=["player"], properties={"collision_layer": 2, "collision_mask": 8})
        ship_sprite = NodeAST(name="Sprite2D", type="Sprite2D", parent=".", properties={"texture": ExtResourceRef("2_tex")})
        ship_col = NodeAST(name="CollisionShape2D", type="CollisionShape2D", parent=".", properties={"shape": SubResourceRef("CircleShape2D_ship")})
        muzzle = NodeAST(name="Muzzle", type="Marker2D", parent=".", properties={"position": Vector2(0.0, -24.0)})
        shoot_sfx = NodeAST(name="ShootAudio", type="AudioStreamPlayer2D", parent=".", properties={"stream": ExtResourceRef("3_shoot_sfx")})
        boom_sfx = NodeAST(name="ExplosionAudio", type="AudioStreamPlayer2D", parent=".", properties={"stream": ExtResourceRef("4_boom_sfx")})
        ship_scn.nodes.extend([ship_node, ship_sprite, ship_col, muzzle, shoot_sfx, boom_sfx])
        scenes.append(ship_scn)

        # 2. Laser Scene
        laser_scn = SceneAST(name="Laser", path="scenes/laser.tscn")
        laser_scn.add_ext_resource("Script", "res://scripts/laser.gd", "1_script")
        laser_scn.add_ext_resource("Texture2D", "res://assets/sprites/sprite_item.png", "2_tex")
        laser_scn.add_sub_resource("RectangleShape2D", "RectangleShape2D_laser", {"size": Vector2(6.0, 18.0)})

        laser_node = NodeAST(name="Laser", type="Area2D", script_id="1_script", groups=["lasers"], properties={"collision_layer": 4, "collision_mask": 8})
        laser_sprite = NodeAST(name="Sprite2D", type="Sprite2D", parent=".", properties={"texture": ExtResourceRef("2_tex")})
        laser_col = NodeAST(name="CollisionShape2D", type="CollisionShape2D", parent=".", properties={"shape": SubResourceRef("RectangleShape2D_laser")})
        laser_vis = NodeAST(name="VisibleNotifier", type="VisibleOnScreenNotifier2D", parent=".")
        laser_scn.nodes.extend([laser_node, laser_sprite, laser_col, laser_vis])
        laser_scn.add_connection("area_entered", ".", ".", "_on_area_entered")
        scenes.append(laser_scn)

        # 3. Asteroid Scene
        ast_scn = SceneAST(name="Asteroid", path="scenes/asteroid.tscn")
        ast_scn.add_ext_resource("Script", "res://scripts/asteroid.gd", "1_script")
        ast_scn.add_ext_resource("Texture2D", "res://assets/sprites/sprite_enemy.png", "2_tex")
        ast_scn.add_sub_resource("CircleShape2D", "CircleShape2D_ast", {"radius": 22.0})

        ast_node = NodeAST(name="Asteroid", type="Area2D", script_id="1_script", groups=["hazards"], properties={"collision_layer": 8, "collision_mask": 6})
        ast_sprite = NodeAST(name="Sprite2D", type="Sprite2D", parent=".", properties={"texture": ExtResourceRef("2_tex")})
        ast_col = NodeAST(name="CollisionShape2D", type="CollisionShape2D", parent=".", properties={"shape": SubResourceRef("CircleShape2D_ast")})
        ast_vis = NodeAST(name="VisibleNotifier", type="VisibleOnScreenNotifier2D", parent=".")
        ast_scn.nodes.extend([ast_node, ast_sprite, ast_col, ast_vis])
        ast_scn.add_connection("area_entered", ".", ".", "_on_area_entered")
        scenes.append(ast_scn)

        # 4. HUD Scene
        hud_scn = SceneAST(name="HUD", path="scenes/hud.tscn")
        hud_scn.add_ext_resource("Script", "res://scripts/hud.gd", "1_script")
        hud_root = NodeAST(name="HUD", type="CanvasLayer", script_id="1_script")
        score_lbl = NodeAST(name="ScoreLabel", type="Label", parent=".", properties={"position": Vector2(20.0, 20.0), "text": "Score: 0"})
        hull_lbl = NodeAST(name="HullLabel", type="Label", parent=".", properties={"position": Vector2(20.0, 50.0), "text": "Hull: 100%"})
        hud_scn.nodes.extend([hud_root, score_lbl, hull_lbl])
        scenes.append(hud_scn)

        # 5. SpaceArena Scene
        arena_scn = SceneAST(name="SpaceArena", path="scenes/space_arena.tscn")
        arena_scn.add_ext_resource("Texture2D", "res://assets/backgrounds/sprite_bg.png", "1_bg_tex")
        arena_scn.add_ext_resource("PackedScene", "res://scenes/starship.tscn", "2_ship_scn")
        arena_scn.add_ext_resource("PackedScene", "res://scenes/hud.tscn", "3_hud_scn")
        arena_scn.add_ext_resource("AudioStream", "res://audio/music/bgm_loop.wav", "4_bgm")

        arena_root = NodeAST(name="SpaceArena", type="Node2D")
        bg_node = NodeAST(name="Background", type="Sprite2D", parent=".", properties={"position": Vector2(640.0, 360.0), "texture": ExtResourceRef("1_bg_tex")})
        ship_inst = NodeAST(name="Starship", parent=".", instance="2_ship_scn", properties={"position": Vector2(640.0, 600.0)})
        laser_container = NodeAST(name="LaserContainer", type="Node2D", parent=".")
        ast_container = NodeAST(name="AsteroidContainer", type="Node2D", parent=".")
        timer_node = NodeAST(name="SpawnTimer", type="Timer", parent=".", properties={"autostart": True, "wait_time": 1.2})
        hud_inst = NodeAST(name="HUD", parent=".", instance="3_hud_scn")
        bgm_player = NodeAST(name="BGMPlayer", type="AudioStreamPlayer", parent=".", properties={"autoplay": True, "stream": ExtResourceRef("4_bgm")})

        arena_scn.nodes.extend([arena_root, bg_node, ship_inst, laser_container, ast_container, timer_node, hud_inst, bgm_player])
        scenes.append(arena_scn)

        return scenes

    def get_scripts(
        self,
        gdd: Optional[GameDesignDocument] = None,
        target_engine: str = "godot4",
    ) -> List[ScriptSpec]:
        scripts: List[ScriptSpec] = []

        starship_gd = """extends Area2D
class_name Starship

signal player_hit(remaining_hp: int)
signal ship_destroyed

@export var speed: float = 380.0
@export var fire_cooldown: float = 0.18
@export var max_health: int = 3

var health: int
var can_shoot: bool = true

@onready var muzzle: Marker2D = $Muzzle
@onready var shoot_sfx: AudioStreamPlayer2D = $ShootAudio
@onready var explosion_sfx: AudioStreamPlayer2D = $ExplosionAudio

func _ready() -> void:
\thealth = max_health

func _process(delta: float) -> void:
\tvar motion := Vector2.ZERO
\tif Input.is_action_pressed("ui_left"):
\t\tmotion.x -= 1.0
\tif Input.is_action_pressed("ui_right"):
\t\tmotion.x += 1.0
\tif Input.is_action_pressed("ui_up"):
\t\tmotion.y -= 1.0
\tif Input.is_action_pressed("ui_down"):
\t\tmotion.y += 1.0

\tposition += motion.normalized() * speed * delta
\tposition.x = clamp(position.x, 24.0, 1256.0)
\tposition.y = clamp(position.y, 24.0, 696.0)

\tif Input.is_action_pressed("ui_accept") and can_shoot:
\t\tfire_laser()

func fire_laser() -> void:
\tcan_shoot = false
\tif shoot_sfx and shoot_sfx.stream:
\t\tshoot_sfx.play()
\tawait get_tree().create_timer(fire_cooldown).timeout
\tcan_shoot = true

func take_damage(amount: int = 1) -> void:
\thealth -= amount
\tplayer_hit.emit(health)
\tif health <= 0:
\t\tif explosion_sfx and explosion_sfx.stream:
\t\t\texplosion_sfx.play()
\t\tship_destroyed.emit()
\t\tqueue_free()
"""
        scripts.append(ScriptSpec(rel_path="scripts/starship.gd", language="gdscript", code=starship_gd))

        laser_gd = """extends Area2D
class_name LaserBeam

@export var speed: float = 650.0

func _process(delta: float) -> void:
\tposition.y -= speed * delta

func _on_area_entered(area: Area2D) -> void:
\tif area.is_in_group("hazards"):
\t\tif area.has_method("destroy"):
\t\t\tarea.destroy()
\t\tqueue_free()
"""
        scripts.append(ScriptSpec(rel_path="scripts/laser.gd", language="gdscript", code=laser_gd))

        asteroid_gd = """extends Area2D
class_name AsteroidHazard

@export var speed: float = 180.0
@export var damage: int = 1

func _process(delta: float) -> void:
\tposition.y += speed * delta

func destroy() -> void:
\tqueue_free()

func _on_area_entered(area: Area2D) -> void:
\tif area.is_in_group("player"):
\t\tif area.has_method("take_damage"):
\t\t\tarea.take_damage(damage)
\t\tqueue_free()
"""
        scripts.append(ScriptSpec(rel_path="scripts/asteroid.gd", language="gdscript", code=asteroid_gd))

        hud_gd = """extends CanvasLayer
class_name ShmupHUD

@onready var score_label: Label = $ScoreLabel
@onready var hull_label: Label = $HullLabel

func update_score(score: int) -> void:
\tif score_label:
\t\tscore_label.text = "Score: " + str(score)

func update_hull(hull: int) -> void:
\tif hull_label:
\t\thull_label.text = "Hull: " + str(hull)
"""
        scripts.append(ScriptSpec(rel_path="scripts/hud.gd", language="gdscript", code=hud_gd))

        # Python / Pygame Shooter standalone
        shooter_py = """# Standalone Python / Pygame Space Shooter implementation
import sys
import random
try:
    import pygame
except ImportError:
    from game.engine_shim import pygame

def run_game(headless: bool = False, frames: int = 60) -> int:
    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    clock = pygame.time.Clock()
    ship_rect = pygame.Rect(620, 600, 36, 36)
    speed = 6.0
    score = 0
    lasers = []
    enemies = [pygame.Rect(random.randint(100, 1180), -40, 32, 32) for _ in range(5)]

    running = True
    frame_count = 0
    cooldown = 0
    while running and (not headless or frame_count < frames):
        frame_count += 1
        clock.tick(60)
        cooldown = max(0, cooldown - 1)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        keys = pygame.key.get_pressed()
        if keys[pygame.K_LEFT] or keys[pygame.K_a]: ship_rect.x = max(20, ship_rect.x - int(speed))
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]: ship_rect.x = min(1224, ship_rect.x + int(speed))
        if (keys[pygame.K_SPACE] or keys[pygame.K_UP]) and cooldown == 0:
            lasers.append(pygame.Rect(ship_rect.centerx - 3, ship_rect.top - 18, 6, 18))
            cooldown = 10

        # Update lasers
        for l in list(lasers):
            l.y -= 12
            if l.bottom < 0:
                lasers.remove(l)

        # Update enemies
        for e in enemies:
            e.y += 3
            if e.top > 720:
                e.x = random.randint(100, 1180)
                e.y = -30
            # Laser collisions
            for l in list(lasers):
                if e.colliderect(l):
                    if l in lasers: lasers.remove(l)
                    e.x = random.randint(100, 1180)
                    e.y = -30
                    score += 100

        if not headless:
            screen.fill((5, 10, 25))
            for l in lasers:
                pygame.draw.rect(screen, (0, 255, 200), l)
            for e in enemies:
                pygame.draw.circle(screen, (255, 80, 80), e.center, 16)
            pygame.draw.polygon(screen, (240, 240, 240), [
                (ship_rect.centerx, ship_rect.top),
                (ship_rect.left, ship_rect.bottom),
                (ship_rect.right, ship_rect.bottom),
            ])
            pygame.display.flip()

    pygame.quit()
    return score

if __name__ == "__main__":
    run_game()
"""
        scripts.append(ScriptSpec(rel_path="scripts/space_shooter_game.py", language="python", code=shooter_py))

        return scripts
