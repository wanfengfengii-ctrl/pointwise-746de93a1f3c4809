from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from .catalog_schema import initialize_catalog_schema
from .batch_registry import BatchBinding, BatchRegistry
from .snapshot_commit import SnapshotCommitter
from .segment_descriptor import descriptor_from_mapping
from .object_store import LocalObjectStore
from .record_model import require_text


@dataclass(frozen=True, slots=True)
class SnapshotView:
    snapshot_id: str
    members: tuple


class Catalog:
    def __init__(self, root, object_store=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.root / "catalog.sqlite3", isolation_level=None)
        try:
            self.connection.execute("PRAGMA foreign_keys=ON")
            initialize_catalog_schema(self.connection)
            self.object_store = object_store if object_store is not None else LocalObjectStore(self.root / "objects")
            self.registry = BatchRegistry(self.connection)
            self.committer = SnapshotCommitter(self.connection)
        except BaseException:
            self.connection.close()
            raise

    @classmethod
    def open(cls, root, object_store=None):
        return cls(root, object_store)

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def current_snapshot_id(self):
        row = self.connection.execute("SELECT snapshot_id FROM current_snapshot WHERE singleton=1").fetchone()
        return None if row is None else row[0]

    def publish_snapshot(self, batch_ids):
        from .segment_reader import SegmentReader
        if isinstance(batch_ids, (str, bytes)):
            raise ValueError("batch_ids must be a sequence of identities")
        ids = tuple(batch_ids)
        for batch_id in ids:
            require_text(batch_id, "batch_id")
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate snapshot member")
        bindings = tuple(self.registry.require(batch_id) for batch_id in ids)
        reader = SegmentReader(self.object_store)
        for binding in bindings:
            reader.read_segment(binding.segment)
        return self.committer.commit_snapshot(bindings)

    def publish_coded(self, batch_id):
        from .coded_publish import CodedPublisher
        return CodedPublisher(self).publish_coded(batch_id)

    def load_snapshot(self, snapshot_id):
        require_text(snapshot_id, "snapshot_id")
        if self.connection.execute("SELECT 1 FROM snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone() is None:
            raise ValueError("unknown snapshot: " + snapshot_id)
        members = []
        rows = self.connection.execute(
            "SELECT batch_id,digest,descriptor FROM snapshot_members WHERE snapshot_id=? ORDER BY ordinal",
            (snapshot_id,)).fetchall()
        for batch_id, digest, encoded in rows:
            members.append(BatchBinding(batch_id, digest, descriptor_from_mapping(json.loads(encoded))))
        return SnapshotView(snapshot_id, tuple(members))
