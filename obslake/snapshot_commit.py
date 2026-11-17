import json
import uuid
from .segment_descriptor import descriptor_to_mapping


class SnapshotCommitter:
    def __init__(self, connection, fault_hook=None):
        self.connection = connection
        self.fault_hook = fault_hook

    def _boundary(self, stage):
        if self.fault_hook is not None:
            self.fault_hook(stage)

    def commit_snapshot(self, bindings):
        bindings = tuple(bindings)
        snapshot_id = "s-" + uuid.uuid4().hex
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute("INSERT INTO snapshots VALUES(?)", (snapshot_id,))
            for ordinal, binding in enumerate(bindings):
                encoded = json.dumps(descriptor_to_mapping(binding.segment), ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False)
                self.connection.execute("INSERT INTO snapshot_members VALUES(?,?,?,?,?)",
                                        (snapshot_id, ordinal, binding.batch_id, binding.digest, encoded))
                self._boundary("after_member")
            self._boundary("before_pointer")
            self.connection.execute(
                "INSERT INTO current_snapshot VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET snapshot_id=excluded.snapshot_id",
                (snapshot_id,))
            self._boundary("before_commit")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return snapshot_id
