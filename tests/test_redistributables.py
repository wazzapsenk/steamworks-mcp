"""Steam's common redistributables: detection from binaries and engine, the Redistributables page, the write plan."""

from __future__ import annotations

import html
import json
import struct
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from steamworks_mcp.execute import guard, importer, redistributables
from steamworks_mcp.execute.browser.transport import Response
from steamworks_mcp.generate import deterministic
from steamworks_mcp.manifest.models import Installation
from steamworks_mcp.scanners.redist import DEPOTS, detect, pe_imports

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def pe(*imports: str) -> bytes:
    """A minimal 64-bit PE file with one section holding an import table for ``imports``."""
    names = b"".join(n.encode() + b"\0" for n in imports)
    desc_size = 20 * (len(imports) + 1)
    section = bytearray(desc_size + len(names))
    at = desc_size
    for i, n in enumerate(imports):
        struct.pack_into("<IIIII", section, 20 * i, 0, 0, 0, 0x1000 + at, 0)
        at += len(n) + 1
    section[desc_size:] = names
    opt = bytearray(240)
    struct.pack_into("<H", opt, 0, 0x20B)
    struct.pack_into("<II", opt, 112 + 8, 0x1000, desc_size)  # data directory 1: imports
    header = b"PE\0\0" + struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, len(opt), 0)
    table = b".rdata\0\0" + struct.pack("<IIIIIIHHI", len(section), 0x1000, len(section), 0x200, 0, 0, 0, 0, 0)
    head = bytearray(b"MZ" + bytes(0x3A) + struct.pack("<I", 0x40)) + header + opt + table
    return bytes(head.ljust(0x200, b"\0")) + bytes(section)


def test_pe_imports() -> None:
    assert pe_imports(pe("KERNEL32.dll", "VCRUNTIME140.dll")) == ["kernel32.dll", "vcruntime140.dll"]
    assert pe_imports(b"not a binary") is None


def test_detect(tmp_path: Path) -> None:
    plugins = tmp_path / "Assets" / "Plugins" / "x86_64"
    plugins.mkdir(parents=True)
    (plugins / "native.dll").write_bytes(pe("KERNEL32.dll", "MSVCP140.dll", "XAudio2_7.dll"))
    found = detect(tmp_path, "unity")
    assert found.value == ["directx", "vc2022"]
    assert found.evidence[0].file == "Assets/Plugins/x86_64/native.dll"

    (plugins / "msvcp140.dll").write_bytes(b"shipped next to the plugin")  # app-local runtime: no installer needed
    assert detect(tmp_path, "unity").value == ["directx"]
    (plugins / "native.dll").unlink()
    assert detect(tmp_path, "unity").value == ["none"]
    assert detect(tmp_path, "godot").value == ["none"]
    assert detect(tmp_path, "unreal").value == ["directx", "vc2022"]  # the Unreal player needs both


def test_detect_reads_the_windows_build(tmp_path: Path) -> None:
    build = tmp_path / "Builds" / "Windows"
    build.mkdir(parents=True)
    (build / "Game.exe").write_bytes(pe("KERNEL32.dll", "MSVCR120.dll", "OpenAL32.dll"))
    values: dict[str, Any] = {"game": {"engine": {"name": "unity"}}, "store": {"platforms": ["windows"]}}
    [finding] = deterministic.redistributables(values, tmp_path)
    assert finding.value == ["openal", "vc2013"] and finding.confidence > 0.8
    mac_only = {**values, "store": {"platforms": ["macos"]}}
    assert deterministic.redistributables(mac_only, tmp_path)[0].value == ["none"]


def test_model_keeps_none_alone() -> None:
    assert Installation(redistributables=["none"]).redistributables == ["none"]
    with pytest.raises(ValidationError):
        Installation(redistributables=["none", "vc2022"])
    with pytest.raises(ValidationError):
        Installation(redistributables=["vc2099"])  # type: ignore[list-item]


def page(checked: list[Any], none_needed: bool = False) -> str:
    props = {
        "rgCommonRedistAllPlatforms": [],
        "rgCommonRedistWindows": [
            ["DirectX", {"category": "directx", "components": {"June 2010": 228990}}],
            ["Visual C++ Redist", {"category": "vc", "components": {"2022": 228989, "2017 (deprecated)": 228987}}],
        ],
        "rgCheckedDepots": [[d, True] for d in checked],
        "nDetectedGameEngine": 0,
        "bNoCommonRedistsNeeded": none_needed,
    }
    attr = html.escape(json.dumps(props), quote=True)
    return f'<div data-featuretarget="storeadmin-steamworksredist-edit" data-props="{attr}"></div>'


class Steam:
    def __init__(self, text: str) -> None:
        self.text, self.posts = text, []  # type: ignore[var-annotated]

    async def get(self, path: str) -> Response:
        assert path == "/apps/installer/1000000"
        return Response(200, "https://partner.steamgames.com" + path, self.text)

    async def post(self, path: str, form: dict[str, str]) -> Response:
        guard.check("POST", "https://partner.steamgames.com" + path)
        self.posts.append((path, form))
        return Response(200, path, '{"success": true, "message": "saved"}')


async def test_read_and_plan() -> None:
    steam = Steam(page([1000001, 228990]))  # the app's own depot is listed too and ignored
    current = await redistributables.read_section(steam, 1000000)  # type: ignore[arg-type]
    assert current == {
        "offered": {"directx": 228990, "vc2022": 228989, "vc2017": 228987},
        "selected": ["directx"],
        "none_needed": False,
    }
    values = {"apps": {"main": {"installation": {"redistributables": ["vc2022"]}}}}
    ops = redistributables.plan(1000000, redistributables.desired(values, "main", current), current)
    assert [(o.action, o.after) for o in ops] == [("set", {"selected": ["vc2022"], "none_needed": False})]
    await ops[0].run(steam)  # type: ignore[arg-type]
    assert steam.posts == [
        ("/apps/setcommonredists/1000000", {"noneneeded": "0", "228990": "0", "228989": "1", "228987": "0"})
    ]

    none = {"apps": {"main": {"installation": {"redistributables": ["none"]}}}}
    [op] = redistributables.plan(1000000, redistributables.desired(none, "main", current), current)
    assert op.after == {"selected": [], "none_needed": True}
    unset = redistributables.desired({}, "main", current)
    assert redistributables.plan(1000000, unset, current) == []  # an empty field leaves Steamworks alone

    old = {"apps": {"main": {"installation": {"redistributables": ["vc2017"]}}}}
    ops = redistributables.plan(1000000, redistributables.desired(old, "main", current), current)
    assert ops[0].action == "skip" and "use vc2022" in ops[0].target


def test_import() -> None:
    assert importer.redistributables({"selected": [], "none_needed": True}, "main") == {
        "apps.main.installation.redistributables": ["none"]
    }
    assert importer.redistributables({"selected": [], "none_needed": False}, "main") == {}


def test_names_match_the_manifest() -> None:
    allowed = set(Installation.model_fields["redistributables"].annotation.__args__[0].__args__)  # type: ignore[union-attr]
    assert allowed == {"none", *DEPOTS}
