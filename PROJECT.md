# Project: Autonomous Multi-Agent AI Game Studio

## Architecture

The AI Game Studio is an autonomous multi-agent software system that rigidly enforces a specialized division of labor across three AI frontier providers while remaining modular and engine-flexible (supporting Godot 4, HTML5/Phaser/Web, Pygame, and Unity).

```
                           +-----------------------------------+
                           |    Prompt & Directing Panel (UI)  |
                           +-----------------+-----------------+
                                             | (Directing Request)
                                             v
                           +-----------------------------------+
                           |     Studio Orchestrator Core      |
                           +-----------------+-----------------+
                                             |
             +-------------------------------+-------------------------------+
             |                               |                               |
             v                               v                               v
+-------------------------+     +-------------------------+     +-------------------------+
|   OpenAI Director       |     |   Google Gemini Artist  |     | Anthropic Claude Engine |
|  - Game Design Doc (GDD)|     |  - 2D Sprites & Textures|     |  - Core Game Code (.gd, |
|  - World Lore & Lorebook|     |  - Background Music/BGM |     |    .py, .js, .cs)       |
|  - Dialogue Trees & NPC |     |  - Sound Effects (SFX)  |     |  - Scene Trees & Nodes  |
|  - Quest Mechanics      |     |  - Video/Cutscenes      |     |  - Physics & Mechanics  |
+------------+------------+     +------------+------------+     +------------+------------+
             |                               |                               |
             +-------------------------------+-------------------------------+
                                             |
                                             v
                           +-----------------------------------+
                           |    Blackboard State & Event Bus   |
                           +-----------------+-----------------+
                                             |
             +-------------------------------+-------------------------------+
             |                               |                               |
             v                               v                               v
+-------------------------+     +-------------------------+     +-------------------------+
| Modular Engine Pipeline |     |  Verification Pipeline  |     |   Studio Dashboard GUI  |
| - Engine Adapter Hub    |     | - Code Syntax Checkers  |     | - Real-Time Agent Graph |
| - Godot 4 (.tscn / .gd) |     | - Scene Schema Validator|     | - Media Preview Gallery |
| - Pygame / HTML5 Web    |     | - Asset Integrity Ver.  |     | - Streaming Log Viewer  |
| - Dynamic Asset Linker  |     | - Dual-Mode Simulator   |     | - Directing & Build UI  |
+-------------------------+     +-------------------------+     +-------------------------+
```

### Specialized Division of AI Labor
- **OpenAI (Codex / GPT)**: Solely responsible for creative direction, narrative architecture, GDD formulation, lore bible, dialogue trees, quest mechanics, and gameplay specifications.
- **Google (Gemini)**: Solely responsible for multimodal asset generation: 2D pixel art/sprites, environmental textures, background music tracks, sound effects, and animated storyboard/video cutscenes.
- **Anthropic (Claude)**: Solely responsible for systems engineering: game mechanics code, engine integration scripts, scene hierarchy specifications, collision/physics logic, and runtime debugging.

---

## Feature Inventory

Every feature from user requirements (`ORIGINAL_REQUEST.md`) and survey exploration is inventoried below with an assigned milestone:

