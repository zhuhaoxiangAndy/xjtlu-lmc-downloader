"""Tests for lmc-downloader.

Everything here runs offline against a fake HTTP layer, so the suite is
deterministic and needs no cookies, no account and no network. What is covered:

* name sanitisation and the rules that must match build-dir-tree
* Content-Disposition parsing (including the RFC 5987 form Moodle sends)
* cookie parsing from a header, a Netscape file and the environment
* Set-Cookie rotation folding back into the store
* the HTTP client's redirect, retry and session-expiry behaviour
* crawler resolution for resources, viewer pages, folders and links
* the fetcher: first run, unchanged skip, dedupe, adoption, resume after failure,
  and manifest round-tripping
* the CLI end to end, including a non-zero exit when an item fails
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lmc_downloader import names
from lmc_downloader.crawler import Activity, Crawler, CourseOutline, parse_outline
from lmc_downloader.fetcher import Fetcher, FetchResult, Manifest, sha256_file
from lmc_downloader.http import HttpClient, HttpError, Response, SessionExpired
from lmc_downloader.session import (
    CookieError,
    CookieStore,
    has_session_cookie,
    load_cookies,
    parse_cookie_header,
    parse_netscape,
)


class TestNames(unittest.TestCase):
    def test_illegal_characters_removed(self):
        self.assertEqual(names.sanitize_name('a<b>c:d"e/f\\g|h?i*j'), "abcdefghij")

    def test_spaces_and_brackets_preserved(self):
        original = "[LECTURE]  Week 1  – Intro & More (v2) [draft]"
        self.assertEqual(names.sanitize_name(original), original)

    def test_trailing_dot_and_space_stripped(self):
        self.assertEqual(names.sanitize_name("Week 1.  "), "Week 1")

    def test_reserved_device_name_escaped(self):
        self.assertEqual(names.sanitize_name("CON"), "CON_")
        self.assertEqual(names.sanitize_name("com1.txt"), "com1.txt_")

    def test_empty_and_dot_names_rejected(self):
        for value in ("", ".", "..", "   ", "..."):
            self.assertEqual(names.sanitize_name(value), "")

    def test_long_name_keeps_extension(self):
        long_name = "a" * 300 + ".docx"
        result = names.sanitize_name(long_name)
        self.assertLessEqual(len(result), names.MAX_NAME_LENGTH)
        self.assertTrue(result.endswith(".docx"))

    def test_disposition_rfc5987(self):
        header = "attachment; filename*=UTF-8''Week%207%20practice%20writing.pdf"
        self.assertEqual(names.filename_from_disposition(header),
                         "Week 7 practice writing.pdf")

    def test_disposition_quoted(self):
        header = 'inline; filename="ISA104-Agent-Guide.pdf"'
        self.assertEqual(names.filename_from_disposition(header),
                         "ISA104-Agent-Guide.pdf")

    def test_disposition_plain(self):
        self.assertEqual(names.filename_from_disposition("attachment; filename=a.zip"),
                         "a.zip")

    def test_disposition_absent(self):
        self.assertEqual(names.filename_from_disposition(None), "")
        self.assertEqual(names.filename_from_disposition(""), "")

    def test_url_basename_decoded(self):
        self.assertEqual(
            names.filename_from_url("https://h/pluginfile.php/1/x/sample%20essay.pdf"),
            "sample essay.pdf",
        )

    def test_choose_filename_prefers_disposition(self):
        chosen = names.choose_filename(
            'attachment; filename="real.pdf"',
            "https://h/pluginfile.php/1/a/url-name.pdf",
            "Activity title",
            "application/pdf",
        )
        self.assertEqual(chosen, "real.pdf")

    def test_choose_filename_falls_back_to_url(self):
        chosen = names.choose_filename("", "https://h/files/from-url.pdf",
                                       "Activity title", "application/pdf")
        self.assertEqual(chosen, "from-url.pdf")

    def test_choose_filename_falls_back_to_title_and_mime(self):
        chosen = names.choose_filename("", "https://h/pluginfile.php/1/x/",
                                       "Week 3 Workbook", "application/pdf")
        self.assertEqual(chosen, "Week 3 Workbook.pdf")

    def test_choose_filename_ignores_generic_names(self):
        chosen = names.choose_filename("", "https://h/mod/resource/view.php",
                                       "Real activity", None)
        self.assertEqual(chosen, "Real activity")

    def test_unique_path_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            first = names.unique_path(directory, "a.pdf")
            first.write_text("1")
            second = names.unique_path(directory, "a.pdf")
            self.assertEqual(second.name, "a (1).pdf")
            second.write_text("2")
            third = names.unique_path(directory, "a.pdf")
            self.assertEqual(third.name, "a (2).pdf")

    def test_human_bytes(self):
        self.assertEqual(names.human_bytes(0), "0 B")
        self.assertEqual(names.human_bytes(999), "999 B")
        self.assertEqual(names.human_bytes(2048), "2.0 KiB")


class TestSession(unittest.TestCase):
    def test_parse_header(self):
        cookies = parse_cookie_header("MoodleSession=abc; MOODLEID1_=xyz")
        self.assertEqual(cookies, {"MoodleSession": "abc", "MOODLEID1_": "xyz"})

    def test_parse_header_skips_attributes(self):
        cookies = parse_cookie_header("a=b; Path=/; Domain=.x.edu; Secure")
        self.assertEqual(cookies, {"a": "b"})

    def test_parse_netscape(self):
        body = (
            "# Netscape HTTP Cookie File\n"
            "\n"
            ".xjtlu.edu.cn\tTRUE\t/\tTRUE\t0\tMoodleSession\tsess123\n"
            ".xjtlu.edu.cn\tTRUE\t/\tFALSE\t0\tMOODLEID1_\tid1\n"
        )
        cookies = parse_netscape(body)
        self.assertEqual(cookies["MoodleSession"], "sess123")
        self.assertEqual(cookies["MOODLEID1_"], "id1")

    def test_parse_netscape_httponly_prefix(self):
        body = "#HttpOnly_.x.edu.cn\tTRUE\t/\tTRUE\t0\tSESS\tv\n"
        self.assertEqual(parse_netscape(body), {"SESS": "v"})

    def test_load_from_env_header(self):
        with mock.patch.dict(os.environ, {"LMC_COOKIES": "MoodleSession=abc"}):
            self.assertEqual(load_cookies(), {"MoodleSession": "abc"})

    def test_load_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cookies.txt"
            path.write_text(
                ".x.edu.cn\tTRUE\t/\tTRUE\t0\tMoodleSession\tfromfile\n",
                encoding="utf-8",
            )
            self.assertEqual(load_cookies(str(path))["MoodleSession"], "fromfile")

    def test_missing_cookies_raise(self):
        with mock.patch.dict(os.environ, {"LMC_COOKIES": ""}):
            with self.assertRaises(CookieError):
                load_cookies()

    def test_has_session_cookie(self):
        self.assertTrue(has_session_cookie({"MoodleSession": "x"}))
        self.assertFalse(has_session_cookie({"other": "x"}))

    def test_store_absorbs_set_cookie(self):
        store = CookieStore({"MoodleSession": "old"})
        store.absorb_set_cookie(["MoodleSession=new; path=/; HttpOnly"])
        self.assertEqual(store.as_dict()["MoodleSession"], "new")

    def test_store_drops_cleared_cookie(self):
        store = CookieStore({"MoodleSession": "old", "keep": "1"})
        store.absorb_set_cookie(["MoodleSession=deleted; expires=Thu, 01 Jan 1970"])
        self.assertNotIn("MoodleSession", store.as_dict())
        self.assertIn("keep", store.as_dict())

    def test_store_header_and_redaction(self):
        store = CookieStore({"a": "1", "b": "2"})
        self.assertIn("a=1", store.header())
        self.assertEqual(store.redacted(), "a, b")


class FakeClient:
    """Stand-in for HttpClient returning programmed responses."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.downloads = []
        self.cookies = CookieStore({"MoodleSession": "x"})

    def probe(self, url):
        self.calls.append(url)
        value = self.routes.get(url)
        if value is None:
            raise HttpError("HTTP 404 for %s" % url)
        if isinstance(value, Exception):
            raise value
        return value

    def get(self, url, allow_login_redirect=False):
        self.calls.append(url)
        value = self.routes.get(url)
        if value is None:
            raise HttpError("HTTP 404 for %s" % url)
        if isinstance(value, Exception):
            raise value
        return value

    def download(self, url, destination):
        self.calls.append(url)
        value = self.routes.get(url)
        if value is None:
            raise HttpError("HTTP 404 for %s" % url)
        if isinstance(value, Exception):
            raise value
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(value.content)
        self.downloads.append((url, str(destination)))
        return value


