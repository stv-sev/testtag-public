"""testtag_public – Excel (lokal synchronisiert) -> öffentliche Testtag-Publikation.

P0: Diagnose-JSON -> Supabase Postgres (Data API) mit Rücklesen (`probe`).
P1B: lokaler, deterministischer Publisher-Kern ohne Upload (`excel_publication`, `publication_model`,
`publication_check`).
P1C: einmaliger Datenbank-Publisher mit Batch-Upsert und öffentlichem Rücklesen (`database_publisher`,
`publisher`; Standard ist ein Probelauf ohne Netzwerk).
P1F: lokale `.env` (`local_config`), Dauerpublisher (`live_runner`) und unveränderliche Archivierung
(`archive`).
"""

__version__ = "0.0.1"