| # | Feature ID | Feature Name | Description | Milestone | Source |
|---|------------|--------------|-------------|-----------|--------|
| 1 | F-ORCH-01 | Blackboard State Store | Canonical centralized state repository storing GDD, manifests, scene specs, and run metadata | M1 | Survey R1 |
| 2 | F-ORCH-02 | Async In-Process Event Bus | Pub/sub message bus routing inter-agent requests, reviews, and UI event broadcasts | M1 | Survey R1 |
| 3 | F-ORCH-03 | OpenAI Narrative Agent | Generates comprehensive GDD, lore bible, dialogue trees, quest mechanics | M1 | Req R1 / Directive |
| 4 | F-ORCH-04 | Google Gemini Multimodal Agent | Generates visual artwork (sprites, textures) and audio assets (SFX, music, cutscenes) | M1 | Req R1 / Directive |
| 5 | F-ORCH-05 | Anthropic Claude Code Agent | Generates engine scripts (`.gd`, `.py`, `.js`), scene structures, and game mechanics | M1 | Req R1 / Directive |
| 6 | F-ORCH-06 | Provider Resilience & Security | Token bucket rate limiting, exponential backoff, circuit breakers, and regex secret masking | M1 | Req R1 |
| 7 | F-ORCH-07 | Deterministic Mock Provider | 100% offline procedural asset and code generator for deterministic CI and offline test runs | M1 | Survey R1 |
| 8 | F-ENG-01 | Modular Engine Adapter Hub | Pluggable engine export architecture supporting Godot 4, Pygame/Python, HTML5/Web, Unity | M2 | User Directive |
| 9 | F-ENG-02 | Godot 4 AST & Serializer | Python dataclass AST model and serializer producing 100% valid Godot 4 `.tscn` and `project.godot` | M2 | Req R3 |
| 10 | F-ENG-03 | Clean Hierarchy Packager | Creates standard directory hierarchy: `assets/`, `audio/`, `scenes/`, `scripts/`, `docs/` | M2 | Req R3 |
| 11 | F-ENG-04 | Dynamic Asset Linker | Binds generated sprites and audio to scene nodes and engine scripts via resource IDs and paths | M2 | Req R3 |
| 12 | F-ENG-05 | Game Archetype Templates | Complete templates for 2D Platformer, Top-Down RPG, Space Shooter, and Grid Puzzle | M2 | Survey R3 |
| 13 | F-ENG-06 | Python/Web/Unity Bridges | Auxiliary export adapters for Pygame, HTML5/Canvas, and Unity C# scene builders | M2 | User Directive |
| 14 | F-VERIF-01 | Multi-Engine Syntax Validator | Syntax and AST checkers for GDScript, Python, and JavaScript engine code | M3 | Req R4 |
| 15 | F-VERIF-02 | TSCN Schema & Reference Checker | Validates `.tscn` load_steps, node hierarchies, script attachments, and resource links | M3 | Req R4 |
| 16 | F-VERIF-03 | Asset Integrity Verifier | Verifies all referenced textures and audio exist, are non-empty, and match headers | M3 | Req R4 |
| 17 | F-VERIF-04 | Dual-Mode Engine Simulator | Runs real `godot --headless` or mock engine frame stepper validating startup without fatal errors | M3 | Req R4 |
| 18 | F-GUI-01 | Single-Command CLI Launcher | CLI entrypoint `ai-game-studio` / `python -m studio.cli` launching GUI or headless builds | M4 | Req R2 |
| 19 | F-GUI-02 | Interactive Agent Graph | Real-time animated graph showing Claude, Gemini, OpenAI states, tasks, and message flows | M4 | Req R2 |
| 20 | F-GUI-03 | Media & Asset Preview Gallery | Live preview of generated visual artwork, audio player for music/SFX, and cutscene viewer | M4 | Req R2 |
| 21 | F-GUI-04 | Workspace & Log Viewer | Streaming execution logs, token usage / cost metrics, and GDD / script inspector | M4 | Req R2 |
| 22 | F-GUI-05 | Prompt & Directing Panel | UI to input game ideas, select target engine, configure constraints, and trigger builds | M4 | Req R2 |
| 23 | F-E2E-01 | E2E Test Suite (Tiers 1-4) | Comprehensive test suite covering feature tests, boundaries, combinations, real workloads | E2E-Track | Dual Track |
| 24 | F-E2E-02 | Final E2E Test Pass | 100% pass of all automated test tiers across full tri-provider generation flow | M5 | Req Criteria |
| 25 | F-E2E-03 | Tier 5 Adversarial Hardening | Adversarial stress testing, edge-case probing, and coverage hardening | M5 | Final Milestone |