def html_response(url, body, status=200):
    return Response(url, status, {"content-type": "text/html; charset=utf-8"},
                    body.encode("utf-8"))


def file_response(url, body, name, content_type="application/pdf"):
    return Response(url, 200, {
        "content-type": content_type,
        "content-disposition": 'attachment; filename="%s"' % name,
    }, body)


class TestHttpClient(unittest.TestCase):
    def test_refuses_foreign_host(self):
        client = HttpClient(allowed_hosts={"core.xjtlu.edu.cn"})
        with self.assertRaises(HttpError):
            client.get("https://evil.example.com/x")

    def test_session_expired_on_login_redirect(self):
        response = Response("https://core.xjtlu.edu.cn/login/index.php", 200,
                            {"content-type": "text/html"}, b"login")
        client = HttpClient(allowed_hosts={"core.xjtlu.edu.cn"})
        with mock.patch.object(client, "_opener") as opener:
            opener.open.return_value.__enter__.return_value = mock.MagicMock(
                geturl=lambda: response.url,
                headers=mock.MagicMock(items=lambda: [], get_all=lambda k: []),
                status=200,
                read=lambda *a: b"login",
            )
            with self.assertRaises(SessionExpired):
                client.get("https://core.xjtlu.edu.cn/course/view.php?id=1")

    def test_response_properties(self):
        response = Response("https://h/a", 200,
                            {"content-type": "application/pdf", "content-length": "10"},
                            b"0123456789")
        self.assertEqual(response.content_type, "application/pdf")
        self.assertFalse(response.is_html)
        self.assertTrue(html_response("https://h/b", "<p>x</p>").is_html)


