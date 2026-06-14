"""TC-PI-001 ~ TC-PI-003: preset-init tests."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _loader import load_script  # noqa: E402

_mod = load_script("audit", "preset-init.py")
detect_from_pom = _mod.detect_from_pom
detect_frameworks = _mod.detect_frameworks

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "脚本", "audit", "preset-init.py")


class PresetInitTests(unittest.TestCase):
    def test_tc_pi_001_pom(self):
        """TC-PI-001: detect_from_pom 提 groupId/artifactId。"""
        with tempfile.TemporaryDirectory() as tmp:
            pom = os.path.join(tmp, "pom.xml")
            with open(pom, "w", encoding="utf-8") as f:
                f.write("<groupId>com.example.x</groupId><artifactId>foo</artifactId>")
            r = detect_from_pom(pom)
            self.assertEqual(r["groupId"], "com.example.x")
            self.assertEqual(r["artifactId"], "foo")

    def test_tc_pi_002_frameworks(self):
        """TC-PI-002: detect_frameworks 含 spring-boot。"""
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src")
            os.makedirs(src)
            with open(os.path.join(src, "A.java"), "w", encoding="utf-8") as f:
                f.write("import org.springframework.boot.SpringApplication;")
            self.assertIn("spring-boot", detect_frameworks(src))

    def test_tc_pi_003_draft_file(self):
        """TC-PI-003: CLI 执行后 projects/{groupId}/preset.json.draft 存在。"""
        with tempfile.TemporaryDirectory() as tmp:
            project = os.path.join(tmp, "proj")
            os.makedirs(project)
            with open(os.path.join(project, "pom.xml"), "w", encoding="utf-8") as f:
                f.write("<groupId>com.example.x</groupId><artifactId>foo</artifactId>")
            src = os.path.join(project, "src", "main", "java")
            os.makedirs(src)
            with open(os.path.join(src, "A.java"), "w", encoding="utf-8") as f:
                f.write("package com.example.x;\nclass A {}")
            # preset-init 写 "projects/{groupId}/preset.json.draft"（相对 CWD）
            # 用 chdir 隔离
            cwd_orig = os.getcwd()
            try:
                os.chdir(tmp)
                r = subprocess.run(
                    [sys.executable, SCRIPT_PATH, "--project-root", project,
                     "--group-id", "com.example.x"],
                    capture_output=True,
                )
                self.assertEqual(r.returncode, 0,
                                 f"stdout={r.stdout!r} stderr={r.stderr!r}")
                draft = os.path.join(tmp, "项目", "com.example.x", "preset.json.draft")
                self.assertTrue(os.path.exists(draft), f"missing: {draft}")
            finally:
                os.chdir(cwd_orig)


if __name__ == "__main__":
    unittest.main()