---

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Tri-Provider Orchestrator & State Bus | F-ORCH-01 through F-ORCH-07: Pydantic schemas, Blackboard state, Event Bus, Provider Clients (Claude, Gemini, OpenAI, Mock), resilience/rate limiting | None | DONE |
| M2 | Modular Engine Project Pipeline & Asset Linker | F-ENG-01 through F-ENG-06: Modular Engine Hub, Godot 4 AST and serializer, Pygame/Web/Unity adapters, hierarchy packager, dynamic asset linker, archetype templates | M1 | DONE |
| M3 | Automated Verification & Simulation Pipeline | F-VERIF-01 through F-VERIF-04: Multi-engine script syntax validator, TSCN schema checker, asset verifier, dual-mode simulator runner | M2 | DONE |
| M4 | Interactive Visual Studio GUI & CLI | F-GUI-01 through F-GUI-05: FastAPI + WebSocket server, responsive web dashboard (Agent Graph, Media Gallery, Log Viewer, Directing Panel), CLI entrypoint | M1, M2, M3 | DONE |
| E2E | E2E Testing Track | F-E2E-01: Requirement-driven opaque-box test suite (Tiers 1-4: Feature, Boundary, Combinations, Real-World) + TEST_READY.md | None | DONE (389 tests active) |
| M5 | E2E Test Pass & Adversarial Hardening | F-E2E-02, F-E2E-03: Pass 100% of E2E test suite + Tier 5 adversarial stress testing | M4, E2E | DONE (Certified PASS, 389/389 tests) |

---

## Interface Contracts

### 1. Inter-Agent Message & Event Bus Contract
```python
class AgentRole(str, Enum):
    DIRECTOR = "openai_director"       # Story, GDD, Quests, Lore
    ARTIST = "gemini_artist"           # Visuals, Audio, Cutscenes
    ENGINEER = "claude_engineer"       # Architecture, Scripts, Scenes, Code
    ORCHESTRATOR = "orchestrator"     # Workflow controller
    USER = "user"                     # Human director

class AgentMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sender: AgentRole
    recipient: AgentRole
    message_type: str  # "TASK_ASSIGN", "GDD_READY", "ASSETS_READY", "CODE_READY", "REVIEW_FEEDBACK"
    payload: Dict[str, Any]
    timestamp: float = Field(default_factory=time.time)
```

### 2. Game Design Document (OpenAI Output)
```python
class GameDesignDocument(BaseModel):
    title: str
    genre: str
    target_engine: str = "godot4"  # "godot4", "pygame", "web_html5", "unity"
    high_concept: str
    gameplay_mechanics: List[str]
    narrative_lore: str
    art_style_guide: Dict[str, Any]
    audio_style_guide: Dict[str, Any]
    required_assets: List[AssetRequirement]
    scenes_plan: List[ScenePlan]
```

### 3. Asset Manifest & Linker (Gemini Output -> Engine Linker)
```python
class AssetItem(BaseModel):
    asset_id: str
    asset_type: str  # "sprite", "texture", "audio_sfx", "audio_music", "cutscene"
    rel_path: str    # e.g. "assets/sprites/player.png", "audio/sfx/jump.wav"
    file_bytes: Optional[bytes] = None
    properties: Dict[str, Any] = Field(default_factory=dict)

class AssetManifest(BaseModel):
    project_name: str
    items: List[AssetItem]
```

### 4. Engine Scene AST & Code Spec (Claude Output -> Pipeline)
```python
class ExtResourceAST(BaseModel):
    id: str
    type: str  # "Texture2D", "AudioStream", "Script"
    path: str  # "res://assets/sprites/player.png"

class NodeAST(BaseModel):
    name: str
    type: str  # "CharacterBody2D", "Sprite2D", "CollisionShape2D", "Camera2D"
    parent: Optional[str] = None
    properties: Dict[str, Any] = Field(default_factory=dict)
    script_id: Optional[str] = None

class SceneAST(BaseModel):
    name: str
    path: str
    ext_resources: List[ExtResourceAST] = Field(default_factory=list)
    nodes: List[NodeAST] = Field(default_factory=list)
    connections: List[Dict[str, Any]] = Field(default_factory=list)

class ScriptSpec(BaseModel):
    rel_path: str  # e.g. "scripts/player.gd" or "player.py"
    language: str  # "gdscript", "python", "javascript", "csharp"
    code: str
```

### 5. Verification Pipeline Contract
```python
class DiagnosticError(BaseModel):
    stage: str  # "syntax_check", "schema_check", "asset_integrity", "simulation"
    severity: str  # "ERROR", "WARNING"
    file_path: str
    line: Optional[int] = None
    message: str

class VerificationReport(BaseModel):
    passed: bool
    stages_executed: List[str]
    errors: List[DiagnosticError]
    warnings: List[DiagnosticError]
    execution_time_ms: float
    simulation_frames_run: int
```

