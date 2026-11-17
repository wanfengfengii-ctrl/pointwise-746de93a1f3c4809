from .validation import validate_batch_id, validate_records
from .normalization import normalize_records
from .batch_digest import compute_batch_digest
from .segment_writer import SegmentWriter


class IntakeService:
    def __init__(self, catalog):
        self.registry = catalog.registry
        self.writer = SegmentWriter(catalog.object_store)

    def accept_batch(self, batch_id, records):
        validate_batch_id(batch_id)
        normalized = normalize_records(validate_records(records))
        digest = compute_batch_digest(normalized)
        old = self.registry.lookup(batch_id)
        if old is not None:
            if old.digest != digest:
                raise ValueError("batch_id already bound to different content")
            return old
        segment = self.writer.write_segment(normalized)
        return self.registry.register_ready(batch_id, digest, segment)
