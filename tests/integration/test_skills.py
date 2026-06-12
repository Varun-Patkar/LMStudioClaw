"""Integration test for skill discovery and use (SC-007).

A valid SKILL.md folder is discovered and (when enabled) injected into the registry;
a malformed skill is marked invalid and never offered. A referenced script is exposed
through the ``run_skill_script`` tool.
"""

from __future__ import annotations

from lmstudioclaw.capabilities.registry import CapabilityRegistry
from lmstudioclaw.capabilities.skills import discover_skills, load_skill
from lmstudioclaw.consent.path_gate import PathGate
from lmstudioclaw.sessions.store import Store


def _make_skill(skills_dir, name, content, script=None):
    folder = skills_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(content, encoding="utf-8")
    if script:
        (folder / script[0]).write_text(script[1], encoding="utf-8")
    return folder


def test_valid_skill_parsed(temp_app_paths):
    _make_skill(
        temp_app_paths.skills, "greeter",
        "---\nname: Greeter\ndescription: Greets people\n---\nSay hello nicely.",
        script=("hello.py", "print('hi')"),
    )
    skills = discover_skills(temp_app_paths.skills)
    assert len(skills) == 1
    s = skills[0]
    assert s.valid and s.name == "Greeter"
    assert s.description == "Greets people"
    assert "hello.py" in s.scripts


def test_malformed_skill_marked_invalid(temp_app_paths):
    folder = temp_app_paths.skills / "broken"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("", encoding="utf-8")  # empty -> invalid
    info = load_skill(folder)
    assert not info.valid and info.error


def test_registry_offers_enabled_skill_and_script(temp_app_paths):
    _make_skill(
        temp_app_paths.skills, "writer",
        "# Writer\nWrite great prose.",
        script=("run.py", "print('ok')"),
    )
    store = Store(temp_app_paths.db_path)
    registry = CapabilityRegistry(temp_app_paths, store, PathGate(temp_app_paths, store))

    # First discover registers the row as disabled by default -> not offered.
    registry.discover()
    assert registry.enabled_skills() == []

    # Enable it, rediscover -> now injected and the script runner is available.
    cap = store.list_capabilities(kind="skill")[0]
    store.update_capability(cap["id"], enabled=True)
    registry.discover()
    names = [s.name for s in registry.enabled_skills()]
    assert "Writer" in names
    tool_names = [t.name for t in registry.enabled_tools()]
    assert "run_skill_script" in tool_names
