"""Shared paths/setup for tests."""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# Add script dirs to sys.path so test files can import directly
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "redis"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "chain"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "audit"))

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
FIXTURE_DB = os.path.join(FIXTURE_DIR, "codegraph-fake.db")


def fixture_db_path() -> str:
    return FIXTURE_DB
