"""P1F: Dauerpublisher – Änderungserkennung, Stabilität, Fehler, Wiederanlauf, Sperre, Protokoll (kein Netzwerk)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import _support
from _support import (AK5, AK9, TEST_ENV, TEST_PUBLISHABLE_KEY, TEST_SECRET_KEY, TT_SECRET_MARKERS, TT_SLUG,
                      FakeDataApi, make_testtag_workbook)

from testtag_public import database_publisher as dp
from testtag_public import live_runner as lr
from testtag_public import supabase_database as db
from testtag_public.excel_probe import SourceError
from testtag_public.excel_publication import read_program_source
from testtag_public.publisher import SafeReport

ROOT = Path(__file__).resolve().parents[1]
AK5_SCORES = {(1, "Boden", 1): (5, 1.5, 7.5), (2, "Pferd", 1): (4, 1, 4), (1, "Kraft", 1): (None, 1, 8)}
AK9_SCORES = {(1, "Sprung", 1): (3, 1, 3), (1, "Sprung", 2): (4, 1, 4)}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.config = db.load_config(TEST_ENV)
        self.paths = {AK5: self.dir / "Ordner mit Leerzeichen" / "ak5.xlsx", AK9: self.dir / "ak9.xlsx"}
        self.paths[AK5].parent.mkdir()
        self.mtime = 1_800_000_000 * 10 ** 9
        self.specs = {AK5: {"scores": AK5_SCORES}, AK9: {"scores": AK9_SCORES}}
        for program in (AK5, AK9):
            self.write(program)
        self.api = FakeDataApi()
        self.reads: list[Path] = []
        self.logs: list[str] = []
        self.clock = [0.0]
        self.stamp = [datetime(2026, 10, 9, 8, tzinfo=timezone.utc)]

    def write(self, program: str, **changes) -> None:
        """Arbeitsmappe neu speichern; Änderungszeit wird ausdrücklich weitergesetzt."""
        self.specs[program].update(changes)
        make_testtag_workbook(self.paths[program], program, **self.specs[program])
        self.touch(program)

    def touch(self, program: str) -> None:
        self.mtime += 10 ** 9
        os.utime(self.paths[program], ns=(self.mtime, self.mtime))

    def read(self, path, **kwargs):
        self.reads.append(Path(path))
        return read_program_source(path, **kwargs)

    def now(self) -> datetime:
        self.stamp[0] += timedelta(minutes=1)
        return self.stamp[0]

    def runner(self, *, slug=TT_SLUG, read=None, api=None) -> lr.Runner:
        report = SafeReport(io.StringIO(), io.StringIO(), (TEST_SECRET_KEY, TEST_PUBLISHABLE_KEY))
        self.log_file = self.dir / "out" / lr.LOG_NAME
        file_log = lr.BetriebsLog(None, report, self.log_file)
        self.addCleanup(file_log.close)

        def log(text):
            self.logs.append(text)
            file_log(text)
        return lr.Runner(self.config, self.paths, confirm_slug=slug, poll=5, log=log, report=report,
                         transport=api or self.api, now=self.now, monotonic=lambda: self.clock[0],
                         read=read or self.read)

    def ticks(self, runner: lr.Runner, n: int) -> list[str]:
        return [runner.tick() for _ in range(n)]

    def started(self) -> lr.Runner:
        runner = self.runner()
        self.assertEqual(self.ticks(runner, 2), ["in_bearbeitung", "publiziert"])
        return runner

    def writes(self, since: int = 0) -> list:
        return [c for c in self.api.calls[since:] if c["method"] != "GET"]

    def batches(self, since: int = 0) -> list:
        return [c for c in self.writes(since) if c["table"] == "veroeffentlichung"]


class ChangeDetectionTests(Base):
    def test_start_runs_once_after_two_stable_checks(self):
        runner = self.started()
        self.assertEqual(len(self.reads), 2)
        self.assertEqual(len(self.batches()), 1)
        self.assertEqual(self.ticks(runner, 3), ["keine_aenderung"] * 3)

    def test_unchanged_files_are_neither_read_nor_published(self):
        runner = self.started()
        reads, calls = len(self.reads), len(self.api.calls)
        self.assertEqual(set(self.ticks(runner, 10)), {"keine_aenderung"})
        self.assertEqual((len(self.reads), len(self.api.calls)), (reads, calls))

    def test_file_being_saved_is_only_read_when_stable(self):
        runner = self.started()
        reads = len(self.reads)
        self.write(AK5, scores={**AK5_SCORES, (2, "Pferd", 1): (5, 1, 5)})
        self.assertEqual(runner.tick(), "in_bearbeitung")
        self.touch(AK5)  # Speichern/Synchronisation läuft noch
        self.assertEqual(runner.tick(), "in_bearbeitung")
        self.assertEqual(len(self.reads), reads)
        self.assertEqual(runner.tick(), "publiziert")
        self.assertIn("AK5-AK9+: Änderung erkannt – Datei noch in Bearbeitung, warte auf stabilen Speicherstand",
                      self.logs)

    def test_change_of_one_file_reads_only_that_file(self):
        runner = self.started()
        reads = len(self.reads)
        self.write(AK9, scores={**AK9_SCORES, (3, "Sprung", 1): (5, 1, 5), (3, "Sprung", 3): (6, 1, 6)})
        self.assertEqual(self.ticks(runner, 2), ["in_bearbeitung", "publiziert"])
        self.assertEqual(self.reads[reads:], [self.paths[AK9]])  # unveränderte Mappe nicht erneut gelesen
        self.assertIn("Datei stabil: AK9-AK12 – gelesen wird AK9-AK12; unveränderte Arbeitsmappe nicht erneut "
                      "gelesen", self.logs)
        restarted = self.runner()  # frisches Lesen beider Mappen ergibt denselben öffentlichen Stand
        before = len(self.api.calls)
        self.assertEqual(self.ticks(restarted, 2), ["in_bearbeitung", "unveraendert"])
        self.assertEqual(self.writes(before), [])

    def test_unchanged_files_are_never_reread_while_other_changes(self):
        runner = self.started()
        reads = len(self.reads)
        for punkte in (5, 6, 7):
            self.write(AK5, scores={**AK5_SCORES, (2, "Pferd", 1): (punkte, 1, punkte)})
            self.ticks(runner, 2)
        self.assertEqual(self.reads[reads:], [self.paths[AK5]] * 3)

    def test_short_poll_waits_two_seconds_of_stability(self):
        runner = self.runner()
        runner.poll = 1
        runner.stable_ticks = lr.Runner(self.config, self.paths, confirm_slug=TT_SLUG, poll=1, log=print).stable_ticks
        self.assertEqual(runner.stable_ticks, 3)  # Takt 1 s: Datei mindestens 2 s unverändert
        self.assertEqual(self.ticks(runner, 3), ["in_bearbeitung", "in_bearbeitung", "publiziert"])
        self.write(AK5, scores={**AK5_SCORES, (2, "Pferd", 1): (5, 1, 5)})
        self.assertEqual(runner.tick(), "in_bearbeitung")
        self.touch(AK5)  # Speichern/Synchronisation läuft noch: Stabilität beginnt neu
        self.assertEqual(self.ticks(runner, 3), ["in_bearbeitung", "in_bearbeitung", "publiziert"])
        self.assertEqual(lr.Runner(self.config, self.paths, confirm_slug=TT_SLUG, poll=5, log=print).stable_ticks, 2)

    def test_simultaneous_changes_give_one_combined_publication(self):
        runner = self.started()
        before = len(self.api.calls)
        self.write(AK5, scores={**AK5_SCORES, (2, "Pferd", 1): (5, 1, 5)})
        self.write(AK9, scores={**AK9_SCORES, (2, "Sprung", 1): (5, 1, 5)})
        self.assertEqual(self.ticks(runner, 3), ["in_bearbeitung", "publiziert", "keine_aenderung"])
        (batch,) = self.batches(before)
        self.assertEqual({r["programm"] for r in batch["body"]}, {AK5, AK9})

    def test_excel_lock_files_are_ignored(self):
        runner = self.started()
        (self.paths[AK5].parent / "~$ak5.xlsx").write_bytes(b"lock")
        (self.dir / "~$ak9.xlsx").write_bytes(b"lock")
        self.assertEqual(set(self.ticks(runner, 3)), {"keine_aenderung"})

    def test_identical_bytes_with_new_timestamp_send_no_request(self):
        runner = self.started()
        calls = len(self.api.calls)
        self.touch(AK5)
        self.assertEqual(self.ticks(runner, 2), ["in_bearbeitung", "inhalt_unveraendert"])
        self.assertEqual(len(self.api.calls), calls)

    def test_resave_without_content_change_writes_nothing(self):
        runner = self.started()
        before = len(self.api.calls)
        self.write(AK5)  # neu gespeichert: andere Bytes, gleicher Inhalt
        results = self.ticks(runner, 2)
        self.assertIn(results[1], ("unveraendert", "inhalt_unveraendert"))
        self.assertEqual(self.writes(before), [])

    def test_correction_is_published_with_new_timestamp(self):
        runner = self.started()
        tid = next(iter(self.api.testtage))
        before = len(self.api.calls)
        self.write(AK5, scores={**AK5_SCORES, (2, "Pferd", 1): (5, 1, 5)})
        self.ticks(runner, 2)
        (batch,) = self.batches(before)
        self.assertEqual({(r["bereich"], r["ak"]) for r in batch["body"]},
                         {("resultate_live", "AK 7"), ("resultate_rangliste", "AK 7")})
        live = self.api.rows[(tid, "resultate_live", AK5, "AK 7")]["inhalt"]["gruppen"][0]["resultate"]
        self.assertEqual(live[0]["T"], 5)  # Korrektur zuoberst
        self.assertGreater(live[0]["geaendert_am"], live[-1]["geaendert_am"])

    def test_change_above_released_rotation_changes_nothing_public(self):
        self.write(AK5, public={"published_rotation_max": 1})
        runner = self.started()
        before = len(self.api.calls)
        self.write(AK5, scores={**AK5_SCORES, (1, "Pferd", 1): (6, 1, 6)})  # Rotation 2, nicht freigegeben
        self.assertEqual(self.ticks(runner, 2), ["in_bearbeitung", "unveraendert"])
        self.assertEqual(self.writes(before), [])

    def test_full_lock_uses_existing_lock_logic(self):
        runner = self.started()
        tid = next(iter(self.api.testtage))
        self.write(AK5, public={"public_active": False})
        self.write(AK9, public={"public_active": False})
        self.assertEqual(self.ticks(runner, 2)[1], "publiziert")
        self.assertFalse(self.api.testtage[tid]["ist_oeffentlich"])
        self.assertTrue(all(dp.is_tombstone(r["inhalt"]) for k, r in self.api.rows.items() if k[0] == tid))


class FailureTests(Base):
    def test_validation_error_keeps_old_state_and_waits_for_next_change(self):
        runner = self.started()
        tid = next(iter(self.api.testtage))
        rows = json.dumps(sorted(map(list, self.api.rows.items())), sort_keys=True)
        self.write(AK5, direct={(1, "Kraft", 1): 8})  # Kraft vor Ort und extern
        self.assertEqual(self.ticks(runner, 2), ["in_bearbeitung", "validierung_fehler"])
        reads = len(self.reads)
        self.assertEqual(set(self.ticks(runner, 4)), {"wartet_auf_aenderung"})
        self.assertEqual(len(self.reads), reads)
        self.assertEqual(json.dumps(sorted(map(list, self.api.rows.items())), sort_keys=True), rows)
        self.assertTrue(any("Validierung fehlgeschlagen (ATHLETIK_BEIDE_QUELLEN)" in line for line in self.logs))
        self.write(AK5, direct={})
        self.assertEqual(runner.tick(), "in_bearbeitung")
        self.assertIn(runner.tick(), ("unveraendert", "inhalt_unveraendert"))
        self.assertEqual(self.api.testtage[tid]["status"], "live")

    def test_locked_file_is_retried_and_later_succeeds(self):
        attempts = {"n": 0}

        def flaky(path, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise SourceError(f"Exceldatei ist gesperrt und kann nicht gelesen werden: {Path(path).name}")
            return self.read(path, **kwargs)
        runner = self.runner(read=flaky)
        self.assertEqual(self.ticks(runner, 2), ["in_bearbeitung", "wiederholung_geplant"])
        self.assertIsNone(runner.verarbeitet)
        self.assertEqual(self.api.calls, [])
        self.assertEqual(runner.tick(), "wartet_auf_wiederholung")
        self.clock[0] += 5
        self.assertEqual(runner.tick(), "publiziert")
        line = next(line for line in self.logs if line.startswith("Dateifehler"))
        self.assertIn("gesperrt", line)
        self.assertIn("AK5-AK9+", line)
        self.assertNotIn("ak5.xlsx", line)

    def test_network_interruption_is_retried_with_backoff(self):
        self.api.offline = True
        runner = self.runner()
        self.assertEqual(self.ticks(runner, 2), ["in_bearbeitung", "wiederholung_geplant"])
        self.clock[0] += 5
        self.assertEqual(runner.tick(), "wiederholung_geplant")
        self.clock[0] += 9
        self.assertEqual(runner.tick(), "wartet_auf_wiederholung")
        delays = [line.rsplit("in ", 1)[1] for line in self.logs if "Wiederholung geplant" in line]
        self.assertEqual(delays, ["5 s", "10 s"])
        for _ in range(6):
            self.clock[0] += 60
            runner.tick()
        self.assertTrue(self.logs[-1].endswith("in 60 s"))  # höchstens 60 s
        self.assertIsNone(runner.verarbeitet)
        self.api.offline = False
        self.clock[0] += 60
        self.assertEqual(runner.tick(), "publiziert")
        self.assertEqual(len(self.batches()), 1)
        self.assertEqual(runner.failures, 0)

    def test_restart_checks_supabase_once_and_writes_nothing(self):
        self.started()
        before = len(self.api.calls)
        restarted = self.runner()
        self.assertEqual(self.ticks(restarted, 3), ["in_bearbeitung", "unveraendert", "keine_aenderung"])
        self.assertEqual(self.writes(before), [])
        self.assertTrue(any(c["method"] == "GET" for c in self.api.calls[before:]))

    def test_archived_testtag_is_never_written(self):
        runner = self.started()
        tid = next(iter(self.api.testtage))
        self.api.testtage[tid]["status"] = "archiviert"
        before = len(self.api.calls)
        self.write(AK5, scores={**AK5_SCORES, (2, "Pferd", 1): (6, 1, 6)})
        self.assertEqual(self.ticks(runner, 2), ["in_bearbeitung", "archiviert"])
        calls = len(self.api.calls)
        self.write(AK5, scores={**AK5_SCORES, (2, "Pferd", 1): (7, 1, 7)})
        self.assertEqual(self.ticks(runner, 2), ["in_bearbeitung", "archiviert"])
        self.assertEqual(len(self.api.calls), calls)  # nach erkanntem Archiv keine Anfrage mehr
        self.assertEqual(self.writes(before), [])
        self.assertIn("Archivstatus: Testtag archiviert – keine weitere Publikation, nichts geschrieben", self.logs)

    def test_wrong_slug_is_rejected_before_network(self):
        runner = self.runner(slug="anderer-testtag")
        self.assertEqual(self.ticks(runner, 2), ["in_bearbeitung", "validierung_fehler"])
        self.assertEqual(self.api.calls, [])
        self.assertTrue(any(lr.SLUG_ABWEICHUNG in line for line in self.logs))

    def test_missing_file_is_reported_and_recovered(self):
        runner = self.started()
        moved = self.dir / "weg.xlsx"
        self.paths[AK9].rename(moved)
        self.assertEqual(runner.tick(), "datei_fehler")
        moved.rename(self.paths[AK9])
        self.touch(AK9)
        self.assertEqual(self.ticks(runner, 2)[1], "inhalt_unveraendert")

    def test_log_contains_no_personal_data_paths_or_keys(self):
        runner = self.started()
        self.write(AK5, scores={**AK5_SCORES, (2, "Pferd", 1): (5, 1, 5)})
        self.ticks(runner, 2)
        self.write(AK5, direct={(1, "Kraft", 1): 8})
        self.ticks(runner, 2)
        self.api.offline = True
        self.write(AK5, direct={}, scores={**AK5_SCORES, (2, "Pferd", 1): (6, 1, 6)})
        self.ticks(runner, 2)
        text = self.log_file.read_text(encoding="utf-8")
        tid = next(iter(self.api.testtage))
        keys = {r["teilnehmer"] for k, row in self.api.rows.items() if k[1] == "resultate_live"
                for g in row["inhalt"]["gruppen"] for r in g["resultate"]}
        ids = {r["ergebnis_id"] for k, row in self.api.rows.items() if k[1] == "resultate_live"
               for g in row["inhalt"]["gruppen"] for r in g["resultate"]}
        self.assertTrue(keys and ids)
        for marker in (*TT_SECRET_MARKERS, *keys, *(k[:16] for k in keys), *ids, TEST_SECRET_KEY,
                       TEST_PUBLISHABLE_KEY, str(self.dir), "ak5.xlsx", "ak9.xlsx", "7.5", tid, "Traceback"):
            self.assertNotIn(marker, text)
        for expected in ("Publikation bestätigt", "Validierung fehlgeschlagen", "Wiederholung geplant",
                         "Datei stabil", "Änderung erkannt"):
            self.assertIn(expected, text)


@contextlib.contextmanager
def held_open(path: Path, access: int, share: int):
    """Windows-Dateihandle wie ein anderes Programm halten (Excel, OneDrive)."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    handle = kernel.CreateFileW(str(path), access, share, None, 3, 0x80, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


