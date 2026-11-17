from dataclasses import dataclass
import json
from .record_model import require_text
from .segment_descriptor import OrdinarySegmentDescriptor, descriptor_to_mapping, descriptor_from_mapping


@dataclass(frozen=True, slots=True)
class BatchBinding:
    batch_id: str
    digest: str
    segment: OrdinarySegmentDescriptor


class BatchRegistry:
    def __init__(self, connection):
        self.connection = connection

    def lookup(self, batch_id):
        require_text(batch_id, "batch_id")
        row = self.connection.execute(
            "SELECT digest, descriptor FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        if row is None:
            return None
        return BatchBinding(batch_id, row[0], descriptor_from_mapping(json.loads(row[1])))

    def require(self, batch_id):
        result = self.lookup(batch_id)
        if result is None:
            raise ValueError("unknown batch: " + batch_id)
        return result

    def register_ready(self, batch_id, digest, segment):
        require_text(batch_id, "batch_id")
        encoded = json.dumps(descriptor_to_mapping(segment), ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            old = self.lookup(batch_id)
            if old is not None:
                if old.digest != digest:
                    raise ValueError("batch_id already bound to different content")
                self.connection.commit()
                return old
            self.connection.execute("INSERT INTO batches VALUES(?,?,?)", (batch_id, digest, encoded))
            self.connection.commit()
            return BatchBinding(batch_id, digest, segment)
        except BaseException:
            self.connection.rollback()
            raise
