import json
from pathlib import Path
import tempfile
import unittest
from obslake import Lake
from obslake.object_store import LocalObjectStore, ObjectWriteResult


class FaultObjectStore(LocalObjectStore):
    def __init__(self, root):
        super().__init__(root)
        self.fail_writes = 0
        self.write_attempts = 0
        self.reads = []

    def put_immutable(self, object_id, data):
        self.write_attempts += 1
        if self.fail_writes:
            self.fail_writes -= 1
            return ObjectWriteResult(object_id, False, error="fixture write failure")
        return super().put_immutable(object_id, data)

    def read_bytes(self, object_id):
        self.reads.append(object_id)
        return super().read_bytes(object_id)


class FailAt:
    def __init__(self, stage):
        self.stage = stage

    def __call__(self, stage):
        if stage == self.stage:
            raise RuntimeError("fixture boundary: " + stage)


class LakeCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="obslake-test-")
        self.root = Path(self.temporary.name)
        self.lake = None
        self.reopen()

    def reopen(self):
        if self.lake is not None:
            self.lake.close()
            self.lake = None
        self.store = FaultObjectStore(self.root / "objects")
        self.lake = Lake(self.root, self.store)
        return self.lake

    def tearDown(self):
        if self.lake is not None:
            self.lake.close()
        self.temporary.cleanup()

    @property
    def db(self):
        return self.lake.catalog.connection

    def count(self, table):
        if table not in {"batches", "snapshots", "snapshot_members", "products"}:
            raise ValueError("unsupported fixture table")
        return self.db.execute("SELECT count(*) FROM " + table).fetchone()[0]

    def sample(self):
        path = Path(__file__).resolve().parent.parent / "samples.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def publish_sample(self):
        sample = self.sample()
        for batch_id, rows in sample["batches"].items():
            self.lake.accept_batch(batch_id, rows)
        return self.lake.publish_snapshot(list(sample["batches"]))

    def lists(self, snapshot, station=None, start=-(10**100), end=10**100):
        return [r.as_list() for r in self.lake.read(snapshot, station, start, end)]
