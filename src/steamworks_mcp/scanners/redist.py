"""Which of Steam's common redistributables a game needs (Steamworks > Installation > Redistributables).

Decided from the engine and from the Windows binaries themselves: the game's native plugins and, when a build exists,
the built game. A binary that imports ``VCRUNTIME140.dll`` needs the Visual C++ 2015-2022 runtime unless the build
ships that DLL next to itself; one that imports ``XAudio2_7.dll`` needs DirectX June 2010; and so on. Steam's own
checklist wants either the needed ones ticked or "none needed" confirmed, so an empty result is ``["none"]``.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable
from pathlib import Path

from steamworks_mcp.manifest.state import Evidence
from steamworks_mcp.scanners.base import SKIP_DIRS, Finding

FIELD = "apps.main.installation.redistributables"

DEPOTS = {
    "directx": 228990,
    "vc2022": 228989,
    "vc2019": 228988,
    "vc2017": 228987,
    "vc2015": 228986,
    "vc2013": 228985,
    "vc2012": 228984,
    "vc2010": 228983,
    "vc2008": 228982,
    "vc2005": 228981,
    "openal": 229020,
    "dotnet48": 229007,
    "dotnet472": 229006,
    "dotnet462": 229005,
    "dotnet452": 229004,
    "dotnet40_client": 229003,
    "dotnet40": 229002,
    "dotnet35_client": 229001,
    "dotnet35": 229000,
    "xna40": 229012,
    "xna31": 229011,
    "xna30": 229010,
    "physx_8_09_04": 229030,
    "physx_9_12_1031": 229031,
    "physx_9_13_1220": 229032,
    "physx_9_14_0702": 229033,
}
"""steamworks.yaml name -> the redistributable's depot id on Steam (the same for every app)."""
DEPRECATED = {"vc2017": "vc2022", "vc2015": "vc2022"}
"""Steamworks no longer lets these be ticked; the 2022 runtime covers them."""

IMPORTS = {
    **dict.fromkeys(
        ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "concrt140.dll"), "vc2022"
    ),
    **dict.fromkeys(("msvcr120.dll", "msvcp120.dll"), "vc2013"),
    **dict.fromkeys(("msvcr110.dll", "msvcp110.dll"), "vc2012"),
    **dict.fromkeys(("msvcr100.dll", "msvcp100.dll"), "vc2010"),
    **dict.fromkeys(("msvcr90.dll", "msvcp90.dll"), "vc2008"),
    **dict.fromkeys(("msvcr80.dll", "msvcp80.dll"), "vc2005"),
    **dict.fromkeys(
        (
            "d3dx9_43.dll",
            "d3dx10_43.dll",
            "d3dx11_43.dll",
            "d3dcompiler_43.dll",
            "xinput1_3.dll",
            "xaudio2_7.dll",
            "x3daudio1_7.dll",
            "xapofx1_5.dll",
        ),
        "directx",
    ),
    "openal32.dll": "openal",
}
"""Imported DLL (lower case) -> the redistributable that installs it."""
ENGINE_NEEDS = {"unreal": ("directx", "vc2022")}
"""What the engine's own player needs. Unity and Godot players need nothing (Steamworks itself flags Unreal)."""
PLUGIN_DIRS = {"unity": ("Assets", "Packages"), "godot": ("addons",), "unreal": ("Plugins",)}
MAX_BINARIES = 400


def pe_imports(data: bytes) -> list[str] | None:
    """The DLL names a Windows binary imports (lower case), or None if it is not a PE file. Managed .NET assemblies
    only import mscoree.dll."""
    try:
        if data[:2] != b"MZ":
            return None
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe : pe + 4] != b"PE\0\0":
            return None
        sections, opt_size = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
        opt = pe + 24
        magic = struct.unpack_from("<H", data, opt)[0]
        dirs = opt + (112 if magic == 0x20B else 96)
        import_rva = struct.unpack_from("<I", data, dirs + 8)[0]
        table = [struct.unpack_from("<IIII", data, opt + opt_size + 40 * i + 8) for i in range(sections)]

        def offset(rva: int) -> int:
            for vsize, vaddr, rsize, raw in table:
                if vaddr <= rva < vaddr + max(vsize, rsize):
                    return int(rva - vaddr + raw)
            raise ValueError(rva)

        names, at = [], offset(import_rva) if import_rva else -1
        while at >= 0:
            name_rva = struct.unpack_from("<I", data, at + 12)[0]
            if not name_rva:
                break
            start = offset(name_rva)
            names.append(data[start : data.index(b"\0", start)].decode("ascii", "replace").lower())
            at += 20
        return names
    except (struct.error, ValueError):
        return None


def binaries(folders: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for folder in folders:
        if not folder.is_dir():
            continue
        for p in sorted(folder.rglob("*")):
            if p.suffix.lower() in (".dll", ".exe") and not SKIP_DIRS & set(p.relative_to(folder).parts[:-1]):
                out.append(p)
                if len(out) >= MAX_BINARIES:
                    return out
    return out


def needs(files: Iterable[Path], root: Path) -> dict[str, list[Evidence]]:
    """Redistributable -> the binaries that need it. A DLL shipped in the binary's own folder needs no installer."""
    found: dict[str, list[Evidence]] = {}
    for f in files:
        imports = pe_imports(f.read_bytes())
        shipped = {p.name.lower() for p in f.parent.iterdir()}
        for dll in imports or []:
            if dll in IMPORTS and dll not in shipped:
                rel = f.relative_to(root).as_posix() if f.is_relative_to(root) else "/".join(f.parts[-3:])
                found.setdefault(IMPORTS[dll], []).append(Evidence(file=rel, note=f"imports {dll}"))
    return found


def detect(root: Path, engine: str | None, build_dirs: Iterable[Path] = ()) -> Finding:
    """The redistributables for steamworks.yaml, from the engine, the native plugins and the built game."""
    engine = (engine or "").lower()
    builds = [d for d in build_dirs if d.is_dir()]
    plugin_dirs = [root / d for d in PLUGIN_DIRS.get(engine, ())]
    found = needs(binaries([*plugin_dirs, *builds]), root)
    for name in ENGINE_NEEDS.get(engine, ()):
        found.setdefault(name, []).append(Evidence(file=".", note=f"the {engine} player needs it"))
    value = sorted(found) or ["none"]
    why = "; ".join(f"{k}: {v[0].file} {v[0].note}" for k, v in sorted(found.items()))
    if not engine and not builds:
        confidence, note = 0.3, "Engine unknown and no build found: check the game's requirements."
    elif builds:
        confidence, note = 0.85, why or "The built game imports nothing Steam has to install."
    else:
        confidence = 0.6
        note = (why or f"A {engine} game needs none by default.") + " Build the game once to confirm."
    evidence = [e for v in found.values() for e in v][:20]
    return Finding(FIELD, value, confidence, evidence, note=note)
