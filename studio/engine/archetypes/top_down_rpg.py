"""
studio/engine/archetypes/top_down_rpg.py - Top-Down RPG Archetype Template.
Provides scenes and scripts for a Top-Down RPG (Hero, WorldMap, NPC, DialogueBox).
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


class TopDownRPGTemplate(BaseArchetypeTemplate):
    """Archetype template for a Top-Down RPG game."""

    @property
    def genre(self) -> GameGenre:
        return GameGenre.TOP_DOWN_RPG

    @property
    def display_name(self) -> str:
        return "Top-Down RPG"

    @property
    def description(self) -> str:
        return "Top-down fantasy adventure featuring 8-directional movement, melee combat, NPC dialogue, and quest progression."

    def get_asset_requirements(self) -> List[AssetRequirement]:
        return [
            AssetRequirement(asset_id="sprite_player", asset_type=AssetType.SPRITE, description="RPG hero adventurer sprite", target_rel_path="assets/sprites/sprite_player.png", width=32, height=32),
            AssetRequirement(asset_id="sprite_enemy", asset_type=AssetType.SPRITE, description="Forest goblin or NPC sprite", target_rel_path="assets/sprites/sprite_enemy.png", width=32, height=32),
            AssetRequirement(asset_id="sprite_item", asset_type=AssetType.SPRITE, description="Health potion or quest relic", target_rel_path="assets/sprites/sprite_item.png", width=16, height=16),
            AssetRequirement(asset_id="sprite_tile", asset_type=AssetType.TILEMAP, description="Dungeon floor and stone walls", target_rel_path="assets/tilesets/sprite_tile.png", width=128, height=128),
            AssetRequirement(asset_id="sprite_bg", asset_type=AssetType.TEXTURE, description="World map background map", target_rel_path="assets/backgrounds/sprite_bg.png", width=1280, height=720),
            AssetRequirement(asset_id="sfx_shoot", asset_type=AssetType.AUDIO_SFX, description="Sword swing swoosh sound", target_rel_path="audio/sfx/sfx_shoot.wav", duration_sec=0.2),
            AssetRequirement(asset_id="sfx_pickup", asset_type=AssetType.AUDIO_SFX, description="Quest item pickup chime", target_rel_path="audio/sfx/sfx_pickup.wav", duration_sec=0.25),
            AssetRequirement(asset_id="sfx_hit", asset_type=AssetType.AUDIO_SFX, description="Combat strike impact sound", target_rel_path="audio/sfx/sfx_hit.wav", duration_sec=0.25),
            AssetRequirement(asset_id="bgm_loop", asset_type=AssetType.AUDIO_MUSIC, description="Medieval atmospheric tavern or dungeon melody", target_rel_path="audio/music/bgm_loop.wav", duration_sec=45.0),
        ]

    def get_gdd(self, prompt: str = "", seed: int = 42, target_engine: str = "godot4") -> GameDesignDocument:
        title = "Chronicles of Aethelgard" if "chronicles" in prompt.lower() else "Dungeon Crawler RPG"
        return GameDesignDocument(
            title=title,
            genre=self.genre.value,
            target_engine=target_engine,
            high_concept="Explore mysterious dungeons, converse with village elders, and defeat monsters with sword combat.",
            gameplay_mechanics=[
                "Smooth 8-directional normalized character movement",
                "Directional melee sword strike with active hitbox window",
                "NPC interaction proximity detection and interactive dialogue UI",
                "Quest objective tracking and completion triggers",
                "Inventory and health tracking HUD",
            ],
            narrative_lore="An ancient darkness stirs beneath the kingdom. The lone traveler must seek the village elder and cleanse the ruins.",
            art_style_guide={"palette": "earthy fantasy 16-bit", "sprite_resolution": "32x32 top-down"},
            audio_style_guide={"music_genre": "orchestral fantasy chiptune", "sfx_style": "organic sword and impact sounds"},
            quests=[
                QuestSpec(
                    quest_id="quest_talk_to_elder",
                    title="The Elder's Guidance",
                    description="Speak with the Village Elder to learn about the subterranean threat.",
                    objectives=[
                        QuestObjective(objective_id="obj_0", description="Find the Village Elder", target_count=1),
                        QuestObjective(objective_id="obj_1", description="Accept the quest", target_count=1),
                    ],
                )
            ],
            dialogue_trees=[
                DialogueNode(node_id="elder_1", speaker="Village Elder", text="Greetings, brave traveler! Goblins have infested the outer ruins.")
            ],
            required_assets=self.get_asset_requirements(),
            scenes_plan=[
                ScenePlan(name="WorldMap", path="scenes/world_map.tscn", root_node_type="Node2D", required_assets=["sprite_bg", "bgm_loop"]),
                ScenePlan(name="Hero", path="scenes/hero.tscn", root_node_type="CharacterBody2D", required_assets=["sprite_player", "sfx_shoot", "sfx_hit"]),
                ScenePlan(name="NPC", path="scenes/npc.tscn", root_node_type="StaticBody2D", required_assets=["sprite_enemy"]),
                ScenePlan(name="DialogueBox", path="scenes/dialogue_box.tscn", root_node_type="CanvasLayer"),
            ],
        )

    def get_scenes(
        self,
        gdd: Optional[GameDesignDocument] = None,
        manifest: Optional[AssetManifest] = None,
    ) -> List[SceneAST]:
        scenes: List[SceneAST] = []

        # 1. Hero Scene
        hero_scn = SceneAST(name="Hero", path="scenes/hero.tscn")
        hero_scn.add_ext_resource("Script", "res://scripts/hero.gd", "1_script")
        hero_scn.add_ext_resource("Texture2D", "res://assets/sprites/sprite_player.png", "2_tex")
        hero_scn.add_ext_resource("AudioStream", "res://audio/sfx/sfx_shoot.wav", "3_swing_sfx")
        hero_scn.add_ext_resource("AudioStream", "res://audio/sfx/sfx_hit.wav", "4_hit_sfx")
        hero_scn.add_sub_resource("CircleShape2D", "CircleShape2D_hero", {"radius": 16.0})
        hero_scn.add_sub_resource("RectangleShape2D", "RectangleShape2D_sword", {"size": Vector2(28.0, 36.0)})

        hero_node = NodeAST(name="Hero", type="CharacterBody2D", script_id="1_script", groups=["hero"], properties={"collision_layer": 2, "collision_mask": 1})
        sprite_node = NodeAST(name="Sprite2D", type="Sprite2D", parent=".", properties={"texture": ExtResourceRef("2_tex")})
        col_node = NodeAST(name="CollisionShape2D", type="CollisionShape2D", parent=".", properties={"shape": SubResourceRef("CircleShape2D_hero")})
        sword_area = NodeAST(name="SwordHitbox", type="Area2D", parent=".", properties={"collision_layer": 16, "collision_mask": 4})
        sword_shape = NodeAST(name="HitShape", type="CollisionShape2D", parent="SwordHitbox", properties={"position": Vector2(24.0, 0.0), "shape": SubResourceRef("RectangleShape2D_sword")})
        swing_audio = NodeAST(name="SwingAudio", type="AudioStreamPlayer2D", parent=".", properties={"stream": ExtResourceRef("3_swing_sfx")})
        hurt_audio = NodeAST(name="HurtAudio", type="AudioStreamPlayer2D", parent=".", properties={"stream": ExtResourceRef("4_hit_sfx")})
        hero_scn.nodes.extend([hero_node, sprite_node, col_node, sword_area, sword_shape, swing_audio, hurt_audio])
        scenes.append(hero_scn)

        # 2. NPC Scene
        npc_scn = SceneAST(name="NPC", path="scenes/npc.tscn")
        npc_scn.add_ext_resource("Script", "res://scripts/npc.gd", "1_script")
        npc_scn.add_ext_resource("Texture2D", "res://assets/sprites/sprite_enemy.png", "2_tex")
        npc_scn.add_sub_resource("CircleShape2D", "CircleShape2D_npc", {"radius": 16.0})
        npc_scn.add_sub_resource("CircleShape2D", "CircleShape2D_zone", {"radius": 48.0})

        npc_node = NodeAST(name="NPC", type="StaticBody2D", script_id="1_script", properties={"collision_layer": 1, "collision_mask": 2})
        npc_sprite = NodeAST(name="Sprite2D", type="Sprite2D", parent=".", properties={"texture": ExtResourceRef("2_tex")})
        npc_shape = NodeAST(name="CollisionShape2D", type="CollisionShape2D", parent=".", properties={"shape": SubResourceRef("CircleShape2D_npc")})
        interact_area = NodeAST(name="InteractionZone", type="Area2D", parent=".", properties={"collision_layer": 0, "collision_mask": 2})
        zone_shape = NodeAST(name="ZoneShape", type="CollisionShape2D", parent="InteractionZone", properties={"shape": SubResourceRef("CircleShape2D_zone")})
        prompt_lbl = NodeAST(name="PromptLabel", type="Label", parent=".", properties={"text": "[E] Talk", "visible": False, "position": Vector2(-20.0, -32.0)})
        npc_scn.nodes.extend([npc_node, npc_sprite, npc_shape, interact_area, zone_shape, prompt_lbl])
        npc_scn.add_connection("body_entered", "InteractionZone", ".", "_on_interaction_zone_body_entered")
        npc_scn.add_connection("body_exited", "InteractionZone", ".", "_on_interaction_zone_body_exited")
        scenes.append(npc_scn)

        # 3. Dialogue Box Scene
        diag_scn = SceneAST(name="DialogueBox", path="scenes/dialogue_box.tscn")
        diag_scn.add_ext_resource("Script", "res://scripts/dialogue_box.gd", "1_script")
        diag_root = NodeAST(name="DialogueBox", type="CanvasLayer", script_id="1_script", properties={"visible": False})
        panel = NodeAST(name="Panel", type="Panel", parent=".", properties={"position": Vector2(240.0, 500.0), "size": Vector2(800.0, 160.0)})
        speaker_lbl = NodeAST(name="SpeakerLabel", type="Label", parent="Panel", properties={"position": Vector2(20.0, 15.0), "text": "Elder Guide"})
        text_lbl = NodeAST(name="DialogueText", type="Label", parent="Panel", properties={"position": Vector2(20.0, 45.0), "text": "Welcome traveler...", "size": Vector2(760.0, 70.0)})
        diag_scn.nodes.extend([diag_root, panel, speaker_lbl, text_lbl])
        scenes.append(diag_scn)

        # 4. WorldMap Scene
        world_scn = SceneAST(name="WorldMap", path="scenes/world_map.tscn")
        world_scn.add_ext_resource("Texture2D", "res://assets/backgrounds/sprite_bg.png", "1_bg_tex")
        world_scn.add_ext_resource("PackedScene", "res://scenes/hero.tscn", "2_hero_scn")
        world_scn.add_ext_resource("PackedScene", "res://scenes/npc.tscn", "3_npc_scn")
        world_scn.add_ext_resource("PackedScene", "res://scenes/dialogue_box.tscn", "4_diag_scn")
        world_scn.add_ext_resource("AudioStream", "res://audio/music/bgm_loop.wav", "5_bgm")

        world_root = NodeAST(name="WorldMap", type="Node2D")
        bg_node = NodeAST(name="Ground", type="Sprite2D", parent=".", properties={"position": Vector2(640.0, 360.0), "texture": ExtResourceRef("1_bg_tex")})
        hero_inst = NodeAST(name="Hero", parent=".", instance="2_hero_scn", properties={"position": Vector2(400.0, 300.0)})
        npc_inst = NodeAST(name="NPC", parent=".", instance="3_npc_scn", properties={"position": Vector2(550.0, 280.0)})
        diag_inst = NodeAST(name="DialogueBox", parent=".", instance="4_diag_scn")
        bgm_node = NodeAST(name="BGMPlayer", type="AudioStreamPlayer", parent=".", properties={"autoplay": True, "stream": ExtResourceRef("5_bgm")})
        world_scn.nodes.extend([world_root, bg_node, hero_inst, npc_inst, diag_inst, bgm_node])
        scenes.append(world_scn)

        return scenes

    def get_scripts(
        self,
        gdd: Optional[GameDesignDocument] = None,
        target_engine: str = "godot4",
    ) -> List[ScriptSpec]:
        scripts: List[ScriptSpec] = []

        hero_gd = """extends CharacterBody2D
