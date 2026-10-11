-- =====================================================================================================
-- testtag-public – Publikationsspeicher in einem EIGENEN Supabase-Projekt (Postgres)
--
-- * Wiederholbar ausführbar: legt nur fehlende Tabellen/Indizes an, setzt Rechte und Richtlinien neu.
--   Es werden keine Tabellen gelöscht und keine Daten entfernt oder verändert.
-- * Excel bleibt die verbindliche Originalquelle; diese Datenbank ist nur Publikationsspeicher.
-- * Keine Verbindung zum internen Projekt `testtag`.
-- * Keine Datenbankfunktionen, insbesondere keine `security definer`-Funktionen.
--
-- Rollen (Supabase):
--   anon / authenticated  – Publishable Key (Streamlit, nur lesen, durch RLS eingeschränkt)
--   service_role          – Secret Key (lokaler Publikationslaptop, schreiben)
--   Hinweis: service_role besitzt in Supabase BYPASSRLS. Seine Grenzen werden deshalb über die
--   Tabellenrechte (GRANT) gezogen; die service_role-Richtlinien unten dokumentieren die Absicht
--   und greifen, falls BYPASSRLS je entfernt wird.
--
-- Ausführung: einmalig (oder erneut nach Änderungen) im Supabase SQL Editor.
-- =====================================================================================================

begin;

-- -----------------------------------------------------------------------------------------------------
-- Tabellen
-- -----------------------------------------------------------------------------------------------------

