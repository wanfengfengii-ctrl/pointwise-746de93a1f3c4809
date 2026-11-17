from .batch_registry import BatchBinding
from .integrity import Unavailable
from .segment_codec import CODED_DATA_SHARDS, encode_coded_bytes
from .segment_descriptor import build_coded_descriptor
from .segment_reader import SegmentReader


class CodedPublisher:
    """Publish one admitted batch as a fixed three-of-five coded snapshot member.

    The ordinary binding in ``batches`` is never modified.  The original
    ordinary segment is fully verified first, then its exact bytes are coded.
    A snapshot is only committed when at least three shards have been written
    with the expected identity and read back successfully; otherwise the
    current snapshot, its members and the batch bindings stay untouched.
    """

    def __init__(self, catalog):
        self.catalog = catalog
        self.reader = SegmentReader(catalog.object_store)

    def publish_coded(self, batch_id):
        binding = self.catalog.registry.require(batch_id)
        raw = self.reader.read_verified_bytes(binding.segment)
        layout, payloads = encode_coded_bytes(raw)
        descriptor, confirmed = build_coded_descriptor(
            binding.segment, layout, payloads, self.catalog.object_store)
        if confirmed < CODED_DATA_SHARDS:
            raise Unavailable("fewer than three coded shards confirmed")
        coded_binding = BatchBinding(binding.batch_id, binding.digest, descriptor)
        return self.catalog.committer.commit_snapshot((coded_binding,))
