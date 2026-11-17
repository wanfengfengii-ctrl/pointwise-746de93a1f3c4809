import hashlib

from .integrity import Unavailable, verify_ordinary_object
from .segment_descriptor import CodedSegmentDescriptor, OrdinarySegmentDescriptor
from .segment_codec import (CODED_DATA_SHARDS, decode_coded_bytes, decode_segment,
                            unpack_coded_shard)
from .segment_stats import compute_segment_statistics

_DECODE_ERRORS = (ValueError, TypeError, UnicodeError, OverflowError, RecursionError)


class SegmentReader:
    def __init__(self, object_store):
        self.object_store = object_store

    def read_segment(self, descriptor):
        if isinstance(descriptor, OrdinarySegmentDescriptor):
            return self._decode_ordinary(descriptor, self.read_verified_bytes(descriptor))
        if isinstance(descriptor, CodedSegmentDescriptor):
            return self._read_coded(descriptor)
        raise Unavailable("unsupported segment representation")

    def read_verified_bytes(self, descriptor):
        """Read and fully verify an ordinary segment, returning its raw bytes."""
        if not isinstance(descriptor, OrdinarySegmentDescriptor):
            raise Unavailable("unsupported segment representation")
        try:
            data = self.object_store.read_bytes(descriptor.object_id)
        except OSError as exc:
            raise Unavailable("ordinary object cannot be read") from exc
        verify_ordinary_object(descriptor, data)
        # Decoding here is the "full validation" step: canonical format,
        # record count and statistics must agree with the descriptor.
        self._decode_ordinary(descriptor, data)
        return data

    def _decode_ordinary(self, descriptor, data):
        try:
            records = decode_segment(data)
            if len(records) != descriptor.record_count:
                raise ValueError("record count mismatch")
            if compute_segment_statistics(records) != descriptor.statistics:
                raise ValueError("segment statistics mismatch")
        except _DECODE_ERRORS as exc:
            raise Unavailable("ordinary segment cannot be decoded") from exc
        return records

    def _read_coded(self, descriptor):
        layout = descriptor.layout
        payloads = {}
        for shard in descriptor.shards:
            try:
                data = self.object_store.read_bytes(shard.object_id)
            except OSError:
                continue
            if not isinstance(data, bytes) or len(data) != shard.byte_length:
                continue
            if hashlib.sha256(data).hexdigest() != shard.sha256:
                continue
            try:
                payload = unpack_coded_shard(layout, shard.index, data)
            except _DECODE_ERRORS:
                continue
            if len(payload) != shard.payload_length:
                continue
            payloads[shard.index] = payload
        if len(payloads) < CODED_DATA_SHARDS:
            raise Unavailable("fewer than three usable coded shards")
        try:
            raw = decode_coded_bytes(layout, payloads)
        except Unavailable:
            raise
        except _DECODE_ERRORS as exc:
            raise Unavailable("coded shards cannot be decoded") from exc
        original = descriptor.original
        if len(raw) != original.byte_length or hashlib.sha256(raw).hexdigest() != original.sha256:
            raise Unavailable("coded reconstruction does not match the original segment")
        # Padding is truncated by decode_coded_bytes; the recovered bytes must
        # be a complete, canonical ordinary segment satisfying the original
        # descriptor before any record can be returned.
        return self._decode_ordinary(original, raw)