class_name RPGHero

signal health_changed(current_hp: int)

@export var speed: float = 220.0
@export var max_health: int = 100
@export var attack_duration: float = 0.2

var current_health: int
var is_attacking: bool = false
var can_move: bool = true
var facing_direction: Vector2 = Vector2.DOWN

@onready var sprite: Sprite2D = $Sprite2D
@onready var sword_hitbox: Area2D = $SwordHitbox
@onready var sword_shape: CollisionShape2D = $SwordHitbox/HitShape
@onready var swing_sfx: AudioStreamPlayer2D = $SwingAudio

func _ready() -> void:
\tcurrent_health = max_health
\thealth_changed.emit(current_health)

func _physics_process(_delta: float) -> void:
\tif not can_move or is_attacking:
\t\tvelocity = Vector2.ZERO
\t\tmove_and_slide()
\t\treturn

\tvar direction := Input.get_vector("ui_left", "ui_right", "ui_up", "ui_down")
\tif direction != Vector2.ZERO:
\t\tvelocity = direction * speed
\t\tfacing_direction = direction.normalized()
\t\t_update_facing(facing_direction)
\telse:
\t\tvelocity = Vector2.ZERO

\tif Input.is_action_just_pressed("ui_accept"):
\t\tattack()

\tmove_and_slide()

