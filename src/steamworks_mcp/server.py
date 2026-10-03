"""The MCP server: a thin layer that registers tools; the work happens in the other modules.

No ``from __future__ import annotations`` here: tool signatures (``Resolve(...)`` markers on local functions)
are evaluated when the tools are registered.
"""

import functools
import inspect
import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import Context, Elicit, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from steamworks_mcp import __version__, present
from steamworks_mcp import fields as fields_ops
from steamworks_mcp import project as proj
from steamworks_mcp.config import Config, WorkspaceError, resolve_in_workspace
from steamworks_mcp.execute.api import SteamApiError
from steamworks_mcp.execute.apply import ApplyRefused
from steamworks_mcp.execute.browser.partner import FormatError
from steamworks_mcp.execute.browser.transport import NotLoggedInError
from steamworks_mcp.execute.guard import GuardError
from steamworks_mcp.execute.service import APPS, INSPECT, SECTIONS, Executor
from steamworks_mcp.export import package, preview, vdf
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
from steamworks_mcp.oauth import LocalOAuth
from steamworks_mcp.references import live, market
from steamworks_mcp.references import reviews as review_study
from steamworks_mcp.references.analyze import analyze
from steamworks_mcp.references.fetch import FetchError, ReferenceFetcher
from steamworks_mcp.scanners import run_scanners
from steamworks_mcp.spec_info import spec_info
from steamworks_mcp.status import project as project_status
from steamworks_mcp.status import workspace as workspace_status
from steamworks_mcp.validate.code import check_code as check_code_fn
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
    ApplyRefused,
    SteamApiError,
    NotLoggedInError,
    FormatError,
    GuardError,
)


HINTS: tuple[tuple[type[BaseException], str], ...] = (
    (WorkspaceError, "Use a folder inside the workspace root (server_info shows it)."),
    (
        ManifestError,
        "If there is no steamworks.yaml yet, call init_project; if the file is broken, show the user the line the "
        "message names.",
    ),
    (FieldPathError, 'Field paths look like store.short_description; get_spec_info("schema") lists them all.'),
    (TransitionError, "Show the value to the user and approve it first; only approved values can be marked done."),
    (FetchError, "Steam's public store did not answer; try again in a minute. Everything offline still works."),
    (NotLoggedInError, "Call steamworks_open and let the user sign in to Steamworks, then call again."),
    (FormatError, "Steamworks changed one of its pages, so nothing was written. Tell the user; do it by hand for now."),
    (GuardError, "This request is outside what the tool may do in Steamworks; do it by hand."),
    (SteamApiError, "Check the publisher key and the app id (server_info shows which keys are set)."),
    (ApplyRefused, "Do what the message asks, then call again."),
    (FileNotFoundError, "Check the path; paths are relative to the game folder or the workspace root."),
    (ValueError, "Fix the arguments as the message says and call again."),
)


def hint_for(exc: BaseException) -> str | None:
    return next((hint for kind, hint in HINTS if isinstance(exc, kind)), None)


def user_errors(fn: F) -> F:
    """Show expected errors to the model in the result shape (the SDK hides unexpected exceptions' details)."""
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except USER_ERRORS as exc:
                raise ToolError(present.error(str(exc), hint_for(exc))) from exc

        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except USER_ERRORS as exc:
            raise ToolError(present.error(str(exc), hint_for(exc))) from exc

    return wrapper  # type: ignore[return-value]


INSTRUCTIONS = """\
steamworks-mcp takes a game from "nothing configured" to "released on Steam".

Start with status: without a path it lists the games in the workspace, with one it says how far the game is and what
to do next. Typical flow: init_project (creates steamworks.yaml and scans the game project) -> gap_report ->
start_interview -> generate / set_field -> validate -> export_package -> apply. Values live in steamworks.yaml;
per-field status (missing, draft, needs_review, approved, applied) lives in .steam-mcp/state.json.

Every result starts with the same four keys: outcome (ok, needs_input, needs_confirmation, partial, refused), summary
(one sentence for the user), next (what to do next) and display (Markdown). Show `display` to the user as it is; if
you talk to the user in another language, translate its words and keep its layout. Errors come in the same shape with
outcome "error".

Never claim something was done in Steamworks unless a tool reported it as applied. Scanned and generated values are
drafts until the user approves them. Writes to Steam always start as a dry run; write only after the user saw the
changes and agreed (user_confirmed=true). Nothing is ever published by this server: the user publishes in Steamworks.
"""


