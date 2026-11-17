"""Public fixtures for ordinary and coded fixed-snapshot delivery; no codec implementation."""
import itertools
import json
from pathlib import Path
import sys
import tempfile
import unittest

from obslake import Lake, Unavailable
from obslake.object_store import LocalObjectStore, ObjectWriteResult
from obslake.segment_descriptor import descriptor_to_mapping
from tests.fixtures import FailAt


ROWS = [
    ("r0", "S", 10, 9007199254740993),
    ("r1", "S", 11, -9007199254740993),
    ("r2", "T", 20, 7),
]
EXPECTED = [
    {"record_id": "r0", "station": "S", "time": 10, "value": 9007199254740993},
    {"record_id": "r1", "station": "S", "time": 11, "value": -9007199254740993},
    {"record_id": "r2", "station": "T", "time": 20, "value": 7},
]


class ControlledStore(LocalObjectStore):
    """Existing object protocol with deterministic failures and access accounting."""
    def __init__(self, root):
        super().__init__(root)
        self.allow = None
        self.attempt = 0
        self.reads = []

    def put_immutable(self, object_id, data):
        index = self.attempt
        self.attempt += 1
        if self.allow is not None and index not in self.allow:
            return ObjectWriteResult(object_id, False, error="public shard decline")
        return super().put_immutable(object_id, data)

    def read_bytes(self, object_id):
        self.reads.append(object_id)
        return super().read_bytes(object_id)


def must_fail(exc_type, operation):
    try:
        operation()
    except exc_type:
        return
    raise AssertionError("operation unexpectedly succeeded: " + repr(operation))


def counts(lake):
    return tuple(lake.catalog.connection.execute(
        "SELECT count(*) FROM " + table).fetchone()[0]
        for table in ("batches", "snapshots", "snapshot_members", "products"))


def export_rows(lake, snapshot):
    return [json.loads(line) for line in lake.export_product(
        snapshot, None, 0, 30).export_bytes.splitlines()]


def v1():
    with tempfile.TemporaryDirectory(prefix="coded-v1-") as root:
        with Lake(root) as lake:
            lake.accept_batch("base", ROWS)
            snapshot = lake.publish_snapshot(["base"])
            assert export_rows(lake, snapshot) == EXPECTED
            summary = lake.summarize(snapshot, None, 0, 30)
            assert [(x.station, x.count, x.sum) for x in summary.summary] == [
                ("S", 2, 0), ("T", 1, 7)]
        with Lake(root) as lake:
            assert lake.get_product(summary.product_id) == summary
    print("V1_FIXED_DELIVERY_OK")


def byte_cases():
    from obslake.segment_codec import encode_coded_bytes, decode_coded_bytes
    tested = 0
    for raw in (b"", b"ABCDEFG", b"ABCDEF", bytes(range(256)), b"\0" * 7):
        layout, payloads = encode_coded_bytes(raw)
        layout = json.loads(json.dumps(layout))
        assert isinstance(layout, dict) and len(payloads) == 5
        assert all(isinstance(p, bytes) for p in payloads)
        assert len({len(p) for p in payloads}) == 1
        assert len(payloads[0]) <= (len(raw) + 2) // 3
        for size in (3, 4, 5):
            for ids in itertools.combinations(range(5), size):
                assert decode_coded_bytes(layout, {i: payloads[i] for i in ids}) == raw
                tested += 1
        must_fail(Unavailable, lambda: decode_coded_bytes(layout, {0: payloads[0], 4: payloads[4]}))
    return tested


def publication_cases():
    for successful in (5, 3, 2):
        with tempfile.TemporaryDirectory(prefix="coded-publish-") as root:
            store = ControlledStore(Path(root) / "objects")
            with Lake(root, store) as lake:
                lake.accept_batch("base", ROWS)
                original_binding = lake.catalog.registry.require("base")
                old = lake.publish_snapshot(["base"])
                before = counts(lake)
                store.attempt = 0
                store.allow = set(range(successful))
                if successful < 3:
                    must_fail(Unavailable, lambda: lake.publish_coded("base"))
                    assert lake.catalog.current_snapshot_id() == old
                    assert counts(lake) == before
                else:
                    coded = lake.publish_coded("base")
                    assert lake.catalog.current_snapshot_id() == coded
                    assert export_rows(lake, coded) == EXPECTED
                assert store.attempt == 5
                assert lake.catalog.registry.require("base") == original_binding

    with tempfile.TemporaryDirectory(prefix="coded-commit-") as root:
        with Lake(root) as lake:
            lake.accept_batch("base", ROWS)
            old = lake.publish_snapshot(["base"])
            before = counts(lake)
            for stage in ("after_member", "before_pointer", "before_commit"):
                lake.catalog.committer.fault_hook = FailAt(stage)
                must_fail(RuntimeError, lambda: lake.publish_coded("base"))
                assert lake.catalog.current_snapshot_id() == old
                assert counts(lake) == before
            lake.catalog.committer.fault_hook = None
            snapshot = lake.publish_coded("base")
            assert export_rows(lake, snapshot) == EXPECTED


