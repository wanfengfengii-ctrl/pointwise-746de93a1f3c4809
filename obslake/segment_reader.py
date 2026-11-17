from .integrity import Unavailable, verify_ordinary_object
from .segment_descriptor import OrdinarySegmentDescriptor
from .segment_codec import decode_segment
from .segment_stats import compute_segment_statistics


class SegmentReader:
    def __init__(self, object_store):
        self.object_store = object_store

    def read_segment(self, descriptor):
        if not isinstance(descriptor, OrdinarySegmentDescriptor):
            raise Unavailable("unsupported segment representation")
        try:
            data = self.object_store.read_bytes(descriptor.object_id)
        except OSError as exc:
            raise Unavailable("ordinary object cannot be read") from exc
        verify_ordinary_object(descriptor, data)
        try:
            records = decode_segment(data)
            if len(records) != descriptor.record_count:
                raise ValueError("record count mismatch")
            if compute_segment_statistics(records) != descriptor.statistics:
                raise ValueError("segment statistics mismatch")
        except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
            raise Unavailable("ordinary segment cannot be decoded") from exc
        return records
