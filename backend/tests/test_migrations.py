"""Schema-migration tests.

The migration contract has three guarantees:

  1. A fresh DB ends at the latest version with every table present.
  2. A pre-migrations DB (one that has v1 tables but no `schema_version`
     row — what every developer laptop looked like before this module
     landed) is adopted cleanly: no error, version stamped to 1, no
     destructive reseed.
  3. Already-current DBs are no-ops on re-init.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from app.spine import db, migrations
from app.spine.migrations import (
    BASELINE_SQL,
    MIGRATIONS,
    apply_migrations,
    latest_version,
)


class MigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "spine.db")
        self._patch = mock.patch.object(db, "DB_PATH", self.db_path)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self.tmp.cleanup()

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def test_fresh_db_ends_at_latest_version(self) -> None:
        db.init_db()
        with self._connect() as c:
            row = c.execute(
                "SELECT MAX(version) AS v FROM schema_version"
            ).fetchone()
            self.assertEqual(row["v"], latest_version())

    def test_fresh_db_has_every_v1_table(self) -> None:
        db.init_db()
        expected = {
            "events",
            "kg_nodes",
            "kg_edges",
            "substrate_skus",
            "substrate_inventory",
            "substrate_sales",
            "substrate_categories",
            "substrate_stores",
            "substrate_store_inventory",
            "substrate_customer_segments",
            "substrate_segment_affinity",
            "substrate_orders",
            "substrate_inbound_pos",
            "substrate_campaigns",
            "action_queue",
            "policy_rules",
            "integration_systems",
            "sync_runs",
            "external_refs",
            "record_cache",
            "outbox_actions",
            "wiki_pages",
            "wiki_revisions",
            "improvement_runs",
            "improvement_suggestions",
            "schema_version",
        }
        with self._connect() as c:
            present = {
                row["name"]
                for row in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertTrue(
            expected <= present,
            msg=f"missing tables: {sorted(expected - present)}",
        )

    def test_pre_migrations_db_is_adopted_cleanly(self) -> None:
        # Simulate the pre-migrations world: v1 schema applied, no
        # schema_version table. This is what a developer's laptop
        # looked like before this module shipped.
        with self._connect() as c:
            c.executescript(BASELINE_SQL)
            # Insert a substrate row to make sure adoption doesn't wipe
            # existing data.
            c.execute(
                "INSERT INTO substrate_skus (sku, name, category, vendor) "
                "VALUES (?, ?, ?, ?)",
                ("test-sku", "Test", "summer_apparel", "Demo"),
            )
            c.commit()
            # Confirm preconditions.
            tables = {
                r["name"]
                for r in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn("events", tables)
            self.assertNotIn("schema_version", tables)

        # Now run init_db — should adopt without complaint.
        db.init_db()

        with self._connect() as c:
            v = c.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()[
                "v"
            ]
            self.assertEqual(v, 1)
            # Pre-existing data still there — adoption was non-destructive.
            row = c.execute(
                "SELECT name FROM substrate_skus WHERE sku = ?", ("test-sku",)
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["name"], "Test")

    def test_re_init_is_a_no_op(self) -> None:
        db.init_db()
        with self._connect() as c:
            first = c.execute(
                "SELECT COUNT(*) AS n FROM schema_version"
            ).fetchone()["n"]
        # Second init shouldn't insert a duplicate row.
        db.init_db()
        with self._connect() as c:
            second = c.execute(
                "SELECT COUNT(*) AS n FROM schema_version"
            ).fetchone()["n"]
        self.assertEqual(first, second)

    def test_apply_migrations_returns_applied_versions(self) -> None:
        # Direct call so we can assert the return value.
        with sqlite3.connect(self.db_path) as raw:
            raw.row_factory = sqlite3.Row
            applied = apply_migrations(raw)
            raw.commit()
        self.assertEqual(applied, [m[0] for m in MIGRATIONS])

        # Re-running on a current DB returns an empty list.
        with sqlite3.connect(self.db_path) as raw:
            raw.row_factory = sqlite3.Row
            again = apply_migrations(raw)
            raw.commit()
        self.assertEqual(again, [])

    def test_versions_are_ascending_and_dense(self) -> None:
        # Lock the contract: versions must be 1..N with no gaps.
        versions = [m[0] for m in migrations.MIGRATIONS]
        self.assertEqual(versions, list(range(1, len(versions) + 1)))


if __name__ == "__main__":
    unittest.main()
