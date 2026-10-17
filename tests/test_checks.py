"""steamworks_check: the field-by-field checks of the store page and the build setup (no network)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from test_redistributables import pe

from steamworks_mcp.execute import checks


def item(**over: Any) -> dict[str, Any]:
    full = {"image": {"english": "x.jpg"}}
    base: dict[str, Any] = {
        "platforms": {"win": "1"},
        "content": {
            "supported_languages": {"english": {"supported": "true"}, "turkish": {"supported": "true"}},
            "sysreqs": {
                "windows": {
                    "min": {
                        "osversion": {"english": "Windows 10"},
                        "processor": {"english": "i5"},
                        "memory": {"amount": "8", "units": "GB"},
                        "graphics": {"english": "GTX 960"},
                        "diskspace": {"amount": "2", "units": "GB"},
                    },
                    "rec": {"memory": {"amount": "4", "units": "GB"}},
                }
            },
            "support_info": {"email": "help@example.com"},
        },
        "classification": {"primary_genre": "1", "genres": ["1"], "category": {"category_2": "true"}},
        "game": {"developers": [{"name": "Dev"}], "publishers": ["Pub"]},
        "release": {"steam_release_date": "1797609600"},
        "assets": {
            "header_image": full,
            "small_capsule": full,
            "main_capsule": full,
            "hero_capsule": full,
            "library_capsule": full,
            "library_header": full,
            "library_hero": full,
            "library_logo": {**full, "logo_position": {"pinned_position": "BottomLeft"}},
            "screenshots": [{"english": f"{i}.jpg", "all_ages": "1"} for i in range(5)],
        },
    }
    base.update(over)
    return base


LOC = {
    "languages": {
        "english": {checks.SHORT: "Short.", checks.ABOUT: "About " * 80},
        "turkish": {checks.SHORT: "Kısa."},
    }
}


def areas(issues: list[dict[str, str]]) -> list[tuple[str, str]]:
    return [(i["severity"], i["area"]) for i in issues]


def test_a_complete_store_page_has_one_finding() -> None:
    issues = checks.store_issues(item(), LOC, list(range(10)), 0, {"byte_quota": 0}, {"1": {"config": {}}}, {})
    assert areas(issues) == [("inconsistent", "system requirements")]  # recommended memory below the minimum


def test_store_page_problems() -> None:
    page = item(
        platforms={"win": "1", "mac": "1"},
        classification={"primary_genre": "0", "category": {"category_38": "true", "category_22": "true"}},
    )
    page["content"]["sysreqs"]["windows"]["min"].pop("processor")
    page["assets"].pop("library_hero")
    page["assets"]["screenshots"] = page["assets"]["screenshots"][:3]
    loc = {"languages": {"english": {checks.SHORT: "x" * 301}}}
    values = {"store": {"short_description": "Something else."}}
    issues = checks.store_issues(
        page, loc, [1, 2], 0, {"byte_quota": 100}, {"1": {"config": {"oslist": "windows"}}}, values
    )
    found = {(i["severity"], i["area"], i["what"]) for i in issues}
    assert ("missing", "system requirements", "windows minimum requirements lack: processor.") in found
    assert (
        "missing",
        "system requirements",
        "macos minimum requirements lack: OS, processor, memory, graphics, storage.",
    ) in found
    assert ("inconsistent", "short description", "301 characters; Steam allows 300.") in found
    assert ("missing", "about this game", "'About This Game' is empty.") in found
    assert ("inconsistent", "platforms", "macos is ticked on the store page but no depot is for macos.") in found
    assert ("missing", "genre", "No primary genre.") in found
    assert ("inconsistent", "categories", "Online Co-op ticked without Multi-player.") in found
    assert ("inconsistent", "categories", "Online Co-op ticked without Co-op.") in found
    assert ("inconsistent", "categories", "Steam Achievements is ticked but the app has no achievements.") in found
    assert ("inconsistent", "categories", "Steam Cloud is set up but its category is not ticked.") in found
    assert ("missing", "images", "Empty image slots: library_hero.") in found
    assert ("missing", "screenshots", "3 screenshot(s); Steam wants at least 5.") in found
    assert ("check", "tags", "2 developer tag(s).") in found
    assert (
        "check",
        "languages",
        "Supported but the store page is not translated: turkish (players see english).",
    ) in found
    assert any(i["area"] == "short description" and "steamworks.yaml" in i["what"] for i in issues)


def setup(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    build = tmp_path / "Builds" / "Windows"
    build.mkdir(parents=True)
    exe = bytearray(pe("KERNEL32.dll", "VCRUNTIME140.dll"))
    exe[exe.index(b"PE\0\0") + 4 : exe.index(b"PE\0\0") + 6] = (0x14C).to_bytes(2, "little")  # a 32-bit program
    (build / "Game.exe").write_bytes(bytes(exe))
    (build / "steam_appid.txt").write_text("480")
    values = {
        "apps": {"main": {"builds": {"depots": [{"name": "w", "os": "windows", "content_root": "Builds/Windows"}]}}}
    }
    return values, tmp_path


def test_build_problems(tmp_path: Path) -> None:
    values, root = setup(tmp_path)
    installation = {
        "install_folder": "",
        "launch_options": [
            {"index": 0, "executable": "Game.exe", "os": "windows", "arch": "64"},
            {"index": 1, "executable": "./Missing.exe", "os": "windows", "arch": ""},
        ],
    }
    achievements = [
        {"api_name": "WIN", "display_name": {"english": "Win"}, "description": {"english": "Win."}, "icon": "a"}
    ]
    issues = checks.build_issues(
        ["windows", "linux"],
        {"1": {"config": {"oslist": "windows"}}},
        installation,
        {"selected": [], "none_needed": True},
        {"byte_quota": 100, "roots": []},
        achievements,
        {"builds": {}},
        ["depots"],
        values,
        root,
    )
    found = {(i["severity"], i["area"], i["what"]) for i in issues}
    assert ("missing", "depots", "No depot is for linux, which the store page lists.") in found
    assert ("missing", "builds", "No build was uploaded yet.") in found
    assert ("inconsistent", "launch options", "#0 is set to 64-bit but 'Game.exe' is 32-bit.") in found
    assert ("inconsistent", "launch options", "#1 './Missing.exe' starts with a slash or dot.") in found
    assert ("inconsistent", "launch options", "#1 './Missing.exe' is not in the windows build (Windows).") in found
    assert ("missing", "install folder", "No install folder.") in found
    assert any(i["area"] == "redistributables" and "vc2022" in i["what"] for i in issues)
    assert ("check", "build", "No steam_api DLL in Windows, though the game uses Steam features.") in found
    assert ("check", "build", "steam_appid.txt is in the build folder.") in found
    assert ("check", "steam cloud", "Steam Cloud has a quota but no Auto-Cloud path.") in found
    assert ("missing", "achievements", "Win: missing icon(s).") in found
    assert ("check", "publishing", "Unpublished changes in: depots.") in found


def test_a_complete_build_has_no_findings(tmp_path: Path) -> None:
    values, root = setup(tmp_path)
    (root / "Builds" / "Windows" / "steam_appid.txt").unlink()
    (root / "Builds" / "Windows" / "steam_api.dll").write_bytes(b"")
    issues = checks.build_issues(
        ["windows"],
        {"1": {"config": {"oslist": "windows"}}},
        {
            "install_folder": "Game",
            "launch_options": [{"index": 0, "executable": "Game.exe", "os": "windows", "arch": "32"}],
        },
        {"selected": ["vc2022"], "none_needed": False},
        {"byte_quota": 0, "roots": []},
        [],
        None,
        [],
        values,
        root,
    )
    assert issues == []
