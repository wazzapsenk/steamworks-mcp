"""Field paths: dotted addresses of values in ``steamworks.yaml``.

* Model attributes and dict keys are path segments: ``store.short_description``, ``store.supported_languages.german``.
* Items of keyed lists (``x-key``) are addressed by their key: ``achievements.ACH_WIN.name``, ``stats.WINS.max``.
* Items of other lists by index: ``apps.main.installation.launch_options.0.executable``.
* Objects marked ``x-unit`` (and lists of plain values) are one field: ``store.system_requirements.windows.minimum``,
  ``store.tags``.

Patterns may use ``*`` for one segment (``achievements.*.icon``). Translations are addressed as
``localization.<language>.<source path>`` and are not part of the values file.
"""

from __future__ import annotations

import types
import typing
from collections.abc import Callable, Iterator, MutableMapping, MutableSequence
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from steamworks_mcp.manifest.models import Manifest

NodeKind = Literal["model", "unit", "keyed_list", "index_list", "dict", "leaf"]


class FieldPathError(ValueError):
    pass


@dataclass(frozen=True)
class Node:
    kind: NodeKind
    model: type[BaseModel] | None = None
    """Model of this node (``model``/``unit``) or of its items/values (lists, dicts)."""
    key: str | None = None
    """Key attribute of a ``keyed_list``."""
    item: Node | None = None
    """Shape of dict values / list items."""


def split(path: str) -> list[str]:
    parts = path.split(".")
    if not path or any(p == "" for p in parts):
        raise FieldPathError(f'"{path}" is not a valid field path')
    return parts


def _strip(annotation: Any) -> Any:
    """Drop ``Optional``/``Annotated`` wrappers."""
    while True:
        origin = get_origin(annotation)
        if origin is typing.Annotated:
            annotation = get_args(annotation)[0]
        elif origin in (Union, types.UnionType):
            args = [a for a in get_args(annotation) if a is not type(None)]
            if len(args) != 1:
                return annotation
            annotation = args[0]
        else:
            return annotation


def _is_model(tp: Any) -> bool:
    return isinstance(tp, type) and issubclass(tp, BaseModel)


def _is_unit(model: type[BaseModel]) -> bool:
    extra = model.model_config.get("json_schema_extra")
    return isinstance(extra, dict) and bool(extra.get("x-unit"))


def node_for(annotation: Any, key: str | None = None) -> Node:
    tp = _strip(annotation)
    if _is_model(tp):
        return Node("unit" if _is_unit(tp) else "model", model=tp)
    origin = get_origin(tp)
    if origin is list:
        (item_tp,) = get_args(tp)
        item = _strip(item_tp)
        if _is_model(item) and not _is_unit(item):
            return Node("keyed_list" if key else "index_list", model=item, key=key, item=node_for(item))
        return Node("leaf")
    if origin is dict:
        value = node_for(get_args(tp)[1])
        return Node("dict", model=value.model, item=value)
    return Node("leaf")


@cache
def children(model: type[BaseModel]) -> dict[str, Node]:
    out: dict[str, Node] = {}
    for name, info in model.model_fields.items():
        extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
        key = extra.get("x-key")
        ann = info.annotation
        out[name] = node_for(ann, key if isinstance(key, str) else None) if ann is not None else Node("leaf")
    return out


ROOT = Node("model", model=Manifest)


def resolve_node(path: str, root: Node = ROOT) -> Node:
    """Schema node a path points to; ``*`` and concrete keys/indexes are both accepted."""
    node = root
    for seg in split(path):
        if node.kind in ("model", "unit"):
            assert node.model is not None
            kids = children(node.model)
            if seg not in kids:
                raise FieldPathError(f'"{path}": "{seg}" is not a field of {node.model.__name__}')
            node = kids[seg]
        elif node.kind in ("keyed_list", "index_list", "dict"):
            if node.kind == "index_list" and seg != "*" and not seg.isdigit():
                raise FieldPathError(f'"{path}": "{seg}" is not a list index')
            assert node.item is not None
            node = node.item
        else:
            raise FieldPathError(f'"{path}": cannot go below "{seg}"')
    return node


def is_valid(path: str) -> bool:
    try:
        resolve_node(path)
    except FieldPathError:
        return False
    return True


def matches(pattern: str, path: str) -> bool:
    p, q = split(pattern), split(path)
    return len(p) == len(q) and all(a in ("*", b) for a, b in zip(p, q, strict=True))


