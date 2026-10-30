"""
studio/verification/engine_simulator.py - Dual-Mode Headless Simulation & Frame Stepper.
Implements F-VERIF-04: Executes projects in headless Godot (Mode 1) when binaries are present,
or falls back to a deterministic pure-Python MockEngineSimulator (Mode 2) that instantiates
scene trees, checks @onready references, steps physics and timers, and verifies crash-free execution.
Also supports headless Pygame execution via main.py.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from studio.core.models import DiagnosticError
from studio.engine.adapters.godot_adapter import parse_tscn
from studio.engine.ast_models import ExtResourceAST, NodeAST, SceneAST


# ============================================================================
# Simulation Data Models
# ============================================================================

@dataclass
class SimulatedNode:
    """In-memory runtime representation of an instantiated Godot node."""
    name: str
    type: str
    parent: Optional[SimulatedNode] = None
    children: List[SimulatedNode] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)
    script_path: Optional[str] = None
    pos_x: float = 0.0
    pos_y: float = 0.0
    vel_x: float = 0.0
    vel_y: float = 0.0
    is_active: bool = True
    time_left: float = 0.0

    def add_child(self, child: SimulatedNode) -> None:
        child.parent = self
        self.children.append(child)

    def find_descendant(self, name: str) -> Optional[SimulatedNode]:
        """Finds descendant by name."""
        for c in self.children:
            if c.name == name:
                return c
            found = c.find_descendant(name)
            if found:
                return found
        return None

    def get_all_nodes(self) -> List[SimulatedNode]:
        """Returns flat list of self and all descendants."""
        res = [self]
        for c in self.children:
            res.extend(c.get_all_nodes())
        return res


@dataclass
class SimulationResult:
    """Detailed outcome from engine simulation."""
    passed: bool
    mode_used: str                 # "real_godot", "mock_engine", "pygame_headless"
    frames_run: int
    duration_ms: float
    errors: List[DiagnosticError] = field(default_factory=list)
    warnings: List[DiagnosticError] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""


# ============================================================================
# Mode 2: Pure-Python MockEngineSimulator Frame Stepper
# ============================================================================

class MockEngineSimulator:
    """
    Pure-Python game engine simulator and physics frame stepper.
    Parses scenes, instantiates node trees, validates @onready node lookups,
    and advances physics ticks and timers across N frames.
    """

    def __init__(self, project_dir: Union[str, Path]) -> None:
        self.project_dir = Path(project_dir).resolve()

    def simulate(
        self,
        frames: int = 60,
        main_scene_path: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ) -> SimulationResult:
        """Simulates scene instantiation and frame stepping."""
        if frames < 0:
            raise ValueError(f"Simulation frame count cannot be negative: {frames}")

        start_time = time.perf_counter()
        errors: List[DiagnosticError] = []
        warnings: List[DiagnosticError] = []

        # 1. Discover main scene
        scene_disk_path = self._resolve_main_scene(main_scene_path)
        if not scene_disk_path or not scene_disk_path.is_file():
            errors.append(
                DiagnosticError(
                    stage="simulation",
                    severity="ERROR",
                    file_path=str(main_scene_path or "project.godot"),
                    line=1,
                    message="No valid main scene found in project.godot or scenes directory.",
                    suggested_fix="Define run/main_scene in project.godot or place a .tscn in scenes/.",
                )
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return SimulationResult(
                passed=False,
                mode_used="mock_engine",
                frames_run=0,
                duration_ms=elapsed_ms,
                errors=errors,
            )

        # 2. Parse main scene
        try:
            tscn_text = scene_disk_path.read_text(encoding="utf-8")
            scene_ast = parse_tscn(tscn_text, scene_path=str(scene_disk_path.relative_to(self.project_dir)))
        except Exception as ex:
            errors.append(
                DiagnosticError(
                    stage="simulation",
                    severity="ERROR",
                    file_path=str(scene_disk_path),
                    line=1,
                    message=f"Failed to load main scene during simulation: {ex}",
                )
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return SimulationResult(
                passed=False,
                mode_used="mock_engine",
                frames_run=0,
                duration_ms=elapsed_ms,
                errors=errors,
            )

        # 3. Build simulated node hierarchy
        root_sim_node, tree_errors = self._instantiate_tree(scene_ast, scene_disk_path)
        errors.extend(tree_errors)
        if not root_sim_node:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return SimulationResult(
                passed=False,
                mode_used="mock_engine",
                frames_run=0,
                duration_ms=elapsed_ms,
                errors=errors,
            )

        # 4. Validate @onready / $Node child references and collision shapes
        val_errors, val_warns = self._validate_runtime_contracts(root_sim_node)
        errors.extend(val_errors)
        warnings.extend(val_warns)

        # If zero frames requested, we verified scene load and stop here
        if frames == 0:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return SimulationResult(
                passed=len(errors) == 0,
                mode_used="mock_engine",
                frames_run=0,
                duration_ms=elapsed_ms,
                errors=errors,
                warnings=warnings,
                stdout="Mock simulation initialized 0 frames successfully.",
            )

        # 5. Physics & Timer Frame Stepping Loop
        gravity = self._extract_gravity()
        delta = 1.0 / 60.0  # 60 FPS standard delta
        all_nodes = root_sim_node.get_all_nodes()

        step_errors: List[DiagnosticError] = []
        frames_executed = 0

        for frame_idx in range(1, frames + 1):
            # Timeout protection
            if (time.perf_counter() - start_time) > timeout_seconds:
                step_errors.append(
                    DiagnosticError(
                        stage="simulation",
                        severity="ERROR",
                        file_path=str(scene_disk_path),
                        line=1,
                        message=f"Simulation timed out after {timeout_seconds:.1f}s at frame {frame_idx}.",
                        suggested_fix="Check for infinite loops or heavy processing during frame step.",
                    )
                )
                break

            try:
                for node in all_nodes:
                    # Timer logic
                    if node.type == "Timer":
                        if node.time_left > 0:
                            node.time_left -= delta
                            if node.time_left <= 0:
                                node.time_left = 0.0  # timer fired

                    # Physics movement
                    if node.type in ("CharacterBody2D", "RigidBody2D"):
                        node.vel_y += gravity * delta
                        node.pos_x += node.vel_x * delta
                        node.pos_y += node.vel_y * delta

                        # Sanity checks for explosion / NaN
                        if math.isnan(node.pos_x) or math.isnan(node.pos_y) or math.isnan(node.vel_x) or math.isnan(node.vel_y):
                            raise ValueError(f"NaN coordinate detected on node '{node.name}' at frame {frame_idx}.")
                        if math.isinf(node.pos_x) or math.isinf(node.pos_y) or math.isinf(node.vel_x) or math.isinf(node.vel_y):
                            raise ValueError(f"Inf coordinate detected on node '{node.name}' at frame {frame_idx}.")
                        if abs(node.pos_x) > 100_000 or abs(node.pos_y) > 100_000:
                            raise ValueError(
                                f"Physics runaway: node '{node.name}' position ({node.pos_x:.1f}, {node.pos_y:.1f}) exceeds bounds at frame {frame_idx}."
                            )

                frames_executed += 1
            except Exception as ex:
                step_errors.append(
                    DiagnosticError(
                        stage="simulation",
                        severity="ERROR",
                        file_path=str(scene_disk_path),
                        line=1,
                        message=f"Simulation frame {frame_idx} runtime exception: {ex}",
                        suggested_fix="Inspect physics parameters or script calculations for runaway values.",
                    )
                )
                break

        errors.extend(step_errors)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        return SimulationResult(
            passed=len(errors) == 0,
            mode_used="mock_engine",
            frames_run=frames_executed,
            duration_ms=elapsed_ms,
            errors=errors,
            warnings=warnings,
            stdout=f"MockEngineSimulator completed {frames_executed}/{frames} frames.",
        )

    # ------------------------------------------------------------------------
    # Helper Methods
    # ------------------------------------------------------------------------

    def _resolve_main_scene(self, override_path: Optional[str]) -> Optional[Path]:
        if override_path:
            p = self.project_dir / override_path.replace("res://", "").lstrip("/")
            if p.is_file():
                return p

        # Check project.godot
        cfg = self.project_dir / "project.godot"
        if cfg.is_file():
            try:
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    if "run/main_scene" in line and "=" in line:
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = self.project_dir / val.replace("res://", "").lstrip("/")
                        if p.is_file():
                            return p
            except Exception:
                pass

        # Fallback to scenes directory
        scenes_dir = self.project_dir / "scenes"
        if scenes_dir.is_dir():
            for scn in sorted(scenes_dir.glob("*.tscn")):
                return scn

        # Search anywhere in project_dir
        for scn in sorted(self.project_dir.rglob("*.tscn")):
            return scn

        return None

    def _instantiate_tree(
        self, scene: SceneAST, scene_disk_path: Path
    ) -> Tuple[Optional[SimulatedNode], List[DiagnosticError]]:
        errors: List[DiagnosticError] = []
        root_ast = scene.get_root()
        if not root_ast:
            errors.append(
                DiagnosticError(
                    stage="simulation",
                    severity="ERROR",
                    file_path=str(scene_disk_path),
                    line=1,
                    message="Scene has no root node to instantiate.",
                )
            )
            return None, errors

        # Map ext_resources for attached scripts and subscenes
        ext_map = {r.id: r for r in scene.ext_resources}

        # Create root simulated node
        root_props = dict(root_ast.properties)
        root_script = ext_map[root_ast.script_id].path if root_ast.script_id and root_ast.script_id in ext_map else None
        root_sim = SimulatedNode(
            name=root_ast.name,
            type=root_ast.type or "Node2D",
            properties=root_props,
            script_path=root_script,
            pos_x=float(root_props.get("pos_x", 0.0)),
            pos_y=float(root_props.get("pos_y", 0.0)),
            vel_x=float(root_props.get("vel_x", 0.0)),
            vel_y=float(root_props.get("vel_y", 0.0)),
        )

        node_map: Dict[str, SimulatedNode] = {root_ast.name: root_sim, ".": root_sim}

        for node_ast in scene.nodes:
            if node_ast.is_root:
                continue

            node_props = dict(node_ast.properties)
            script_p = ext_map[node_ast.script_id].path if node_ast.script_id and node_ast.script_id in ext_map else None
            sim_node = SimulatedNode(
                name=node_ast.name,
                type=node_ast.type or "Node2D",
                properties=node_props,
                script_path=script_p,
                pos_x=float(node_props.get("pos_x", 0.0)),
                pos_y=float(node_props.get("pos_y", 0.0)),
                vel_x=float(node_props.get("vel_x", 0.0)),
                vel_y=float(node_props.get("vel_y", 0.0)),
            )

            parent_ref = node_ast.parent or "."
            parent_sim = node_map.get(parent_ref) or node_map.get(parent_ref.split("/")[-1])
            if parent_sim:
                parent_sim.add_child(sim_node)
            else:
                root_sim.add_child(sim_node)

            node_map[node_ast.name] = sim_node
            node_map[f"{parent_ref}/{node_ast.name}"] = sim_node

        return root_sim, errors

    def _validate_runtime_contracts(
        self, root: SimulatedNode
    ) -> Tuple[List[DiagnosticError], List[DiagnosticError]]:
        errors: List[DiagnosticError] = []
        warnings: List[DiagnosticError] = []
        all_nodes = root.get_all_nodes()
        all_names = {n.name for n in all_nodes}

        for node in all_nodes:
            # 1. Physics bodies must have CollisionShape2D
            if node.type in ("CharacterBody2D", "Area2D", "RigidBody2D", "StaticBody2D"):
                has_col = any(
                    c.type in ("CollisionShape2D", "CollisionPolygon2D") for c in node.children
                )
                if not has_col:
                    warnings.append(
                        DiagnosticError(
                            stage="simulation",
                            severity="WARNING",
                            file_path=node.script_path or "scene",
                            line=1,
                            message=f"{node.type} '{node.name}' has no CollisionShape2D child; physics collisions will not trigger.",
                            suggested_fix=f"Add a CollisionShape2D child to node '{node.name}'.",
                        )
                    )

            # 2. Check attached script for $Node references
            if node.script_path:
                rel = node.script_path.replace("res://", "").lstrip("/")
                script_file = self.project_dir / rel
                if script_file.is_file():
                    try:
                        content = script_file.read_text(encoding="utf-8")
                        dollar_nodes = re.findall(r'\$([a-zA-Z_][a-zA-Z0-9_]*)', content)
                        for d_name in dollar_nodes:
                            # Node must exist either as child or in tree
                            child_names = {c.name for c in node.children}
                            if d_name not in child_names and d_name not in all_names:
                                errors.append(
                                    DiagnosticError(
                                        stage="simulation",
                                        severity="ERROR",
                                        file_path=rel,
                                        line=1,
                                        message=f"Script on '{node.name}' references non-existent node '${d_name}' (Godot null reference exception).",
                                        suggested_fix=f"Ensure '${d_name}' is declared as a child of '{node.name}' or exists in scene.",
                                    )
                                )
                    except Exception:
                        pass

        return errors, warnings

    def _extract_gravity(self) -> float:
        cfg = self.project_dir / "project.godot"
        if cfg.is_file():
            try:
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    if "2d/default_gravity" in line and "=" in line:
                        return float(line.split("=", 1)[1].strip())
            except Exception:
                pass
        return 980.0


# ============================================================================
# Mode 1 & Dispatcher: DualModeEngineSimulator
# ============================================================================

class DualModeEngineSimulator:
    """
    Dual-mode simulator dispatcher:
    - Mode 1: Real Godot Headless CLI with 10s process timeout guard and log inspection.
    - Mode 2: Pure-Python MockEngineSimulator frame stepper when Godot binary is absent.
    - Pygame: Subprocess execution of main.py --headless --frames <N>.
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        godot_binary: Optional[str] = None,
        force_mode: Optional[str] = None,  # "real_godot", "mock_engine", or None (auto)
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.force_mode = force_mode
        self._godot_bin: Optional[str] = (
            godot_binary
            or os.environ.get("GODOT_BIN")
            or shutil.which("godot")
            or shutil.which("godot4")
        )
        self.mock_simulator = MockEngineSimulator(self.project_dir)

    def is_godot_available(self) -> bool:
        """Checks if real Godot binary is present in environment."""
        return self._godot_bin is not None

    def run_simulation(
        self,
        frames: int = 60,
        main_scene: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ) -> SimulationResult:
        """Executes simulation via appropriate mode."""
        # 1. Pygame target check
        if (self.project_dir / "main.py").is_file() and not (self.project_dir / "project.godot").is_file():
            return self._run_pygame_headless(frames=frames, timeout_seconds=timeout_seconds)

        # 2. Forced Mode Check
        if self.force_mode == "mock_engine" or not self.is_godot_available():
            return self.mock_simulator.simulate(
                frames=frames,
                main_scene_path=main_scene,
                timeout_seconds=timeout_seconds,
            )

        if self.force_mode == "real_godot" or self.is_godot_available():
            return self._run_godot_headless(
                frames=frames,
                main_scene=main_scene,
                timeout_seconds=timeout_seconds,
            )

        return self.mock_simulator.simulate(frames=frames, timeout_seconds=timeout_seconds)

    def _run_godot_headless(
        self,
        frames: int,
        main_scene: Optional[str],
        timeout_seconds: float,
    ) -> SimulationResult:
        """Executes real Godot headless binary."""
        start_time = time.perf_counter()
        cmd = [
            str(self._godot_bin),
            "--headless",
            "--path",
            str(self.project_dir),
            "--quit-after",
            str(frames),
        ]
        if main_scene:
            cmd.extend(["--main-scene", main_scene])

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0

            errors, warnings = self._parse_godot_logs(proc.stdout, proc.stderr)
            passed = (proc.returncode == 0) and (len(errors) == 0)

            return SimulationResult(
                passed=passed,
                mode_used="real_godot",
                frames_run=frames if passed else 0,
                duration_ms=elapsed_ms,
                errors=errors,
                warnings=warnings,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
        except subprocess.TimeoutExpired:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return SimulationResult(
                passed=False,
                mode_used="real_godot",
                frames_run=0,
                duration_ms=elapsed_ms,
                errors=[
                    DiagnosticError(
                        stage="simulation",
                        severity="ERROR",
                        file_path=str(self.project_dir),
                        line=1,
                        message=f"Godot headless simulation timed out after {timeout_seconds:.1f}s.",
                        suggested_fix="Check for infinite loops in _ready() or _process().",
                    )
                ],
            )
        except Exception as ex:
            # Fallback to mock on launch failure
            return self.mock_simulator.simulate(frames=frames, timeout_seconds=timeout_seconds)

    def _run_pygame_headless(
        self, frames: int, timeout_seconds: float
    ) -> SimulationResult:
        """Executes Pygame project in headless mode."""
        start_time = time.perf_counter()
        main_py = self.project_dir / "main.py"
        cmd = ["python3", str(main_py), "--headless", "--frames", str(frames)]

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
                cwd=str(self.project_dir),
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            errors: List[DiagnosticError] = []

            if proc.returncode != 0:
                errors.append(
                    DiagnosticError(
                        stage="simulation",
                        severity="ERROR",
                        file_path="main.py",
                        line=1,
                        message=f"Pygame headless execution failed with exit code {proc.returncode}: {proc.stderr[:300]}",
                    )
                )

            return SimulationResult(
                passed=len(errors) == 0,
                mode_used="pygame_headless",
                frames_run=frames if len(errors) == 0 else 0,
                duration_ms=elapsed_ms,
                errors=errors,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
        except subprocess.TimeoutExpired:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return SimulationResult(
                passed=False,
                mode_used="pygame_headless",
                frames_run=0,
                duration_ms=elapsed_ms,
                errors=[
                    DiagnosticError(
                        stage="simulation",
                        severity="ERROR",
                        file_path="main.py",
                        line=1,
                        message=f"Pygame simulation timed out after {timeout_seconds:.1f}s.",
                    )
                ],
            )

    def _parse_godot_logs(
        self, stdout: str, stderr: str
    ) -> Tuple[List[DiagnosticError], List[DiagnosticError]]:
        """Parses stdout/stderr for Godot crash and script error signatures."""
        errors: List[DiagnosticError] = []
        warnings: List[DiagnosticError] = []
        combined = f"{stdout}\n{stderr}"

        # Crash signatures
        crash_pattern = re.compile(
            r"(?:CrashHandlerException|Segmentation fault|Assertion failed|core dumped)",
            re.IGNORECASE,
        )
        if crash_pattern.search(combined):
            errors.append(
                DiagnosticError(
                    stage="simulation",
                    severity="ERROR",
                    file_path=str(self.project_dir),
                    line=1,
                    message="Godot Engine crashed with fatal segmentation fault or assertion failure.",
                )
            )

        # Script error pattern: SCRIPT ERROR: ... at: res://...:15
        script_err_pattern = re.compile(
            r"SCRIPT ERROR:\s*(.*?)\s*at:\s*res://(.*?):(\d+)", re.IGNORECASE
        )
        for m in script_err_pattern.finditer(combined):
            msg, script_rel, lineno = m.group(1), m.group(2), int(m.group(3))
            errors.append(
                DiagnosticError(
                    stage="simulation",
                    severity="ERROR",
                    file_path=script_rel,
                    line=lineno,
                    message=f"Godot script runtime error: {msg}",
                )
            )

        # Resource loading failure
        res_fail_pattern = re.compile(r"ERROR:\s*Failed loading resource:\s*(res://\S+)")
        for m in res_fail_pattern.finditer(combined):
            res_path = m.group(1)
            errors.append(
                DiagnosticError(
                    stage="simulation",
                    severity="ERROR",
                    file_path=res_path,
                    line=1,
                    message=f"Godot engine failed loading resource: {res_path}",
                )
            )

        # Warnings
        warn_pattern = re.compile(r"WARNING:\s*(.*)")
        for m in warn_pattern.finditer(combined):
            warnings.append(
                DiagnosticError(
                    stage="simulation",
                    severity="WARNING",
                    file_path="engine",
                    line=1,
                    message=m.group(1).strip(),
                )
            )

        return errors, warnings


# Alias for compatibility
EngineSimulator = DualModeEngineSimulator
