"""Custom Python tool loading.

A custom tool is a ``.py`` module in the Documents ``tools/`` folder that exposes:

* ``NAME`` (str) — the tool name offered to the model,
* ``DESCRIPTION`` (str),
* ``PARAMETERS`` (dict) — a JSON-schema object for the arguments,
* ``run(**kwargs) -> str`` — the callable (may be sync or async).

Custom tools execute **arbitrary code**, so they require an explicit user trust
confirmation before they can be enabled (FR-014/FR-015). Execution is in-process with
a per-call timeout and exception capture (FR-018).
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class ToolModule:
    """A discovered custom tool module."""

    name: str
    description: str
    parameters: dict
    run: Callable[..., Any]
    source_path: str
    valid: bool = True
    error: str | None = None


def _load_module(path: Path):
    """Import a standalone ``.py`` file as a module object."""
    spec = importlib.util.spec_from_file_location(f"lmstudioclaw_tool_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load tool module {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_tool(path: Path) -> ToolModule:
    """Load and validate one custom tool module (invalid on any error)."""
    try:
        module = _load_module(path)
    except Exception as exc:  # pragma: no cover - depends on user code
        return ToolModule(path.stem, "", {}, lambda **_: None, str(path),
                          valid=False, error=f"Import failed: {exc}")
    name = getattr(module, "NAME", None) or path.stem
    description = getattr(module, "DESCRIPTION", "") or ""
    parameters = getattr(module, "PARAMETERS", None) or {"type": "object", "properties": {}}
    run = getattr(module, "run", None)
    if not callable(run):
        return ToolModule(name, description, parameters, lambda **_: None, str(path),
                          valid=False, error="Module has no callable 'run'")
    return ToolModule(name, description, parameters, run, str(path), valid=True)


def discover_tools(tools_dir: Path) -> list[ToolModule]:
    """Scan the tools folder; return one :class:`ToolModule` per ``.py`` file."""
    if not tools_dir.exists():
        return []
    out: list[ToolModule] = []
    for path in sorted(tools_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        out.append(load_tool(path))
    return out