def iter_fields(data: Any, node: Node = ROOT, prefix: str = "", skip: str | None = None) -> Iterator[tuple[str, Any]]:
    """Tracked fields of JSON-like manifest data (``Manifest.model_dump(mode="json")``), with their values.

    Leaves and units are yielded even when empty (``None``/``""``/``[]``), so missing values are visible. Optional
    sections that are absent (e.g. ``apps.demo``, ``store.system_requirements.macos``) yield nothing: whether they
    are needed is for the gate rules to say. The key attribute of keyed-list items is part of the path only.
    """
    join = (lambda s: f"{prefix}.{s}") if prefix else (lambda s: s)
    if node.kind in ("leaf", "unit"):
        yield prefix, data
        return
    if node.kind == "model":
        assert node.model is not None
        if data is None and prefix:
            return
        values = data if isinstance(data, dict) else {}
        for name, child in children(node.model).items():
            if name != skip:
                yield from iter_fields(values.get(name), child, join(name))
        return
    assert node.item is not None
    if node.kind == "dict":
        for k, v in (data or {}).items():
            yield from iter_fields(v, node.item, join(str(k)))
    elif node.kind == "keyed_list":
        assert node.key is not None
        for item in data or []:
            yield from iter_fields(item, node.item, join(str(item[node.key])), skip=node.key)
    else:
        for i, item in enumerate(data or []):
            yield from iter_fields(item, node.item, join(str(i)))


def get(data: Any, path: str) -> Any:
    """Value at ``path`` in JSON-like data (``None`` when any part is missing)."""
    node, cur = ROOT, data
    for seg in split(path):
        if cur is None:
            return None
        if node.kind in ("model", "unit"):
            assert node.model is not None
            node = children(node.model).get(seg) or _bad(path, seg)
            cur = cur.get(seg) if isinstance(cur, dict) else None
        elif node.kind == "dict":
            assert node.item is not None
            node, cur = node.item, (cur.get(seg) if isinstance(cur, dict) else None)
        elif node.kind == "keyed_list":
            assert node.item is not None and node.key is not None
            node, cur = node.item, next((x for x in cur if x.get(node.key) == seg), None)
        elif node.kind == "index_list":
            assert node.item is not None
            idx = int(seg) if seg.isdigit() else _bad(path, seg)
            node, cur = node.item, (cur[idx] if idx < len(cur) else None)
        else:
            _bad(path, seg)
    return cur


def _bad(path: str, seg: str) -> Any:
    raise FieldPathError(f'"{path}": unexpected segment "{seg}"')


def set_in(
    data: MutableMapping[str, Any],
    path: str,
    value: Any,
    new_map: Callable[[], MutableMapping[str, Any]] = dict,
) -> None:
    """Set ``path`` in raw (YAML) data in place, creating parents; ``value=None`` removes the entry.

    Works on ruamel's round-trip containers, so comments elsewhere in the file survive. ``new_map`` builds the
    containers that have to be created (pass ruamel's ``CommentedMap`` to keep block style).
    """
    segs = split(path)
    node: Node = ROOT
    cur: Any = data
    for i, seg in enumerate(segs):
        last = i == len(segs) - 1
        if node.kind in ("model", "unit", "dict"):
            if node.kind == "dict":
                assert node.item is not None
                child = node.item
            else:
                assert node.model is not None
                child = children(node.model).get(seg) or _bad(path, seg)
            if last:
                if value is None:
                    cur.pop(seg, None)
                else:
                    cur[seg] = value
                return
            if cur.get(seg) is None:
                cur[seg] = [] if child.kind in ("keyed_list", "index_list") else new_map()
            node, cur = child, cur[seg]
        elif node.kind == "keyed_list":
            assert node.item is not None and node.key is not None
            items: MutableSequence[Any] = cur
            found = next((x for x in items if x.get(node.key) == seg), None)
            if last:
                if value is None:
                    if found is not None:
                        items.remove(found)
                    return
                if not isinstance(value, dict):
                    raise FieldPathError(f'"{path}": a list item must be an object')
                if found is None:
                    m = new_map()
                    m[node.key] = seg
                    items.append(m)
                    found = m
                for k, v in value.items():
                    if k != node.key:
                        found[k] = v
                return
            if found is None:
                found = new_map()
                found[node.key] = seg
                items.append(found)
            node, cur = node.item, found
        elif node.kind == "index_list":
            assert node.item is not None
            if not seg.isdigit():
                _bad(path, seg)
            idx, seq = int(seg), cur
            if idx > len(seq):
                raise FieldPathError(f'"{path}": index {idx} skips items (list has {len(seq)})')
            if last:
                if value is None:
                    if idx < len(seq):
                        del seq[idx]
                elif idx == len(seq):
                    seq.append(value)
                else:
                    seq[idx] = value
                return
            if idx == len(seq):
                seq.append(new_map())
            node, cur = node.item, seq[idx]
        else:
            _bad(path, seg)
