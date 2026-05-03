"""Pin the event-kind contract.

`EVENT_KINDS` in `app.spine.events` is the closed vocabulary that the
architecture doc + Analyst measurement queries depend on. Adding a kind
without updating the set silently drops the new event from the audit
log; removing one without removing the producer leaves a runtime
crasher. These tests catch both.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from app.spine import db, events


class EventKindsContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(
            db, "DB_PATH", os.path.join(self.tmp.name, "spine.db")
        )
        self._patch.start()
        db.init_db()

    def tearDown(self) -> None:
        self._patch.stop()
        self.tmp.cleanup()

    def test_every_known_kind_is_appendable(self) -> None:
        # Round-trip every kind through append_event so adding one to
        # EVENT_KINDS is paired with at least the trivial proof that the
        # SQLite path accepts it.
        for kind in sorted(events.EVENT_KINDS):
            event_id = events.append_event(
                agent="test",
                kind=kind,
                payload={"smoke": True},
            )
            self.assertGreater(event_id, 0, msg=f"failed to append {kind!r}")

    def test_unknown_kind_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            events.append_event(
                agent="test",
                kind="not_a_real_kind",
                payload={},
            )
        self.assertIn("unknown event kind", str(ctx.exception))

    def test_kinds_are_lowercase_underscores(self) -> None:
        # Mixed case or hyphens would split telemetry queries (the Analyst's
        # measurement reads are kind-equality joins). Pin the convention.
        for kind in events.EVENT_KINDS:
            self.assertRegex(kind, r"^[a-z][a-z_]*$", msg=kind)


if __name__ == "__main__":
    unittest.main()