def create_server(config: Config, executor: Executor | None = None, oauth: LocalOAuth | None = None) -> MCPServer:
    if oauth is None:
        server = MCPServer("steamworks-mcp", instructions=INSTRUCTIONS)
    else:
        server = MCPServer(
            "steamworks-mcp",
            instructions=INSTRUCTIONS,
            auth_server_provider=oauth,
            auth=AuthSettings(
                issuer_url=oauth.public_url,
                resource_server_url=oauth.resource_url,
                validate_token_resource=True,
                client_registration_options=ClientRegistrationOptions(enabled=True),
                revocation_options=RevocationOptions(enabled=True),
            ),
        )
        server.custom_route("/oauth/approve", methods=["GET", "POST"])(oauth.approval_page)
    execu = executor or Executor(config)

    def project_dir(path: str | None) -> Path:
        return resolve_in_workspace(config.workspace_root, path)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
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
        return present.init_project(result)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
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
        return present.scan(_scan(root, project_dir(source_dir) if source_dir else root))

    def _scan(root: Path, source: Path) -> dict[str, Any]:
        project = proj.Project.open(root)
        results = run_scanners(source)
        if not results:
            return {"scanners": [], "message": f"No supported engine project found in {source} (Unity is supported)."}
        report = proj.merge_scan(project, results)
        project.save()
        out = report.as_dict()
        out["counts"] = {
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

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @user_errors
    def status(path: str | None = None) -> dict[str, Any]:
        """Start here. Without `path`: the games in the workspace (tracked ones and engine projects not tracked
        yet). With `path`: how far the game is on each release step, its values (approved, drafts, to fill, done in
        Steamworks), store text and translations, and the one thing to do next.

        Args:
            path: The game's folder (the one with steamworks.yaml), relative to the workspace root.
        """
        if path is None:
            return present.status_workspace(workspace_status(config.workspace_root))
        root = project_dir(path)
        if not (root / "steamworks.yaml").is_file():
            out = workspace_status(root)
            if not out["games"]:
                return present.result(
                    {"path": path, "engine": out["not_tracked_yet"][0]["engine"] if out["not_tracked_yet"] else None},
                    f"{path} is not tracked yet.",
                    outcome="needs_input",
                    next=f"Ask the user whether to start tracking it, then call init_project(path='{path}').",
                )
            return present.status_workspace(out)
        return present.status_project(project_status(open_project(path), browser=config.browser_enabled))

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
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
        return present.gap_report(
            gap_report_fn(
                project.values(),
                project.state,
                project.files.root,
                gate,
                browser=config.browser_enabled,
                include_info=include_info,
            )
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

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
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
        return present.start_interview(out)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False))
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
        return present.set_field(out)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
    @user_errors
    def approve_fields(path: str, fields: list[str]) -> dict[str, Any]:
        """Mark values as approved by the user (after showing them). Patterns work: "achievements.*.name".
        A section approves everything under it: "apps.main.cloud". Never approve without the user's agreement."""
        project = open_project(path)
        out = fields_ops.approve_fields(project, fields)
        project.save()
        return present.approve_fields(out)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
    @user_errors
    def mark_applied(path: str, fields: list[str], notes: str = "") -> dict[str, Any]:
        """The user confirms something is done in Steamworks: a manual step ("checklist.<rule id>" from gap_report),
        or approved values they entered there themselves. Only approved, unchanged values can be marked applied.
        Never call this on your own: only when the user says it is done."""
        project = open_project(path)
        out = fields_ops.mark_applied(project, fields, notes)
        project.save()
        return present.mark_applied(out)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @user_errors
    def get_spec_info(kind: str) -> dict[str, Any]:
        """Reference data (the tool equivalent of this server's resources, for clients that only use tools).

        kind: "schema" (steamworks.yaml fields), "gates" (overview), "gate:<0-3>" (all rules of a gate),
        "capabilities", "store_rules", "asset_specs", "events", "code_rules" (what check_code checks), "estimates"
        (the rules of thumb of estimate_sales), "style_guide:<id>", "reference:<appid>"
        (derived analysis of a reference game), "references" (catalog), "store_patterns" (what the store pages of
        popular new releases look like, per Steam genre; numbers only), "store_patterns:<Steam genre>".
        """
        return present.result(spec_info(kind), f"Reference data: {kind}.")

    # ------------------------------------------------------------------ generators & validators

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
    @user_errors
    def generate(path: str, section: str, stage: str | None = None) -> dict[str, Any]:
        """Produce drafts for a part of the release.

        Text sections return a brief for YOU to write from (the server never invents marketing text itself):
          - "store_short": write one variant per strategy the brief lists (fantasy, mechanic, situation_humor, and
            after a market study market_common and market_contrast), save each with save_draft.
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
            return present.generate(gen_text.brief(values, project.files, section, stage), section, stage)
        if section == "code":
            findings, code_report = gen_det.code_definitions(values, root)
            report = proj.propose(project, findings)
            project.save()
            return present.generate({**report.as_dict(), "code_vs_manifest": code_report}, section, stage)
        makers = {"cloud": gen_det.cloud, "builds": gen_det.builds, "requirements": gen_det.requirements}
        if section not in makers:
            raise ValueError("section: store_short, store_long, achievements, cloud, builds, requirements or code.")
        report = proj.propose(project, makers[section](values, root))
        project.save()
        out = report.as_dict()
        if section == "builds":
            scripts = vdf.build_scripts(project.values(), root, project.files.export_dir(2) / "steam")
            out["scripts"] = scripts if isinstance(scripts, dict) else {"not_yet": scripts}
        return present.generate(out, section, stage)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
    @user_errors
    def save_draft(path: str, field: str, value: str, strategy: str | None = None, notes: str = "") -> dict[str, Any]:
        """Store a text YOU wrote as a draft (it does not change steamworks.yaml). The server rejects text that breaks
        Valve's store rules or copies 8+ consecutive words from a reference game, scores it against the rubric, and
        returns rubric findings plus questions for you to judge. strategy: fantasy | mechanic | situation_humor |
        market_common | market_contrast | outline | text | revision | … . The user picks a draft with
        set_field(path, field, from_draft=<id>)."""
        project = open_project(path)
        return present.save_draft(
            gen_text.save_text_draft(
                project.values(), project.files, field, value, strategy, config.cache_dir, notes=notes
            )
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @user_errors
    def validate(path: str, section: str = "all", llm_judgements: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Review what is in steamworks.yaml (and the translations) without changing it.

        section: "store" (Valve's rules in every language, the rubric, questions for you to judge), "achievements",
        "localization" or "all" (adds every failing gate rule). For store text, judge the returned questions and call
        again with llm_judgements=[{rule_id, field, outcome: pass|warn|fail, note}]; deterministic and judged results
        are reported separately. To propose a fix, save a new version with save_draft(strategy="revision").
        """
        project = open_project(path)
        return present.validate(
            validate_project(
                project.values(),
                project.state,
                project.files.root,
                section,
                browser=config.browser_enabled,
                llm_judgements=llm_judgements,
            )
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @user_errors
    def check_code(path: str, source_dir: str | None = None, rules: list[str] | None = None) -> dict[str, Any]:
        """Check the game's own code, engine settings and SteamPipe scripts against Steamworks rules (read-only):
        app ids (480, mismatches), the SDK start (result checked, restart through Steam, callbacks, shutdown),
        stats and achievements (StoreStats, names that steamworks.yaml does not define), keys and Steam login files
        in the project, build scripts (setlive default, missing paths, steam_appid.txt in builds), Steam Deck (fixed
        resolution, no gamepad input, anti-cheat), saves (PlayerPrefs, Windows paths, BinaryFormatter) and networking
        (old P2P API, unverified auth tickets). Every finding has a file, a line and a fix; a secret is never shown.

        Args:
            path: The game's folder (with steamworks.yaml).
            source_dir: The engine project folder, when it is not `path` itself.
            rules: Only these rule or group ids (get_spec_info("code_rules") lists them).
        """
        project = open_project(path)
        source = project_dir(source_dir) if source_dir else project.files.root
        return present.check_code(check_code_fn(source, project.values(), rules))

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
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
        return present.preview_store(
            {"file": out.relative_to(project.files.root).as_posix(), "fold_px": fold_px, "fold_verified": False}
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
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
        return present.prepare_images(result)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
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
        return present.fetch_reference(result.model_dump(mode="json"))

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True))
    @user_errors
    def study_market(path: str, tags: list[str] | None = None, games: int = market.DEFAULT_PEERS) -> dict[str, Any]:
        """Start a market study before writing the store text: find the game's closest popular Steam games (its most
        specific store tags, on the Popular New Releases and Top Sellers lists, released in the last 3 years) and
        return their short descriptions and About texts for YOU to read and label with the returned vocabulary.
        Only their app ids are saved; the texts stay in the local cache, and drafts are checked against them. Then
        call save_market_study.

        Args:
            path: Folder that holds steamworks.yaml.
            tags: Steam store tag names to search with instead of store.tags, most specific first.
            games: How many games to study (3-15, default 10).
        """
        project = open_project(path)
        fetcher = ReferenceFetcher(config.cache_dir, web_api_key=config.web_api_key)
        return present.study_market(market.start(fetcher, project.values(), project.files, tags, games))

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
    @user_errors
    def save_market_study(path: str, notes: list[dict[str, Any]]) -> dict[str, Any]:
        """Save your labels for the pages study_market returned: one note per page with appid, short_opening,
        short_moves, about_shape, about_sections, tone (all from its vocabulary) and technique (one sentence in your
        own words; a note that repeats 4+ consecutive words of the page is rejected). The study keeps labels, notes
        and numbers, never page text; generate(store_short / store_long) builds on it from then on."""
        project = open_project(path)
        fetcher = ReferenceFetcher(config.cache_dir, web_api_key=config.web_api_key)
        return present.save_market_study(market.save(fetcher, project.files, notes))

    # ------------------------------------------------------------------ live market numbers

    def fetcher() -> ReferenceFetcher:
        return ReferenceFetcher(config.cache_dir, web_api_key=config.web_api_key)

    def own_appid(project: proj.Project) -> int | None:
        appid = ((project.values().get("apps") or {}).get("main") or {}).get("appid")
        return int(appid) if appid else None

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
    @user_errors
    def store_lookup(query: str) -> dict[str, Any]:
        """Look a game up on the Steam store by name, app id or store link: price, review score, players right now,
        release date, genres, modes (co-op, PvP…), Steam features, platforms, languages, achievements and DLC.
        Public data, cached for a few hours; nothing is saved in the project."""
        return present.store_lookup(live.lookup(fetcher(), query))

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
    @user_errors
    def compare_games(path: str | None = None, appids: list[int] | None = None) -> dict[str, Any]:
        """Compare games side by side: price, reviews, positive share, players right now, release, modes, and which
        Steam features most of them use. Default: the games of the market study (study_market) plus this game once
        it is on the store.

        Args:
            path: The game's folder, to compare with its market study.
            appids: Games to compare instead (store_lookup finds app ids by name). At most 15.
        """
        project = open_project(path) if path else None
        ids = live.peers(project.files if project else None, appids)
        own = own_appid(project) if project else None
        if own and own not in ids:
            ids = [own, *ids]
        return present.compare_games(live.compare(fetcher(), ids, own))

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
    @user_errors
    def price_brief(path: str, appids: list[int] | None = None, countries: list[str] | None = None) -> dict[str, Any]:
        """What close games charge: their US full prices (median and middle half), and for each country store how
        much they charge per US dollar, applied to this game's base price (pricing.base_price_usd, else their
        median). Default games: the market study's. Nothing is saved; the user decides the price.

        Args:
            path: The game's folder.
            appids: Games to compare with instead of the market study's.
            countries: Two-letter store codes (default us, gb, de, pl, tr, br, cn, jp, kr, in).
        """
        project = open_project(path)
        ids = live.peers(project.files, appids)
        return present.price_brief(live.price_brief(fetcher(), project.values(), ids, countries))

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
    @user_errors
    def estimate_sales(
        path: str | None = None, appids: list[int] | None = None, wishlists: int | None = None
    ) -> dict[str, Any]:
        """Rough copies sold for close games, from their review counts, and, with `wishlists`, a first-week range
        for this game at its base price and launch discount. Rules of thumb with every assumption listed, never a
        forecast. Default games: the market study's.

        Args:
            path: The game's folder (its market study and price).
            appids: Games to estimate instead.
            wishlists: This game's wishlists on release day, if the user knows them (Steamworks shows them).
        """
        project = open_project(path) if path else None
        values = project.values() if project else {}
        ids: list[int] = []
        try:
            ids = live.peers(project.files if project else None, appids)
        except ValueError:
            if wishlists is None:
                raise
        return present.estimate_sales(live.estimate_sales(fetcher(), values, ids, wishlists))

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True))
    @user_errors
    def study_reviews(path: str, appids: list[int] | None = None, per_kind: int = 10) -> dict[str, Any]:
        """Start a review study: the most helpful positive and negative English reviews of the close games (default:
        the market study's) or of any games, e.g. this game after launch. Read them and label each game with the
        returned themes (what players praise and criticize), then call save_review_study. Only the app ids are saved;
        the texts stay in the local cache and nothing about reviewers is fetched.

        Args:
            path: The game's folder.
            appids: Games to study instead of the market study's.
            per_kind: Reviews per game and kind (positive, negative): 3-25, default 10.
        """
        project = open_project(path)
        ids = live.peers(project.files, appids)
        return present.study_reviews(review_study.start(fetcher(), project.files, ids, per_kind))

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
    @user_errors
    def save_review_study(path: str, notes: list[dict[str, Any]]) -> dict[str, Any]:
        """Save your labels for the games study_reviews returned: per game appid, praised (1-4 themes, most important
        first), criticized (0-4) and insight (one sentence in your own words; one that repeats 4+ consecutive words
        of a review is rejected). Keeps labels and shares, never review text; the store-text briefs use it."""
        project = open_project(path)
        return present.save_review_study(review_study.save(fetcher(), project.files, notes))

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True))
    @user_errors
    def launch_watch(path: str) -> dict[str, Any]:
        """After release: the game's review score and count, players right now and price, against the last check
        (each check is kept in .steam-mcp/market/launch.jsonl). Call it again any day to see the trend."""
        project = open_project(path)
        return present.launch_watch(live.launch_watch(fetcher(), project.values(), project.files))

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
    @user_errors
    def localization_status(path: str) -> dict[str, Any]:
        """Per target language: how many player-facing texts (store page, Early Access answers, achievements, launch
        options) are translated, missing, or stale (the source changed after translating; stale translations are
        marked needs_review). `next` names the language to translate next."""
        project = open_project(path)  # loading marks stale translations needs_review
        project.save()
        return present.localization_status(loc.report(project.values(), project.files.root))

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    @user_errors
    def localization_pending(path: str, language: str, limit: int = 30) -> dict[str, Any]:
        """Texts YOU should translate into `language` (Steam API code) from the source language, with context, length
        limits and glossary terms (localization/glossary.yaml). Stale ones carry the previous translation to update.
        Translate them, then call localization_set; repeat until nothing is pending."""
        project = open_project(path)
        items = loc.pending(project.values(), project.files.root, language, limit)
        return present.localization_pending(
            {
                "language": language,
                "entries": items,
                "submit": "localization_set(path, language, translations={key: text})",
            }
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
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
        return present.localization_set(out)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
    @user_errors
    def export_package(path: str, gate: int) -> dict[str, Any]:
        """Write everything needed to finish a gate by hand to .steam-mcp/exports/gate_<gate>/: correctly named
        files (store localization JSON and per-language texts, store/library images and icons, SteamPipe scripts,
        achievement icons and CSV) and a CHECKLIST.md that says, for every open item, which Steamworks page and
        field it goes to and what to paste. Done items are ticked. Nothing is uploaded or published."""
        project = open_project(path)
        return present.export_package(
            package.export_package(project.values(), project.state, project.files, gate, browser=config.browser_enabled)
        )

    # ------------------------------------------------------------------ execution (API, BROWSER, steamcmd)

    @server.tool(annotations=ToolAnnotations(destructive_hint=True, open_world_hint=True))
    @user_errors
    async def apply(
        path: str,
        section: Literal[
            "cloud",
            "installation",
            "achievements",
            "store_text",
            "store_page",
            "store_assets",
            "depots",
            "store_tags",
            "leaderboards",
            "build",
        ],
        app: Literal["main", "demo", "playtest"] = "main",
        dry_run: bool = True,
        user_confirmed: bool = False,
        remove_extra: bool = False,
        upload_icons: bool = False,
        goes_live_now: bool = False,
    ) -> dict[str, Any]:
        """Make Steam match the approved values of one section. Always call with dry_run=true first and show the user
        the changes; write (dry_run=false, user_confirmed=true) only after they agreed. Nothing is ever published:
        BROWSER writes are drafts the user reviews and publishes in Steamworks. The one exception is "store_tags":
        Steam applies tags at once, so it also needs goes_live_now=true, and only after the user agreed to exactly
        that.

        Sections: "leaderboards" (Web API, publisher key), "build" (uploads the SteamPipe scripts with steamcmd),
        and with the BROWSER mode: "cloud", "installation", "achievements" (main game), "store_text" (short and long
        description in every approved language), "store_page" (the store page form: links, support info, legal
        line, system requirements, platforms, language table, genres, categories, third-party DRM/accounts; empty
        fields never clear Steam's), "store_assets" (uploads prepare_images' capsules and library images into the
        slots Steam has no image for yet; never replaces one, and also sets the library logo position), "depots"
        (OS, architecture and language of depots that already exist, through the Depots page's own Save),
        "store_tags" (store.tags in order, through the Tag Wizard; live at once; community tags are never removed).
        Every call saves a snapshot of what Steam had first.

        Args:
            path: Folder that holds steamworks.yaml.
            section: What to apply.
            app: main, demo or playtest (each has its own app id).
            dry_run: Only show the differences (default).
            user_confirmed: The user saw the dry-run changes and agreed.
            remove_extra: Also delete rows that exist only in Steam. Only when the user explicitly asks for it.
            upload_icons: achievements: also upload the icons (prepared from achievements.*.icon).
            goes_live_now: store_tags: the user agreed that the tags go live on the store at once.
        """
        return present.steam_write(
            await execu.apply(
                open_project(path),
                section,
                app,
                dry_run=dry_run,
                user_confirmed=user_confirmed,
                remove_extra=remove_extra,
                upload_icons=upload_icons,
                goes_live_now=goes_live_now,
            )
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
    @user_errors
    async def steamworks_inspect(
        path: str,
        what: Literal[
            "builds",
            "leaderboards",
            "achievement_schema",
            "snapshots",
            "pending",
            "checklist",
            "cloud",
            "installation",
            "achievements",
            "store_text",
            "store_page",
            "store_assets",
            "depots",
            "store_tags",
        ],
        app: Literal["main", "demo", "playtest"] = "main",
    ) -> dict[str, Any]:
        """Read-only look at what Steam has now. With the publisher key: "builds" (recent builds and branches),
        "leaderboards", "achievement_schema". With the BROWSER mode: "cloud", "installation", "achievements",
        "store_text", "store_page", "store_assets" (which image slots are filled), "pending" (the unpublished changes
        the Publish tab would show), and "checklist" (the
        release checklists of the app's Steamworks landing page, each item linked to its gap_report rule).
        "snapshots" lists the snapshots saved before writes (local)."""
        return present.steam_read(await execu.inspect(open_project(path), what, app), what)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True))
    @user_errors
    async def import_from_steamworks(
        path: str,
        app: Literal["main", "demo", "playtest"] = "main",
        sections: list[Literal["store_text", "achievements", "cloud", "installation", "leaderboards", "checklist"]]
        | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """For a game that already exists in Steamworks: read what Steam has (store texts in every language,
        achievements, Steam Cloud, installation, leaderboards, the landing page's release checklists) and fill the
        EMPTY fields of steamworks.yaml with it, marked "applied". Never writes to Steamworks and never overwrites a
        value in the file: differences come back as conflicts for the user to settle. Dry run first; save with
        dry_run=false after the user agreed. Leaderboards need the publisher key, the rest the BROWSER mode."""
        return present.import_from_steamworks(
            await execu.import_from_steamworks(open_project(path), app, list(sections or []) or None, dry_run=dry_run),
            dry_run,
        )

    @server.tool(annotations=ToolAnnotations(destructive_hint=True, open_world_hint=True))
    @user_errors
    def set_build_live(
        path: str,
        build_id: int,
        branch: str,
        app: Literal["main", "demo", "playtest"] = "main",
        description: str = "",
        dry_run: bool = True,
        user_confirmed: bool = False,
    ) -> dict[str, Any]:
        """Set an uploaded build live on a beta branch (Web API, publisher key). Dry run first; set live only after
        the user explicitly agreed. The default branch (what every player gets) is never set live by this tool: the
        user does that in Steamworks Settings > SteamPipe > Builds."""
        return present.steam_write(
            execu.set_build_live(
                open_project(path),
                app,
                build_id,
                branch,
                description=description,
                dry_run=dry_run,
                user_confirmed=user_confirmed,
            )
        )

    if config.browser_enabled:

        @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True))
        @user_errors
        async def steamworks_open(accept_risks: bool = False) -> dict[str, Any]:
            """Open the Steamworks site in a browser window for the BROWSER mode. The first time this returns terms
            the user has to read and accept (then call again with accept_risks=true). The user signs in themselves;
            this tool never types passwords or Steam Guard codes and never reads cookie values."""
            return present.steamworks_open(await execu.open(accept_risks))

        @server.tool(annotations=ToolAnnotations(destructive_hint=True, open_world_hint=True))
        @user_errors
        async def restore_snapshot(
            path: str, snapshot: str | None = None, dry_run: bool = True, user_confirmed: bool = False
        ) -> dict[str, Any]:
            """Write a saved snapshot back to Steamworks (BROWSER mode): undoes an apply. Without `snapshot`, lists
            the snapshots. The first write on every app has to be a restore of the snapshot just taken: it writes
            every row back unchanged and reads it again, proving this tool reads and writes that app correctly.
            Dry run first; restore only after the user agreed."""
            return present.restore_snapshot(await execu.restore(open_project(path), snapshot, dry_run, user_confirmed))

    # ------------------------------------------------------------------ resources (get_spec_info is the tool twin)

    def as_json(data: Any) -> str:
        return json.dumps(data, indent=2, ensure_ascii=False, default=str)

    @server.resource("steam://capabilities", mime_type="application/json")
    def capabilities_resource() -> str:
        """What the tool can do in Steamworks per area, and how (API, BROWSER, ARTIFACT, MANUAL)."""
        return as_json(spec_info("capabilities"))

    @server.resource("steam://gates/{n}", mime_type="application/json")
    def gate_resource(n: str) -> str:
        """All rules of a release gate (0 prerequisites, 1 store page, 2 build review, 3 release)."""
        return as_json(spec_info(f"gate:{int(n)}"))

    @server.resource("steam://style-guide/{genre}", mime_type="text/markdown")
    def style_guide_resource(genre: str) -> str:
        """A genre style guide for store text (e.g. coop_party)."""
        return str(spec_info(f"style_guide:{genre}")["guide"])

    @server.resource("steam://references/{appid}", mime_type="application/json")
    def reference_resource(appid: str) -> str:
        """Derived measurements of a bundled reference game (no raw texts)."""
        return as_json(spec_info(f"reference:{int(appid)}"))

    @server.resource("steam://store-patterns", mime_type="application/json")
    def store_patterns_resource() -> str:
        """What the store pages of popular new Steam releases look like, per Steam genre and overall: lengths,
        structure, headers, lists, media, mentions, languages. Derived measurements only, no text."""
        return as_json(spec_info("store_patterns"))

    @server.resource("steam://manifest/{project}", mime_type="application/json")
    def manifest_resource(project: str) -> str:
        """Current values and per-field status of a project (a folder directly under the workspace root)."""
        p = open_project(project)
        return as_json({"values": p.values(), "state": {k: v.status for k, v in p.state.fields.items()}})

    # ------------------------------------------------------------------ prompts

    @server.prompt(title="Release assistant")
    def release_assistant(path: str) -> str:
        """Walk a game from nothing configured to released on Steam."""
        return (
            f"Help me release the game in `{path}` on Steam. Work in this order and keep me in the loop:\n"
            "1. init_project (or scan_project if steamworks.yaml exists), then gap_report.\n"
            "2. start_interview: ask me the questions in small batches and save my answers with set_field.\n"
            "3. generate the drafts (store_short, store_long, achievements, cloud, builds, requirements, code); show "
            "me each one and approve_fields only what I agree with. Before the store text, offer a market study "
            "(the market_research prompt).\n"
            "4. validate, translate every text into every target language (the localize_everything prompt: "
            "localization_pending / localization_set), prepare_images.\n"
            "5. export_package for the next gate; with the publisher key or the BROWSER mode, apply section by section "
            "(always a dry run first, then only with my OK).\n"
            "Never claim something is done in Steamworks unless a tool reported it applied, and never publish: I do "
            "that myself."
        )

    @server.prompt(title="Write the store page")
    def write_store_page(path: str) -> str:
        """Short description and About This Game, from brief to approved text."""
        return (
            f"Write the Steam store text for the game in `{path}`. Call generate(section='store_short'). If its "
            "`market` says not_studied, offer me a market study first (study_market, then save_market_study). "
            "Write one variant per strategy the brief lists, each saved with save_draft. Then "
            "generate(section='store_long', stage='outline'), let me pick an outline, and write the text with "
            "stage='text'. Run validate(section='store') after each draft, judge its questions, fix what fails, and "
            "show me preview_store before I pick drafts with set_field(from_draft=...).\n"
            "If a brief lists missing_recommended answers, ask me those first (start_interview). Build on its "
            "use_the_answers, and use recent_successful_pages (also steam://store-patterns) as what successful "
            "recent pages in this genre look like: lengths, structure, headers, lists, media. Numbers only, never "
            "a text to imitate. Its `market` is what the game's closest popular games do: labels and techniques, "
            "never their wording. Write in the source language only; when I approved the texts, translate them with "
            "the localize_everything prompt."
        )

    @server.prompt(title="Study the market")
    def market_research(path: str) -> str:
        """What the store pages of the game's closest popular Steam games do, before writing its store text."""
        return (
            f"Study the Steam store pages of the games closest to the game in `{path}` before we write its store "
            "text.\n"
            "1. Call study_market. If it finds no usable tags, ask me which Steam tags describe the game best "
            "(most specific first) and call it again with tags=[...].\n"
            "2. Read every page it returns and label it with its vocabulary: how the short description opens, the "
            "job of each sentence, how About is built and in what order, the tone, and one technique sentence in "
            "your own words. Save all notes with save_market_study; fix and resend rejected ones.\n"
            "3. Tell me in a few lines what these games do: the most common opening, the usual About order, the "
            "tones, two or three techniques worth using and one thing to avoid. Never quote their pages and never "
            "suggest naming them on our page.\n"
            "4. Offer to write the store text now (the write_store_page prompt): its briefs include the study and "
            "two strategies built on it, market_common and market_contrast."
        )

    @server.prompt(title="Localize everything")
    def localize_everything(path: str) -> str:
        """Translate the Steam store page, achievements and other player-facing texts into every target language."""
        return (
            f"Translate the game in `{path}` into all of its target languages. Every text is written once in the "
            "source language; you translate it, the server checks it.\n"
            "1. Call localization_status. If there are no target languages, ask me which languages the store page "
            "and achievements should be translated into (start_interview asks it) and stop there.\n"
            "2. For each language with missing or stale texts: call localization_pending(language=..., limit=20), "
            "translate every entry from the source language following its context, format (keep BBCode tags "
            "exactly and in order), max_length and glossary (do_not_translate, use_terms), and save the batch with "
            "localization_set(language=..., translations={key: text}). Stale entries show the previous "
            "translation: update it to the new source instead of starting over. Fix and resend rejected entries. "
            "Repeat until localization_pending returns nothing, then go to the next language.\n"
            "3. Write natural text for players of that language, not word for word; keep names and numbers.\n"
            "4. Call localization_status again and give me a short summary per language. Your translations are "
            "drafts: approve_fields(['localization.<language>.*']) only after I reviewed and agreed."
        )

    @server.prompt(title="Design achievements")
    def design_achievements(path: str) -> str:
        """Steam achievement names, descriptions and icon briefs that fit the game."""
        return (
            f"Design the Steam achievements for the game in `{path}`. Start with generate(section='code') to see "
            "which achievements and stats the code already uses, then generate(section='achievements') and write "
            "names, descriptions and icon briefs for the ones missing. Keep a balance of progression, skill and "
            "secret/funny ones, save them with set_field(source='generated'), run validate(section='achievements') "
            "and show me the list for approval."
        )

    @server.prompt(title="Review a gate")
    def review_gate(path: str, gate: str) -> str:
        """Everything still open before one Steam release gate, and the next three steps."""
        return (
            f"Review release gate {gate} for the game in `{path}`: call gap_report(gate={gate}) and validate, then "
            "explain what blocks the gate, what I have to approve, which steps are manual (with the Steamworks page "
            "for each) and what can be applied. Suggest the next three actions."
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def server_info() -> dict[str, Any]:
        """Version, workspace root and which optional features are enabled (no secrets)."""
        return present.server_info(
            {
                "version": __version__,
                "workspace_root": str(config.workspace_root),
                "browser_mode": config.browser_enabled,
                "publisher_key_configured": config.publisher_key is not None,
                "web_api_key_configured": config.web_api_key is not None,
                "steamcmd_configured": config.steamcmd_path is not None and config.steamcmd_username is not None,
                "browser_consent_given": execu.consent.accepted() if config.browser_enabled else None,
                "apply_sections": list(SECTIONS),
                "inspect": list(INSPECT),
                "apps": list(APPS),
            }
        )

    return server