func _update_facing(dir: Vector2) -> void:
\tif abs(dir.x) > abs(dir.y):
\t\tsprite.flip_h = dir.x < 0.0
\t\tsword_hitbox.rotation = 0.0 if dir.x > 0.0 else PI
\telse:
\t\tsword_hitbox.rotation = PI * 0.5 if dir.y > 0.0 else -PI * 0.5

func attack() -> void:
\tif is_attacking:
\t\treturn
\tis_attacking = true
\tif swing_sfx and swing_sfx.stream:
\t\tswing_sfx.play()
\tsword_shape.disabled = false
\tawait get_tree().create_timer(attack_duration).timeout
\tsword_shape.disabled = true
\tis_attacking = false
"""
        scripts.append(ScriptSpec(rel_path="scripts/hero.gd", language="gdscript", code=hero_gd))

        npc_gd = """extends StaticBody2D
class_name NonPlayerCharacter

@onready var prompt: Label = $PromptLabel
var player_in_range: bool = false

func _on_interaction_zone_body_entered(body: Node2D) -> void:
\tif body.is_in_group("hero"):
\t\tplayer_in_range = true
\t\tif prompt:
\t\t\tprompt.visible = true

func _on_interaction_zone_body_exited(body: Node2D) -> void:
\tif body.is_in_group("hero"):
\t\tplayer_in_range = false
\t\tif prompt:
\t\t\tprompt.visible = false

