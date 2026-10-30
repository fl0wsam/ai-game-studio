# E2E Test Infra: Autonomous Multi-Agent AI Game Studio

## Test Philosophy
- **Opaque-box, requirement-driven**: Test suite is derived strictly from `ORIGINAL_REQUEST.md` and user-facing specifications, completely independent of internal implementation details.
- **Methodology**: Systematic 4-Tier Strategy:
  - **Tier 1 (Feature Coverage, ≥5 per feature)**: Equivalence class partitioning covering every feature in isolation.
  - **Tier 2 (Boundary & Corner Cases, ≥5 per feature)**: Extreme values, empty inputs, malformed configurations, rate limit boundaries, offline fallbacks.
  - **Tier 3 (Cross-Feature Combinations, Pairwise)**: Interaction between providers, state bus, engine generation, asset linking, and verification pipeline.
  - **Tier 4 (Real-World Application Scenarios, ≥11 scenarios)**: Full end-to-end game production runs across multiple genres and constraints.

---

## Feature Inventory & Test Distribution

| # | Feature ID | Feature Name | Source | Tier 1 (Req ≥5) | Tier 2 (Req ≥5) | Tier 3 (Pairwise) | Tier 4 (E2E) |
|---|------------|--------------|--------|:---------------:|:---------------:|:-----------------:|:------------:|
| 1 | F-ORCH-01 | Blackboard State Store | Survey R1 | 5 | 5 | ✓ | ✓ |
| 2 | F-ORCH-02 | Async In-Process Event Bus | Survey R1 | 5 | 5 | ✓ | ✓ |
| 3 | F-ORCH-03 | OpenAI Narrative Agent | Req R1 | 5 | 5 | ✓ | ✓ |
| 4 | F-ORCH-04 | Google Gemini Multimodal Agent | Req R1 | 5 | 5 | ✓ | ✓ |
| 5 | F-ORCH-05 | Anthropic Claude Code Agent | Req R1 | 5 | 5 | ✓ | ✓ |
| 6 | F-ORCH-06 | Provider Resilience & Security | Req R1 | 5 | 5 | ✓ | ✓ |
| 7 | F-ORCH-07 | Deterministic Mock Provider | Survey R1 | 5 | 5 | ✓ | ✓ |
| 8 | F-ENG-01 | Godot Project Configurator | Req R3 | 5 | 5 | ✓ | ✓ |
| 9 | F-ENG-02 | TSCN AST & Serializer | Req R3 | 5 | 5 | ✓ | ✓ |
| 10 | F-ENG-03 | Clean Hierarchy Packager | Req R3 | 5 | 5 | ✓ | ✓ |
| 11 | F-ENG-04 | Dynamic Asset Linker | Req R3 | 5 | 5 | ✓ | ✓ |
| 12 | F-ENG-05 | Game Archetype Templates | Survey R3 | 5 | 5 | ✓ | ✓ |
| 13 | F-ENG-06 | Unity Pipeline Bridge | Req R3 | 5 | 5 | ✓ | ✓ |
| 14 | F-VERIF-01 | GDScript Syntax Validator | Req R4 | 5 | 5 | ✓ | ✓ |
| 15 | F-VERIF-02 | TSCN Schema & Reference Checker | Req R4 | 5 | 5 | ✓ | ✓ |
| 16 | F-VERIF-03 | Asset Integrity Verifier | Req R4 | 5 | 5 | ✓ | ✓ |
| 17 | F-VERIF-04 | Dual-Mode Engine Simulator | Req R4 | 5 | 5 | ✓ | ✓ |
| 18 | F-GUI-01 | Single-Command CLI Launcher | Req R2 | 5 | 5 | ✓ | ✓ |
| 19 | F-GUI-02 | Interactive Agent Graph | Req R2 | 5 | 5 | ✓ | ✓ |
| 20 | F-GUI-03 | Media & Asset Preview Gallery | Req R2 | 5 | 5 | ✓ | ✓ |
| 21 | F-GUI-04 | Workspace & Log Viewer | Req R2 | 5 | 5 | ✓ | ✓ |
| 22 | F-GUI-05 | Prompt & Directing Panel | Req R2 | 5 | 5 | ✓ | ✓ |
| **Totals** | | **22 Features** | | **110** | **110** | **22** | **11** |