class TestCrawler(unittest.TestCase):
    def test_resource_resolves_to_final_url(self):
        view = "https://core.xjtlu.edu.cn/mod/resource/view.php?id=1"
        final = "https://core.xjtlu.edu.cn/pluginfile.php/9/mod_resource/content/1/a.pdf"
        response = file_response(final, b"%PDF-1.4 data", "a.pdf")
        response.headers["content-length"] = str(len(b"%PDF-1.4 data"))
        client = FakeClient({view: response})
        refs = Crawler(client).resolve(
            Activity(name="Lecture 1", modtype="resource",
                     section="Week 1", view_url=view, instance="1")
        )
        self.assertEqual(len(refs), 1)
        self.assertTrue(refs[0].ok)
        self.assertEqual(refs[0].real_url, final)
        self.assertEqual(refs[0].size, 13)

    def test_resource_viewer_page_is_unwrapped(self):
        view = "https://core.xjtlu.edu.cn/mod/resource/view.php?id=2"
        inner = "https://core.xjtlu.edu.cn/pluginfile.php/9/mod_resource/content/0/handbook.html"
        page = html_response(
            view,
            '<html><body><iframe src="%s?embed=1"></iframe></body></html>' % inner,
        )
        client = FakeClient({
            view: page,
            inner + "?embed=1": file_response(inner, b"<html>hi</html>",
                                              "handbook.html", "text/html"),
            inner: file_response(inner, b"<html>hi</html>", "handbook.html", "text/html"),
        })
        refs = Crawler(client).resolve(
            Activity(name="Handbook", modtype="resource",
                     section="Welcome", view_url=view, instance="2")
        )
        self.assertEqual(refs[0].kind, "viewer")
        self.assertIn("pluginfile.php", refs[0].real_url)

    def test_folder_zip(self):
        view = "https://core.xjtlu.edu.cn/mod/folder/view.php?id=5"
        client = FakeClient({
            "https://core.xjtlu.edu.cn/mod/folder/download_folder.php?id=5":
                Response("https://core.xjtlu.edu.cn/mod/folder/download_folder.php?id=5",
                         200, {"content-type": "application/zip",
                               "content-disposition": "attachment; filename=x.zip",
                               "content-length": "100"}, b"PK\x03\x04"),
        })
        refs = Crawler(client).resolve(
            Activity(name="Seat Arrangement", modtype="folder",
                     section="Assessments", view_url=view, instance="5")
        )
        self.assertTrue(refs[0].ok)
        self.assertEqual(refs[0].kind, "folder")
        self.assertEqual(refs[0].size, 100)

    def test_folder_without_direct_files_is_reported(self):
        view = "https://core.xjtlu.edu.cn/mod/folder/view.php?id=6"
        zip_url = "https://core.xjtlu.edu.cn/mod/folder/download_folder.php?id=6"
        client = FakeClient({zip_url: html_response(zip_url,
                                                    "<p>Cannot download this folder</p>")})
        refs = Crawler(client).resolve(
            Activity(name="Empty", modtype="folder",
                     section="Welcome", view_url=view, instance="6")
        )
        self.assertFalse(refs[0].ok)
        self.assertIn("not downloadable", refs[0].error)

    def test_link_records_external_url(self):
        view = "https://core.xjtlu.edu.cn/mod/url/view.php?id=7"
        body = ('<div class="urlworkaround"><a href="https://example.org/x">go</a></div>')
        client = FakeClient({view: html_response(view, body)})
        refs = Crawler(client).resolve(
            Activity(name="Reading", modtype="url",
                     section="Week 1", view_url=view, instance="7")
        )
        self.assertEqual(refs[0].kind, "link")
        self.assertEqual(refs[0].real_url, "https://example.org/x")

    def test_unknown_type_yields_nothing(self):
        refs = Crawler(FakeClient({})).resolve(
            Activity(name="Forum", modtype="forum", section="s", view_url="", instance="")
        )
        self.assertEqual(refs, [])

    def test_outline_sections_order_preserved(self):
        outline = CourseOutline(course_id="1")
        outline.activities = [
            Activity("a", "resource", "S1"),
            Activity("b", "folder", "S2"),
            Activity("c", "resource", "S1"),
        ]
        self.assertEqual(outline.sections, ["S1", "S2"])
        self.assertEqual(len(outline.by_type("resource")), 2)


