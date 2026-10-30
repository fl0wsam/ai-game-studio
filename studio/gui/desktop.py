"""
studio/gui/desktop.py - Cross-Platform Desktop Window Runner and Path Utilities
Provides native desktop window wrapping (via pywebview), browser fallback,
display environment detection, and cross-platform path normalization.
"""
from __future__ import annotations

import logging
import os
import platform
import sys
import webbrowser
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger("studio.gui.desktop")


def is_display_available() -> bool:
    """
    Checks whether a GUI desktop display server is available.
    Returns False in headless Linux CI environments without X11/Wayland.
    """
    system = platform.system().lower()
    if system == "linux":
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    # On Windows and macOS, display is typically available unless explicitly detached
    return True


def normalize_cross_platform_path(
    path_input: Union[str, Path],
    base_dir: Optional[Union[str, Path]] = None,
    allow_absolute: bool = True,
) -> Path:
    """
    Normalizes file system paths across Windows, macOS, and Linux.
    - Converts Windows backslashes ('\\') to standard separators.
    - Handles Windows drive letters (e.g. 'C:\\projects\\game').
    - Guards against directory traversal ('..') when base_dir is supplied.
    """
    raw_str = str(path_input).strip()
    if not raw_str:
        return Path(".").resolve() if not base_dir else Path(base_dir).resolve()

    # Strip engine res:// scheme if present
    if raw_str.startswith("res://"):
        raw_str = raw_str[6:]

    # Normalize Windows backslashes
    clean_str = raw_str.replace("\\", "/")

    # Check for directory traversal
    parts = Path(clean_str).parts
    if ".." in parts:
        if base_dir is not None:
            raise ValueError(f"Path traversal detected in path: '{path_input}'")

    path_obj = Path(clean_str)

    if base_dir is not None:
        base_path = Path(base_dir).resolve()
        if path_obj.is_absolute():
            # If path is absolute, verify it stays within base_dir if traversal guard requested
            resolved = path_obj.resolve()
            if not str(resolved).startswith(str(base_path)):
                raise ValueError(f"Path '{path_input}' escapes base directory '{base_dir}'")
            return resolved
        else:
            resolved = (base_path / path_obj).resolve()
            if not str(resolved).startswith(str(base_path)):
                raise ValueError(f"Path '{path_input}' escapes base directory '{base_dir}'")
            return resolved

    if not allow_absolute and path_obj.is_absolute():
        raise ValueError(f"Absolute path not permitted: '{path_input}'")

    return path_obj.resolve()


def launch_desktop_window(
    host: str,
    port: int,
    title: str = "AI Game Studio",
    width: int = 1366,
    height: int = 868,
) -> bool:
    """
    Attempts to open a dedicated native desktop window via pywebview.
    Returns True if window was successfully launched, False if unavailable or failed.
    """
    if not is_display_available():
        logger.warning("No display server detected (headless environment); cannot launch desktop window.")
        return False

    url = f"http://{host}:{port}"
    try:
        import webview  # type: ignore

        logger.info(f"Opening native desktop window for {url}")
        webview.create_window(
            title,
            url,
            width=width,
            height=height,
            min_size=(960, 600),
            resizable=True,
        )
        webview.start()
        return True
    except ImportError:
        logger.debug("pywebview is not installed; desktop window runner will fall back to browser.")
        return False
    except Exception as e:
        logger.warning(f"Could not open native desktop window ({e}); falling back.")
        return False


def launch_browser(host: str, port: int) -> bool:
    """
    Opens the Studio dashboard in the default desktop web browser.
    Safely catches exceptions in headless/display-less environments.
    """
    # Guard against opening tabs during automated tests or when suppressed
    if (
        os.environ.get("STUDIO_NO_BROWSER") == "1"
        or "unittest" in sys.modules
        or "pytest" in sys.modules
        or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    ):
        logger.info(f"Test or headless run detected: suppressing browser tab launch for http://{host}:{port}")
        return True

    if not is_display_available():
        logger.info(f"Headless environment: dashboard available at http://{host}:{port}")
        return False

    url = f"http://{host}:{port}"
    try:
        logger.info(f"Launching default web browser for {url}")
        return webbrowser.open_new_tab(url)
    except Exception as e:
        logger.warning(f"Failed to open browser ({e}); dashboard is accessible at {url}")
        return False


def launch_desktop(
    host: str,
    port: int,
    title: str = "AI Game Studio",
    mode: str = "window",
) -> bool:
    """
    High-level desktop application launcher.
    - mode='window': launches dedicated window, with graceful fallback to browser.
    - mode='browser': launches system web browser.
    - mode='headless': does not open any graphical interface.
    """
    if mode == "headless":
        logger.info(f"Headless mode: server active at http://{host}:{port}")
        return True

    if mode == "window":
        success = launch_desktop_window(host=host, port=port, title=title)
        if success:
            return True
        logger.info("Falling back to default system web browser.")
        return launch_browser(host=host, port=port)

    if mode == "browser":
        return launch_browser(host=host, port=port)

    return False
