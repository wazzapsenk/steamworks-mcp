"""The MCP server: a thin layer that registers tools; the work happens in the other modules.

No ``from __future__ import annotations`` here: tool signatures (``Resolve(...)`` markers on local functions)
are evaluated when the tools are registered.
"""

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

from mcp.server.mcpserver import Context, Elicit, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

from steamworks_mcp import __version__
from steamworks_mcp import fields as fields_ops
from steamworks_mcp import project as proj
from steamworks_mcp.config import Config, WorkspaceError, resolve_in_workspace
from steamworks_mcp.gates.engine import evaluate_gates
from steamworks_mcp.gates.report import gap_report as gap_report_fn
from steamworks_mcp.interview.forms import field_id, form_model
from steamworks_mcp.interview.questions import Question, next_questions
from steamworks_mcp.manifest.io import ManifestError
from steamworks_mcp.manifest.paths import FieldPathError, iter_fields
from steamworks_mcp.manifest.state import TransitionError, is_empty
from steamworks_mcp.scanners import run_scanners
from steamworks_mcp.spec_info import spec_info

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

    # ------------------------------------------------------------------ gaps & interview

    def open_project(path: str) -> proj.Project:
        return proj.Project.open(project_dir(path))

    @server.tool()
    @user_errors
    def gap_report(path: str, gate: int | None = None, include_info: bool = False) -> dict[str, Any]:
        """What is still missing before each release gate, and how each item gets done.

        Gates: 0 prerequisites, 1 store page review / Coming Soon, 2 build review, 3 release. For every blocking item
        you get its status (fail, review = values waiting for the user's approval, todo = a manual step or an image
        to produce, unknown), the fields involved, the execution mode (API, BROWSER, ARTIFACT, MANUAL), where it is
        done in Steamworks and Valve's source page. `next_steps` says what to do first.

        Args:
            path: Folder that holds steamworks.yaml.
            gate: Only this gate (0-3); default all.
            include_info: Also list the background notes (permissions, timings) that never block.
        """
        project = open_project(path)
        project.save()  # persist the reconciliation (edited values -> needs_review)
        return gap_report_fn(
            project.values(),
            project.state,
            project.files.root,
            gate,
            browser=config.browser_enabled,
            include_info=include_info,
        )

    def _questions(project: proj.Project, gate: int | None, max_questions: int) -> tuple[list[Question], int]:
        results = evaluate_gates(
            project.values(),
            project.state,
            project.files.root,
            None if gate is None else [gate],
            browser=config.browser_enabled,
        )
        return next_questions(
            results, project.values(), project.state, max(1, min(max_questions, 5)), project.files.root
        )

    def interview_form(
        ctx: Context, path: str, gate: int | None = None, max_questions: int = 3, use_form: bool = True
    ) -> Any:
        caps = ctx.client_capabilities
        if not use_form or caps is None or caps.elicitation is None:
            return None
        questions, _ = _questions(open_project(path), gate, max_questions)
        if not questions:
            return None
        return Elicit("A few questions about your game for its Steam release.", form_model(questions))

    @server.tool()
    @user_errors
    def start_interview(
        path: str,
        gate: int | None = None,
        max_questions: int = 3,
        use_form: bool = True,
        form: Annotated[BaseModel | None, Resolve(interview_form)] = None,
    ) -> dict[str, Any]:
        """Next questions for the user (at most `max_questions`, related ones together, earliest gate first), each with
        a suggested answer to confirm or change. `confirm: true` means a value was found (e.g. by the scan): just ask
        whether it is right.

        Ask the user these questions in chat, then save the answers with set_field(values={question id: answer}).
        Answers can be plain text ("yes", "a, b, c", "2027-05-12"); they are converted to the right type.
        Store texts are not asked here: use generate. When the client supports forms (MCP elicitation) the questions
        are shown as a form instead and the answers are saved right away; pass use_form=false to avoid that.

        Args:
            path: Folder that holds steamworks.yaml.
            gate: Only questions for this gate (0-3).
            max_questions: 1-5, default 3.
            use_form: Show a form when the client supports it.
        """
        project = open_project(path)
        answered: dict[str, Any] = {}
        if form is not None:
            answered = {field_id(k): v for k, v in form.model_dump().items() if v not in (None, "")}
            if answered:
                fields_ops.set_fields(project, answered, "user")
                project.save()
        questions, remaining = _questions(project, gate, max_questions)
        out: dict[str, Any] = {
            "questions": [q.as_dict() for q in questions],
            "remaining_after_these": remaining,
        }
        if answered:
            out["saved_from_form"] = sorted(answered)
        if not questions:
            out["message"] = "Nothing left to ask for this gate. Call gap_report for the remaining steps."
        else:
            out["how_to_answer"] = "set_field(path, values={<question id>: <answer>, ...})"
        return out

    @server.tool()
    @user_errors
    def set_field(
        path: str,
        field: str | None = None,
        value: Any = None,
        values: dict[str, Any] | None = None,
        source: Literal["user", "generated"] = "user",
        from_draft: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Write one value (`field` + `value`) or several (`values`) into steamworks.yaml, keeping its comments.

        source="user" (the user's own answer or decision) stores the value as approved. source="generated" (text you
        wrote) stores it as a draft for the user to review; generated store text that breaks Valve's store rules is
        rejected. `from_draft` picks a saved draft for `field` and approves it. Field paths look like
        store.short_description, achievements.ACH_WIN.name, apps.main.installation.launch_options.0.executable.
        Setting a value to null removes it.
        """
        project = open_project(path)
        if from_draft:
            if not field:
                raise ValueError("from_draft needs `field`.")
            out = fields_ops.set_from_draft(project, field, from_draft)
        else:
            changes = dict(values or {})
            if field is not None:
                changes[field] = value
            out = fields_ops.set_fields(project, changes, source, notes=notes)
        project.save()
        return out

    @server.tool()
    @user_errors
    def approve_fields(path: str, fields: list[str]) -> dict[str, Any]:
        """Mark values as approved by the user (after showing them). Patterns work: "achievements.*.name".
        A section approves everything under it: "apps.main.cloud". Never approve without the user's agreement."""
        project = open_project(path)
        out = fields_ops.approve_fields(project, fields)
        project.save()
        return out

    @server.tool()
    @user_errors
    def mark_applied(path: str, fields: list[str], notes: str = "") -> dict[str, Any]:
        """The user confirms something is done in Steamworks: a manual step ("checklist.<rule id>" from gap_report),
        or approved values they entered there themselves. Only approved, unchanged values can be marked applied.
        Never call this on your own: only when the user says it is done."""
        project = open_project(path)
        out = fields_ops.mark_applied(project, fields, notes)
        project.save()
        return out

    @server.tool()
    @user_errors
    def get_spec_info(kind: str) -> dict[str, Any]:
        """Reference data (the tool equivalent of this server's resources, for clients that only use tools).

        kind: "schema" (steamworks.yaml fields), "gates" (overview), "gate:<0-3>" (all rules of a gate),
        "capabilities", "store_rules", "asset_specs", "events", "style_guide:<id>", "reference:<appid>"
        (derived analysis of a reference game), "references" (catalog).
        """
        return spec_info(kind)

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
