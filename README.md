# AI Game Studio

An autonomous, multi-agent game creation studio and IDE workspace coordinating specialized AI agents to autonomously generate complete, playable games across HTML5 Canvas, Godot 4, Pygame, and Unity 2D.

![AI Game Studio Dashboard](studio/gui/static/favicon.ico)

## Overview

AI Game Studio divides game production labor across specialized agent roles:
- **Studio Orchestrator**: Coordinates the pipeline, state blackboard, telemetry, and live agent graph.
- **OpenAI Director (Narrative & GDD)**: Crafts the Game Design Document, narrative lore, progression curve, and mechanics specifications.
- **Gemini Artist (Multimodal Media)**: Generates procedural sprite sheets, textures, audio sound effects, and synth music tracks.
- **Claude Engineer (Systems Architecture)**: Synthesizes engine code, scene trees (AST), physics, and gameplay loops.
- **Automated Validator (QA & Simulation)**: Enforces syntax validation, asset integrity, scene verification, and playable simulation smoke tests.

## Features

- **Multi-Engine Target Support**:
  - `web_html5`: Playable directly in browser with retro audio synthesizer, particle systems, screen shake, and boss encounters.
  - `godot4`: Generates `.tscn` scene trees and GDScript scripts.
  - `pygame`: Standalone Python/Pygame game packages.
  - `unity`: C# scripts and scene definitions.
- **Professional IDE Workspace**:
  - Central Agent Graph with active streaming edge animations and topology status.
  - Project File Explorer, Scene Hierarchy, GDD Inspector, and Live Telemetry Terminal.
  - Zero-CDN architecture: completely self-contained and air-gapped.
- **Iterative Refinement**:
  - Post-build prompt refinement bar allowing live tweaking of player mechanics, difficulty, speed, health, and visual styling.
- **Offline Deterministic Mock Mode**:
  - Runs fully offline without API keys required for development and automated testing.

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/fl0wsam/ai_game_studio.git
cd ai_game_studio

# Create an isolated environment and install dependencies
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

### Running the IDE Dashboard

```bash
# Launch the web IDE on port 8080 without opening a browser
.venv/bin/python -m studio.cli --gui --no-browser --port 8080
```

Open `http://localhost:8080` in your browser.

### First game: Director mode

The dashboard is designed as a small studio control room for learning how game teams work:

1. Pick a starter template, or describe your own game idea.
2. Give the project a name such as `Rocket Garden`.
3. Keep **Offline Mock** enabled for a free, deterministic demo with no API keys.
4. Click **Build My Game** and watch the stages move from Imagine to Plan to Build to Play.
5. Click **Play Game** when the demo is ready, then use **Refine Game** to request a small change.

The project name is the director's workspace name. The AI may create a different in-game title
for the generated GDD; both are shown separately so children can see the difference between
running a studio project and designing the game inside it.

### Headless CLI Generation

```bash
# Generate a playable web game from a text prompt
.venv/bin/python -m studio.cli --headless \
  --prompt "Cyberpunk ninja runner with high-speed obstacles and synthwave audio" \
  --engine web_html5 \
  --output-dir ./output \
  --mock \
  --verify
```

### Running Tests

AI Game Studio comes with a comprehensive multi-tier test suite:

```bash
# Run all test tiers (Tiers 1-5)
.venv/bin/python tests/test_runner.py --tier all
```

## Architecture

```
studio/
├── cli.py                    # Unified CLI entrypoint (GUI + Headless)
├── core/                     # Orchestrator, blackboard, event bus, credentials
├── engine/                   # Engine hub & adapters (Web, Godot4, Pygame, Unity)
├── gui/                      # FastAPI/Uvicorn server, routes, WebSocket, static IDE
├── packager/                 # Clean hierarchy packager & export manifests
├── providers/                # Client abstractions (Mock, OpenAI, Gemini, Claude)
└── verification/             # Multi-stage validator pipeline & diagnostic reports
```

## License

MIT License. See [LICENSE](LICENSE) for details.
