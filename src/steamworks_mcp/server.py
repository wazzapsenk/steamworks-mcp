"""The MCP server: a thin layer that registers tools; the work happens in the other modules."""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from steamworks_mcp import __version__
from steamworks_mcp import project as proj
from steamworks_mcp.config import Config, WorkspaceError, resolve_in_workspace
from steamworks_mcp.manifest.io import ManifestError
from steamworks_mcp.manifest.paths import FieldPathError, iter_fields
from steamworks_mcp.manifest.state import TransitionError, is_empty
from steamworks_mcp.scanners import run_scanners

F = TypeVar("F", bound=Callable[..., Any])
USER_ERRORS = (WorkspaceError, ManifestError, FieldPathError, TransitionError, ValueError, FileNotFoundError)


def user_errors(fn: F) -> F:
    """Show the message of expected errors to the model (the SDK hides unexpected exceptions' details)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except USER_ERRORS as exc:
            raise ToolError(str(exc)) from exc

    return wrapper  # type: ignore[return-value]


INSTRUCTIONS = """\
steamworks-mcp takes a game from "nothing configured" to "released on Steam".

Typical flow: init_project (creates steamworks.yaml and scans the game project) -> gap_report -> start_interview ->
generate / set_field -> validate -> export_package -> apply. Values live in steamworks.yaml; per-field status
(missing, draft, needs_review, approved, applied) lives in .steam-mcp/state.json.

Never claim something was done in Steamworks unless a tool reported it as applied. Scanned and generated values are
drafts until the user approves them. Nothing is ever published by this server.
"""


def create_server(config: Config) -> MCPServer:
    server = MCPServer("steamworks-mcp", instructions=INSTRUCTIONS)

    def project_dir(path: str | None) -> Path:
        return resolve_in_workspace(config.workspace_root, path)

    @server.tool()
    @user_errors
    def init_project(
        path: str,
        name: str | None = None,
        appid: int | None = None,
        scan: bool = True,
        source_dir: str | None = None,
    ) -> dict[str, Any]:
        """Start tracking a game: create steamworks.yaml and .steam-mcp/ in `path` (existing files are kept, never
        overwritten), then scan the game project unless `scan` is false.

        Args:
            path: Folder for steamworks.yaml, usually the game project's root (relative to the workspace root).
            name: Product name as it will appear on Steam, if known.
            appid: The main game's Steam app id, if already created in Steamworks.
            scan: Run the project scanners (Unity, …) right away.
            source_dir: The engine project folder, when it is not `path` itself.

        Returns created/existing files, .gitignore lines to suggest to the user (this server never edits the
        project's own .gitignore), and the scan report when scanning.
        """
        root = project_dir(path)
        if not root.is_dir():
            raise ValueError(f"{root} is not a folder")
        result: dict[str, Any] = {"project": str(root), **proj.init_project(root, name, appid)}
        if scan:
            result["scan"] = _scan(root, project_dir(source_dir) if source_dir else root)
        return result

    @server.tool()
    @user_errors
    def scan_project(path: str, source_dir: str | None = None) -> dict[str, Any]:
        """Scan the game project and merge what is found into steamworks.yaml as drafts (source "scan", with file and
        line evidence and a confidence). Values the user approved are never overwritten: differences come back under
        `conflicts`. Read-only towards the game project; raw observations are kept in .steam-mcp/scan/.

        Args:
            path: Folder that holds steamworks.yaml.
            source_dir: The engine project folder, when it is not `path` itself.
        """
        root = project_dir(path)
        return _scan(root, project_dir(source_dir) if source_dir else root)

    def _scan(root: Path, source: Path) -> dict[str, Any]:
        project = proj.Project.open(root)
        results = run_scanners(source)
        if not results:
            return {"scanners": [], "message": f"No supported engine project found in {source} (Unity is supported)."}
        report = proj.merge_scan(project, results)
        project.save()
        out = report.as_dict()
        out["summary"] = {
            "applied": len(report.applied),
            "conflicts": len(report.conflicts),
            "unchanged": len(report.unchanged),
            "rejected": len(report.rejected),
            "warnings": len(report.warnings),
            "fields_with_values": sum(1 for _, v in iter_fields(project.values()) if not is_empty(v)),
        }
        return out

    @server.tool()
    def server_info() -> dict[str, Any]:
        """Version, workspace root and which optional features are enabled (no secrets)."""
        return {
            "version": __version__,
            "workspace_root": str(config.workspace_root),
            "browser_mode": config.browser_enabled,
            "publisher_key_configured": config.publisher_key is not None,
            "web_api_key_configured": config.web_api_key is not None,
            "steamcmd_configured": config.steamcmd_path is not None,
        }

    return server