**Total Target Minimum**: 253 Test Cases

---

## Test Architecture

- **Test Runner Location**: `tests/test_runner.py`
- **Invocation**: `python3 tests/test_runner.py [--tier 1|2|3|4|all]`
- **Pass/Fail Semantics**: Exit code 0 if all executed tests pass; non-zero on assertion failure or unhandled exception.
- **Directory Layout**:
  - `tests/tier1_features/`: Unit and feature-level tests validating individual components in isolation.
  - `tests/tier2_boundaries/`: Edge cases, invalid inputs, boundary conditions, zero-byte assets, malformed scenes.
  - `tests/tier3_combinations/`: Cross-module interaction tests (Orchestrator + Event Bus + Provider + Generator + Verifier).
  - `tests/tier4_applications/`: Full end-to-end studio workflow scenarios generating complete Godot projects and validating them through the verification pipeline.

---

## Real-World Application Scenarios (Tier 4)

| # | Scenario Name | Features Exercised | Archetype / Genre | Expected Outcome |
|---|---------------|--------------------|-------------------|------------------|
| S1 | Neon Cyber Runner | F-ORCH-01..07, F-ENG-01..05, F-VERIF-01..04 | 2D Endless Runner | Valid Godot project with player, parallax background, coin pickup, jump SFX, 100% verifier pass |
| S2 | Dungeon Crawler RPG | F-ORCH-01..07, F-ENG-01..05, F-VERIF-01..04 | Top-Down RPG | Valid Godot project with tilemap, player character, enemy actor, sword swing SFX, GDD lore |
| S3 | Retro Cosmic Defender | F-ORCH-01..07, F-ENG-01..05, F-VERIF-01..04 | Space Shooter | Valid Godot project with laser beams, asteroid hazards, score HUD, explosion audio, 60-frame sim pass |
| S4 | Ancient Glyph Puzzle | F-ORCH-01..07, F-ENG-01..05, F-VERIF-01..04 | Grid Match Puzzle | Valid Godot project with grid state controller, jewel sprites, match sound effects, zero errors |
| S5 | Unity Project Export | F-ORCH-01..07, F-ENG-06, F-VERIF-03 | Unity 2D Arcade | Valid C# scripts, `EditorSceneBuilder.cs`, asset manifest, zero missing textures |
| S6 | Mock Offline Studio Generation | F-ORCH-01..07, F-ENG-01..05, F-VERIF-01..04 | Platformer (Offline) | Completely offline synthesis using Pillow & Wave, zero API tokens, 100% reproducible hash |
| S7 | Live GUI Directed Workflow | F-ORCH-01..07, F-GUI-01..05, F-VERIF-01..04 | Directing Panel Prompt | REST direct trigger generates project, broadcasts live WebSocket telemetry, updates media gallery |
| S8 | Multi-Room Metroidvania | F-ORCH-01..07, F-ENG-01..05, F-VERIF-01..04 | Multi-Scene Platformer | Multiple interconnected `.tscn` scenes, room transition scripts, verifier validates scene tree cross-references |
| S9 | Branching Dialogue Quest | F-ORCH-01..03, F-ENG-01..03, F-GUI-04 | Narrative RPG | JSON dialogue tree, NPC interaction script, quest state autoload, GDD inspector verification |
| S10 | Asset-Heavy Dynamic Loader | F-ORCH-04, F-ENG-04, F-VERIF-02..03 | High-Asset Platformer | Over 15 distinct sprites/audio streams dynamically linked and validated by Asset Integrity Verifier |
| S11 | Resilient Rate-Limit Recovery | F-ORCH-06, F-ORCH-07, F-VERIF-01..04 | High-Concurrency Stress | Simulates provider 429 rate limit backoff and verifies graceful retry and fallback to mock |

---

## Coverage Thresholds
- Tier 1: ≥5 per feature (Total: 110)
- Tier 2: ≥5 per feature (Total: 110)
- Tier 3: 22 pairwise interaction combinations (Total: 22)
- Tier 4: ≥11 comprehensive end-to-end scenarios (Total: 11)
- **Total Minimum Test Count: 253**
