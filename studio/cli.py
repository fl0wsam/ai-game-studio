"""
studio/cli.py - Single-Command CLI & Desktop Launcher (F-GUI-01)
Provides command-line interface for running autonomous headless builds,
starting the local FastAPI GUI server, and opening native desktop application windows.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

import uvicorn

from studio.core.blackboard import Blackboard
from studio.core.event_bus import EventBus
from studio.core.models import EngineTarget, NodeAST, ProjectStatus, SceneAST

if not hasattr(SceneAST, "sub_resources"):
    setattr(SceneAST, "sub_resources", property(lambda self: getattr(self, "_sub_resources_val", [])))
if not hasattr(SceneAST, "format_version"):
    setattr(SceneAST, "format_version", property(lambda self: 3))
if not hasattr(NodeAST, "is_root"):
    setattr(NodeAST, "is_root", property(lambda self: self.parent is None or self.parent == ""))
if not hasattr(NodeAST, "instance"):
    setattr(NodeAST, "instance", property(lambda self: getattr(self, "_instance", None)))

from studio.core.orchestrator import StudioOrchestrator
from studio.core.security import CredentialManager
from studio.engine.hierarchy import CleanHierarchyPackager
from studio.engine.hub import get_engine_hub
from studio.gui.desktop import (
    is_display_available,
    launch_desktop,
    normalize_cross_platform_path,
)
from studio.gui.server import create_app
from studio.providers.claude_client import ClaudeClient
from studio.providers.gemini_client import GeminiClient
from studio.providers.mock_provider import MockProviderClient
from studio.providers.openai_client import OpenAIClient
from studio.verification.pipeline import VerificationPipeline

logger = logging.getLogger("studio.cli")


def build_parser() -> argparse.ArgumentParser:
    """Builds the comprehensive CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="ai-game-studio",
        description="Autonomous Multi-Agent AI Game Studio (Claude + Gemini + OpenAI)",
    )

    # Operational Modes
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--gui",
        action="store_true",
        default=True,
        help="Launch the interactive Studio GUI web dashboard / desktop app (default: True)",
    )
    mode_group.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run autonomous generation pipeline directly in terminal without GUI",
    )

    # Desktop Window and Browser Flags
    parser.add_argument(
        "--window",
        action="store_true",
        default=False,
        help="Open native desktop application window (falls back to browser if unavailable)",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        default=False,
        help="Explicitly launch default system web browser upon server startup",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        default=False,
        help="Do not open browser or desktop window automatically",
    )

    # Server Networking
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host interface to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to listen on (default: 8080)",
    )

    # Autonomous Pipeline Options
    parser.add_argument(
        "--prompt",
        type=str,
        default=None,
        help="Game idea prompt (required in headless mode)",
    )
    parser.add_argument(
        "--engine",
        type=str,
        default="godot4",
        choices=["godot4", "pygame", "web_html5", "unity"],
        help="Target game engine (default: godot4)",
    )
    parser.add_argument(
        "--genre",
        type=str,
        default="platformer_2d",
        help="Game archetype/genre hint (default: platformer_2d)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./output",
        help="Directory where game project is exported (default: ./output)",
    )
    parser.add_argument(
        "--project-name",
        type=str,
        default=None,
        help="Custom project title (auto-generated if omitted)",
    )
    parser.add_argument(
        "--mock",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Force deterministic 100%% offline mock AI provider (default: True)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="Maximum review iterations (default: 3, range 1..10)",
    )
    parser.add_argument(
        "--verify",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Execute automated verification pipeline after generation (default: True)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )

    return parser


def validate_arguments(args: argparse.Namespace) -> Optional[int]:
    """
    Validates parsed CLI arguments for boundary violations.
    Returns None if valid, or non-zero exit code if validation fails.
    """
    if not (1 <= args.port <= 65535):
        sys.stderr.write(f"Error: Invalid port '{args.port}'. Port must be between 1 and 65535.\n")
        return 1

    if args.headless:
        if not args.prompt or not args.prompt.strip():
            sys.stderr.write("Error: --prompt is required when running in --headless mode.\n")
            return 1

    if not (1 <= args.iterations <= 10):
        sys.stderr.write(f"Error: Invalid iterations '{args.iterations}'. Must be between 1 and 10.\n")
        return 1

    return None


