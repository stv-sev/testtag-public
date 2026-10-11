"""P1F: lokale .env – Parser, Vorrang der Prozessumgebung, einmalige Anlage, Git-Ausschluss (kein Netzwerk)."""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support
from _support import AK5, AK9, TEST_ENV, TEST_PUBLISHABLE_KEY, TEST_SECRET_KEY, TT_SLUG, FakeDataApi, make_testtag_workbook

from testtag_public import local_config as lc
from testtag_public import publisher

ROOT = Path(__file__).resolve().parents[1]
SCORES = {(1, "Boden", 1): (5, 1.5, 7.5)}


class ParserTests(unittest.TestCase):
    def test_values_quotes_paths_with_spaces_and_comments(self):
        text = ("\ufeff# Kommentar\n\n"
                f"SUPABASE_URL=https://x.supabase.co\n"
                f"SUPABASE_SECRET_KEY = {TEST_SECRET_KEY}\n"
                "TESTTAG_WORKBOOK_AK5_AK9PLUS=\"C:\\Users\\a b\\Testtag # 1\\AK5.xlsx\"\n"
                "TESTTAG_WORKBOOK_AK9_AK12='D:\\x y\\AK9.xlsx'\n"
                "TESTTAG_POLL_SECONDS=7\n")
        values = lc.parse_env(text)
        self.assertEqual(values["SUPABASE_SECRET_KEY"], TEST_SECRET_KEY)
        self.assertEqual(values["TESTTAG_WORKBOOK_AK5_AK9PLUS"], "C:\\Users\\a b\\Testtag # 1\\AK5.xlsx")
        self.assertEqual(values["TESTTAG_WORKBOOK_AK9_AK12"], "D:\\x y\\AK9.xlsx")
        self.assertEqual(lc.poll_seconds(values), 7)

    def test_no_substitution_or_command_execution(self):
        values = lc.parse_env('SUPABASE_URL="$(whoami)${HOME}%USERNAME%`id`"\n')
        self.assertEqual(values["SUPABASE_URL"], "$(whoami)${HOME}%USERNAME%`id`")

    def test_invalid_entries_are_rejected_without_values(self):
        cases = {
            f"UNBEKANNT={TEST_SECRET_KEY}\n": "unbekannte Variable UNBEKANNT",
            f"SUPABASE_SECRET_KEY={TEST_SECRET_KEY}\nSUPABASE_SECRET_KEY={TEST_SECRET_KEY}\n": "doppelt",
            f"SUPABASE_SECRET_KEY {TEST_SECRET_KEY}\n": "NAME=WERT",
            f'SUPABASE_SECRET_KEY="{TEST_SECRET_KEY}\nweiter"\n': "Anführungszeichen",
            f'SUPABASE_SECRET_KEY="{TEST_SECRET_KEY}\n': "Anführungszeichen",
            f"SUPABASE_SECRET_KEY={TEST_SECRET_KEY} # Kommentar\n": "Leerzeichen",
            f"export SUPABASE_SECRET_KEY={TEST_SECRET_KEY}\n": "NAME=WERT",
            f'SUPABASE_SECRET_KEY="a"b"\n': "Anführungszeichen",
        }
        for text, expected in cases.items():
            with self.subTest(expected=expected), self.assertRaises(lc.LocalConfigError) as ctx:
                lc.parse_env(text)
            self.assertIn(expected, str(ctx.exception))
            self.assertNotIn(TEST_SECRET_KEY, str(ctx.exception))
            self.assertNotIn("sb_secret", str(ctx.exception))

    def test_poll_seconds_range(self):
        self.assertEqual(lc.poll_seconds({}), 5)
        for good in ("2", "60", "15"):
            self.assertEqual(lc.poll_seconds({"TESTTAG_POLL_SECONDS": good}), int(good))
        for bad in ("1", "61", "5.5", "-3", "abc", "0x10", "999"):
            with self.subTest(bad=bad), self.assertRaises(lc.LocalConfigError):
                lc.poll_seconds({"TESTTAG_POLL_SECONDS": bad})


class LoadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write(self, text: str) -> None:
        (self.root / ".env").write_text(text, encoding="utf-8")

    def test_process_environment_has_precedence(self):
        self.write(f"SUPABASE_URL=https://datei.supabase.co\nSUPABASE_SECRET_KEY={TEST_SECRET_KEY}\n"
                   "TESTTAG_POLL_SECONDS=9\n")
        env = lc.load_env({"SUPABASE_URL": "https://prozess.supabase.co", "TESTTAG_POLL_SECONDS": " ",
                           "PATH": "x", "ANDERES": "y"}, self.root)
        self.assertEqual(env["SUPABASE_URL"], "https://prozess.supabase.co")
        self.assertEqual(env["SUPABASE_SECRET_KEY"], TEST_SECRET_KEY)
        self.assertEqual(env["TESTTAG_POLL_SECONDS"], "9")  # leere Prozessvariable zählt nicht
        self.assertNotIn("PATH", env)
        self.assertNotIn("ANDERES", env)

    def test_missing_file_is_empty_and_invalid_file_fails(self):
        self.assertEqual(lc.load_env({}, self.root), {})
        self.write("NICHT_ERLAUBT=1\n")
        with self.assertRaises(lc.LocalConfigError):
            lc.load_env({}, self.root)

    def test_publisher_loads_env_file_automatically(self):
        ak5 = make_testtag_workbook(self.root / "ak5 mit leerzeichen.xlsx", AK5, scores=SCORES)
        ak9 = make_testtag_workbook(self.root / "ak9.xlsx", AK9)
        self.write("\n".join(f'{k}="{v}"' for k, v in TEST_ENV.items()) +
                   f'\nTESTTAG_WORKBOOK_AK5_AK9PLUS="{ak5}"\nTESTTAG_WORKBOOK_AK9_AK12="{ak9}"\n')
        api = FakeDataApi()
        out, err = io.StringIO(), io.StringIO()
        clean = {k: v for k, v in os.environ.items() if k not in lc.KNOWN}
        with mock.patch.object(lc, "PROJECT_ROOT", self.root), mock.patch.dict(os.environ, clean, clear=True):
            code = publisher.run(["--publish", "--confirm-slug", TT_SLUG, "--stability-seconds", "0"],
                                 transport=api, stdout=out, stderr=err)
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("publiziert und öffentlich geprüft", out.getvalue())
        for text in (out.getvalue(), err.getvalue()):
            self.assertNotIn(TEST_SECRET_KEY, text)
            self.assertNotIn(TEST_PUBLISHABLE_KEY, text)
            self.assertNotIn(str(self.root), text)

    def test_publisher_requires_workbooks_from_args_or_env(self):
        out, err = io.StringIO(), io.StringIO()
        code = publisher.run([], env={}, stdout=out, stderr=err)
        self.assertEqual(code, 2)
        self.assertIn("TESTTAG_WORKBOOK_AK5_AK9PLUS", err.getvalue())


class EnsureEnvTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.calls = []

    def fake_run(self, ignored=True):
        def run(cmd, **_kwargs):
            self.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0 if (cmd[0] != "git" or ignored) else 1)
        return run

    def test_existing_env_is_left_untouched(self):
        (self.root / ".env").write_text("# eigene Datei\n", encoding="utf-8")
        self.assertEqual(lc.ensure_env_file(TEST_ENV, self.root, run=self.fake_run()), "vorhanden")
        self.assertEqual((self.root / ".env").read_text(encoding="utf-8"), "# eigene Datei\n")
        self.assertEqual(self.calls, [])

    def test_missing_process_variables_create_nothing(self):
        self.assertEqual(lc.ensure_env_file({"SUPABASE_URL": "https://x.supabase.co"}, self.root,
                                            run=self.fake_run()), "fehlt")
        self.assertFalse((self.root / ".env").exists())

    def test_not_ignored_by_git_creates_nothing(self):
        with self.assertRaises(lc.LocalConfigError):
            lc.ensure_env_file(TEST_ENV, self.root, run=self.fake_run(ignored=False))
        self.assertFalse((self.root / ".env").exists())

    def test_creates_env_with_values_and_only_existing_workbooks(self):
        book = self.root / "Ordner mit Leerzeichen" / "AK5.xlsx"
        book.parent.mkdir()
        book.write_bytes(b"x")
        environ = {**TEST_ENV, "TESTTAG_WORKBOOK_AK5_AK9PLUS": str(book),
                   "TESTTAG_WORKBOOK_AK9_AK12": str(self.root / "fehlt.xlsx")}
        status = lc.ensure_env_file(environ, self.root, run=self.fake_run())
        self.assertTrue(status.startswith("angelegt"))
        self.assertNotIn(TEST_SECRET_KEY, status)
        self.assertEqual(self.calls[0][:3], ["git", "check-ignore", "-q"])
        values = lc.read_env_file(self.root / ".env")
        self.assertEqual(values["SUPABASE_SECRET_KEY"], TEST_SECRET_KEY)
        self.assertEqual(values["TESTTAG_WORKBOOK_AK5_AK9PLUS"], str(book))
        self.assertNotIn("TESTTAG_WORKBOOK_AK9_AK12", values)
        self.assertEqual(values["TESTTAG_POLL_SECONDS"], "5")


@unittest.skipIf(shutil.which("git") is None or not (ROOT / ".git").exists(), "Git-Ablage nicht verfügbar")
class GitExclusionTests(unittest.TestCase):
    def check_ignore(self, name: str) -> int:
        return subprocess.run(["git", "check-ignore", "-q", "--", name], cwd=ROOT, capture_output=True).returncode

    def test_real_env_files_and_runtime_output_are_ignored(self):
        for name in (".env", ".env.local", ".env.production", "out/live_runner.log", "out/live-abc.lock"):
            with self.subTest(name=name):
                self.assertEqual(self.check_ignore(name), 0)
        self.assertTrue(lc.git_ignores(ROOT))

    def test_env_example_is_versioned_and_contains_only_placeholders(self):
        self.assertEqual(self.check_ignore(".env.example"), 1)
        values = lc.read_env_file(ROOT / ".env.example")
        self.assertEqual(set(values), set(lc.KNOWN))
        for name, value in values.items():
            if name != "TESTTAG_POLL_SECONDS":
                self.assertIn("<", value, name)


if __name__ == "__main__":
    unittest.main()