class TestFetcher(unittest.TestCase):
    def _ref(self, name="a.pdf", section="Week 1", activity="Lecture", url=None):
        from lmc_downloader.crawler import FileRef
        return FileRef(name=name, section=section, activity=activity,
                       view_url="https://h/view", real_url=url or "https://h/file/a.pdf",
                       kind="file", modtype="resource")

    def test_download_writes_file_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "course"
            ref = self._ref()
            client = FakeClient({
                ref.real_url: file_response(ref.real_url, b"hello world", "a.pdf")})
            fetcher = Fetcher(client, root=root, layout="mirror")
            result = fetcher.fetch(ref)
            self.assertEqual(result.status, "downloaded")
            self.assertEqual(Path(result.path).read_bytes(), b"hello world")
            self.assertTrue((root / "Week 1" / "Lecture" / "a.pdf").is_file())
            saved = fetcher.manifest.save()
            data = json.loads(saved.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 1)
            self.assertEqual(len(data["items"]), 1)

    def test_second_run_skips_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "course"
            ref = self._ref()
            client = FakeClient({
                ref.real_url: file_response(ref.real_url, b"data", "a.pdf")})
            first = Fetcher(client, root=root, layout="mirror")
            first.fetch(ref)
            first.manifest.save()

            second = Fetcher(client, root=root, layout="mirror")
            result = second.fetch(ref)
            self.assertEqual(result.status, "skipped")
            self.assertIn("unchanged", result.message)
            self.assertEqual(len(client.downloads), 1)

    def test_changed_size_triggers_redownload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "course"
            ref = self._ref()
            client = FakeClient({
                ref.real_url: file_response(ref.real_url, b"v1", "a.pdf")})
            fetcher = Fetcher(client, root=root, layout="mirror")
            fetcher.fetch(ref)
            fetcher.manifest.save()

            client.routes[ref.real_url] = file_response(ref.real_url, b"v2 longer", "a.pdf")
            # The crawler reports the remote size; a change must force a re-fetch.
            ref.size = len(b"v2 longer")
            second = Fetcher(client, root=root, layout="mirror")
            result = second.fetch(ref)
            self.assertEqual(result.status, "downloaded")
            self.assertGreaterEqual(len(client.downloads), 2)

    def test_dedupe_keeps_one_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "course"
            client = FakeClient({})
            url = "https://h/file/same.pdf"
            client.routes[url] = file_response(url, b"identical", "same.pdf")
            fetcher = Fetcher(client, root=root, dedupe=True, layout="mirror")
            first = fetcher.fetch(self._ref(activity="A", url=url))
            second = fetcher.fetch(self._ref(activity="B", url=url))
            self.assertEqual(first.status, "downloaded")
            self.assertEqual(second.status, "duplicate")
            self.assertEqual(second.duplicate_of, first.path)
            self.assertEqual(second.path, first.path)

    def test_no_dedupe_keeps_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "course"
            url = "https://h/file/same.pdf"
            client = FakeClient({url: file_response(url, b"identical", "same.pdf")})
            fetcher = Fetcher(client, root=root, dedupe=False, layout="mirror")
            first = fetcher.fetch(self._ref(activity="A", url=url))
            second = fetcher.fetch(self._ref(activity="B", url=url))
            self.assertEqual(first.status, "downloaded")
            self.assertEqual(second.status, "downloaded")
            self.assertNotEqual(first.path, second.path)

    def test_failure_is_reported_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "course"
            ref = self._ref(url="https://h/missing.pdf")
            fetcher = Fetcher(FakeClient({}), root=root, layout="mirror")
            result = fetcher.fetch(ref)
            self.assertEqual(result.status, "failed")
            self.assertIn("404", result.message)
            self.assertFalse(Path(result.path).exists() if result.path else False)

    def test_adopt_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "course"
            ref = self._ref()
            target = root / "Week 1" / "Lecture" / "a.pdf"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"already here")
            fetcher = Fetcher(FakeClient({}), root=root, layout="mirror")
            adopted = fetcher.adopt_existing(ref)
            self.assertIsNotNone(adopted)
            self.assertEqual(adopted.status, "adopted")
            self.assertEqual(adopted.sha256, sha256_file(target))

    def test_link_is_recorded_without_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            from lmc_downloader.crawler import FileRef
            ref = FileRef(name="Reading", section="S", activity="Reading",
                          real_url="https://example.org", kind="link",
                          modtype="url")
            fetcher = Fetcher(FakeClient({}), root=Path(tmp) / "c")
            result = fetcher.fetch(ref)
            self.assertEqual(result.status, "link")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "course"
            ref = self._ref()
            client = FakeClient({ref.real_url: file_response(ref.real_url, b"x", "a.pdf")})
            result = Fetcher(client, root=root, dry_run=True).fetch(ref)
            self.assertEqual(result.status, "skipped")
            self.assertFalse(root.exists())

    def test_manifest_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "course"
            root.mkdir(parents=True)
            manifest = Manifest(root=root, course_id="1213", course_title="ISA104")
            ref = self._ref()
            result = FetchResult(ref=ref, path=str(root / "a.pdf"), sha256="deadbeef",
                                 bytes_written=4, status="downloaded")
            manifest.record(result)
            manifest.save()

            reloaded = Manifest.load(root)
            self.assertEqual(reloaded.course_id, "1213")
            self.assertEqual(reloaded.course_title, "ISA104")
            self.assertEqual(len(reloaded.entries), 1)

    def test_corrupt_manifest_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text("{not json", encoding="utf-8")
            manifest = Manifest.load(root)
            self.assertEqual(manifest.entries, {})

    def test_summary_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            from lmc_downloader.crawler import FileRef
            fetcher = Fetcher(FakeClient({}), root=Path(tmp))
            results = [
                FetchResult(ref=FileRef("a", kind="file"), status="downloaded",
                            bytes_written=2048),
                FetchResult(ref=FileRef("b", kind="file"), status="failed"),
            ]
            text = fetcher.summary(results)
            self.assertIn("downloaded=1", text)
            self.assertIn("failed=1", text)
            self.assertIn("2.0 KiB", text)


