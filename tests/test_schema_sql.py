"""Statische Prüfung von supabase/schema.sql (keine Datenbankverbindung)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import _support  # noqa: F401

SQL = (Path(__file__).resolve().parents[1] / "supabase" / "schema.sql").read_text(encoding="utf-8")
CODE = "\n".join(line.split("--", 1)[0] for line in SQL.splitlines()).lower()
TABLES = ("testtage", "veroeffentlichung", "archiv_snapshots")


def normalized() -> str:
    return re.sub(r"\s+", " ", CODE)


class SchemaSqlTests(unittest.TestCase):
    def test_repeatable_and_non_destructive(self):
        for table in TABLES:
            self.assertIn(f"create table if not exists public.{table}", normalized())
        for forbidden in ("drop table", "truncate", "delete from", "drop schema", "alter table public.testtage drop",
                          "security definer", "create function", "create or replace function"):
            self.assertNotIn(forbidden, normalized(), forbidden)

    def test_rls_enabled_on_all_tables(self):
        for table in TABLES:
            self.assertIn(f"alter table public.{table} enable row level security", normalized())

    def test_default_privileges_revoked_before_grants(self):
        text = normalized()
        revoke = text.index("revoke all privileges on table public.testtage, public.veroeffentlichung, "
                            "public.archiv_snapshots from public, anon, authenticated, service_role")
        self.assertLess(revoke, text.index("grant select on table"))

    def test_grants(self):
        grants = re.findall(r"grant ([a-z, ]+?) on table ([a-z_., ]+?) to ([a-z_, ]+?);", normalized())
        summary = {}
        for privileges, tables, roles in grants:
            for table in (t.strip().removeprefix("public.") for t in tables.split(",")):
                for role in (r.strip() for r in roles.split(",")):
                    summary.setdefault((table, role), set()).update(p.strip() for p in privileges.split(","))
        for table in TABLES:
            for role in ("anon", "authenticated"):
                self.assertEqual(summary[(table, role)], {"select"}, (table, role))
        self.assertEqual(summary[("testtage", "service_role")], {"select", "insert", "update"})
        self.assertEqual(summary[("veroeffentlichung", "service_role")], {"select", "insert", "update"})
        self.assertEqual(summary[("archiv_snapshots", "service_role")], {"select", "insert"})

    def test_public_policies_are_select_only(self):
        policies = re.findall(r"create policy (\w+) on public\.(\w+) for (\w+) to ([a-z_, ]+?) (using|with)",
                              normalized())
        self.assertTrue(policies)
        for name, _table, command, roles, _ in policies:
            if "anon" in roles or "authenticated" in roles:
                self.assertEqual(command, "select", name)
        archive_cmds = {cmd for _n, table, cmd, _r, _ in policies if table == "archiv_snapshots"}
        self.assertEqual(archive_cmds, {"select", "insert"})

    def test_required_columns_and_constraints(self):
        text = normalized()
        for fragment in (
            "id uuid primary key default gen_random_uuid()",
            "slug text not null constraint testtage_slug_key unique",
            "status text not null default 'vorbereitet'",
            "check (status in ('vorbereitet', 'live', 'archiviert'))",
            "ist_oeffentlich boolean not null default false",
            "stand_am timestamptz not null default now()",
            "references public.testtage (id)",
            "('probe', 'resultate_live', 'resultate_rangliste', 'startliste', 'zeitplan')",
            "programm text not null default ''",
            "ak text not null default ''",
            "inhalt jsonb not null",
            "primary key (oeffentliche_testtag_id, bereich, programm, ak)",
            "create index if not exists veroeffentlichung_testtag_idx on public.veroeffentlichung (oeffentliche_testtag_id)",
            "oeffentliche_testtag_id uuid primary key",
            "eingefroren_am timestamptz not null default now()",
            "and t.status = 'archiviert'",
        ):
            self.assertIn(fragment, text, fragment)


if __name__ == "__main__":
    unittest.main()
