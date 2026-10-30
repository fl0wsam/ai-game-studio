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

# Install dependencies
pip install -e .
```

### Running the IDE Dashboard

```bash
# Launch the web IDE on port 8080 (offline mock mode by default)
python3 -m studio.cli --gui --no-browser --port 8080
```

Open `http://localhost:8080` in your browser.

### Headless CLI Generation

```bash
# Generate a playable web game from a text prompt
python3 -m studio.cli --headless \
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
python3 tests/test_runner.py --tier all
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