func _unhandled_input(event: InputEvent) -> void:
\tif player_in_range and event.is_action_pressed("ui_accept"):
\t\tvar diag = get_tree().root.find_child("DialogueBox", true, false)
\t\tif diag and diag.has_method("start_dialogue"):
\t\t\tdiag.start_dialogue("Village Elder", "Greetings, brave traveler! Goblins have infested the outer ruins.")
"""
        scripts.append(ScriptSpec(rel_path="scripts/npc.gd", language="gdscript", code=npc_gd))

        dialogue_gd = """extends CanvasLayer
class_name DialogueController

@onready var speaker_label: Label = $Panel/SpeakerLabel
@onready var text_label: Label = $Panel/DialogueText

func start_dialogue(speaker: str, text: str) -> void:
\tif speaker_label:
\t\tspeaker_label.text = speaker
\tif text_label:
\t\ttext_label.text = text
\tvisible = true

func close_dialogue() -> void:
\tvisible = false
"""
        scripts.append(ScriptSpec(rel_path="scripts/dialogue_box.gd", language="gdscript", code=dialogue_gd))

        # Python / Pygame RPG standalone
        rpg_py = """# Standalone Python / Pygame Top-Down RPG implementation
import sys
try:
    import pygame
except ImportError:
    from game.engine_shim import pygame

def run_game(headless: bool = False, frames: int = 60) -> int:
    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    clock = pygame.time.Clock()
    player_rect = pygame.Rect(400, 300, 32, 32)
    npc_rect = pygame.Rect(550, 280, 32, 32)
    speed = 4.0
    score = 0
    in_dialogue = False

    running = True
    frame_count = 0
    while running and (not headless or frame_count < frames):
        frame_count += 1
        clock.tick(60)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        keys = pygame.key.get_pressed()
        dx, dy = 0.0, 0.0
        if keys[pygame.K_LEFT] or keys[pygame.K_a]: dx -= speed
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]: dx += speed
        if keys[pygame.K_UP] or keys[pygame.K_w]: dy -= speed
        if keys[pygame.K_DOWN] or keys[pygame.K_s]: dy += speed

        player_rect.x += int(dx)
        player_rect.y += int(dy)

        # NPC interaction check
        dist = abs(player_rect.centerx - npc_rect.centerx) + abs(player_rect.centery - npc_rect.centery)
        if dist < 60 and (keys[pygame.K_SPACE] or keys[pygame.K_e]):
            in_dialogue = True
            score += 50

        if not headless:
            screen.fill((30, 45, 30))
            pygame.draw.rect(screen, (80, 180, 80), npc_rect)
            pygame.draw.rect(screen, (0, 200, 255), player_rect)
            if in_dialogue:
                pygame.draw.rect(screen, (10, 10, 20), pygame.Rect(200, 500, 880, 150))
            pygame.display.flip()

    pygame.quit()
    return score

if __name__ == "__main__":
    run_game()
"""
        scripts.append(ScriptSpec(rel_path="scripts/rpg_game.py", language="python", code=rpg_py))

        return scripts