GENERIC_READ, DELETE = 0x80000000, 0x00010000
SHARE_ALL = 0x7  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE


class OpenWorkbookTests(Base):
    """P1I: Arbeitsmappe bleibt in Excel geöffnet (AutoSpeichern, OneDrive/SharePoint)."""

    @unittest.skipUnless(os.name == "nt", "Windows-Freigabemodus")
    def test_workbook_open_in_excel_with_autosave_is_published(self):
        runner = self.started()
        self.write(AK5, scores={**AK5_SCORES, (2, "Pferd", 1): (5, 1, 5)})
        (self.paths[AK5].parent / "~$ak5.xlsx").write_bytes(b"lock")
        before, calls = self.paths[AK5].read_bytes(), len(self.api.calls)
        # So hält Excel eine synchronisierte Mappe offen: mit Löschzugriff, Lesen für andere erlaubt.
        with held_open(self.paths[AK5], GENERIC_READ | DELETE, SHARE_ALL):
            with self.assertRaises(PermissionError):  # Ursache des Fehlers vor P1I: open() ohne FILE_SHARE_DELETE
                open(self.paths[AK5], "rb").close()
            self.assertEqual(self.ticks(runner, 3), ["in_bearbeitung", "publiziert", "keine_aenderung"])
            self.assertEqual(len(self.batches(calls)), 1)
        self.assertEqual(self.paths[AK5].read_bytes(), before)  # Quelle nie beschrieben
        self.assertFalse(any(line.startswith("Dateifehler") for line in self.logs))

    @unittest.skipUnless(os.name == "nt", "Windows-Freigabemodus")
    def test_restart_with_open_workbook_writes_nothing(self):
        self.started()
        before = len(self.api.calls)
        with held_open(self.paths[AK5], GENERIC_READ | DELETE, SHARE_ALL), \
                held_open(self.paths[AK9], GENERIC_READ | DELETE, SHARE_ALL):
            restarted = self.runner()
            self.assertEqual(self.ticks(restarted, 3), ["in_bearbeitung", "unveraendert", "keine_aenderung"])
        self.assertEqual(self.writes(before), [])

    @unittest.skipUnless(os.name == "nt", "Windows-Freigabemodus")
    def test_exclusive_lock_is_retried_and_published_after_release(self):
        runner = self.started()
        calls = len(self.api.calls)
        self.write(AK5, scores={**AK5_SCORES, (2, "Pferd", 1): (5, 1, 5)})
        with held_open(self.paths[AK5], GENERIC_READ, 0):  # z. B. Synchronisation ersetzt die Datei gerade
            self.assertEqual(self.ticks(runner, 2), ["in_bearbeitung", "wiederholung_geplant"])
        self.assertEqual(self.api.calls[calls:], [])
        self.assertIn("gesperrt", next(line for line in self.logs if line.startswith("Dateifehler")))
        self.clock[0] += 5
        self.assertEqual(runner.tick(), "publiziert")

    def test_partially_saved_file_is_never_published(self):
        runner = self.started()
        tid = next(iter(self.api.testtage))
        rows = json.dumps(sorted(map(list, self.api.rows.items())), sort_keys=True)
        calls = len(self.api.calls)
        complete = self.paths[AK5].read_bytes()
        self.paths[AK5].write_bytes(complete[: len(complete) // 2])  # abgebrochener Speicher-/Synchronisationsstand
        self.touch(AK5)
        self.assertEqual(self.ticks(runner, 2), ["in_bearbeitung", "wiederholung_geplant"])
        self.assertEqual(self.api.calls[calls:], [])
        self.assertEqual(json.dumps(sorted(map(list, self.api.rows.items())), sort_keys=True), rows)
        self.assertFalse(any("Validierung" in line for line in self.logs))
        self.write(AK5, scores={**AK5_SCORES, (2, "Pferd", 1): (5, 1, 5)})  # vollständig synchronisiert
        self.assertEqual(self.ticks(runner, 2), ["in_bearbeitung", "publiziert"])
        self.assertEqual(self.api.testtage[tid]["status"], "live")


class InstanceLockTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "out" / "live-test.lock"

    def test_running_instance_blocks_second_start(self):
        first = lr.InstanceLock(self.path)
        self.assertEqual(first.acquire(), "angelegt")
        info = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(info), {"pid", "gestartet", "gestartet_epoch"})
        second = lr.InstanceLock(self.path, pid=os.getpid() + 100_000)  # anderer Prozess, Sperre gehört uns
        with self.assertRaises(lr.InstanceLockError):
            second.acquire()
        first.release()
        self.assertFalse(self.path.exists())
        self.assertEqual(second.acquire(), "angelegt")  # Neustart nach normalem Ende zulässig
        second.release()

    def test_stale_lock_after_crash_is_replaced(self):
        done = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
        dead_pid = int(done.stdout.strip())
        self.assertFalse(lr.process_alive(dead_pid, 0))
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"pid": dead_pid, "gestartet_epoch": 0}), encoding="utf-8")
        lock = lr.InstanceLock(self.path)
        self.assertEqual(lock.acquire(), "veraltete Sperre ersetzt")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["pid"], os.getpid())
        lock.release()

    def test_reused_pid_and_unreadable_lock_count_as_stale(self):
        self.assertTrue(lr.process_alive(os.getpid(), datetime.now().timestamp()))
        if os.name == "nt":
            self.assertFalse(lr.process_alive(os.getpid(), 0))  # PID existiert, Prozess aber jünger als die Sperre
        self.path.parent.mkdir(parents=True)
        self.path.write_text("kaputt", encoding="utf-8")
        lock = lr.InstanceLock(self.path)
        self.assertEqual(lock.acquire(), "veraltete Sperre ersetzt")
        lock.release()

    def test_lock_name_contains_no_path(self):
        name = lr.lock_path(Path("out"), {AK5: Path("C:/Users/x/Geheim/a.xlsx"), AK9: Path("C:/b.xlsx")}).name
        self.assertRegex(name, r"^live-[0-9a-f]{12}\.lock$")