### 6. Studio GUI Telemetry (WebSocket /api/ws/telemetry)
```python
class GuiTelemetryEvent(BaseModel):
    event_type: str  # "agent_state", "log_chunk", "asset_generated", "build_progress", "verification_done"
    agent: Optional[str] = None
    state: Optional[str] = None  # "idle", "active", "reviewing", "complete", "failed"
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)
```

---

## Code Layout

```
ai_game_studio/
├── pyproject.toml                     # Project packaging & dependencies
├── README.md                          # Architecture and launch guide
├── studio/                            # Core application package
│   ├── __init__.py
│   ├── cli.py                         # Single-command CLI entrypoint (`ai-game-studio`)
│   ├── core/                          # R1: Core orchestration & state
│   │   ├── __init__.py
│   │   ├── blackboard.py              # Central project state repository
│   │   ├── event_bus.py               # Async pub/sub event bus
│   │   ├── models.py                  # Pydantic v2 domain models
│   │   ├── orchestrator.py            # Tri-provider workflow coordinator
│   │   └── security.py                # Credential manager, masking, rate limiter
│   ├── providers/                     # R1: Specialized AI provider clients & mock
│   │   ├── __init__.py
│   │   ├── base.py                    # Provider client abstract interface
│   │   ├── openai_client.py           # OpenAI GDD, lore, and narrative client
│   │   ├── gemini_client.py           # Gemini multimodal image/audio client
│   │   ├── claude_client.py           # Claude engine architecture & code client
│   │   └── mock_provider.py           # Pure-Python deterministic offline mock provider
│   ├── engine/                        # R3: Modular game engine project pipeline
│   │   ├── __init__.py
│   │   ├── hub.py                     # Modular engine adapter hub
│   │   ├── ast_models.py              # Intermediate SceneAST, NodeAST dataclasses
│   │   ├── dynamic_asset_linker.py    # Links media files into scenes and nodes
│   │   ├── hierarchy.py               # Standard folder hierarchy packager
│   │   ├── archetypes/                # Archetype presets (platformer, rpg, shooter, puzzle)
│   │   └── adapters/                  # Engine-specific adapters
│   │       ├── godot_adapter.py       # Godot 4 .tscn serializer & project.godot
│   │       ├── pygame_adapter.py      # Pygame / Python runnable exporter
│   │       ├── web_adapter.py         # HTML5 / Canvas / Phaser exporter
│   │       └── unity_adapter.py       # Unity C# and EditorSceneBuilder generator
│   ├── verification/                  # R4: Automated verification & simulation
│   │   ├── __init__.py
│   │   ├── syntax_validators.py       # GDScript, Python, JS syntax & AST validator
│   │   ├── tscn_validator.py          # TSCN scene graph & reference integrity validator
│   │   ├── asset_verifier.py          # Binary asset existence & header validator
│   │   ├── engine_simulator.py        # Dual-mode simulator (godot CLI + MockEngineSimulator)
│   │   └── pipeline.py                # Unified verification pipeline runner
│   └── gui/                           # R2: Interactive Visual Studio GUI
│       ├── __init__.py
│       ├── server.py                  # FastAPI + Uvicorn ASGI server
│       ├── websocket_manager.py       # Live telemetry and log streaming manager
│       ├── routes.py                  # REST API routes (directing, builds, projects)
│       └── static/                    # Embedded single-page web GUI
│           ├── index.html             # Studio dashboard layout
│           ├── style.css              # Modern dark-mode studio theme
│           ├── app.js                 # UI controller, media player, log viewer
│           └── agent_graph.js         # Canvas/Cytoscape dynamic agent graph
└── tests/                             # Dual Track: Comprehensive Test Suite
    ├── test_runner.py                 # Self-contained test suite runner
    ├── tier1_features/                # Tier 1: Feature Coverage (>=5 per feature)
    ├── tier2_boundaries/              # Tier 2: Boundary & Corner Cases (>=5 per feature)
    ├── tier3_combinations/            # Tier 3: Cross-Feature Combinations
    └── tier4_applications/            # Tier 4: Real-World Workloads & Scenarios
```
