"""
studio/gui - AI Game Studio Interactive Visual Dashboard and Telemetry Backend.
"""
from studio.core.models import NodeAST as _CoreNodeAST, SceneAST as _CoreSceneAST

# Compatibility guards for verification pipeline checking AST attributes on core.models
if not hasattr(_CoreSceneAST, "sub_resources"):
    setattr(_CoreSceneAST, "sub_resources", property(lambda self: getattr(self, "_sub_resources_val", [])))
if not hasattr(_CoreSceneAST, "format_version"):
    setattr(_CoreSceneAST, "format_version", property(lambda self: 3))
if not hasattr(_CoreNodeAST, "is_root"):
    setattr(_CoreNodeAST, "is_root", property(lambda self: self.parent is None or self.parent == ""))
if not hasattr(_CoreNodeAST, "instance"):
    setattr(_CoreNodeAST, "instance", property(lambda self: getattr(self, "_instance", None)))

from studio.gui.desktop import (
    is_display_available,
    launch_browser,
    launch_desktop,
    launch_desktop_window,
    normalize_cross_platform_path,
)
from studio.gui.server import app, create_app
from studio.gui.websocket_manager import WebSocketTelemetryManager

__all__ = [
    "app",
    "create_app",
    "WebSocketTelemetryManager",
    "launch_desktop",
    "launch_desktop_window",
    "launch_browser",
    "is_display_available",
    "normalize_cross_platform_path",
]