create table if not exists public.testtage (
    id              uuid        primary key default gen_random_uuid(),
    slug            text        not null
                                constraint testtage_slug_key unique
                                constraint testtage_slug_check check (slug ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$'),
    titel           text        not null,
    datum           date        null,
    ort             text        null,
    status          text        not null default 'vorbereitet'
                                constraint testtage_status_check check (status in ('vorbereitet', 'live', 'archiviert')),
    ist_oeffentlich boolean     not null default false,
    stand_am        timestamptz not null default now()
);

create table if not exists public.veroeffentlichung (
    oeffentliche_testtag_id uuid        not null
                                        constraint veroeffentlichung_testtag_fkey
                                        references public.testtage (id) on update restrict on delete restrict,
    bereich                 text        not null
                                        constraint veroeffentlichung_bereich_check check (
                                            bereich in ('probe', 'resultate_live', 'resultate_rangliste',
                                                        'startliste', 'zeitplan')),
    programm                text        not null default '',
    ak                      text        not null default '',
    pruefsumme              text        not null
                                        constraint veroeffentlichung_pruefsumme_check check (pruefsumme ~ '^[0-9a-f]{64}$'),
    stand_am                timestamptz not null,
    inhalt                  jsonb       not null,
    constraint veroeffentlichung_pkey primary key (oeffentliche_testtag_id, bereich, programm, ak)
);

create index if not exists veroeffentlichung_testtag_idx
    on public.veroeffentlichung (oeffentliche_testtag_id);

-- Genau ein endgültiger Snapshot pro Testtag (Primärschlüssel = Testtag). P0 schreibt hier nichts.
create table if not exists public.archiv_snapshots (
    oeffentliche_testtag_id uuid        primary key
                                        constraint archiv_snapshots_testtag_fkey
                                        references public.testtage (id) on update restrict on delete restrict,
    schema_version          text        not null,
    eingefroren_am          timestamptz not null default now(),
    pruefsumme              text        not null
                                        constraint archiv_snapshots_pruefsumme_check check (pruefsumme ~ '^[0-9a-f]{64}$'),
    inhalt                  jsonb       not null
);

-- -----------------------------------------------------------------------------------------------------
-- Row Level Security
-- -----------------------------------------------------------------------------------------------------

alter table public.testtage          enable row level security;
alter table public.veroeffentlichung enable row level security;
alter table public.archiv_snapshots  enable row level security;

-- -----------------------------------------------------------------------------------------------------
-- Tabellenrechte: Supabase vergibt über Default Privileges standardmässig ALL an anon, authenticated
-- und service_role. Diese Rechte werden hier ausdrücklich entzogen und gezielt neu vergeben.
-- -----------------------------------------------------------------------------------------------------

revoke all privileges on table public.testtage, public.veroeffentlichung, public.archiv_snapshots
    from public, anon, authenticated, service_role;

-- Lesen für den Publishable Key (weiter eingeschränkt durch RLS)
grant select on table public.testtage, public.veroeffentlichung, public.archiv_snapshots
    to anon, authenticated;

-- Schreiben für den Secret Key
grant select, insert, update on table public.testtage, public.veroeffentlichung to service_role;
-- Archiv: nur lesen und einfügen – kein UPDATE, kein DELETE (einmaliger Snapshot)
grant select, insert on table public.archiv_snapshots to service_role;

-- -----------------------------------------------------------------------------------------------------
-- Richtlinien (werden bei jeder Ausführung neu angelegt)
-- -----------------------------------------------------------------------------------------------------

-- testtage
drop policy if exists testtage_oeffentlich_lesen  on public.testtage;
drop policy if exists testtage_service_lesen      on public.testtage;
drop policy if exists testtage_service_einfuegen  on public.testtage;
drop policy if exists testtage_service_aendern    on public.testtage;

create policy testtage_oeffentlich_lesen on public.testtage
    for select to anon, authenticated
    using (ist_oeffentlich);

create policy testtage_service_lesen on public.testtage
    for select to service_role using (true);
create policy testtage_service_einfuegen on public.testtage
    for insert to service_role with check (true);
create policy testtage_service_aendern on public.testtage
    for update to service_role using (true) with check (true);

-- veroeffentlichung
drop policy if exists veroeffentlichung_oeffentlich_lesen on public.veroeffentlichung;
drop policy if exists veroeffentlichung_service_lesen     on public.veroeffentlichung;
drop policy if exists veroeffentlichung_service_einfuegen on public.veroeffentlichung;
drop policy if exists veroeffentlichung_service_aendern   on public.veroeffentlichung;

create policy veroeffentlichung_oeffentlich_lesen on public.veroeffentlichung
    for select to anon, authenticated
    using (exists (
        select 1 from public.testtage t
        where t.id = veroeffentlichung.oeffentliche_testtag_id
          and t.ist_oeffentlich
    ));

create policy veroeffentlichung_service_lesen on public.veroeffentlichung
    for select to service_role using (true);
create policy veroeffentlichung_service_einfuegen on public.veroeffentlichung
    for insert to service_role with check (true);
create policy veroeffentlichung_service_aendern on public.veroeffentlichung
    for update to service_role using (true) with check (true);

-- archiv_snapshots
drop policy if exists archiv_snapshots_oeffentlich_lesen on public.archiv_snapshots;
drop policy if exists archiv_snapshots_service_lesen     on public.archiv_snapshots;
drop policy if exists archiv_snapshots_service_einfuegen on public.archiv_snapshots;

create policy archiv_snapshots_oeffentlich_lesen on public.archiv_snapshots
    for select to anon, authenticated
    using (exists (
        select 1 from public.testtage t
        where t.id = archiv_snapshots.oeffentliche_testtag_id
          and t.ist_oeffentlich
          and t.status = 'archiviert'
    ));

create policy archiv_snapshots_service_lesen on public.archiv_snapshots
    for select to service_role using (true);
create policy archiv_snapshots_service_einfuegen on public.archiv_snapshots
    for insert to service_role with check (true);

commit;

-- Data API (PostgREST) über die geänderte Struktur informieren
notify pgrst, 'reload schema';

-- -----------------------------------------------------------------------------------------------------
-- Kontrolle (nur lesend). Erwartet:
--   anon / authenticated : SELECT auf allen drei Tabellen
--   service_role         : SELECT, INSERT, UPDATE auf testtage und veroeffentlichung;
--                          SELECT, INSERT auf archiv_snapshots
--   PUBLIC               : keine Rechte
--   rls_aktiv            : true für alle drei Tabellen
-- -----------------------------------------------------------------------------------------------------

select c.relname                                        as tabelle,
       c.relrowsecurity                                 as rls_aktiv,
       coalesce((select string_agg(g.grantee || ':' || g.privilege_type, ', '
                                   order by g.grantee, g.privilege_type)
                 from information_schema.role_table_grants g
                 where g.table_schema = 'public'
                   and g.table_name = c.relname
                   and g.grantee in ('PUBLIC', 'anon', 'authenticated', 'service_role')), '–') as rechte
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public'
  and c.relname in ('testtage', 'veroeffentlichung', 'archiv_snapshots')
order by c.relname;
