"""Derive every store/library/icon image from one key art + one logo (or hand-made overrides).

Rules: images are cropped to fill around the focus point, never stretched; upscaling is reported; artwork is never
generated. Output goes to ``.steam-mcp/exports/images/`` with a preview page.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageEnhance, ImageOps

from steamworks_mcp.gates.engine import asset_specs
from steamworks_mcp.languages import is_api_code
from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.validate.rules_data import AssetSpec

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
ACHIEVEMENT_ICON = 256
ICO_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]


@dataclass
class ImageResult:
    id: str
    label: str
    status: str
    """generated | override | skipped"""
    file: str | None = None
    size: str | None = None
    notes: list[str] = field(default_factory=list)


def _open(path: Path) -> Image.Image:
    im = Image.open(path)
    im.load()
    return im.convert("RGBA")


def _fit(
    src: Image.Image, size: tuple[int, int], focus: tuple[float, float], notes: list[str], what: str
) -> Image.Image:
    w, h = size
    scale = max(w / src.width, h / src.height)
    if scale > 1.0:
        notes.append(f"{what} is {src.width}x{src.height}; upscaled {scale:.2f}x to fill {w}x{h}, it may look soft.")
    if abs(src.width / src.height - w / h) > 0.01:
        notes.append(f"{what} was cropped to {w}:{h} around the focus point.")
    return ImageOps.fit(src, size, method=Image.Resampling.LANCZOS, centering=focus)


def _contain(src: Image.Image, box: tuple[int, int], notes: list[str], what: str) -> Image.Image:
    scale = min(box[0] / src.width, box[1] / src.height)
    if scale > 1.0:
        notes.append(f"{what} is {src.width}x{src.height}; upscaled {scale:.2f}x, it may look soft.")
    return src.resize((max(1, round(src.width * scale)), max(1, round(src.height * scale))), Image.Resampling.LANCZOS)


def _paste_center(base: Image.Image, top: Image.Image) -> Image.Image:
    out = base.copy()
    out.alpha_composite(top, ((base.width - top.width) // 2, (base.height - top.height) // 2))
    return out


def _save(im: Image.Image, path: Path, fmt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jpg":
        flat = Image.new("RGB", im.size, (0, 0, 0))
        flat.paste(im, mask=im.getchannel("A"))
        flat.save(path, "JPEG", quality=92, optimize=True)
    else:
        im.save(path, "PNG", optimize=True)


def compose(
    spec: AssetSpec, key_art: Image.Image | None, logo: Image.Image | None, focus: tuple[float, float], notes: list[str]
) -> Image.Image | str:
    """The composed image, or why it cannot be made."""
    w, h = spec.width or 0, spec.height or 0
    if spec.composition == "logo_only":
        if logo is None:
            return "needs assets.logo"
        return _contain(logo, (w, h), notes, "Logo")
    if spec.composition == "icon":
        if logo is not None:
            inner = round(w * 0.82)
            base = (
                _fit(key_art, (w, h), focus, [], "Key art")
                if key_art is not None
                else Image.new("RGBA", (w, h), (0, 0, 0, 0))
            )
            return _paste_center(base, _contain(logo, (inner, inner), notes, "Logo"))
        if key_art is None:
            return "needs assets.logo (or assets.key_art)"
        return _fit(key_art, (w, h), focus, notes, "Key art")
    if key_art is None:
        return "needs assets.key_art"
    art = _fit(key_art, (w, h), focus, notes, "Key art")
    if spec.composition == "art_only":
        return art
    if logo is None:
        return "needs assets.logo (capsules must show the game's name)"
    box = (round(w * (spec.logo_scale or 0.6)), round(h * 0.7))
    return _paste_center(art, _contain(logo, box, notes, "Logo"))


def prepare_store_images(
    values: dict[str, Any], root: Path, out_dir: Path, only: list[str] | None = None
) -> list[ImageResult]:
    key_path = fp.get(values, "assets.key_art")
    logo_path = fp.get(values, "assets.logo")
    key_art = _open(root / key_path) if key_path and (root / key_path).is_file() else None
    logo = _open(root / logo_path) if logo_path and (root / logo_path).is_file() else None
    f = fp.get(values, "assets.key_art_focus") or {}
    focus = (float(f.get("x", 0.5)), float(f.get("y", 0.5)))
    overrides: dict[str, str] = fp.get(values, "assets.overrides") or {}
    results: list[ImageResult] = []
    for spec in asset_specs().values():
        if not spec.composition or (only and spec.id not in only):
            continue
        notes = [spec.notes] if spec.notes else []
        fmt = "jpg" if spec.formats == ["jpg"] else "png"
        target = out_dir / f"{spec.id}.{fmt}"
        if spec.id in overrides:
            src = root / overrides[spec.id]
            if not src.is_file():
                results.append(
                    ImageResult(
                        spec.id, spec.label, "skipped", notes=[f"Override not found: {overrides[spec.id]}", *notes]
                    )
                )
                continue
            im = _open(src)
            if spec.composition == "logo_only":
                made = _contain(im, (spec.width or im.width, spec.height or im.height), notes, "Override")
            else:
                made = _fit(im, (spec.width or im.width, spec.height or im.height), (0.5, 0.5), notes, "Override")
            status = "override"
        else:
            composed = compose(spec, key_art, logo, focus, notes)
            if isinstance(composed, str):
                results.append(ImageResult(spec.id, spec.label, "skipped", notes=[composed, *notes]))
                continue
            made, status = composed, "generated"
        _save(made, target, fmt)
        if spec.id == "shortcut_icon":
            made.save(target.with_suffix(".ico"), sizes=ICO_SIZES)
        results.append(
            ImageResult(
                spec.id, spec.label, status, target.relative_to(root).as_posix(), f"{made.width}x{made.height}", notes
            )
        )
    write_preview(results, root, out_dir)
    return results


def prepare_achievement_icons(values: dict[str, Any], root: Path, out_dir: Path) -> list[ImageResult]:
    """256x256 JPG icons; a darkened greyscale 'locked' icon when none is given."""
    results = []
    for a in values.get("achievements") or []:
        notes: list[str] = []
        if not a.get("icon"):
            results.append(ImageResult(a["id"], a["id"], "skipped", notes=["No icon set."]))
            continue
        src = root / a["icon"]
        if not src.is_file():
            results.append(ImageResult(a["id"], a["id"], "skipped", notes=[f"Not found: {a['icon']}"]))
            continue
        unlocked = _fit(_open(src), (ACHIEVEMENT_ICON, ACHIEVEMENT_ICON), (0.5, 0.5), notes, "Icon")
        _save(unlocked, out_dir / f"{a['id']}.jpg", "jpg")
        if a.get("icon_locked") and (root / a["icon_locked"]).is_file():
            locked = _fit(
                _open(root / a["icon_locked"]), (ACHIEVEMENT_ICON, ACHIEVEMENT_ICON), (0.5, 0.5), notes, "Locked icon"
            )
        else:
            grey = ImageOps.grayscale(unlocked.convert("RGB"))
            locked = ImageEnhance.Brightness(grey).enhance(0.55).convert("RGBA")
            notes.append("Locked icon generated (greyscale, darkened).")
        _save(locked, out_dir / f"{a['id']}_locked.jpg", "jpg")
        results.append(
            ImageResult(
                a["id"],
                a["id"],
                "generated",
                (out_dir / f"{a['id']}.jpg").relative_to(root).as_posix(),
                "256x256",
                notes,
            )
        )
    return results


def screenshot_report(values: dict[str, Any], root: Path, min_size: tuple[int, int] = (1920, 1080)) -> dict[str, Any]:
    """Base screenshots and localized variants (``shot1_japanese.png``), with problems per file."""
    folder = root / str(fp.get(values, "assets.screenshots_dir") or "store/screenshots")
    if not folder.is_dir():
        return {"folder": folder.name, "count": 0, "screenshots": [], "problems": [f"Folder not found: {folder.name}"]}
    shots, base = [], 0
    for p in sorted(x for x in folder.iterdir() if x.suffix.lower() in IMAGE_EXT):
        with Image.open(p) as im:
            w, h = im.size
        problems = []
        if w < min_size[0] or h < min_size[1]:
            problems.append(f"below {min_size[0]}x{min_size[1]}")
        if abs(w / h - 16 / 9) > 0.02:
            problems.append("not 16:9")
        suffix = p.stem.rsplit("_", 1)[-1] if "_" in p.stem else ""
        language = suffix if is_api_code(suffix) else None
        if language is None:
            base += 1
        shots.append({"file": p.name, "size": f"{w}x{h}", "language": language, "problems": problems})
    return {
        "folder": folder.name,
        "count": base,
        "screenshots": shots,
        "problems": [] if base >= 5 else [f"{base} of at least 5"],
    }


SAFE_BOX = (
    f"left:{(3840 - 860) / 2 / 3840 * 100:.3f}%;top:{(1240 - 380) / 2 / 1240 * 100:.3f}%;"
    f"width:{860 / 3840 * 100:.3f}%;height:{380 / 1240 * 100:.3f}%"
)
"""The library hero's centred 860x380 safe area, as CSS percentages of the 3840x1240 image."""


