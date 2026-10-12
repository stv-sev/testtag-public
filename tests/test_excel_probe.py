from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import _support
from _support import DEFAULT_PUBLIC, SECRET_MARKERS, make_workbook

from testtag_public import excel_probe
from testtag_public.excel_probe import SourceError, ValidationError

FIXED_NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)


def probe(path: Path, **kwargs) -> dict:
    return excel_probe.probe_workbook(path, wait_seconds=0, now=FIXED_NOW, sleep=lambda _s: None, **kwargs)


class ExcelProbeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def wb(self, name="261009-Test-AK5-AK9plus.xlsx", **kwargs) -> Path:
        return make_workbook(self.dir / name, **kwargs)

    def test_missing_required_sheets_are_reported(self):
        path = self.wb(sheets=("Public", "Noteneingabe"))
        with self.assertRaises(ValidationError) as ctx:
            probe(path)
        self.assertIn("Startliste", str(ctx.exception))
        self.assertIn("Zeitplan", str(ctx.exception))
        self.assertEqual(ctx.exception.exit_code, 4)

    def test_public_b2_b6_read_correctly(self):
        report = probe(self.wb())
        self.assertEqual(report["event"], DEFAULT_PUBLIC)
        self.assertEqual(list(report["event"]), list(DEFAULT_PUBLIC))
        self.assertIs(report["event"]["public_active"], True)
        self.assertIsInstance(report["event"]["published_rotation_max"], int)

    def test_public_validation(self):
        for override, cell in (
            ({"event_id": None}, "B2"),
            ({"event_id": "a/b"}, "B2"),
            ({"program": ""}, "B4"),
            ({"published_rotation_max": "sieben"}, "B5"),
            ({"public_active": "ja"}, "B6"),
        ):
            with self.subTest(cell=cell):
                path = self.wb(name=f"wb-{cell}.xlsx", public=override)
                with self.assertRaises(ValidationError) as ctx:
                    probe(path)
                self.assertIn(cell, str(ctx.exception))

    def test_noteneingabe_counts_and_samples(self):
        report = probe(self.wb())
        noten = report["noteneingabe"]
        self.assertEqual(noten["formula_cells"], 4)
        self.assertEqual(noten["cached_nonempty_cells"], 3)
        self.assertEqual(noten["cached_empty_cells"], 1)
        self.assertEqual(noten["cached_empty_sample_addresses"], ["E3"])
        self.assertEqual(noten["dimension"], "A1:F3")

    def test_dimension_not_limited_to_fixed_range(self):
        report = probe(self.wb(extra_noten_cell="OI128", cached={"D2": 1, "D3": 2, "E2": 1, "E3": "", "OI128": 3}))
        self.assertEqual(report["noteneingabe"]["dimension"], "A1:OI128")
        self.assertEqual(report["noteneingabe"]["formula_cells"], 5)

    def test_empty_sample_limited_to_20(self):
        cached = {"D2": "", "D3": "", "E2": "", "E3": ""}
        path = self.wb(cached=cached)
        report = probe(path)
        self.assertLessEqual(len(report["noteneingabe"]["cached_empty_sample_addresses"]), 20)
        self.assertEqual(report["noteneingabe"]["cached_empty_cells"], 4)

    def test_missing_cached_values_are_counted_empty_not_error(self):
        report = probe(self.wb(cached={}))
        self.assertEqual(report["noteneingabe"]["cached_empty_cells"], 4)
        self.assertEqual(report["noteneingabe"]["cached_nonempty_cells"], 0)

    def test_fingerprint_changes_with_cached_results(self):
        a = probe(self.wb(name="a.xlsx"))["noteneingabe"]["cached_values_sha256"]
        a2 = probe(self.wb(name="a2.xlsx"))["noteneingabe"]["cached_values_sha256"]
        b = probe(self.wb(name="b.xlsx", cached={"D2": 19.0, "D3": 16.25, "E2": 1, "E3": ""}))[
            "noteneingabe"]["cached_values_sha256"]
        self.assertEqual(a, a2)
        self.assertNotEqual(a, b)

    def test_zeitplan_row_count(self):
        self.assertEqual(probe(self.wb())["zeitplan"]["data_row_count"], 3)

    def test_source_sha256_and_metadata(self):
        path = self.wb()
        report = probe(path)
        self.assertEqual(report["source"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(report["source"]["size_bytes"], path.stat().st_size)
        self.assertEqual(report["source"]["filename"], path.name)
        self.assertTrue(report["source"]["modified_at"].endswith("Z"))

    def test_source_file_stays_byte_identical(self):
        path = self.wb()
        before = path.read_bytes()
        mtime = path.stat().st_mtime_ns
        probe(path)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, mtime)

    def test_temporary_copy_is_removed(self):
        created = []
        real = excel_probe.tempfile.TemporaryDirectory

        def tracking(*args, **kwargs):
            tmp = real(*args, **kwargs)
            created.append(Path(tmp.name))
            return tmp

        with mock.patch.object(excel_probe.tempfile, "TemporaryDirectory", side_effect=tracking):
            probe(self.wb())
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].exists())

    def test_no_absolute_paths_in_json(self):
        path = self.wb()
        text = excel_probe.serialize_report(probe(path)).decode("utf-8")
        self.assertNotIn(str(self.dir), text)
        self.assertNotIn(str(self.dir).replace("\\", "\\\\"), text)
        self.assertNotIn(":\\", text)
        self.assertNotIn(tempfile.gettempdir(), text)

    def test_no_participant_or_result_values_in_json(self):
        text = excel_probe.serialize_report(probe(self.wb())).decode("utf-8")
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, text, marker)

    def test_stable_serialization(self):
        path = self.wb()
        first = excel_probe.serialize_report(probe(path))
        second = excel_probe.serialize_report(probe(path))
        self.assertEqual(first, second)
        data = json.loads(first)
        self.assertEqual(
            list(data),
            ["schema_version", "generated_at", "event", "source", "sheets", "noteneingabe", "zeitplan"],
        )
        self.assertEqual(data["schema_version"], "probe-1")
        self.assertEqual(data["generated_at"], "2026-10-10T12:00:00Z")
        self.assertTrue(first.endswith(b"\n"))
        first.decode("utf-8")

    def test_filename_suffix_like_copy_number_is_accepted(self):
        report = probe(self.wb(name="261009-Test-AK9-AK12 (1).xlsx"))
        self.assertEqual(report["source"]["filename"], "261009-Test-AK9-AK12 (1).xlsx")

    def test_missing_file(self):
        with self.assertRaises(SourceError) as ctx:
            probe(self.dir / "fehlt.xlsx")
        self.assertEqual(ctx.exception.exit_code, 3)
        self.assertNotIn(str(self.dir), str(ctx.exception))

    def test_invalid_file(self):
        path = self.dir / "kaputt.xlsx"
        path.write_bytes(b"keine Arbeitsmappe")
        with self.assertRaises(SourceError):
            probe(path)

    def test_lock_file_and_wrong_suffix_rejected(self):
        for name in ("~$Datei.xlsx", "Datei.csv"):
            with self.subTest(name=name):
                path = self.dir / name
                path.write_bytes(b"x")
                with self.assertRaises(SourceError):
                    probe(path)

    def test_unstable_file_aborts(self):
        path = self.wb()

        def touch(_seconds):
            with open(path, "ab") as handle:
                handle.write(b"\0")

        with self.assertRaises(SourceError) as ctx:
            excel_probe.probe_workbook(path, wait_seconds=0, sleep=touch)
        self.assertIn("verändert", str(ctx.exception))

    def test_locked_file_aborts(self):
        path = self.wb()

        def fake_open(file):
            self.assertEqual(Path(file), path)
            raise PermissionError("gesperrt")

        with mock.patch.object(excel_probe, "_open_shared", side_effect=fake_open):
            with self.assertRaises(SourceError) as ctx:
                probe(path)
        self.assertIn("gesperrt", str(ctx.exception))

    def test_onedrive_placeholder_aborts(self):
        path = self.wb()
        real_stat = Path.stat

        class FakeStat:
            def __init__(self, st):
                self._st = st
                self.st_file_attributes = 0x400000

            def __getattr__(self, name):
                return getattr(self._st, name)

        def fake_stat(self, *args, **kwargs):
            st = real_stat(self, *args, **kwargs)
            return FakeStat(st) if self == path else st

        with mock.patch.object(Path, "stat", fake_stat):
            with self.assertRaises(SourceError) as ctx:
                probe(path)
        self.assertIn("synchronisiert", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
