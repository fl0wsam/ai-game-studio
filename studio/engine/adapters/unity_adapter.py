"""
studio/engine/adapters/unity_adapter.py - Unity C# Engine Export Adapter.
Generates structured Unity 2D projects including C# MonoBehaviour scripts,
Packages/manifest.json, and an automated EditorSceneBuilder.cs editor script.
"""
from __future__ import annotations

import json
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


EDITOR_SCENE_BUILDER_CS = """#if UNITY_EDITOR
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Studio.Editor {
    public static class EditorSceneBuilder {
        [MenuItem("Studio/Build Generated Scene")]
        public static void BuildScene() {
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

            // 1. Camera
            var camGo = new GameObject("Main Camera");
            var cam = camGo.AddComponent<Camera>();
            cam.orthographic = true;
            cam.orthographicSize = 5.0f;
            camGo.transform.position = new Vector3(0, 0, -10);
            camGo.tag = "MainCamera";

            // 2. Game Manager
            var gmGo = new GameObject("GameManager");
            gmGo.AddComponent<GameManager>();

            // 3. Audio Manager
            var audioGo = new GameObject("AudioManager");
            audioGo.AddComponent<AudioManager>();

            // 4. Player
            var playerGo = new GameObject("Player");
            var sr = playerGo.AddComponent<SpriteRenderer>();
            var sprite = AssetDatabase.LoadAssetAtPath<Sprite>("Assets/Sprites/sprite_player.png");
            if (sprite != null) sr.sprite = sprite;

            var rb = playerGo.AddComponent<Rigidbody2D>();
            rb.freezeRotation = true;
            playerGo.AddComponent<BoxCollider2D>();
            playerGo.AddComponent<PlayerController>();

            // 5. Save Scene
            System.IO.Directory.CreateDirectory("Assets/Scenes");
            EditorSceneManager.SaveScene(scene, "Assets/Scenes/Main.unity");

            var buildScenes = new EditorBuildSettingsScene[] {
                new EditorBuildSettingsScene("Assets/Scenes/Main.unity", true)
            };
            EditorBuildSettings.scenes = buildScenes;

            Debug.Log("[Studio] Successfully built Assets/Scenes/Main.unity!");
        }
    }
}
#endif
"""

PLAYER_CONTROLLER_CS = """using UnityEngine;

public class PlayerController : MonoBehaviour {
    [SerializeField] private float speed = 6.0f;
    [SerializeField] private float jumpForce = 12.0f;
    private Rigidbody2D rb;
    private bool isGrounded = true;

    void Start() {
        rb = GetComponent<Rigidbody2D>();
    }

    void Update() {
        float move = Input.GetAxisRaw("Horizontal");
        if (rb != null) {
            rb.linearVelocity = new Vector2(move * speed, rb.linearVelocity.y);
        }

        if (Input.GetButtonDown("Jump") && isGrounded) {
            if (rb != null) {
                rb.linearVelocity = new Vector2(rb.linearVelocity.x, jumpForce);
            }
            isGrounded = false;
        }
    }

    private void OnCollisionEnter2D(Collision2D collision) {
        if (collision.contacts.Length > 0 && collision.contacts[0].normal.y > 0.5f) {
            isGrounded = true;
        }
    }
}
"""

GAME_MANAGER_CS = """using UnityEngine;

public class GameManager : MonoBehaviour {
    public static GameManager Instance { get; private set; }
    public int Score { get; private set; } = 0;
    public int Lives { get; private set; } = 3;

    private void Awake() {
        if (Instance == null) Instance = this;
        else Destroy(gameObject);
    }

    public void AddScore(int points) {
        Score += points;
    }

    public void TakeDamage(int damage) {
        Lives = Mathf.Max(0, Lives - damage);
    }
}
"""

AUDIO_MANAGER_CS = """using UnityEngine;

public class AudioManager : MonoBehaviour {
    public static AudioManager Instance { get; private set; }
    private AudioSource sfxSource;

    private void Awake() {
        if (Instance == null) Instance = this;
        else Destroy(gameObject);
        sfxSource = gameObject.AddComponent<AudioSource>();
    }

    public void PlaySound(AudioClip clip) {
        if (clip != null && sfxSource != null) {
            sfxSource.PlayOneShot(clip);
        }
    }
}
"""

