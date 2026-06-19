"""
Regression + feature tests for merge_knowledge_from_memurai.

Bug: suffix whitelist in existing dict silently skips new knowledge categories.
Fix: prefix match on groupId:knowledge:*, auto-init new categories.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "audit"))
sys.path.insert(0, str(ROOT / "scripts" / "redis"))

import pytest

from self_evolution import merge_knowledge_from_memurai


class FakeMemurai:
    """Minimal fake memurai client for testing."""

    def __init__(self, keys_and_values: dict):
        self._store = keys_and_values

    def scan(self, pattern: str):
        # simple glob: only return keys that match the pattern
        # pattern is like "mygroup:knowledge:*"
        prefix = pattern.rstrip("*")
        return [k for k in self._store if k.startswith(prefix)]

    def get(self, key: str):
        return self._store.get(key)


@pytest.fixture
def loop_dir(tmp_path):
    """Create a loop_audit dir with an initial knowledge.json."""
    d = tmp_path / "loop_audit"
    d.mkdir()
    initial = {
        "annotations": ["ann1"],
        "sanitizers": ["san1"],
        "routes": ["route1"],
        "findings": ["find1"],
    }
    (d / "knowledge.json").write_text(
        json.dumps(initial, ensure_ascii=False), encoding="utf-8"
    )
    return d


# --- Regression: existing 4 categories still work ---
class TestRegression:
    def test_existing_suffixes_merged(self, loop_dir):
        client = FakeMemurai({
            "grp:knowledge:annotations": json.dumps(["ann2", "ann3"]),
            "grp:knowledge:sanitizers": json.dumps(["san2"]),
            "grp:knowledge:routes": json.dumps(["route2"]),
            "grp:knowledge:findings": json.dumps(["find2"]),
        })
        count = merge_knowledge_from_memurai(loop_dir, client, "grp")
        assert count == 5  # 2+1+1+1

        merged = json.loads((loop_dir / "knowledge.json").read_text(encoding="utf-8"))
        assert merged["annotations"] == ["ann1", "ann2", "ann3"]
        assert merged["sanitizers"] == ["san1", "san2"]
        assert merged["routes"] == ["route1", "route2"]
        assert merged["findings"] == ["find1", "find2"]

    def test_none_client_returns_zero(self, loop_dir):
        assert merge_knowledge_from_memurai(loop_dir, None, "grp") == 0

    def test_non_list_value_appended_as_dict(self, loop_dir):
        client = FakeMemurai({
            "grp:knowledge:annotations": json.dumps({"note": "single"}),
        })
        count = merge_knowledge_from_memurai(loop_dir, client, "grp")
        assert count == 1

        merged = json.loads((loop_dir / "knowledge.json").read_text(encoding="utf-8"))
        assert merged["annotations"] == ["ann1", {"note": "single"}]

    def test_no_matching_keys_returns_zero(self, loop_dir):
        client = FakeMemurai({"grp:other:x": "val"})
        count = merge_knowledge_from_memurai(loop_dir, client, "grp")
        assert count == 0

    def test_raw_string_value(self, loop_dir):
        client = FakeMemurai({
            "grp:knowledge:annotations": "just-a-string",
        })
        count = merge_knowledge_from_memurai(loop_dir, client, "grp")
        assert count == 1

        merged = json.loads((loop_dir / "knowledge.json").read_text(encoding="utf-8"))
        assert merged["annotations"] == ["ann1", {"raw": "just-a-string"}]


# --- Feature: new suffix auto-initialized, not skipped ---
class TestNewSuffixAcceptance:
    def test_new_suffix_auto_init_and_merged(self, loop_dir):
        """A knowledge key with an unregistered suffix should create a new category."""
        client = FakeMemurai({
            "grp:knowledge:dynamic_routes": json.dumps(["dr1", "dr2"]),
        })
        count = merge_knowledge_from_memurai(loop_dir, client, "grp")
        assert count == 2

        merged = json.loads((loop_dir / "knowledge.json").read_text(encoding="utf-8"))
        assert "dynamic_routes" in merged
        assert merged["dynamic_routes"] == ["dr1", "dr2"]
        # existing categories untouched
        assert merged["annotations"] == ["ann1"]

    def test_multiple_new_suffixes(self, loop_dir):
        client = FakeMemurai({
            "grp:knowledge:dynamic_routes": json.dumps(["dr1"]),
            "grp:knowledge:custom_sinks": json.dumps(["cs1", "cs2"]),
        })
        count = merge_knowledge_from_memurai(loop_dir, client, "grp")
        assert count == 3

        merged = json.loads((loop_dir / "knowledge.json").read_text(encoding="utf-8"))
        assert merged["dynamic_routes"] == ["dr1"]
        assert merged["custom_sinks"] == ["cs1", "cs2"]

    def test_new_suffix_dict_value(self, loop_dir):
        client = FakeMemurai({
            "grp:knowledge:patterns": json.dumps({"type": "xss", "count": 3}),
        })
        count = merge_knowledge_from_memurai(loop_dir, client, "grp")
        assert count == 1

        merged = json.loads((loop_dir / "knowledge.json").read_text(encoding="utf-8"))
        assert merged["patterns"] == [{"type": "xss", "count": 3}]