def write_preview(results: list[ImageResult], root: Path, out_dir: Path) -> Path:
    """A local HTML page to eyeball the images: the small capsule at its smallest size, the hero's safe area."""
    cards = []
    for r in results:
        if not r.file:
            cards.append(
                f"<div class=card><h3>{html.escape(r.label)}</h3>"
                f"<p class=skip>{html.escape('; '.join(r.notes))}</p></div>"
            )
            continue
        name = Path(r.file).name
        extra = ""
        if r.id == "small_capsule":
            extra = f'<p>At the smallest size Steam uses (120x45):</p><img src="{name}" width=120 height=45>'
        if r.id == "library_hero":
            extra = "<p>Dashed box: the centred 860x380 safe area (scaled).</p>"
            img = f'<div class=hero><img src="{name}"><span class=safe></span></div>'
        else:
            img = f'<img src="{name}" class=main>'
        notes = "".join(f"<li>{html.escape(n)}</li>" for n in r.notes)
        cards.append(
            f"<div class=card><h3>{html.escape(r.label)} <small>{r.size} · {r.status}</small></h3>"
            f"{img}{extra}<ul>{notes}</ul></div>"
        )
    page = f"""<!doctype html><meta charset=utf-8><title>Store images</title>
<style>body{{font:14px system-ui;background:#1b2838;color:#c7d5e0;margin:24px}}.card{{margin:0 0 28px}}
img.main{{max-width:100%;height:auto;border:1px solid #2a475e}}.skip{{color:#e88}}small{{color:#8f98a0}}
.hero{{position:relative;max-width:100%}}.hero img{{width:100%;height:auto;display:block}}
.safe{{position:absolute;{SAFE_BOX};outline:2px dashed #66c0f4}}</style>
<h1>Store images</h1>{"".join(cards)}"""
    path = out_dir / "preview.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path