PACKAGE_MANIFEST_JSON = {
    "dependencies": {
        "com.unity.2d.sprite": "1.0.0",
        "com.unity.2d.tilemap": "1.0.0",
        "com.unity.modules.physics2d": "1.0.0",
        "com.unity.modules.audio": "1.0.0",
        "com.unity.modules.ui": "1.0.0"
    }
}


class UnityAdapter(BaseEngineAdapter):
    """Unity export adapter generating C# scripts, package manifests, and scene builders."""

    @property
    def target(self) -> EngineTarget:
        return EngineTarget.UNITY

    @property
    def display_name(self) -> str:
        return "Unity (C# 2D)"

    @property
    def file_extensions(self) -> List[str]:
        return [".cs", ".unity", ".prefab"]

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

        # 1. Directory Structure
        for d in ("Assets/Editor", "Assets/Scripts", "Assets/Sprites", "Assets/Audio", "Assets/Scenes", "Assets/Docs", "Packages", "ProjectSettings"):
            (out_path / d).mkdir(parents=True, exist_ok=True)

        # 2. Materialize Assets
        for item in manifest.items:
            rel = item.rel_path.lstrip("/")
            filename = Path(rel).name
            atype = str(item.asset_type).lower()
            if "audio" in atype or "sfx" in atype or "music" in atype:
                target_rel = f"Assets/Audio/{filename}"
            else:
                target_rel = f"Assets/Sprites/{filename}"

            dest = out_path / target_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if item.file_bytes:
                dest.write_bytes(item.file_bytes)
            else:
                dest.write_text(f"// Placeholder for {item.asset_id}\n", encoding="utf-8")
            files_created.append(target_rel)

        # 3. Documentation
        doc_files = self.write_documentation(gdd, out_path / "Assets")
        for df in doc_files:
            files_created.append(f"Assets/{df}")

        # 4. Write C# Scripts
        (out_path / "Assets/Editor/EditorSceneBuilder.cs").write_text(EDITOR_SCENE_BUILDER_CS, encoding="utf-8")
        files_created.append("Assets/Editor/EditorSceneBuilder.cs")

        (out_path / "Assets/Scripts/PlayerController.cs").write_text(PLAYER_CONTROLLER_CS, encoding="utf-8")
        files_created.append("Assets/Scripts/PlayerController.cs")

        (out_path / "Assets/Scripts/GameManager.cs").write_text(GAME_MANAGER_CS, encoding="utf-8")
        files_created.append("Assets/Scripts/GameManager.cs")

        (out_path / "Assets/Scripts/AudioManager.cs").write_text(AUDIO_MANAGER_CS, encoding="utf-8")
        files_created.append("Assets/Scripts/AudioManager.cs")

        # 5. Packages/manifest.json
        (out_path / "Packages/manifest.json").write_text(json.dumps(PACKAGE_MANIFEST_JSON, indent=2), encoding="utf-8")
        files_created.append("Packages/manifest.json")

        # 6. ProjectSettings/ProjectSettings.asset
        project_settings_content = "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n--- !u!129 &1\nPlayerSettings:\n  companyName: Studio\n  productName: " + gdd.title + "\n"
        (out_path / "ProjectSettings" / "ProjectSettings.asset").write_text(project_settings_content, encoding="utf-8")
        files_created.append("ProjectSettings/ProjectSettings.asset")

        # 7. README.md
        readme = (
            f"# {gdd.title} (Unity 2D Edition)\n\n"
            "## How to Open and Build in Unity\n"
            "1. Open Unity Hub and click **Add Project from Disk**.\n"
            f"2. Select the directory: `{out_path}`.\n"
            "3. Once Unity loads, click the top menu: **Studio > Build Generated Scene**.\n"
            "4. The scene `Assets/Scenes/Main.unity` will be generated and registered in Build Settings.\n"
            "5. Press **Play** in the Unity Editor to test.\n"
        )
        (out_path / "README.md").write_text(readme, encoding="utf-8")
        files_created.append("README.md")

        return EngineProjectManifest(
            target=self.target,
            project_name=gdd.title,
            output_dir=str(out_path),
            entry_file="Assets/Editor/EditorSceneBuilder.cs",
            files_created=files_created,
            assets_linked=len(manifest.items),
            scenes_created=1,
            scripts_created=4,
            readme_path="README.md",
            run_command=self.get_run_command(out_path),
            metadata={"unity_editor_menu": "Studio/Build Generated Scene"},
        )

    def get_run_command(self, project_dir: Union[str, Path]) -> List[str]:
        return ["unity-editor", "-projectPath", str(project_dir)]
