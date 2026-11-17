from dataclasses import replace
import hashlib
import json
from tests.fixtures import LakeCase, FailAt
from obslake import Unavailable
from obslake.segment_descriptor import descriptor_to_mapping


class TestFailures(LakeCase):
    def test_corrupt_source_no_partial_product(self):
        binding = self.lake.accept_batch("one", [("inside", "S", 10, 1), ("outside", "S", 20, 999)])
        snapshot = self.lake.publish_snapshot(["one"])
        old_summary = self.lake.summarize(snapshot, None, 10, 11)
        old_export = self.lake.export_product(snapshot, None, 10, 11)
        path = self.store.object_path(binding.segment.object_id)
        original = path.read_bytes()
        variants = {"missing": None, "truncated": original[:-1],
                    "outside_window": original.replace(b"999", b"998")}
        for mode, damaged in variants.items():
            with self.subTest(mode=mode):
                if damaged is None:
                    path.unlink()
                else:
                    path.write_bytes(damaged)
                self.reopen()
                before = self.count("products")
                with self.assertRaises(Unavailable):
                    self.lake.read(snapshot, None, 10, 11)
                for operation in (self.lake.summarize, self.lake.export_product):
                    with self.assertRaises(Unavailable):
                        operation(snapshot, None, 10, 11)
                self.assertEqual(self.count("products"), before)
                self.assertEqual(self.lake.get_product(old_summary.product_id), old_summary)
                self.assertEqual(self.lake.get_product(old_export.product_id), old_export)
                path.write_bytes(original)
        self.assertEqual(self.lists(snapshot, None, 10, 11), [["inside", "S", 10, 1]])

    def test_late_source_failure(self):
        first = self.lake.accept_batch("first", [("a", "S", 10, 3)])
        second = self.lake.accept_batch("second", [("b", "S", 11, 4)])
        snapshot = self.lake.publish_snapshot(["first", "second"])
        old = self.lake.summarize(snapshot, None, 10, 12)
        old_export = self.lake.export_product(snapshot, None, 10, 12)
        self.store.object_path(second.segment.object_id).unlink()
        self.store.reads.clear()
        with self.assertRaises(Unavailable):
            self.lake.read(snapshot, None, 10, 12)
        self.assertEqual(self.store.reads, [first.segment.object_id, second.segment.object_id])
        for operation in (self.lake.summarize, self.lake.export_product):
            with self.assertRaises(Unavailable):
                operation(snapshot, None, 10, 12)
        self.assertEqual(self.count("products"), 2)
        self.assertEqual(self.lake.get_product(old.product_id), old)
        self.assertEqual(self.lake.get_product(old_export.product_id), old_export)

    def test_malformed_segment_with_matching_digest(self):
        binding = self.lake.accept_batch("one", [("a", "S", 10, 3)])
        snapshot = self.lake.publish_snapshot(["one"])
        old = self.lake.summarize(snapshot, None, 0, 20)
        malformed = [
            b"OBSLAKE/2\n[]\n",
            b"OBSLAKE/1\nnot-json\n",
            b'OBSLAKE/1\n[["a","S",10,true]]\n',
            b'OBSLAKE/1\n[["a","S",10,3],["a","S",11,4]]\n',
            b'OBSLAKE/1\n[["z","S",11,3],["a","S",10,4]]\n',
            b"OBSLAKE/1\n[]\n",
            b'OBSLAKE/1\n[["a","T",10,3]]\n']
        for data in malformed:
            with self.subTest(data=data):
                digest = hashlib.sha256(data).hexdigest()
                self.store.put_immutable(digest, data)
                descriptor = replace(binding.segment, object_id=digest, sha256=digest, byte_length=len(data))
                encoded = json.dumps(descriptor_to_mapping(descriptor), ensure_ascii=False, separators=(",", ":"))
                self.db.execute("UPDATE snapshot_members SET descriptor=? WHERE snapshot_id=?", (encoded, snapshot))
                with self.assertRaises(Unavailable):
                    self.lake.read(snapshot, None, 0, 20)
                with self.assertRaises(Unavailable):
                    self.lake.summarize(snapshot, None, 0, 20)
                with self.assertRaises(Unavailable):
                    self.lake.export_product(snapshot, None, 0, 20)
                self.assertEqual(self.count("products"), 1)
                self.assertEqual(self.lake.get_product(old.product_id), old)

    def test_product_commit_failure(self):
        snapshot = self.publish_sample()
        old_summary = self.lake.summarize(snapshot, None, 10, 20)
        old_export = self.lake.export_product(snapshot, None, 10, 20)
        for stage in ("after_content", "before_commit"):
            for operation_name in ("summarize", "export_product"):
                with self.subTest(stage=stage, operation=operation_name):
                    self.lake.products.store.fault_hook = FailAt(stage)
                    operation = getattr(self.lake, operation_name)
                    with self.assertRaisesRegex(RuntimeError, stage):
                        operation(snapshot, None, 0, 30)
                    self.reopen()
                    self.assertEqual(self.count("products"), 2)
                    self.assertEqual(self.lake.get_product(old_summary.product_id), old_summary)
                    self.assertEqual(self.lake.get_product(old_export.product_id), old_export)
        new_summary = self.lake.summarize(snapshot, None, 0, 30)
        new_export = self.lake.export_product(snapshot, None, 0, 30)
        self.assertEqual(sum(row.count for row in new_summary.summary), 6)
        self.assertEqual(len(new_export.export_bytes.splitlines()), 6)
