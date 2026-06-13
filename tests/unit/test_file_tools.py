"""Unit tests for the default file tools (US2): read range, edit modes, grep/find/ls/write."""

from __future__ import annotations

import pytest

from lmstudioclaw.capabilities import file_tools
from lmstudioclaw.consent.path_gate import PathGate


class _FakeStore:
    """Grant store stub with no grants (workspace is always allowed)."""

    def active_grants(self, session_id=None):  # noqa: ANN001
        return []


def _gate(paths) -> PathGate:
    return PathGate(paths, _FakeStore())


async def _yes(_path, _access):
    """Consent callback that always grants."""
    return True


pytestmark = pytest.mark.asyncio


@pytest.fixture()
def gate(temp_app_paths):
    return _gate(temp_app_paths)


async def test_write_then_read(gate, temp_app_paths):
    target = temp_app_paths.workspace / "sub" / "a.txt"
    res = await file_tools.write_file(gate, _yes, path=str(target), content="hello\nworld\n")
    assert res.ok and target.exists()
    read = await file_tools.read_file(gate, _yes, path=str(target))
    assert read.ok and "hello" in read.output


async def test_read_range(gate, temp_app_paths):
    target = temp_app_paths.workspace / "lines.txt"
    target.write_text("l1\nl2\nl3\nl4\nl5\n", encoding="utf-8")
    res = await file_tools.read_file(gate, _yes, path=str(target), start_line=2, end_line=4)
    assert res.ok and res.output == "l2\nl3\nl4"


async def test_edit_exact_unique(gate, temp_app_paths):
    target = temp_app_paths.workspace / "e.txt"
    target.write_text("alpha beta gamma\n", encoding="utf-8")
    res = await file_tools.edit(gate, _yes, path=str(target), old_string="beta", new_string="DELTA")
    assert res.ok
    assert target.read_text(encoding="utf-8") == "alpha DELTA gamma\n"


async def test_edit_exact_ambiguous_fails(gate, temp_app_paths):
    target = temp_app_paths.workspace / "e.txt"
    target.write_text("x x x\n", encoding="utf-8")
    res = await file_tools.edit(gate, _yes, path=str(target), old_string="x", new_string="y")
    assert not res.ok and "ambiguous" in res.error.lower()
    assert target.read_text(encoding="utf-8") == "x x x\n"  # unchanged


async def test_edit_exact_not_found_fails(gate, temp_app_paths):
    target = temp_app_paths.workspace / "e.txt"
    target.write_text("hello\n", encoding="utf-8")
    res = await file_tools.edit(gate, _yes, path=str(target), old_string="zzz", new_string="y")
    assert not res.ok and "not found" in res.error.lower()


async def test_edit_line_range(gate, temp_app_paths):
    target = temp_app_paths.workspace / "e.txt"
    target.write_text("a\nb\nc\nd\n", encoding="utf-8")
    res = await file_tools.edit(gate, _yes, path=str(target), start_line=2, end_line=3, new_content="B\nC\nX")
    assert res.ok
    assert target.read_text(encoding="utf-8") == "a\nB\nC\nX\nd\n"


async def test_edit_line_range_out_of_bounds_fails(gate, temp_app_paths):
    target = temp_app_paths.workspace / "e.txt"
    target.write_text("a\nb\n", encoding="utf-8")
    res = await file_tools.edit(gate, _yes, path=str(target), start_line=5, end_line=9, new_content="z")
    assert not res.ok and "out of bounds" in res.error.lower()
    assert target.read_text(encoding="utf-8") == "a\nb\n"  # unchanged


async def test_grep_and_find(gate, temp_app_paths):
    ws = temp_app_paths.workspace
    (ws / "one.py").write_text("import os\nNEEDLE = 1\n", encoding="utf-8")
    (ws / "two.txt").write_text("nothing here\n", encoding="utf-8")
    g = await file_tools.grep(gate, _yes, pattern="NEEDLE")
    assert g.ok and "one.py" in g.output
    f = await file_tools.find(gate, _yes, glob="*.py")
    assert f.ok and "one.py" in f.output


async def test_list_dir(gate, temp_app_paths):
    ws = temp_app_paths.workspace
    (ws / "f.txt").write_text("x", encoding="utf-8")
    (ws / "d").mkdir()
    res = await file_tools.list_dir(gate, _yes, path=str(ws))
    assert res.ok and "f.txt" in res.output and "d/" in res.output


async def test_denied_outside_workspace(gate, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret-ish", encoding="utf-8")

    async def _no(_p, _a):
        return False

    res = await file_tools.read_file(gate, _no, path=str(outside))
    assert not res.ok and "denied" in res.error.lower()
