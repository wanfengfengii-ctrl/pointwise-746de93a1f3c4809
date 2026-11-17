import hashlib
import itertools
import json
import tempfile
import unittest
from pathlib import Path

from obslake import Lake, Unavailable
from obslake.object_store import LocalObjectStore, ObjectWriteResult
from obslake.segment_codec import (encode_coded_bytes, decode_coded_bytes,
                                   pack_coded_shard, unpack_coded_shard,
                                   validate_coded_layout)
from obslake.segment_descriptor import (CodedSegmentDescriptor,
                                        descriptor_from_mapping,
                                        descriptor_to_mapping)

ROWS = [
    ("r0", "S", 10, 9007199254740993),
    ("r1", "S", 11, -9007199254740993),
    ("r2", "T", 20, 7),
]
EXPECTED_ROWS = [
    {"record_id": "r0", "station": "S", "time": 10, "value": 9007199254740993},
    {"record_id": "r1", "station": "S", "time": 11, "value": -9007199254740993},
    {"record_id": "r2", "station": "T", "time": 20, "value": 7},
]


class SelectiveStore(LocalObjectStore):
    """Object port that can decline, lie about identity or truncate read-back."""

    def __init__(self, root):
        super().__init__(root)
        self.declined = frozenset()
        self.attempt = 0
        self.lying = frozenset()
        self.truncating = frozenset()
        self.reads = []

    def put_immutable(self, object_id, data):
        index = self.attempt
        self.attempt += 1
        if index in self.declined:
            return ObjectWriteResult(object_id, False, error="declined")
        if index in self.lying:
            return ObjectWriteResult("0" * 64, True)
        if index in self.truncating:
            target = self.object_path(object_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data[:-1])
            return ObjectWriteResult(object_id, True)
        return super().put_immutable(object_id, data)

    def read_bytes(self, object_id):
        self.reads.append(object_id)
        return super().read_bytes(object_id)


def must_fail(exception, operation):
    try:
        operation()
    except exception:
        return True
    raise AssertionError("operation unexpectedly succeeded")


class CodedCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="coded-test-")
        self.root = Path(self.temporary.name)
        self.store = SelectiveStore(self.root / "objects")
        self.lake = Lake(self.root, self.store)

    def tearDown(self):
        self.lake.close()
        self.temporary.cleanup()

    def counts(self):
        return tuple(self.lake.catalog.connection.execute(
            "SELECT count(*) FROM " + table).fetchone()[0]
            for table in ("batches", "snapshots", "snapshot_members", "products"))

    def descriptor(self, snapshot):
        return self.lake.catalog.load_snapshot(snapshot).members[0].segment

    def shard_paths(self, snapshot):
        return [self.store.object_path(shard.object_id)
                for shard in self.descriptor(snapshot).shards]