def recovery_case():
    with tempfile.TemporaryDirectory(prefix="coded-recovery-") as root:
        store = ControlledStore(Path(root) / "objects")
        with Lake(root, store) as lake:
            binding = lake.accept_batch("base", ROWS)
            ordinary = lake.publish_snapshot(["base"])
            reference = lake.export_product(ordinary, None, 0, 30).export_bytes
            coded = lake.publish_coded("base")
            saved_summary = lake.summarize(coded, None, 0, 30)
            saved_export = lake.export_product(coded, None, 0, 30)
            mapping = descriptor_to_mapping(lake.catalog.load_snapshot(coded).members[0].segment)
            assert set(mapping) == {"kind", "original", "layout", "shards"}
            assert mapping["kind"] == "coded"
            assert mapping["original"] == descriptor_to_mapping(binding.segment)
            entries = mapping["shards"]
            assert [e["index"] for e in entries] == list(range(5))
            assert len({e["object_id"] for e in entries}) == 5
            assert len({e["payload_length"] for e in entries}) == 1
            assert entries[0]["payload_length"] <= (binding.segment.byte_length + 2) // 3
            paths = [store.object_path(e["object_id"]) for e in entries]
            objects = [p.read_bytes() for p in paths]
            for e, data in zip(entries, objects):
                import hashlib
                assert e["sha256"] == e["object_id"] == hashlib.sha256(data).hexdigest()
                assert e["byte_length"] == len(data)
            lake.accept_batch("other", [("x", "S", 10, 42)])
            other = lake.publish_coded("other")
            other_entry = descriptor_to_mapping(
                lake.catalog.load_snapshot(other).members[0].segment)["shards"][1]
            replacement = store.read_bytes(other_entry["object_id"])

        for retained in itertools.combinations(range(5), 3):
            for i, path in enumerate(paths):
                if i in retained:
                    path.write_bytes(objects[i])
                else:
                    path.unlink(missing_ok=True)
            with Lake(root, store) as lake:
                store.reads.clear()
                assert lake.export_product(coded, None, 0, 30).export_bytes == reference
                assert lake.summarize(coded, None, 0, 30) == saved_summary
                assert binding.segment.object_id not in store.reads
                assert lake.get_product(saved_export.product_id) == saved_export

        for path, data in zip(paths, objects):
            path.write_bytes(data)
        paths[0].unlink()
        damaged = bytearray(objects[1])
        damaged[-1] ^= 1
        paths[1].write_bytes(bytes(damaged))
        with Lake(root, store) as lake:
            assert export_rows(lake, coded) == EXPECTED
        paths[1].write_bytes(replacement)
        with Lake(root, store) as lake:
            assert export_rows(lake, coded) == EXPECTED
        paths[2].unlink()
        with Lake(root, store) as lake:
            before = counts(lake)
            for operation in (lake.read, lake.summarize, lake.export_product):
                must_fail(Unavailable, lambda op=operation: op(coded, None, 0, 30))
            assert counts(lake) == before
            assert lake.get_product(saved_summary.product_id) == saved_summary
            assert lake.get_product(saved_export.product_id) == saved_export
            assert lake.read(coded, None, 0, 0) == []
            assert lake.read(coded, "absent", 0, 30) == []
            paths[1].write_bytes(objects[1])
            assert export_rows(lake, coded) == EXPECTED
    return {"retained_triplets": 10, "records": EXPECTED,
            "summary": [["S", 2, 0], ["T", 1, 7]],
            "insufficient_shards": "Unavailable; no new product"}


def check():
    tested = byte_cases()
    publication_cases()
    result = recovery_case()
    print("CODED_CHECK " + json.dumps(
        {"byte_cases": tested, **result}, ensure_ascii=False, sort_keys=True))


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("v1", "check", "verify", "demo"):
        raise SystemExit("usage: python3.11 -B coded_examples.py v1|check|verify|demo")
    command = sys.argv[1]
    if command == "v1":
        v1()
    elif command == "demo":
        print("CODED_DELIVERY " + json.dumps(recovery_case(), ensure_ascii=False, sort_keys=True))
    else:
        if command == "verify":
            result = unittest.TextTestRunner(verbosity=2).run(
                unittest.defaultTestLoader.discover("tests"))
            if not result.wasSuccessful():
                raise SystemExit(1)
        check()


if __name__ == "__main__":
    main()
