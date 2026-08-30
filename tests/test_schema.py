"""Static assertions on supabase_schema.sql — the authoritative schema the
operator pastes into a fresh Supabase project. Pure text checks: no DB, no
network. These lock in the six-table shape and the hardening block (RLS,
revokes, service_role grants, sequence grants) so a regression is caught in
CI before it reaches a live project.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "supabase_schema.sql"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"

TABLES = [
    "seen_deals",
    "price_history",
    "run_log",
    "posted_deals",
    "guild_destinations",
    "guild_deal_posts",
]


class SchemaHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = SCHEMA_PATH.read_text(encoding="utf-8")

    def test_schema_file_exists(self):
        self.assertTrue(SCHEMA_PATH.is_file(), "supabase_schema.sql missing from repo root")

    def test_all_six_tables_created(self):
        for t in TABLES:
            with self.subTest(table=t):
                self.assertIn(f"create table if not exists {t} (", self.sql)

    def test_rls_enabled_on_every_table(self):
        for t in TABLES:
            with self.subTest(table=t):
                pattern = rf"alter table\s+public\.{t}\s+enable row level security;"
                self.assertIsNotNone(re.search(pattern, self.sql, re.IGNORECASE), f"no RLS for {t}")

    def test_revokes_from_public_anon_authenticated_on_every_table(self):
        for t in TABLES:
            with self.subTest(table=t):
                pattern = rf"revoke all on public\.{t}\s+from public, anon, authenticated;"
                self.assertIsNotNone(re.search(pattern, self.sql, re.IGNORECASE), f"no revoke for {t}")

    def test_service_role_dml_grant_on_every_table(self):
        for t in TABLES:
            with self.subTest(table=t):
                pattern = rf"grant select, insert, update, delete on public\.{t}\s+to service_role;"
                self.assertIsNotNone(re.search(pattern, self.sql, re.IGNORECASE), f"no service_role grant for {t}")

    def test_sequence_grants_for_bigserial_tables(self):
        for seq in ("price_history_id_seq", "run_log_id_seq"):
            with self.subTest(sequence=seq):
                grant = f"grant usage, select on sequence public.{seq} to service_role;"
                revoke = f"revoke all on sequence public.{seq} from public, anon, authenticated;"
                self.assertIn(grant, self.sql)
                self.assertIn(revoke, self.sql)

    def test_no_grants_to_public_api_roles(self):
        # No grant statement may end in "to anon/authenticated/public".
        self.assertEqual(
            re.findall(r"grant\s[^;]*?\bto\s+(?:anon|authenticated|public)\s*;", self.sql, re.IGNORECASE),
            [],
            "found a grant to a public API role",
        )

    def test_dedupe_constraints_preserved(self):
        self.assertIn("price_history_deal_id_observed_date_key", self.sql)
        self.assertIn("primary key (guild_id, deal_id)", self.sql)
        self.assertIn("create unique index if not exists price_history_deal_id_observed_date_key", self.sql)

    def test_no_real_secrets_in_schema_or_env_example(self):
        for path in (SCHEMA_PATH, ENV_EXAMPLE_PATH):
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("eyJ", text, f"possible JWT in {path.name}")
                self.assertIsNone(
                    re.search(r"sb_secret_[A-Za-z0-9]{8,}", text),
                    f"possible real secret key in {path.name}",
                )


if __name__ == "__main__":
    unittest.main()