class TestCodedBytes(unittest.TestCase):
    def test_any_three_of_five_and_length_budget(self):
        for raw in (b"", b"A", b"ABCDEF", b"ABCDEFG", bytes(range(256)), b"\0" * 1000):
            with self.subTest(length=len(raw)):
                layout, payloads = encode_coded_bytes(raw)
                layout = json.loads(json.dumps(layout))
                self.assertEqual(validate_coded_layout(layout), layout)
                self.assertEqual(len(payloads), 5)
                self.assertEqual(len({len(p) for p in payloads}), 1)
                width = len(payloads[0])
                self.assertEqual(width, (len(raw) + 2) // 3)
                self.assertLessEqual(width * 5, 5 * ((len(raw) + 2) // 3))
                for size in (3, 4, 5):
                    for ids in itertools.combinations(range(5), size):
                        self.assertEqual(
                            decode_coded_bytes(layout, {i: payloads[i] for i in ids}), raw)

    def test_shortfall_and_bad_inputs(self):
        layout, payloads = encode_coded_bytes(b"ABCDEFG")
        must_fail(Unavailable, lambda: decode_coded_bytes(layout, {0: payloads[0]}))
        must_fail(Unavailable, lambda: decode_coded_bytes(layout, {}))
        must_fail(Unavailable, lambda: decode_coded_bytes(
            layout, {0: payloads[0], 1: payloads[1][:-1], 2: payloads[2]}))
        tampered = dict(layout)
        tampered["original_length"] = 8
        must_fail(ValueError, lambda: decode_coded_bytes(tampered, {0: payloads[0],
                                                                   1: payloads[1], 2: payloads[2]}))
        must_fail(Unavailable, lambda: decode_coded_bytes(
            layout, {0: payloads[0], 1: payloads[1], 2: b"\0" * len(payloads[2])}))

    def test_shard_envelope_binds_layout_and_index(self):
        layout, payloads = encode_coded_bytes(b"payload")
        packed = pack_coded_shard(layout, 3, payloads[3])
        self.assertEqual(unpack_coded_shard(layout, 3, packed), payloads[3])
        must_fail(ValueError, lambda: unpack_coded_shard(layout, 4, packed))
        other_layout, other_payloads = encode_coded_bytes(b"different")
        must_fail(ValueError, lambda: unpack_coded_shard(
            other_layout, 3, pack_coded_shard(layout, 3, payloads[3])))
        self.assertNotIn(payloads[3], packed[:-len(payloads[3])])
        # Equal payload bytes of two indices remain distinct physical objects.
        empty = encode_coded_bytes(b"")
        self.assertEqual(empty[1][0], empty[1][2])
        self.assertNotEqual(pack_coded_shard(empty[0], 0, empty[1][0]),
                            pack_coded_shard(empty[0], 2, empty[1][2]))


class TestCodedPublish(CodedCase):
    def test_publish_only_one_batch_and_keep_bindings(self):
        self.lake.accept_batch("base", ROWS)
        binding = self.lake.catalog.registry.require("base")
        ordinary = self.lake.publish_snapshot(["base"])
        coded = self.lake.publish_coded("base")
        self.assertNotEqual(coded, ordinary)
        self.assertEqual(self.lake.catalog.current_snapshot_id(), coded)
        view = self.lake.catalog.load_snapshot(coded)
        self.assertEqual([member.batch_id for member in view.members], ["base"])
        self.assertIsInstance(view.members[0].segment, CodedSegmentDescriptor)
        self.assertEqual(self.lake.catalog.registry.require("base"), binding)
        self.assertIsInstance(self.lake.catalog.load_snapshot(ordinary).members[0].segment,
                              type(binding.segment))
        self.assertEqual(self.lake.read(ordinary, None, 0, 30)[0].as_mapping(),
                         EXPECTED_ROWS[0])
        second = self.lake.publish_coded("base")
        self.assertNotEqual(second, coded)

    def test_reject_unknown_and_invalid_batch(self):
        self.lake.accept_batch("base", ROWS)
        old = self.lake.publish_snapshot(["base"])
        before = self.counts()
        for bad in ("missing", "", None, True, 42):
            with self.subTest(batch=bad):
                must_fail(ValueError, lambda value=bad: self.lake.publish_coded(value))
        self.assertEqual(self.lake.catalog.current_snapshot_id(), old)
        self.assertEqual(self.counts(), before)

    def test_confirmation_thresholds(self):
        for successful in (5, 4, 3, 2, 0):
            with self.subTest(successful=successful):
                with tempfile.TemporaryDirectory(prefix="coded-threshold-") as root:
                    store = SelectiveStore(Path(root) / "objects")
                    with Lake(root, store) as lake:
                        lake.accept_batch("base", ROWS)
                        old = lake.publish_snapshot(["base"])
                        before = tuple(lake.catalog.connection.execute(
                            "SELECT count(*) FROM " + table).fetchone()[0]
                            for table in ("batches", "snapshots", "snapshot_members"))
                        store.attempt = 0
                        store.declined = frozenset(range(successful, 5))
                        if successful >= 3:
                            snapshot = lake.publish_coded("base")
                            self.assertEqual(lake.catalog.current_snapshot_id(), snapshot)
                            self.assertEqual(lake.export_product(
                                snapshot, None, 0, 30).export_bytes,
                                lake.export_product(old, None, 0, 30).export_bytes)
                        else:
                            must_fail(Unavailable, lambda: lake.publish_coded("base"))
                            self.assertEqual(lake.catalog.current_snapshot_id(), old)
                            after = tuple(lake.catalog.connection.execute(
                                "SELECT count(*) FROM " + table).fetchone()[0]
                                for table in ("batches", "snapshots", "snapshot_members"))
                            self.assertEqual(after, before)
                        self.assertEqual(store.attempt, 5)

    def test_bad_acknowledgement_and_readback_are_unusable(self):
        # Identity lie on two declared shards plus a truncated third leaves 2 usable.
        store = self.store
        self.lake.accept_batch("base", ROWS)
        old = self.lake.publish_snapshot(["base"])
        store.attempt = 0
        store.lying = frozenset({0, 1})
        store.truncating = frozenset({2})
        must_fail(Unavailable, lambda: self.lake.publish_coded("base"))
        self.assertEqual(self.lake.catalog.current_snapshot_id(), old)
        store.lying = store.truncating = frozenset()
        snapshot = self.lake.publish_coded("base")
        self.assertEqual([row.as_mapping() for row in self.lake.read(snapshot, None, 0, 30)],
                         EXPECTED_ROWS)

    def test_commit_hook_failure_keeps_state_and_retry_succeeds(self):
        from tests.fixtures import FailAt
        self.lake.accept_batch("base", ROWS)
        old = self.lake.publish_snapshot(["base"])
        before = self.counts()
        for stage in ("after_member", "before_pointer", "before_commit"):
            with self.subTest(stage=stage):
                self.lake.catalog.committer.fault_hook = FailAt(stage)
                must_fail(RuntimeError, lambda: self.lake.publish_coded("base"))
                self.assertEqual(self.lake.catalog.current_snapshot_id(), old)
                self.assertEqual(self.counts(), before)
        self.lake.catalog.committer.fault_hook = None
        snapshot = self.lake.publish_coded("base")
        self.assertEqual(self.lake.catalog.current_snapshot_id(), snapshot)


class TestCodedReading(CodedCase):
    def setUp(self):
        super().setUp()
        self.binding = self.lake.accept_batch("base", ROWS)
        self.ordinary = self.lake.publish_snapshot(["base"])
        self.reference = self.lake.export_product(self.ordinary, None, 0, 30).export_bytes
        self.coded = self.lake.publish_coded("base")
        self.summary = self.lake.summarize(self.coded, None, 0, 30)
        self.exported = self.lake.export_product(self.coded, None, 0, 30)

    def test_descriptor_mapping_roundtrip_and_metadata(self):
        descriptor = self.descriptor(self.coded)
        mapping = descriptor_to_mapping(descriptor)
        self.assertEqual(set(mapping), {"kind", "original", "layout", "shards"})
        self.assertEqual(mapping["kind"], "coded")
        self.assertEqual(mapping["original"], descriptor_to_mapping(self.binding.segment))
        self.assertEqual(descriptor_from_mapping(json.loads(json.dumps(mapping))), descriptor)
        self.assertEqual([entry["index"] for entry in mapping["shards"]], list(range(5)))
        self.assertEqual(len({entry["object_id"] for entry in mapping["shards"]}), 5)
        payload_length = mapping["layout"]["payload_length"]
        self.assertEqual(payload_length, (self.binding.segment.byte_length + 2) // 3)
        text = json.dumps(mapping, ensure_ascii=False)
        # Metadata carries descriptors, lengths and digests only: no record
        # identities, no record values and no copy of the ordinary bytes.
        raw = self.store.read_bytes(self.binding.segment.object_id)
        for marker in ("r0", "r1", "r2", "9007199254740993", "OBSLAKE/1"):
            self.assertNotIn(marker, text)
        self.assertNotIn(raw.decode("utf-8"), text)
        for shard in descriptor.shards:
            data = self.store.read_bytes(shard.object_id)
            header = data[:data.index(b"\n", 8) + 1]
            self.assertNotIn(b"r0", header)
            self.assertNotIn(b"OBSLAKE/1", header)
            self.assertEqual(shard.sha256, shard.object_id)
            self.assertEqual(shard.sha256, hashlib.sha256(data).hexdigest())
            self.assertEqual(shard.byte_length, len(data))

    def test_all_ten_triplets_exact(self):
        paths = self.shard_paths(self.coded)
        objects = [path.read_bytes() for path in paths]
        for retained in itertools.combinations(range(5), 3):
            with self.subTest(retained=retained):
                for index, path in enumerate(paths):
                    if index in retained:
                        path.write_bytes(objects[index])
                    else:
                        path.unlink(missing_ok=True)
                self.store.reads.clear()
                self.assertEqual(self.lake.export_product(
                    self.coded, None, 0, 30).export_bytes, self.reference)
                self.assertEqual(self.lake.summarize(self.coded, None, 0, 30), self.summary)
                self.assertEqual(self.lake.get_product(self.exported.product_id), self.exported)
                self.assertNotIn(self.binding.segment.object_id, self.store.reads)
        for path, data in zip(paths, objects):
            path.write_bytes(data)

    def test_missing_and_same_length_corruption_then_shortfall(self):
        paths = self.shard_paths(self.coded)
        objects = [path.read_bytes() for path in paths]
        paths[0].unlink()
        damaged = bytearray(objects[1])
        damaged[-1] ^= 1
        paths[1].write_bytes(bytes(damaged))
        self.assertEqual(self.lake.export_product(self.coded, None, 0, 30).export_bytes,
                         self.reference)
        paths[2].unlink()
        before = self.counts()
        for operation in (self.lake.read, self.lake.summarize, self.lake.export_product):
            must_fail(Unavailable, lambda op=operation: op(self.coded, None, 0, 30))
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.lake.get_product(self.summary.product_id), self.summary)
        paths[1].write_bytes(objects[1])
        paths[2].write_bytes(objects[2])
        self.assertEqual(self.lake.export_product(self.coded, None, 0, 30).export_bytes,
                         self.reference)

    def test_wrong_segment_shard_is_excluded(self):
        self.lake.accept_batch("other", [("x", "S", 10, 42), ("y", "T", 11, 43)])
        other = self.lake.publish_coded("other")
        other_paths = self.shard_paths(other)
        paths = self.shard_paths(self.coded)
        objects = [path.read_bytes() for path in paths]
        paths[3].write_bytes(other_paths[3].read_bytes())
        self.store.reads.clear()
        self.assertEqual(self.lake.export_product(self.coded, None, 0, 30).export_bytes,
                         self.reference)
        self.assertNotIn(self.binding.segment.object_id, self.store.reads)
        # Three foreign same-index shards leave only two genuine shards.
        paths[0].write_bytes(other_paths[0].read_bytes())
        paths[4].write_bytes(other_paths[4].read_bytes())
        must_fail(Unavailable, lambda: self.lake.read(self.coded, None, 0, 30))
        for path, data in zip(paths, objects):
            path.write_bytes(data)
        self.assertEqual(self.lake.export_product(self.coded, None, 0, 30).export_bytes,
                         self.reference)

    def test_safe_pruning_skips_coded_shards(self):
        self.store.reads.clear()
        self.assertEqual(self.lake.read(self.coded, None, 5, 5), [])
        self.assertEqual(self.lake.read(self.coded, "absent", 0, 30), [])
        self.assertEqual(self.lake.read(self.coded, "S", 100, 200), [])
        self.assertEqual(self.store.reads, [])

    def test_partial_window_still_verifies_every_participating_shard(self):
        paths = self.shard_paths(self.coded)
        objects = [path.read_bytes() for path in paths]
        damaged = bytearray(objects[0])
        damaged[-2] ^= 1
        paths[0].write_bytes(bytes(damaged))
        # Only r2@20 is requested; the single hit still validates all shards.
        self.assertEqual(self.lake.read(self.coded, "T", 20, 21)[0].record_id, "r2")
        paths[0].write_bytes(objects[0])


class TestCodedInterplay(CodedCase):
    def test_reopen_after_delete_damage_and_new_snapshot(self):
        self.lake.accept_batch("base", ROWS)
        ordinary = self.lake.publish_snapshot(["base"])
        reference = self.lake.export_product(ordinary, None, 0, 30).export_bytes
        coded = self.lake.publish_coded("base")
        saved = self.lake.summarize(coded, None, 0, 30)
        paths = self.shard_paths(coded)
        objects = [path.read_bytes() for path in paths]
        paths[4].unlink()
        self.lake.close()
        with Lake(self.root, self.store) as lake:
            self.assertEqual(lake.export_product(coded, None, 0, 30).export_bytes, reference)
            self.assertEqual(lake.get_product(saved.product_id), saved)
            lake.accept_batch("later", [("z", "S", 12, 1)])
            lake.publish_snapshot(["later"])
        with Lake(self.root, self.store) as lake:
            self.assertEqual(lake.export_product(coded, None, 0, 30).export_bytes, reference)
            self.assertEqual(lake.summarize(coded, None, 0, 30), saved)
        for path, data in zip(paths, objects):
            path.write_bytes(data)
        with Lake(self.root, self.store) as lake:
            self.assertEqual(lake.export_product(coded, None, 0, 30).export_bytes, reference)

    def test_coded_and_ordinary_snapshots_coexist(self):
        self.lake.accept_batch("base", ROWS)
        mixed = self.lake.publish_snapshot(["base"])
        coded = self.lake.publish_coded("base")
        self.lake.accept_batch("extra", [("e", "T", 21, 5)])
        ordinary_again = self.lake.publish_snapshot(["base", "extra"])
        self.assertEqual([row.as_mapping() for row in self.lake.read(coded, None, 0, 30)],
                         EXPECTED_ROWS)
        self.assertEqual(len(self.lake.read(ordinary_again, None, 0, 30)), 4)
        self.assertEqual(self.lake.catalog.current_snapshot_id(), ordinary_again)
        self.assertEqual(len(self.lake.read(mixed, None, 0, 30)), 3)

    def test_failed_coded_read_does_not_publish_partial_product(self):
        self.lake.accept_batch("base", ROWS)
        coded = self.lake.publish_coded("base")
        counts_before = self.counts()
        for path in self.shard_paths(coded)[:3]:
            path.unlink()
        with self.assertRaises(Unavailable):
            self.lake.export_product(coded, None, 0, 30)
        self.assertEqual(self.counts(), counts_before)
        with self.assertRaises(Unavailable):
            self.lake.summarize(coded, None, 0, 30)
        self.assertEqual(self.counts(), counts_before)


if __name__ == "__main__":
    unittest.main()
