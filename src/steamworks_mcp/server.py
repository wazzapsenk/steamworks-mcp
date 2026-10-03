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
from steamworks_mcp.export import preview, vdf
from steamworks_mcp.gates.engine import evaluate_gates
from steamworks_mcp.gates.report import gap_report as gap_report_fn
from steamworks_mcp.generate import deterministic as gen_det
from steamworks_mcp.generate import text as gen_text
from steamworks_mcp.interview.forms import field_id, form_model
from steamworks_mcp.interview.questions import Question, next_questions
from steamworks_mcp.localization import store as loc
from steamworks_mcp.manifest.io import ManifestError, atomic_write, load_drafts
from steamworks_mcp.manifest.paths import FieldPathError, iter_fields
from steamworks_mcp.manifest.state import TransitionError, is_empty
from steamworks_mcp.media import images
from steamworks_mcp.references.analyze import analyze
from steamworks_mcp.references.fetch import FetchError, ReferenceFetcher
from steamworks_mcp.scanners import run_scanners
from steamworks_mcp.spec_info import spec_info
from steamworks_mcp.validate.report import validate_project

F = TypeVar("F", bound=Callable[..., Any])
USER_ERRORS = (
    WorkspaceError,
    ManifestError,
    FieldPathError,
    TransitionError,
    ValueError,
    FileNotFoundError,
    FetchError,
)


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

    # ------------------------------------------------------------------ generators & validators

    @server.tool()
    @user_errors
    def generate(path: str, section: str, stage: str | None = None) -> dict[str, Any]:
        """Produce drafts for a part of the release.

        Text sections return a brief for YOU to write from (the server never invents marketing text itself):
          - "store_short": write 3 variants (strategies fantasy, mechanic, situation_humor), save each with save_draft.
          - "store_long": stage "outline" first; after the user approved an outline, stage "text".
          - "achievements": names, descriptions and icon briefs for achievements that lack them.
        Deterministic sections write drafts into steamworks.yaml directly (never over approved values):
          - "cloud" (quotas, enable), "builds" (a depot per OS; returns the SteamPipe scripts when depot ids exist),
            "requirements" (minimum system requirements from the engine, always to review), "code" (stats,
            leaderboards and achievements used in code).
        """
        project = open_project(path)
        values, root = project.values(), project.files.root
        if section in ("store_short", "store_long", "achievements"):
            return gen_text.brief(values, project.files, section, stage)
        if section == "code":
            findings, code_report = gen_det.code_definitions(values, root)
            report = proj.propose(project, findings)
            project.save()
            return {**report.as_dict(), "code_vs_manifest": code_report}
        makers = {"cloud": gen_det.cloud, "builds": gen_det.builds, "requirements": gen_det.requirements}
        if section not in makers:
            raise ValueError("section: store_short, store_long, achievements, cloud, builds, requirements or code.")
        report = proj.propose(project, makers[section](values, root))
        project.save()
        out = report.as_dict()
        if section == "builds":
            scripts = vdf.build_scripts(project.values(), root, project.files.export_dir(2) / "steam")
            out["scripts"] = scripts if isinstance(scripts, dict) else {"not_yet": scripts}
        return out

    @server.tool()
    @user_errors
    def save_draft(path: str, field: str, value: str, strategy: str | None = None, notes: str = "") -> dict[str, Any]:
        """Store a text YOU wrote as a draft (it does not change steamworks.yaml). The server rejects text that breaks
        Valve's store rules or copies 8+ consecutive words from a reference game, scores it against the rubric, and
        returns rubric findings plus questions for you to judge. strategy: fantasy | mechanic | situation_humor |
        outline | text | revision | … . The user picks a draft with set_field(path, field, from_draft=<id>)."""
        project = open_project(path)
        return gen_text.save_text_draft(
            project.values(), project.files, field, value, strategy, config.cache_dir, notes=notes
        )

    @server.tool()
    @user_errors
    def validate(path: str, section: str = "all", llm_judgements: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Review what is in steamworks.yaml (and the translations) without changing it.

        section: "store" (Valve's rules in every language, the rubric, questions for you to judge), "achievements",
        "localization" or "all" (adds every failing gate rule). For store text, judge the returned questions and call
        again with llm_judgements=[{rule_id, field, outcome: pass|warn|fail, note}]; deterministic and judged results
        are reported separately. To propose a fix, save a new version with save_draft(strategy="revision").
        """
        project = open_project(path)
        return validate_project(
            project.values(),
            project.state,
            project.files.root,
            section,
            browser=config.browser_enabled,
            llm_judgements=llm_judgements,
        )

    @server.tool()
    @user_errors
    def preview_store(
        path: str, short_draft: str | None = None, about_draft: str | None = None, fold_px: int = preview.FOLD_PX
    ) -> dict[str, Any]:
        """Write an HTML preview of the store text (current values, or the given drafts) to .steam-mcp/exports/,
        with the estimated end of the first screen marked (an estimate; Steam's cut-off height is not documented)."""
        project = open_project(path)
        values = project.values()
        short, about = values["store"].get("short_description") or "", values["store"].get("about") or ""
        for draft_id, field in ((short_draft, "store.short_description"), (about_draft, "store.about")):
            if draft_id:
                d = next((d for d in load_drafts(project.files, field) if d.id == draft_id), None)
                if d is None:
                    raise ValueError(f"No draft {draft_id} for {field}.")
                short, about = (str(d.value), about) if field == "store.short_description" else (short, str(d.value))
        out = project.files.state_dir / "exports" / "store_preview.html"
        atomic_write(out, preview.store_preview(values["game"].get("name") or "Your game", short, about, fold_px))
        return {"file": out.relative_to(project.files.root).as_posix(), "fold_px": fold_px, "fold_verified": False}

    @server.tool()
    @user_errors
    def prepare_images(path: str, only: list[str] | None = None, achievement_icons: bool = True) -> dict[str, Any]:
        """Make every store, library and icon image from assets.key_art + assets.logo (or hand-made
        assets.overrides) at the exact sizes Steam wants, plus achievement icons (256x256 JPG, with greyscale locked
        versions). Crops around assets.key_art_focus, never stretches, reports upscaling, never generates artwork.
        Output and a preview page go to .steam-mcp/exports/images/ for the user to upload."""
        project = open_project(path)
        out_dir = project.files.state_dir / "exports" / "images"
        values, root = project.values(), project.files.root
        result: dict[str, Any] = {
            "images": [r.__dict__ for r in images.prepare_store_images(values, root, out_dir, only)]
        }
        if achievement_icons:
            result["achievement_icons"] = [
                r.__dict__ for r in images.prepare_achievement_icons(values, root, out_dir / "achievements")
            ]
        result["screenshots"] = images.screenshot_report(values, root)
        result["preview"] = (out_dir / "preview.html").relative_to(root).as_posix()
        return result

    @server.tool()
    @user_errors
    def fetch_reference(appid: int, tags: list[str] | None = None, refresh: bool = False) -> dict[str, Any]:
        """Fetch a successful Steam game's public data (on this machine, cached) and return derived measurements
        only: description structure and length, media counts, categories, achievement count/style/distribution.
        Its raw texts stay in the local cache, where the anti-copy check uses them."""
        fetcher = ReferenceFetcher(config.cache_dir, web_api_key=config.web_api_key)
        details = fetcher.appdetails(appid, refresh=refresh)
        pct = fetcher.achievement_percentages(appid, refresh=refresh)
        texts = fetcher.achievement_texts(appid, refresh=refresh)
        schema = fetcher.schema(appid, refresh=refresh)
        result = analyze(
            appid,
            details.data,
            pct.data,
            texts.data,
            schema.data if schema else None,
            details.fetched_at.date(),
            tags or [],
        )
        return result.model_dump(mode="json")

    @server.tool()
    @user_errors
    def localization_status(path: str) -> dict[str, Any]:
        """Per target language: how many player-facing texts are translated, missing, or stale (the source changed
        after translating). Stale translations are marked needs_review."""
        project = open_project(path)
        statuses = loc.status(project.values(), project.files.root)
        for st in statuses:
            for key in st.stale:
                fs = project.state.fields.get(f"localization.{st.language}.{key}")
                if fs is not None and fs.status != "needs_review":
                    fs.status = "needs_review"
        project.save()
        return {"languages": [s.__dict__ for s in statuses]}

    @server.tool()
    @user_errors
    def localization_pending(path: str, language: str, limit: int = 30) -> dict[str, Any]:
        """Texts YOU should translate into `language` (Steam API code), with context, length limits and glossary terms
        (localization/glossary.yaml). Translate them, then call localization_set."""
        project = open_project(path)
        items = loc.pending(project.values(), project.files.root, language, limit)
        return {
            "language": language,
            "entries": items,
            "submit": "localization_set(path, language, translations={key: text})",
        }

    @server.tool()
    @user_errors
    def localization_set(
        path: str, language: str, translations: dict[str, str], source: Literal["generated", "user"] = "generated"
    ) -> dict[str, Any]:
        """Save translations ({key: text}). Rejected: BBCode tags that differ from the source, a short description over
        300 characters, store text breaking Valve's rules. Your translations are drafts until the user approves them
        (approve_fields(["localization.<language>.*"]))."""
        project = open_project(path)
        out = loc.set_translations(project.values(), project.files.root, project.state, language, translations, source)
        project.save()
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