class MainTests(Base):
    def environ(self, **extra) -> dict:
        return {**TEST_ENV, "TESTTAG_WORKBOOK_AK5_AK9PLUS": str(self.paths[AK5]),
                "TESTTAG_WORKBOOK_AK9_AK12": str(self.paths[AK9]), "TESTTAG_POLL_SECONDS": "2", **extra}

    def main(self, *argv, environ=None, stop=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(lr.local_config, "PROJECT_ROOT", self.dir):  # keine echte .env
            code = lr.main(list(argv), environ=environ or self.environ(), out_dir=self.dir / "out", stdout=out,
                           stderr=err, transport=self.api, stop=stop)
        return code, out.getvalue(), err.getvalue()

    def assert_safe(self, text: str) -> None:
        for marker in (*TT_SECRET_MARKERS, TEST_SECRET_KEY, TEST_PUBLISHABLE_KEY, str(self.dir)):
            self.assertNotIn(marker, text)

    def test_ctrl_c_ends_cleanly_and_removes_lock(self):
        with mock.patch.object(lr.Runner, "run", side_effect=KeyboardInterrupt):
            code, out, err = self.main("--confirm-slug", TT_SLUG)
        self.assertEqual(code, 0, err)
        self.assertIn("Ende: mit Ctrl+C beendet", out)
        self.assertEqual(list((self.dir / "out").glob("*.lock")), [])
        self.assertIn("Ende: mit Ctrl+C beendet", (self.dir / "out" / lr.LOG_NAME).read_text(encoding="utf-8"))
        self.assert_safe(out + err)

    def test_stop_runs_publication_and_ends_normally(self):
        stop = threading.Event()
        calls = {"n": 0}
        original = lr.Runner.tick

        def tick(runner):
            result = original(runner)
            calls["n"] += 1
            if calls["n"] >= 2:
                stop.set()
            return result
        with mock.patch.object(lr.Runner, "tick", tick):
            code, out, err = self.main("--confirm-slug", TT_SLUG, stop=stop)
        self.assertEqual(code, 0, err)
        self.assertIn("Publikation bestätigt", out)
        self.assertIn("Ende: Dauerpublisher beendet", out)
        self.assertEqual(list((self.dir / "out").glob("*.lock")), [])
        self.assert_safe(out + err)

    def test_second_instance_is_refused(self):
        held = lr.InstanceLock(lr.lock_path(self.dir / "out", self.paths), pid=os.getpid() + 100_000,
                               alive=lambda _pid, _started: True)
        held.acquire()
        with mock.patch.object(lr, "process_alive", lambda _pid, _started: True):
            code, out, err = self.main("--confirm-slug", TT_SLUG)
        self.assertEqual(code, lr.LOCK_EXIT)
        self.assertIn("läuft bereits", err)
        self.assertEqual(self.api.calls, [])

    def test_usage_and_configuration_errors_stop_before_network(self):
        self.assertEqual(self.main()[0], 2)  # --confirm-slug fehlt
        self.assertEqual(self.main("--confirm-slug", TT_SLUG, environ=self.environ(TESTTAG_POLL_SECONDS="0"))[0], 5)
        environ = self.environ()
        del environ["TESTTAG_WORKBOOK_AK9_AK12"]
        code, _out, err = self.main("--confirm-slug", TT_SLUG, environ=environ)
        self.assertEqual(code, 5)
        self.assertIn("TESTTAG_WORKBOOK_AK9_AK12", err)
        self.assertEqual(self.api.calls, [])
        self.assertEqual(list((self.dir / "out").glob("*.lock")) if (self.dir / "out").exists() else [], [])

    def test_check_mode_validates_without_network(self):
        code, out, err = self.main("--check")
        self.assertEqual(code, 0, err)
        self.assertIn("Prüfung erfolgreich", out)
        self.assertEqual(self.api.calls, [])
        self.assert_safe(out + err)


@unittest.skipUnless(os.name == "nt", "start_live.cmd nur unter Windows")
class StartScriptTests(Base):
    def run_cmd(self, *args, extra_env=None):
        env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")
               and not k.startswith(("SUPABASE_", "TESTTAG_"))}
        env.update(extra_env or {})
        return subprocess.run(["cmd", "/c", str(ROOT / "start_live.cmd"), *args], cwd=self.dir, env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)

    def test_check_works_without_activated_environment_from_any_directory(self):
        result = self.run_cmd("--check", extra_env={**TEST_ENV, "TESTTAG_WORKBOOK_AK5_AK9PLUS": str(self.paths[AK5]),
                                                     "TESTTAG_WORKBOOK_AK9_AK12": str(self.paths[AK9])})
        text = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, text)
        self.assertIn("Prüfung erfolgreich", text)
        for marker in (str(self.dir), TEST_SECRET_KEY, TEST_PUBLISHABLE_KEY, *TT_SECRET_MARKERS):
            self.assertNotIn(marker, text)

    def test_without_slug_shows_usage_and_starts_nothing(self):
        result = self.run_cmd()
        self.assertEqual(result.returncode, 2)
        self.assertIn("start_live.cmd <Event-Slug>", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