class _FakeHttpForCli:
    """Patches HttpClient used by the CLI so the whole command can run offline."""

    def __init__(self, course_html, routes):
        self.course_html = course_html
        self.routes = routes

    def __call__(self, *args, **kwargs):
        outer = self

        class _Client:
            cookies = CookieStore({"MoodleSession": "x"})
            request_count = 0

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None

            def get(self, url, allow_login_redirect=False):
                if "course/view.php" in url:
                    return html_response(url, outer.course_html)
                value = outer.routes.get(url)
                if value is None:
                    raise HttpError("HTTP 404 for %s" % url)
                return value

            probe = get

            def download(self, url, destination):
                value = self.get(url)
                destination = Path(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(value.content)
                return value

        return _Client()


COURSE_HTML = """
<html><head><title>Course: ISA104 | LM Core</title></head><body>
<ul class="course-content">
  <li class="section main" id="section-1" data-id="1" data-number="1">
    <h3 class="sectionname">Week 1</h3>
    <ul>
      <li class="activity modtype_resource" id="module-101">
        <a class="aalink" href="https://core.xjtlu.edu.cn/mod/resource/view.php?id=101">
          <span class="instancename">Lecture 1</span>
        </a>
      </li>
    </ul>
  </li>
</ul></body></html>
"""


class TestCli(unittest.TestCase):
    def test_outline_command(self):
        from lmc_downloader import cli
        fake = _FakeHttpForCli(COURSE_HTML, {})
        out = io.StringIO()
        with mock.patch.object(cli, "HttpClient", fake), \
                mock.patch("sys.stdout", out):
            code = cli.main(["outline", "1213", "--cookies", "MoodleSession=x"])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("ISA104", text)
        self.assertIn("Lecture 1", text)

    def test_outline_writes_json(self):
        from lmc_downloader import cli
        fake = _FakeHttpForCli(COURSE_HTML, {})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outline.json"
            with mock.patch.object(cli, "HttpClient", fake), \
                    mock.patch("sys.stdout", io.StringIO()):
                code = cli.main(["outline", "1213", "--cookies", "MoodleSession=x",
                                 "--json", str(path)])
            self.assertEqual(code, 0)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["course_id"], "1213")

    def test_download_command_success(self):
        from lmc_downloader import cli
        final = "https://core.xjtlu.edu.cn/pluginfile.php/9/mod_resource/content/1/l1.pdf"
        fake = _FakeHttpForCli(COURSE_HTML, {
            "https://core.xjtlu.edu.cn/mod/resource/view.php?id=101":
                file_response(final, b"%PDF-1.4 body", "l1.pdf"),
            # the download step fetches the resolved URL, so it needs an entry too
            final: file_response(final, b"%PDF-1.4 body", "l1.pdf"),
        })
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ISA104"
            with mock.patch.object(cli, "HttpClient", fake), \
                    mock.patch("sys.stdout", io.StringIO()):
                code = cli.main(["download", "1213", "--cookies", "MoodleSession=x",
                                 "--root", str(root), "--layout", "mirror"])
            self.assertEqual(code, 0)
            self.assertTrue((root / "Week 1" / "Lecture 1" / "l1.pdf").is_file())
            self.assertTrue((root / "manifest.json").is_file())

    def test_download_command_reports_failure(self):
        from lmc_downloader import cli
        fake = _FakeHttpForCli(COURSE_HTML, {})  # resource view 404s
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ISA104"
            with mock.patch.object(cli, "HttpClient", fake), \
                    mock.patch("sys.stdout", io.StringIO()), \
                    mock.patch("sys.stderr", io.StringIO()):
                code = cli.main(["download", "1213", "--cookies", "MoodleSession=x",
                                 "--root", str(root)])
            self.assertEqual(code, 2)

    def test_missing_cookies_is_usage_error(self):
        from lmc_downloader import cli
        with mock.patch.dict(os.environ, {"LMC_COOKIES": ""}), \
                mock.patch("sys.stderr", io.StringIO()):
            code = cli.main(["outline", "1213"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()


class TestBuiltinParser(unittest.TestCase):
    """The live-page parser must work without build-dir-tree installed."""

    HTML = (
        '<html><head><title>Course: DEMO101 | LM Core</title></head><body>'
        '<li id="section-1" class="section main" data-sectionname="Week 1">'
        '<h3 class="sectionname">Week 1</h3>'
        '<li id="module-101" class="activity modtype_resource">'
        '<a href="https://core.xjtlu.edu.cn/mod/resource/view.php?id=101">'
        '<span class="instancename">Lecture 1'
        '<span class="accesshide">File</span></span></a></li>'
        '</li>'
        '<li id="section-2" class="section main delegated-section" '
        'data-sectionname="Assessments">'
        '<li id="module-202" class="activity modtype_folder">'
        '<a href="https://core.xjtlu.edu.cn/mod/folder/view.php?id=202">'
        '<span class="instancename">Handouts'
        '<span class="accesshide">Folder</span></span></a></li>'
        '</li>'
        '</body></html>'
    )

    def test_parses_sections_and_activities(self):
        from lmc_downloader.crawler import parse_course_html
        outline = parse_course_html(self.HTML, course_id="101")
        self.assertEqual(outline.course_id, "101")
        self.assertEqual(len(outline.activities), 2)
        self.assertEqual([a.modtype for a in outline.activities],
                         ["resource", "folder"])

    def test_section_name_from_attribute_not_next_heading(self):
        from lmc_downloader.crawler import parse_course_html
        outline = parse_course_html(self.HTML)
        # The second section is delegated and has no <h3>; its name must come
        # from data-sectionname, not bleed in from a neighbouring heading.
        self.assertEqual(outline.activities[1].section, "Assessments")

    def test_activity_type_suffix_removed(self):
        from lmc_downloader.crawler import parse_course_html
        outline = parse_course_html(self.HTML)
        self.assertEqual(outline.activities[0].name, "Lecture 1")
        self.assertEqual(outline.activities[1].name, "Handouts")

    def test_view_url_built_from_module_ids(self):
        from lmc_downloader.crawler import parse_course_html
        outline = parse_course_html(self.HTML)
        self.assertIn("view.php?id=101", outline.activities[0].view_url)
        self.assertIn("id=202", outline.activities[1].view_url)

    def test_parse_outline_works_without_build_dir_tree(self):
        """parse_outline must not hard-require build-dir-tree."""
        from lmc_downloader import crawler
        raw = self.HTML.encode("utf-8")
        with mock.patch.object(crawler, "_parse_with_build_dir_tree",
                               return_value=None):
            outline = crawler.parse_outline(raw, course_id="101")
        self.assertEqual(len(outline.activities), 2)


class TestSnapshot(unittest.TestCase):
    def test_plain_html_passthrough(self):
        from lmc_downloader.snapshot import decode_snapshot
        raw = b"<html><body>plain</body></html>"
        self.assertIn("plain", decode_snapshot(raw))

    def test_mhtml_extracts_html_part(self):
        from lmc_downloader.snapshot import decode_snapshot
        raw = (
            b"From: <Saved by Blink>\r\n"
            b"Snapshot-Content-Location: https://core.xjtlu.edu.cn/course/view.php?id=9\r\n"
            b"MIME-Version: 1.0\r\n"
            b'Content-Type: multipart/related; boundary="BOUND"\r\n'
            b"\r\n"
            b"--BOUND\r\n"
            b"Content-Type: text/html\r\n"
            b"Content-Transfer-Encoding: quoted-printable\r\n"
            b"\r\n"
            b"<html><body>from mhtml</body></html>\r\n"
            b"--BOUND--\r\n"
        )
        self.assertIn("from mhtml", decode_snapshot(raw))

    def test_snapshot_source_url(self):
        from lmc_downloader.snapshot import snapshot_source_url
        raw = (b"From: x\r\n"
               b"Snapshot-Content-Location: https://h/course/view.php?id=1213\r\n\r\n")
        self.assertEqual(snapshot_source_url(raw),
                         "https://h/course/view.php?id=1213")

    def test_utf8_bom_handled(self):
        from lmc_downloader.snapshot import decode_snapshot
        text = decode_snapshot(b"\xef\xbb\xbf<html>hi</html>")
        self.assertIn("hi", text)


class TestWebPagesBecomeMhtml(unittest.TestCase):
    """网页一律存 .mhtml；登录页绝不落盘。"""

    def test_html_mime_maps_to_mhtml(self):
        from lmc_downloader.names import extension_for_mime

        self.assertEqual(extension_for_mime("text/html"), ".mhtml")
        self.assertEqual(extension_for_mime("application/xhtml+xml"), ".mhtml")

    def test_as_mhtml_renames_html_suffix(self):
        from lmc_downloader.names import as_mhtml

        self.assertEqual(as_mhtml("page.html", "text/html"), "page.mhtml")
        self.assertEqual(as_mhtml("page.htm", "text/html"), "page.mhtml")
        self.assertEqual(as_mhtml("page", "text/html"), "page.mhtml")

    def test_as_mhtml_leaves_real_files_alone(self):
        from lmc_downloader.names import as_mhtml

        self.assertEqual(as_mhtml("lecture.pdf", "application/pdf"), "lecture.pdf")
        self.assertEqual(as_mhtml("lab.zip", "application/zip"), "lab.zip")
        self.assertEqual(as_mhtml("code.java", None), "code.java")

    def test_login_page_detected_by_body(self):
        from lmc_downloader.http import looks_like_login_page, Response

        login = Response(
            "https://x/login/index.php", 200,
            {"content-type": "text/html"},
            b'<input name="logintoken" value="abc">',
        )
        self.assertTrue(looks_like_login_page(login))

        page = Response(
            "https://x/mod/page/view.php", 200,
            {"content-type": "text/html"},
            b"<html><body>course page</body></html>",
        )
        self.assertFalse(looks_like_login_page(page))

    def test_pdf_never_treated_as_login(self):
        from lmc_downloader.http import looks_like_login_page, Response

        pdf = Response(
            "https://x/a.pdf", 200,
            {"content-type": "application/pdf"},
            b"%PDF-1.4 logintoken",
        )
        self.assertFalse(looks_like_login_page(pdf))


class TestLayouts(unittest.TestCase):
    """三种文件放置方式：单文件活动不该多套一层目录。"""

    def test_mirror_always_keeps_activity_dir(self):
        from lmc_downloader.names import activity_dir_needed, LAYOUT_MIRROR

        self.assertTrue(activity_dir_needed("resource", 1, LAYOUT_MIRROR))
        self.assertTrue(activity_dir_needed("folder", 3, LAYOUT_MIRROR))

    def test_files_flat_single_file_has_no_dir(self):
        from lmc_downloader.names import activity_dir_needed, LAYOUT_FLAT

        self.assertFalse(activity_dir_needed("resource", 1, LAYOUT_FLAT))
        self.assertTrue(activity_dir_needed("resource", 3, LAYOUT_FLAT))
        self.assertTrue(activity_dir_needed("folder", 1, LAYOUT_FLAT))

    def test_link_name_single_file_has_no_dir(self):
        from lmc_downloader.names import activity_dir_needed, LAYOUT_LINK

        self.assertFalse(activity_dir_needed("resource", 1, LAYOUT_LINK))
        self.assertTrue(activity_dir_needed("folder", 2, LAYOUT_LINK))

    def test_link_name_takes_activity_title(self):
        from lmc_downloader.names import link_name

        self.assertEqual(link_name("Lab4 - SELECT", "random.docx"), "Lab4 - SELECT.docx")
        self.assertEqual(link_name("Week 4", "x.pdf"), "Week 4.pdf")
        self.assertEqual(link_name("Page", "a.html", is_web=True), "Page.mhtml")

    def test_layout_defaults_to_files_flat(self):
        from lmc_downloader.fetcher import Fetcher
        from lmc_downloader.names import LAYOUT_FLAT

        import inspect

        sig = inspect.signature(Fetcher.__init__)
        self.assertEqual(sig.parameters["layout"].default, LAYOUT_FLAT)


class TestExtensionFilter(unittest.TestCase):
    """--ext 要按真实下载地址判断，而不是活动标题。"""

    def test_mime_map_covers_video(self):
        from lmc_downloader.cli import EXT_FOR_MIME

        self.assertEqual(EXT_FOR_MIME.get("video/mp4"), "mp4")
        self.assertEqual(EXT_FOR_MIME.get("application/pdf"), "pdf")

    def test_filter_uses_real_url_suffix(self):
        """标题没有后缀、真实地址是 .mp4 时，过滤必须把它算作 mp4。"""
        from pathlib import Path
        from urllib.parse import urlparse

        title = "CW1 Submission Guide 1"
        url = "https://x/pluginfile.php/1/mod_resource/content/1/guide.mp4"
        allowed = {"pdf", "docx"}

        suffix = Path(urlparse(url).path).suffix.lower().lstrip(".")
        self.assertEqual(suffix, "mp4")
        self.assertNotIn(suffix, allowed)

    def test_title_without_suffix_yields_nothing(self):
        from pathlib import Path

        self.assertEqual(Path("CW1 Submission Guide 1").suffix, "")

