#!/usr/bin/env python3
"""Core logic tests for mctx-budget."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add parent dir to path so we can import the script
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import mctx_budget as mb


class TestReadJson(unittest.TestCase):
    def test_valid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"key": "value"}, f)
            f.flush()
            result = mb.read_json(Path(f.name))
        self.assertEqual(result, {"key": "value"})
        os.unlink(f.name)

    def test_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json {{{")
            f.flush()
            result = mb.read_json(Path(f.name))
        self.assertEqual(result, {})
        os.unlink(f.name)

    def test_missing_file(self):
        result = mb.read_json(Path("/tmp/nonexistent_mctx_test.json"))
        self.assertEqual(result, {})


class TestGetEffectiveState(unittest.TestCase):
    def test_local_overrides_global(self):
        plugins = ["a@market"]
        with patch.object(mb, "get_enabled_plugins") as mock:
            mock.side_effect = lambda p: {
                mb.GLOBAL_SETTINGS: {"a@market": True},
                mb.PROJECT_SETTINGS: {},
                mb.LOCAL_SETTINGS: {"a@market": False},
            }.get(p, {})
            result = mb.get_effective_state(plugins)
        self.assertFalse(result["a@market"])

    def test_project_overrides_global(self):
        plugins = ["a@market"]
        with patch.object(mb, "get_enabled_plugins") as mock:
            mock.side_effect = lambda p: {
                mb.GLOBAL_SETTINGS: {"a@market": False},
                mb.PROJECT_SETTINGS: {"a@market": True},
                mb.LOCAL_SETTINGS: {},
            }.get(p, {})
            result = mb.get_effective_state(plugins)
        self.assertTrue(result["a@market"])

    def test_default_is_true(self):
        """Fix #1A: installed plugins default to enabled."""
        plugins = ["unmentioned@market"]
        with patch.object(mb, "get_enabled_plugins", return_value={}):
            result = mb.get_effective_state(plugins)
        self.assertTrue(result["unmentioned@market"])


class TestParseSkillFrontmatter(unittest.TestCase):
    def test_valid_frontmatter(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("---\nname: my-skill\ndescription: A test skill\n---\n\nContent here")
            f.flush()
            result = mb.parse_skill_frontmatter(Path(f.name))
        self.assertEqual(result["name"], "my-skill")
        self.assertEqual(result["description"], "A test skill")
        os.unlink(f.name)

    def test_no_frontmatter(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Just a markdown file\nNo frontmatter here.")
            f.flush()
            result = mb.parse_skill_frontmatter(Path(f.name))
        self.assertEqual(result, {})
        os.unlink(f.name)

    def test_missing_file(self):
        result = mb.parse_skill_frontmatter(Path("/tmp/nonexistent_skill.md"))
        self.assertEqual(result, {})


class TestClassifyUserSkill(unittest.TestCase):
    def test_regular_dir(self):
        with tempfile.TemporaryDirectory() as d:
            skill_dir = Path(d) / "my-skill"
            skill_dir.mkdir()
            result = mb._classify_user_skill(skill_dir)
        self.assertEqual(result, "user:standalone")

    def test_gstack_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            # Create a target that has /gstack/ in its path
            gstack_dir = Path(d) / "gstack" / "my-skill"
            gstack_dir.mkdir(parents=True)
            link = Path(d) / "link-skill"
            link.symlink_to(gstack_dir)
            result = mb._classify_user_skill(link)
        self.assertEqual(result, "user:gstack")

    def test_path_segment_not_substring(self):
        """Fix #2A: 'my-gstack-fork' should NOT match as gstack."""
        with tempfile.TemporaryDirectory() as d:
            fork_dir = Path(d) / "my-gstack-fork" / "skill"
            fork_dir.mkdir(parents=True)
            link = Path(d) / "link-skill"
            link.symlink_to(fork_dir)
            result = mb._classify_user_skill(link)
        self.assertEqual(result, "user:standalone")

    def test_broken_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            link = Path(d) / "broken-link"
            link.symlink_to(Path(d) / "nonexistent")
            result = mb._classify_user_skill(link)
        self.assertEqual(result, "user:standalone")


class TestDetectProjectTags(unittest.TestCase):
    def test_marker_files(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "go.mod").touch()
            (Path(d) / "Dockerfile").touch()
            tags = mb.detect_project_tags(Path(d))
        self.assertIn("go", tags)
        self.assertIn("docker", tags)

    def test_extension_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            # 1 .py file = not enough (threshold is 2)
            (Path(d) / "one.py").touch()
            tags = mb.detect_project_tags(Path(d))
            self.assertNotIn("python", tags)
            # 2 .py files = enough
            (Path(d) / "two.py").touch()
            tags = mb.detect_project_tags(Path(d))
            self.assertIn("python", tags)


class TestWalkLimited(unittest.TestCase):
    def test_skips_hidden_dirs(self):
        """Fix #3A: all hidden dirs should be skipped."""
        with tempfile.TemporaryDirectory() as d:
            # Visible file
            (Path(d) / "visible.py").touch()
            # Hidden dir with a file inside
            hidden = Path(d) / ".hidden"
            hidden.mkdir()
            (hidden / "secret.py").touch()
            results = mb._walk_limited(Path(d), 3)
        names = [r.name for r in results]
        self.assertIn("visible.py", names)
        self.assertNotIn("secret.py", names)

    def test_respects_depth_limit(self):
        with tempfile.TemporaryDirectory() as d:
            deep = Path(d) / "a" / "b" / "c" / "d"
            deep.mkdir(parents=True)
            (Path(d) / "a" / "shallow.py").touch()
            (deep / "deep.py").touch()
            results = mb._walk_limited(Path(d), 2)
        names = [r.name for r in results]
        self.assertIn("shallow.py", names)
        self.assertNotIn("deep.py", names)


class TestMeasureSkillDir(unittest.TestCase):
    def test_skill_md_only(self):
        with tempfile.TemporaryDirectory() as d:
            skill_md = Path(d) / "SKILL.md"
            skill_md.write_text("---\nname: test\n---\nContent")
            size = mb._measure_skill_dir(Path(d))
        self.assertGreater(size, 0)

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            size = mb._measure_skill_dir(Path(d))
        self.assertEqual(size, 0)


if __name__ == "__main__":
    unittest.main()