async def execute_headless_pipeline(args: argparse.Namespace) -> int:
    """
    Executes autonomous multi-agent game generation directly in the terminal without GUI.
    Returns 0 on success, 1 on failure.
    """
    prompt = (args.prompt or "").strip()
    project_name = args.project_name or f"Game_{int(time.time())}"
    target_engine = args.engine

    try:
        output_dir = normalize_cross_platform_path(args.output_dir)
    except Exception as e:
        sys.stderr.write(f"Error resolving output-dir: {e}\n")
        return 1

    print(f"\n[AI Game Studio] Launching Headless Build: '{project_name}'")
    print(f"   Target Engine: {target_engine}")
    print(f"   Prompt: {prompt}")
    print(f"   Output Directory: {output_dir}\n")

    blackboard = Blackboard(
        project_name=project_name,
        target_engine=target_engine,
        user_prompt=prompt,
    )
    event_bus = EventBus()
    verification_pipeline = VerificationPipeline(event_bus=event_bus, blackboard=blackboard)

    # Initialize Provider Clients (deterministic Mock or Live API)
    if args.mock:
        mock_c = MockProviderClient()
        openai_c, gemini_c, claude_c = mock_c, mock_c, mock_c
    else:
        cred_mgr = CredentialManager()
        openai_c = OpenAIClient(api_key=cred_mgr.get_key("openai"))
        gemini_c = GeminiClient(api_key=cred_mgr.get_key("gemini"))
        claude_c = ClaudeClient(api_key=cred_mgr.get_key("anthropic"))

    orchestrator = StudioOrchestrator(
        blackboard=blackboard,
        event_bus=event_bus,
        openai_client=openai_c,
        gemini_client=gemini_c,
        claude_client=claude_c,
        verification_pipeline=verification_pipeline,
    )

    try:
        state = await orchestrator.run_pipeline(
            prompt=prompt,
            target_engine=target_engine,
            project_name=project_name,
        )

        if state.status != ProjectStatus.COMPLETED or not state.gdd or not state.asset_manifest:
            sys.stderr.write("\n[FAIL] Pipeline failed to complete successfully.\n")
            return 1

        print(f"\n[PACKAGE] Packaging & Exporting project to {output_dir}...")
        output_dir.mkdir(parents=True, exist_ok=True)

        hub = get_engine_hub()
        hub_manifest = hub.export(
            target=target_engine,
            gdd=state.gdd,
            manifest=state.asset_manifest,
            scenes=state.scenes,
            scripts=state.scripts,
            output_dir=output_dir,
        )

        packager = CleanHierarchyPackager(target_engine=target_engine)
        packager.create_hierarchy(output_dir)
        packager.package_docs(output_dir, state.gdd)
        packager.package_assets(output_dir, state.asset_manifest)
        packager.package_scripts(output_dir, state.scripts)
        packager.package_scenes(output_dir, state.scenes)
        hierarchy_manifest = packager.generate_manifest(output_dir, project_name=project_name)

        print(f"[SUCCESS] Export completed: {hierarchy_manifest.total_files} files packaged.")

        # Verification step
        if args.verify:
            print("[VERIFY] Running Final Verification Pipeline...")
            report = verification_pipeline.verify_project(state)
            if report.passed:
                print(f"[SUCCESS] Game generated and verified with 0 errors in {report.execution_time_ms:.1f}ms.")
                return 0
            else:
                sys.stderr.write(f"[WARN] Verification reported {len(report.errors)} errors:\n")
                for err in report.errors:
                    sys.stderr.write(f"   - [{err.stage}] {err.file_path}: {err.message}\n")
                return 1

        return 0

    except Exception as e:
        sys.stderr.write(f"\n[FATAL] Fatal error during headless build: {e}\n")
        logger.exception("Headless build exception")
        return 1
    finally:
        await asyncio.sleep(0.05)
        await event_bus.close()


def find_available_port(host: str, start_port: int, max_attempts: int = 20) -> int:
    """Finds an open port starting from start_port up to start_port + max_attempts."""
    import socket
    for p in range(start_port, min(65536, start_port + max_attempts)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    return start_port


def run_gui_server(args: argparse.Namespace) -> int:
    """Starts the Uvicorn ASGI server hosting the GUI dashboard and REST/WebSocket API."""
    target_port = args.port
    actual_port = find_available_port(args.host, target_port)
    if actual_port != target_port:
        print(f"[WARN] Port {target_port} is already in use. Automatically falling back to open port {actual_port}.")
        args.port = actual_port

    app = create_app()

    def _open_desktop() -> None:
        time.sleep(1.2)  # Give server brief time to bind port
        if not args.no_browser:
            mode = "window" if args.window else "browser"
            launch_desktop(host=args.host, port=args.port, mode=mode)

    # Launch desktop window or browser thread if not suppressed
    if not args.no_browser and (args.window or args.browser or is_display_available()):
        t = threading.Thread(target=_open_desktop, daemon=True)
        t.start()

    print(f"\n[STUDIO] AI Game Studio Visual Dashboard running at: http://{args.host}:{args.port}")
    print("   Press Ctrl+C to terminate cleanly.\n")

    config = uvicorn.Config(
        app=app,
        host=args.host,
        port=args.port,
        log_level=args.log_level.lower(),
        access_log=False,
    )
    server = uvicorn.Server(config)

    def _sig_handler(sig, frame):
        print("\nShutting down AI Game Studio...")
        server.should_exit = True

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    try:
        server.run()
        return 0
    except (KeyboardInterrupt, SystemExit):
        return 0
    except Exception as e:
        sys.stderr.write(f"Server error: {e}\n")
        return 1


def main(args: Optional[List[str]] = None) -> int:
    """Main CLI entrypoint."""
    parser = build_parser()
    try:
        parsed_args = parser.parse_args(args=args if args is not None else sys.argv[1:])
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2

    # Validate arguments
    val_err = validate_arguments(parsed_args)
    if val_err is not None:
        return val_err

    logging.basicConfig(
        level=getattr(logging, parsed_args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if parsed_args.headless:
        return asyncio.run(execute_headless_pipeline(parsed_args))
    else:
        return run_gui_server(parsed_args)


if __name__ == "__main__":
    sys.exit(main())
