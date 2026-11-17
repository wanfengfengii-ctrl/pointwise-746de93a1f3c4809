import hashlib
import sqlite3
from tests.fixtures import LakeCase, FailAt
from obslake import Catalog, Unavailable
from obslake.catalog_schema import initialize_catalog_schema


class TestCatalog(LakeCase):
    def test_failed_object_write(self):
        self.lake.accept_batch("old", [("o", "S", 0, 3)])
        old = self.lake.publish_snapshot(["old"])
        self.store.fail_writes = 1
        with self.assertRaises(OSError):
            self.lake.accept_batch("new", [("n", "S", 1, 7)])
        self.assertIsNone(self.lake.catalog.registry.lookup("new"))
        with self.assertRaises(ValueError):
            self.lake.publish_snapshot(["new"])
        self.assertEqual(self.lake.catalog.current_snapshot_id(), old)
        self.assertEqual(self.count("snapshots"), 1)
        self.lake.accept_batch("new", [("n", "S", 1, 7)])
        current = self.lake.publish_snapshot(["old", "new"])
        self.assertEqual(self.lists(current), [["o", "S", 0, 3], ["n", "S", 1, 7]])
        self.assertEqual(self.lists(old), [["o", "S", 0, 3]])

    def test_existing_object_is_immutable(self):
        rows = [("x", "S", 1, 9)]
        binding = self.lake.accept_batch("one", rows)
        second = self.lake.accept_batch("two", rows)
        self.assertEqual(binding.segment, second.segment)
        data = self.store.read_bytes(binding.segment.object_id)
        ack = self.store.put_immutable(hashlib.sha256(data).hexdigest(), data)
        self.assertTrue(ack.success)
        self.assertTrue(ack.reused)
        snapshot = self.lake.publish_snapshot(["one", "two"])
        path = self.store.object_path(binding.segment.object_id)
        damaged = data[:-1] + b"x"
        path.write_bytes(damaged)
        with self.assertRaises(OSError):
            self.lake.accept_batch("three", rows)
        self.assertIsNone(self.lake.catalog.registry.lookup("three"))
        self.assertEqual(path.read_bytes(), damaged)
        with self.assertRaises(Unavailable):
            self.lake.read(snapshot, None, 0, 2)

    def test_atomic_snapshot_failure(self):
        self.lake.accept_batch("old", [("o", "S", 0, 3)])
        old = self.lake.publish_snapshot(["old"])
        self.lake.accept_batch("left", [("l", "S", 1, 7)])
        self.lake.accept_batch("right", [("r", "S", 2, 8)])
        for stage in ("after_member", "before_pointer", "before_commit"):
            with self.subTest(stage=stage):
                self.lake.catalog.committer.fault_hook = FailAt(stage)
                with self.assertRaisesRegex(RuntimeError, stage):
                    self.lake.publish_snapshot(["left", "right"])
                self.reopen()
                self.assertEqual(self.count("snapshots"), 1)
                self.assertEqual(self.count("snapshot_members"), 1)
                self.assertEqual(self.lake.catalog.current_snapshot_id(), old)
                self.assertEqual(self.lists(old), [["o", "S", 0, 3]])
        snapshot = self.lake.publish_snapshot(["right", "left"])
        view = self.lake.catalog.load_snapshot(snapshot)
        self.assertEqual([m.batch_id for m in view.members], ["right", "left"])
        self.assertEqual(self.lists(snapshot), [["l", "S", 1, 7], ["r", "S", 2, 8]])

    def test_member_validation(self):
        binding = self.lake.accept_batch("one", [("a", "S", 0, 1)])
        old = self.lake.publish_snapshot(["one"])
        for members in (["missing"], ["one", "one"], "one"):
            with self.assertRaises(ValueError):
                self.lake.publish_snapshot(members)
        self.store.object_path(binding.segment.object_id).unlink()
        with self.assertRaises(Unavailable):
            self.lake.publish_snapshot(["one"])
        self.assertEqual(self.count("snapshots"), 1)
        self.assertEqual(self.lake.catalog.current_snapshot_id(), old)

    def test_repeat_initialization_and_version(self):
        snapshot = self.publish_sample()
        saved = self.lake.summarize(snapshot, None, 10, 20)
        expected_rows = self.lists(snapshot)
        initialize_catalog_schema(self.db)
        self.reopen()
        self.assertEqual(self.lake.catalog.current_snapshot_id(), snapshot)
        self.assertEqual(self.lists(snapshot), expected_rows)
        self.assertEqual(self.lake.get_product(saved.product_id), saved)
        with Catalog.open(self.root) as another:
            self.assertEqual(another.current_snapshot_id(), snapshot)
        self.db.execute("PRAGMA user_version=99")
        self.lake.close()
        self.lake = None
        with self.assertRaisesRegex(ValueError, "unsupported"):
            Catalog.open(self.root)
        with sqlite3.connect(self.root / "catalog.sqlite3") as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 99)
            self.assertEqual(connection.execute("SELECT count(*) FROM batches").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT count(*) FROM products").fetchone()[0], 1)
