import hashlib
from .segment_codec import encode_segment
from .segment_stats import compute_segment_statistics
from .segment_descriptor import OrdinarySegmentDescriptor


class SegmentWriter:
    def __init__(self, object_store):
        self.object_store = object_store

    def write_segment(self, records):
        records = tuple(records)
        data = encode_segment(records)
        digest = hashlib.sha256(data).hexdigest()
        statistics = compute_segment_statistics(records)
        acknowledgement = self.object_store.put_immutable(digest, data)
        if not acknowledgement.success:
            raise OSError(acknowledgement.error or "object write declined")
        if acknowledgement.object_id != digest:
            raise OSError("object write acknowledged a different identity")
        return OrdinarySegmentDescriptor(digest, digest, len(data), len(records), statistics)
