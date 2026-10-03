"""Optional plugin for projects that use the "Steam Build Pipeline" Unity package (a ``SteamBuildConfig`` asset with
per-environment app ids, depots and branches). Projects without it are skipped silently.

Never read: the Steam user name and local tool paths stored in the same asset.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from steamworks_mcp.scanners import unity_yaml
from steamworks_mcp.scanners.base import Finding, ScanResult, ev, iter_files, line_of, read_text

PACKAGE = re.compile(r'"com\.[a-z0-9_-]+\.steam-build-pipeline"')
NEVER_READ = {"steamUsername", "steamCmdPath", "steamWorkingDir"}
ENV_TO_PROFILE = {
    "prod": "main",
    "production": "main",
    "release": "main",
    "main": "main",
    "demo": "demo",
    "playtest": "playtest",
}


def _config_assets(root: Path) -> list[Path]:
    found = []
    for p in iter_files(root, (".asset",), "Assets"):
        head = read_text(p)[:4000]
        if "environments:" in head and "buildOutputRoot:" in head:
            found.append(p)
    return found


class SteamBuildPipelineScanner:
    name = "steam_build_pipeline"

    def detect(self, root: Path) -> bool:
        manifest = read_text(root / "Packages" / "manifest.json")
        return bool(PACKAGE.search(manifest)) or bool(_config_assets(root))

    def scan(self, root: Path) -> ScanResult:
        r = ScanResult(self.name)
        assets = _config_assets(root)
        if not assets:
            return r
        path = assets[0]
        text = read_text(path)
        body: dict[str, Any] = next((b for _, b in unity_yaml.read(path) if "environments" in b), {})
        for key in NEVER_READ:
            body.pop(key, None)
        output_root = str(body.get("buildOutputRoot") or "Builds")
        envs = body.get("environments") or []
        summary = []
        for env in envs:
            name = str(env.get("name") or "").strip()
            profile = ENV_TO_PROFILE.get(name.lower())
            appid = env.get("appId")
            i = text.find(f"name: {name}")
            evidence = [ev(root, path, line_of(text, i) if i >= 0 else None, f"environment {name}")]
            summary.append({"name": name, "profile": profile, "appid": appid, "depots": len(env.get("depots") or [])})
            if not profile or not appid:
                continue
            r.findings.append(Finding(f"apps.{profile}.appid", int(appid), 0.9, evidence))
            depots = [
                {
                    "name": str(d.get("depotPathLabel") or f"depot_{d.get('depotId')}"),
                    "depot_id": int(d["depotId"]) if d.get("depotId") else None,
                    "os": "windows",
                    "arch": "64",
                    "content_root": f"{output_root}/{name}",
                }
                for d in env.get("depots") or []
            ]
            if depots:
                r.findings.append(
                    Finding(
                        f"apps.{profile}.builds.depots",
                        depots,
                        0.7,
                        evidence,
                        note="The pipeline builds Windows 64-bit; content_root is its default output folder.",
                    )
                )
            branches = [b for b in env.get("allowedBranches") or [] if b and b != "default"]
            if branches:
                r.findings.append(
                    Finding(f"apps.{profile}.builds.branches", [{"name": b} for b in branches], 0.7, evidence)
                )
        r.facts["environments"] = summary
        r.facts["platforms"] = ["windows"]
        return r
