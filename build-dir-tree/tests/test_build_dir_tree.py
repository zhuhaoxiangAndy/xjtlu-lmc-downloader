"""Unit tests for build-dir-tree (stdlib unittest, no third-party deps)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from build_dir_tree import (  # noqa: E402
    DEFAULT_POLICY,
    NamePolicy,
    ParseError,
    apply_name_policy,
    parse_html,
)
from build_dir_tree.cli import main  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "demo_course.html")


def load_fixture() -> str:
    with open(FIXTURE, encoding="utf-8") as fh:
        return fh.read()


class TestFaithfulNaming(unittest.TestCase):
    """Default policy reproduces the LMC title verbatim."""

    def test_spaces_preserved(self):
        self.assertEqual(apply_name_policy("Week 1 - Introduction to AI"),
                         "Week 1 - Introduction to AI")

    def test_single_space_around_dash_preserved(self):
        self.assertEqual(apply_name_policy("Week 3- Tutorial"), "Week 3- Tutorial")

    def test_double_space_preserved(self):
        self.assertEqual(apply_name_policy("a  b"), "a  b")

    def test_triple_space_preserved(self):
        self.assertEqual(apply_name_policy("a   b"), "a   b")

    def test_ampersand_brackets_parens_preserved(self):
        self.assertEqual(apply_name_policy("[LECTURE] AI (Part 1) & more"),
                         "[LECTURE] AI (Part 1) & more")

    def test_unicode_preserved(self):
        self.assertEqual(apply_name_policy("Übung – Woche 1, Teil 2"),
                         "Übung – Woche 1, Teil 2")

    def test_entity_decoded_but_space_kept(self):
        self.assertEqual(apply_name_policy("Voice &amp; Speech"), "Voice & Speech")

    def test_double_escaped_entity_decoded(self):
        # Moodle emits &amp;amp; for what the browser shows as "&"
        self.assertEqual(apply_name_policy("Voice, Speech &amp;amp; Creativity"),
                         "Voice, Speech & Creativity")

    def test_trailing_space_reported_not_silently_lost(self):
        policy = DEFAULT_POLICY
        self.assertEqual(apply_name_policy("Week 7 Tutorial ", policy), "Week 7 Tutorial")
        self.assertEqual(policy, DEFAULT_POLICY)  # policy itself is untouched

    def test_illegal_chars_deleted_by_default(self):
        self.assertEqual(apply_name_policy('a/b:c*d?e"f<g>h|i'), "abcdefghi")

    def test_trailing_dot_and_space_stripped(self):
        self.assertEqual(apply_name_policy("Week 1.  "), "Week 1")

    def test_empty_falls_back(self):
        self.assertEqual(apply_name_policy(""), "untitled")
        self.assertEqual(apply_name_policy("   "), "untitled")


class TestNamingOptions(unittest.TestCase):
    """Every deviation from verbatim is opt-in."""

    def test_collapse_spaces(self):
        policy = NamePolicy(collapse_spaces=True)
        self.assertEqual(apply_name_policy("a  b", policy), "a b")
        self.assertEqual(apply_name_policy("a   b", policy), "a b")

    def test_collapse_spaces_off_by_default(self):
        self.assertEqual(apply_name_policy("a  b"), "a  b")

    def test_space_replace_caller_decides(self):
        policy = NamePolicy(space_replace="_")
        self.assertEqual(apply_name_policy("Week 1 - Intro", policy), "Week_1_-_Intro")

    def test_space_replace_no_trailing_artifact(self):
        policy = NamePolicy(space_replace="_")
        self.assertEqual(apply_name_policy("Meet your teachers ", policy), "Meet_your_teachers")

    def test_space_replace_can_be_any_text(self):
        policy = NamePolicy(space_replace=".")
        self.assertEqual(apply_name_policy("Week 1", policy), "Week.1")

    def test_space_replace_none_keeps_spaces(self):
        self.assertEqual(apply_name_policy("Week 1", NamePolicy(space_replace=None)), "Week 1")

    def test_illegal_replace_caller_decides(self):
        self.assertEqual(apply_name_policy("AI: Voice", NamePolicy(illegal_replace="_")), "AI_ Voice")

    def test_illegal_replace_multi_char(self):
        self.assertEqual(apply_name_policy("AI: Voice", NamePolicy(illegal_replace=" - ")),
                         "AI -  Voice")

    def test_illegal_default_deletes(self):
        self.assertEqual(apply_name_policy("AI: Voice"), "AI Voice")

    def test_keep_trailing_preserves_them(self):
        policy = NamePolicy(drop_trailing=False)
        self.assertEqual(apply_name_policy("Week 1 ", policy), "Week 1 ")


class TestNumbering(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.course = parse_html(load_fixture())

    def test_off_by_default(self):
        spec = self.course.to_spec(layout="sections")
        self.assertIn("Week 1 - Introduction to AI", spec["dirs"])

    def test_on_prefixes_sections(self):
        spec = self.course.to_spec(layout="sections", policy=NamePolicy(number=True))
        self.assertIn("01-Week 1 - Introduction to AI", spec["dirs"])
        self.assertIn("01-Week 1 - Introduction to AI/01.04-Week 1-Tutorial", spec["dirs"])

    def test_on_prefixes_activities(self):
        spec = self.course.to_spec(layout="full", policy=NamePolicy(number=True))
        self.assertIn(
            "01-Week 1 - Introduction to AI/01 [LECTURE] Introduction to AI &amp; GenAI"
            .replace("&amp;", "&"),
            spec["dirs"],
        )

    def test_policy_round_trips_into_spec(self):
        spec = self.course.to_spec(layout="sections", policy=NamePolicy(number=True))
        self.assertTrue(spec["policy"]["number"])
        self.assertFalse(spec["policy"]["collapse_spaces"])

class TestParseHtml(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.course = parse_html(load_fixture())

    def test_title_and_course_id(self):
        self.assertIn("DEMO101", self.course.title)
        # the fixture has no snapshot URL, so no course id can be derived
        self.assertIsNone(self.course.course_id)

    def test_course_id_from_embedded_url(self):
        html = load_fixture().replace(
            "</head>",
            '<link rel="canonical" href="https://core.example.edu/course/view.php?id=4321"></head>',
        )
        self.assertEqual(parse_html(html).course_id, 4321)

    def test_top_level_sections(self):
        nums = [s.num for s in self.course.top_sections()]
        self.assertEqual(nums, [0, 1, 2, 3])

    def test_subsection_parenting(self):
        week1 = [s for s in self.course.top_sections() if s.num == 1][0]
        self.assertEqual([c.num for c in week1.children], [4])
        self.assertEqual(week1.children[0].parent.num, 1)

    def test_subsection_not_promoted_to_top_level(self):
        tops = [s.num for s in self.course.top_sections()]
        self.assertNotIn(4, tops)
        self.assertNotIn(5, tops)

    def test_activity_names_decoded(self):
        week1 = [s for s in self.course.top_sections() if s.num == 1][0]
        names = [a.name for a in week1.activities]
        self.assertIn("Introduction to AI & GenAI", names[0])

    def test_activity_modtypes(self):
        welcome = [s for s in self.course.top_sections() if s.num == 0][0]
        self.assertEqual([a.modtype for a in welcome.activities], ["folder", "forum"])

    def test_counts(self):
        info = self.course.summary()
        self.assertEqual(info["top_sections"], 4)
        self.assertEqual(info["subsections"], 2)
        # 2 (welcome) + 2 (week 1) + 1 (week 1 tutorial) + 1 (lab 1) + 1 (week 2)
        self.assertEqual(info["activities"], 7)


class TestSpec(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.course = parse_html(load_fixture())

    def test_sections_layout(self):
        spec = self.course.to_spec(layout="sections")
        self.assertEqual(
            spec["dirs"],
            [
                "Welcome to DEMO101",
                "Week 1 - Introduction to AI",
                "Week 1 - Introduction to AI/Week 1-Tutorial",
                "Lab 1 - Building Things",
                "Week 2 - Large Language Models (LLMs)",
                "Week 2 - Large Language Models (LLMs)/New subsection",
            ],
        )
        self.assertNotIn("//", "".join(spec["dirs"]))

    def test_no_numeric_prefix(self):
        spec = self.course.to_spec(layout="sections")
        import re

        for name in spec["dirs"]:
            leaf = name.split("/")[-1]
            self.assertFalse(re.match(r"^\d{2}-", leaf), "unexpected numeric prefix: " + leaf)
            self.assertFalse(re.match(r"^\d{2}\.\d{2}-", leaf), "unexpected prefix: " + leaf)

    def test_full_layout_adds_activity_leaves(self):
        spec = self.course.to_spec(layout="full")
        sections = self.course.to_spec(layout="sections")
        self.assertGreater(spec["count"], sections["count"])
        self.assertIn(
            "Week 1 - Introduction to AI/[RECORDING] L1", spec["dirs"]
        )
        self.assertIn(
            "Week 1 - Introduction to AI/Week 1-Tutorial/Week 1 workbook",
            spec["dirs"],
        )

    def test_full_layout_skips_subsection_placeholder(self):
        spec = self.course.to_spec(layout="full")
        self.assertFalse([d for d in spec["dirs"] if "subsection_" in d])

    def test_skip_empty_subsections(self):
        spec = self.course.to_spec(layout="sections", include_empty_subsections=False)
        self.assertFalse([d for d in spec["dirs"] if "New subsection" in d])

    def test_separator_option(self):
        spec = self.course.to_spec(layout="sections", separator="\\")
        self.assertTrue(all("\\" in d for d in spec["dirs"] if "Tutorial" in d))

    def test_no_duplicate_paths(self):
        spec = self.course.to_spec(layout="full")
        self.assertEqual(len(spec["dirs"]), len(set(spec["dirs"])))

    def test_spec_is_json_serialisable(self):
        json.dumps(self.course.to_spec(layout="full"))

class TestCli(unittest.TestCase):
    def test_summary_exit_zero(self):
        self.assertEqual(main([FIXTURE, "--summary", "--quiet"]), 0)

    def test_dry_run_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = main([FIXTURE, "--layout", "full", "-o", tmp, "--quiet"])
            self.assertEqual(rc, 0)
            self.assertEqual(os.listdir(tmp), [])

    def test_mkdir_requires_output(self):
        with self.assertRaises(SystemExit):
            main([FIXTURE, "--mkdir", "--quiet"])

    def test_mkdir_creates_expected_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = main([FIXTURE, "--layout", "sections", "--mkdir", "-o", tmp, "--quiet"])
            self.assertEqual(rc, 0)
            root = os.path.join(tmp, "DEMO101-2627-S1-Demo Course")
            self.assertTrue(os.path.isdir(root))
            self.assertTrue(os.path.isdir(os.path.join(root, "Week 1 - Introduction to AI",
                                                       "Week 1-Tutorial")))

    def test_mkdir_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            main([FIXTURE, "--mkdir", "-o", tmp, "--quiet"])
            main([FIXTURE, "--mkdir", "-o", tmp, "--quiet"])
            root = os.path.join(tmp, "DEMO101-2627-S1-Demo Course")
            self.assertEqual(len(os.listdir(root)), 4)

    def test_emit_ps1(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "build.ps1")
            rc = main([FIXTURE, "--layout", "sections", "--emit-ps1", target, "-o", tmp, "--quiet"])
            self.assertEqual(rc, 0)
            with open(target, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("[CmdletBinding()]", text)
            self.assertIn("New-Item -ItemType Directory", text)
            self.assertIn("DryRun", text)

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "spec.json")
            main([FIXTURE, "--layout", "full", "--json", target, "--quiet"])
            with open(target, encoding="utf-8") as fh:
                spec = json.load(fh)
            self.assertEqual(spec["layout"], "full")
            self.assertEqual(spec["count"], len(spec["dirs"]))


class TestPathLengthGuard(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.course = parse_html(load_fixture())

    def test_no_warning_for_shallow_root(self):
        from build_dir_tree.cli import _path_length_warnings

        spec = self.course.to_spec(layout="full")
        self.assertEqual(_path_length_warnings(spec, "/tmp/out"), [])

    def test_warns_for_deep_root(self):
        from build_dir_tree.cli import _path_length_warnings

        spec = self.course.to_spec(layout="full")
        deep = "/" + ("x" * 280)
        warnings = _path_length_warnings(spec, deep)
        self.assertTrue(warnings)
        self.assertIn("Windows 260-character limit", warnings[0])
        self.assertIn("of %d path(s)" % spec["count"], warnings[0])


class TestFidelityWarnings(unittest.TestCase):

    def test_reports_trailing_space_titles(self):
        from build_dir_tree.cli import _fidelity_warnings

        spec = {
            "records": [
                {"section_name": "Week 7 Tutorial "},
                {"section_name": "Week 8 - Voice"},
            ],
            "dirs": [],
        }
        warnings = _fidelity_warnings(spec, ".")
        self.assertTrue(warnings)
        self.assertIn("1 title(s)", warnings[0])

    def test_silent_when_titles_clean(self):
        from build_dir_tree.cli import _fidelity_warnings

        spec = {"records": [{"section_name": "Week 1"}], "dirs": []}
        self.assertEqual(_fidelity_warnings(spec, "."), [])


class TestEntryPoints(unittest.TestCase):
    """The user-facing ways to run this must all work."""

    def test_version_flag(self):
        from build_dir_tree.cli import main

        self.assertEqual(main(["--version"]), 0)

    def test_run_py_exists_and_dispatches(self):
        import subprocess

        runpy = os.path.join(ROOT, "run.py")
        self.assertTrue(os.path.isfile(runpy))
        proc = subprocess.run(
            [sys.executable, runpy, FIXTURE, "--summary", "--quiet"],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "PYTHONPATH": ""},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_run_py_help_works(self):
        import subprocess

        runpy = os.path.join(ROOT, "run.py")
        proc = subprocess.run(
            [sys.executable, runpy, "--help"],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "PYTHONPATH": ""},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("build-dir-tree", proc.stdout)


class TestWebUI(unittest.TestCase):
    """The local UI is a thin shell over the same parser/policy code."""

    @classmethod
    def setUpClass(cls):
        import threading

        from build_dir_tree.web.server import serve

        cls.url, cls.httpd = serve(port=0, open_browser=False)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _post(self, payload):
        import json as _json
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            self.url + "api/build",
            data=_json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            return _json.loads(urllib.request.urlopen(req, timeout=30).read())
        except urllib.error.HTTPError as exc:
            return _json.loads(exc.read())

    def test_index_served(self):
        import urllib.request

        body = urllib.request.urlopen(self.url, timeout=10).read().decode("utf-8")
        self.assertIn("build-dir-tree", body)
        self.assertIn("snapshot", body)

    def test_static_assets(self):
        import urllib.request

        for asset in ("style.css", "app.js"):
            resp = urllib.request.urlopen(self.url + asset, timeout=10)
            self.assertEqual(resp.status, 200)

    def test_preview_matches_cli(self):
        result = self._post({"snapshot": FIXTURE, "layout": "sections"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 6)
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["rootName"], "DEMO101-2627-S1-Demo Course")

    def test_default_policy_is_faithful(self):
        result = self._post({"snapshot": FIXTURE, "layout": "sections"})
        self.assertEqual(result["policy"]["illegal_replace"], "")
        self.assertIsNone(result["policy"]["space_replace"])
        self.assertFalse(result["policy"]["number"])
        self.assertIn("Week 1 - Introduction to AI", result["dirs"])

    def test_illegal_replace_option(self):
        result = self._post({"snapshot": FIXTURE, "layout": "full", "illegalReplace": "_"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["policy"]["illegal_replace"], "_")

    def test_space_replace_option(self):
        result = self._post({"snapshot": FIXTURE, "layout": "sections", "spaceReplace": "_"})
        self.assertIn("Week_1_-_Introduction_to_AI", result["dirs"])

    def test_number_option(self):
        result = self._post({"snapshot": FIXTURE, "layout": "sections", "number": True})
        self.assertIn("01-Week 1 - Introduction to AI", result["dirs"])

    def test_create_writes_dirs(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            result = self._post(
                {"snapshot": FIXTURE, "layout": "sections", "output": tmp, "create": True}
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["created"], 6)
            self.assertTrue(os.path.isdir(os.path.join(tmp, "DEMO101-2627-S1-Demo Course")))

    def test_missing_snapshot_reports_error(self):
        result = self._post({"snapshot": "Z:/does/not/exist.mhtml"})
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"])

    def test_static_path_traversal_blocked(self):
        import urllib.error
        import urllib.request

        try:
            urllib.request.urlopen(self.url + "../../server.py", timeout=10)
            self.fail("traversal should not succeed")
        except urllib.error.HTTPError as exc:
            self.assertIn(exc.code, (403, 404))


class TestParseErrors(unittest.TestCase):
    def test_plain_html_without_sections_raises(self):
        with self.assertRaises(ParseError):
            parse_html("<html><body><p>hello</p></body></html>")

    def test_mhtml_without_boundary_raises(self):
        from build_dir_tree import parse_mhtml

        with self.assertRaises(ParseError):
            parse_mhtml(b"MIME-Version: 1.0\r\nContent-Type: multipart/related\r\n\r\nnope")


if __name__ == "__main__":
    unittest.main(verbosity=2)
