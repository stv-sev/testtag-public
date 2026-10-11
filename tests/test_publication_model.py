"""Reine Modellfunktionen: Normalisierung, Schlüssel, Ränge, Live-Vergleich, Prüfsummen, Publikationszeilen."""
from __future__ import annotations

import unittest

import _support  # noqa: F401

from testtag_public import publication_model as pm

T1 = "2026-10-09T08:00:00Z"
T2 = "2026-10-09T09:00:00Z"
T3 = "2026-10-09T10:00:00Z"


def result(rid: str, value=5, **extra) -> dict:
    return {"ergebnis_id": rid, "programm": "AK5-AK9+", "ak": "AK 7", "bereich": "technik", "T": value, **extra}


class NormalizationTests(unittest.TestCase):
    def test_clean_text_removes_disturbing_spaces_but_keeps_case(self):
        self.assertEqual(pm.clean_text("  Anna   Maria \t Muster "), "Anna Maria Muster")
        self.assertEqual(pm.clean_text(None), "")

    def test_key_text_is_nfkc_and_casefolded(self):
        self.assertEqual(pm.normalize_key_text("  ＴＶ   Testdorf "), "tv testdorf")

    def test_participant_key_ignores_whitespace_and_case(self):
        a = pm.participant_key("slug", "AK5-AK9+", "Muster ", "Anna", 2016, "TV  Testdorf")
        b = pm.participant_key("slug", "AK5-AK9+", " muster", "ANNA", "2016", "tv testdorf")
        self.assertEqual(a, b)
        self.assertRegex(a, r"^[0-9a-f]{64}$")

    def test_participant_key_depends_on_event_program_and_person(self):
        base = ("slug", "AK5-AK9+", "Muster", "Anna", 2016, "TV Testdorf")
        keys = {pm.participant_key(*base)}
        for index, changed in ((0, "anderer-slug"), (1, "AK9-AK12"), (2, "Mustermann"), (3, "Anja"), (4, 2017),
                               (5, "STV Testdorf")):
            variant = list(base)
            variant[index] = changed
            keys.add(pm.participant_key(*variant))
        self.assertEqual(len(keys), 7)

    def test_number_out_is_stable(self):
        self.assertEqual(pm.number_out(7.0), 7)
        self.assertIsInstance(pm.number_out(7.0), int)
        self.assertEqual(pm.number_out(0.1 + 0.2), 0.3)
        self.assertEqual(pm.number_out(-0.0), 0)


class RankingTests(unittest.TestCase):
    @staticmethod
    def entry(name: str, points):
        return {"name": name, "punkte": points, "_sortierung": (name,)}

    def test_ties_share_rank_and_next_rank_is_skipped(self):
        ranked = pm.assign_ranks([self.entry("d", 5), self.entry("b", 8), self.entry("a", 10), self.entry("c", 8)])
        self.assertEqual([(e["name"], e["rang"]) for e in ranked], [("a", 1), ("b", 2), ("c", 2), ("d", 4)])
        self.assertTrue(all("_sortierung" not in e for e in ranked))

    def test_display_order_within_tie_does_not_change_rank(self):
        ranked = pm.assign_ranks([self.entry("zeta", 3), self.entry("alpha", 3), self.entry("mid", 3)])
        self.assertEqual([e["name"] for e in ranked], ["alpha", "mid", "zeta"])
        self.assertEqual({e["rang"] for e in ranked}, {1})

    def test_zero_points_are_ranked(self):
        ranked = pm.assign_ranks([self.entry("a", 0), self.entry("b", 0), self.entry("c", 1)])
        self.assertEqual([e["rang"] for e in ranked], [1, 2, 2])


class LiveMergeTests(unittest.TestCase):
    def test_first_run_uses_observation_time(self):
        merged = pm.merge_live(None, [result("a"), result("b")], T1)
        self.assertEqual({r["geaendert_am"] for r in merged}, {T1})
        self.assertEqual([r["ergebnis_id"] for r in merged], ["a", "b"])  # stabile Zweitsortierung

    def test_unchanged_keeps_time_new_and_corrected_come_first(self):
        first = pm.merge_live(None, [result("a", 5), result("b", 6)], T1)
        second = pm.merge_live(first, [result("a", 5), result("b", 7), result("c", 1)], T2)
        times = {r["ergebnis_id"]: r["geaendert_am"] for r in second}
        self.assertEqual(times, {"a": T1, "b": T2, "c": T2})
        self.assertEqual([r["ergebnis_id"] for r in second], ["b", "c", "a"])
        third = pm.merge_live(second, [result("a", 5), result("b", 7), result("c", 1)], T3)
        self.assertEqual(third, second)

    def test_removed_or_hidden_results_disappear(self):
        first = pm.merge_live(None, [result("a"), result("b")], T1)
        self.assertEqual([r["ergebnis_id"] for r in pm.merge_live(first, [result("b")], T2)], ["b"])

    def test_zero_correction_is_a_change(self):
        first = pm.merge_live(None, [result("a", 0)], T1)
        corrected = pm.merge_live(first, [result("a", 4)], T2)
        self.assertEqual(corrected[0]["geaendert_am"], T2)
        back = pm.merge_live(corrected, [result("a", 0)], T3)
        self.assertEqual(back[0]["geaendert_am"], T3)

    def test_previously_hidden_result_counts_as_new_when_released(self):
        first = pm.merge_live(None, [result("a")], T1)  # Resultat b war noch nicht freigegeben
        released = pm.merge_live(first, [result("a"), result("b")], T2)
        self.assertEqual(released[0]["ergebnis_id"], "b")
        self.assertEqual(released[0]["geaendert_am"], T2)

    def test_observation_time_requires_timezone(self):
        with self.assertRaises(ValueError):
            pm.merge_live(None, [result("a")], "2026-10-09T08:00:00")


class ChecksumTests(unittest.TestCase):
    def test_canonically_equal_content_has_same_checksum(self):
        a = {"art": "x", "gruppen": [{"b": 1, "a": 2.0}], "schema_version": pm.SCHEMA_VERSION}
        b = {"schema_version": pm.SCHEMA_VERSION, "gruppen": [{"a": 2.0, "b": 1}], "art": "x"}
        self.assertEqual(pm.content_checksum(a), pm.content_checksum(b))
        self.assertNotEqual(pm.content_checksum(a), pm.content_checksum(dict(a, art="y")))

    def test_change_timestamps_are_not_part_of_checksum(self):
        a = {"gruppen": [{"resultate": [dict(result("a"), geaendert_am=T1)]}]}
        b = {"gruppen": [{"resultate": [dict(result("a"), geaendert_am=T3)]}]}
        self.assertEqual(pm.content_checksum(a), pm.content_checksum(b))

    def test_canonical_serialization_is_compact_sorted_utf8(self):
        self.assertEqual(pm.canonical_json({"b": "ä", "a": [1, 2]}), '{"a":[1,2],"b":"ä"}'.encode("utf-8"))


class ErrorTests(unittest.TestCase):
    def test_error_carries_stable_codes_and_limits_message(self):
        issues = [pm.Issue(pm.Code.ROTATION_UNGUELTIG, f"Zelle {i}") for i in range(30)]
        error = pm.PublicationError(issues)
        self.assertEqual(error.code, pm.Code.ROTATION_UNGUELTIG)
        self.assertEqual(error.codes, [pm.Code.ROTATION_UNGUELTIG])
        self.assertIn("weitere Meldungen", str(error))
        self.assertEqual(error.exit_code, 4)


if __name__ == "__main__":
    unittest.main()
