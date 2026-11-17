from dataclasses import dataclass
import hashlib
import json
import re
from .record_model import is_integer
from .segment_stats import SegmentStatistics
from .segment_codec import (CODED_SHARD_COUNT, pack_coded_shard,
                            validate_coded_layout)

_ORDINARY_FIELDS = {"kind", "codec_version", "object_id", "sha256", "byte_length",
                    "record_count", "statistics"}
_CODED_FIELDS = {"kind", "original", "layout", "shards"}
_SHARD_FIELDS = {"index", "object_id", "sha256", "byte_length", "payload_length"}
_IDENTITY = re.compile("[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class OrdinarySegmentDescriptor:
    object_id: str
    sha256: str
    byte_length: int
    record_count: int
    statistics: SegmentStatistics
    codec_version: int = 1

    def __post_init__(self):
        if not isinstance(self.object_id, str) or not re.fullmatch("[0-9a-f]{64}", self.object_id):
            raise ValueError("invalid object identity")
        if self.sha256 != self.object_id:
            raise ValueError("ordinary object identity must equal its digest")
        if not is_integer(self.codec_version) or self.codec_version != 1:
            raise ValueError("unsupported ordinary codec version")
        if not is_integer(self.byte_length) or self.byte_length <= 0:
            raise ValueError("invalid byte length")
        if not is_integer(self.record_count) or self.record_count < 0:
            raise ValueError("invalid record count")
        if not isinstance(self.statistics, SegmentStatistics) or self.statistics.count != self.record_count:
            raise ValueError("descriptor count mismatch")


@dataclass(frozen=True, slots=True)
class CodedShardDescriptor:
    index: int
    object_id: str
    sha256: str
    byte_length: int
    payload_length: int

    def __post_init__(self):
        if not is_integer(self.index) or not 0 <= self.index < CODED_SHARD_COUNT:
            raise ValueError("invalid coded shard index")
        if not isinstance(self.object_id, str) or not _IDENTITY.fullmatch(self.object_id):
            raise ValueError("invalid coded shard identity")
        if not isinstance(self.sha256, str) or not _IDENTITY.fullmatch(self.sha256):
            raise ValueError("invalid coded shard digest")
        if self.sha256 != self.object_id:
            raise ValueError("coded shard identity must equal its digest")
        if not is_integer(self.byte_length) or self.byte_length <= 0:
            raise ValueError("invalid coded shard byte length")
        if not is_integer(self.payload_length) or self.payload_length < 0:
            raise ValueError("invalid coded shard payload length")


@dataclass(frozen=True, slots=True)
class CodedSegmentDescriptor:
    original: OrdinarySegmentDescriptor
    layout: dict
    shards: tuple

    def __post_init__(self):
        if not isinstance(self.original, OrdinarySegmentDescriptor):
            raise ValueError("coded descriptor requires an ordinary original")
        try:
            validate_coded_layout(self.layout)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError("invalid coded layout") from exc
        # Freeze the layout so the immutable descriptor cannot be mutated later.
        object.__setattr__(self, "layout", json.loads(json.dumps(
            self.layout, ensure_ascii=False, allow_nan=False)))
        if self.layout["original_length"] != self.original.byte_length:
            raise ValueError("coded layout length must match the original segment")
        if self.layout["original_sha256"] != self.original.sha256:
            raise ValueError("coded layout digest must match the original segment")
        if not isinstance(self.shards, tuple) or len(self.shards) != CODED_SHARD_COUNT:
            raise ValueError("coded descriptor requires five shards")
        if any(not isinstance(shard, CodedShardDescriptor) for shard in self.shards):
            raise ValueError("invalid coded shard entry")
        if [shard.index for shard in self.shards] != list(range(CODED_SHARD_COUNT)):
            raise ValueError("coded shards must be ordered by index")
        if len({shard.object_id for shard in self.shards}) != CODED_SHARD_COUNT:
            raise ValueError("coded shards must be separately addressable")
        payload_length = self.layout["payload_length"]
        if any(shard.payload_length != payload_length for shard in self.shards):
            raise ValueError("coded shard payload lengths must be equal")

    @property
    def record_count(self):
        return self.original.record_count

    @property
    def statistics(self):
        return self.original.statistics

    def shard(self, index):
        return self.shards[index]


def descriptor_to_mapping(descriptor):
    if isinstance(descriptor, OrdinarySegmentDescriptor):
        return {"kind": "ordinary", "codec_version": descriptor.codec_version,
                "object_id": descriptor.object_id, "sha256": descriptor.sha256,
                "byte_length": descriptor.byte_length, "record_count": descriptor.record_count,
                "statistics": descriptor.statistics.to_mapping()}
    if isinstance(descriptor, CodedSegmentDescriptor):
        return {"kind": "coded",
                "original": descriptor_to_mapping(descriptor.original),
                "layout": descriptor.layout,
                "shards": [{"index": shard.index, "object_id": shard.object_id,
                            "sha256": shard.sha256, "byte_length": shard.byte_length,
                            "payload_length": shard.payload_length}
                           for shard in descriptor.shards]}
    raise ValueError("unsupported descriptor")


def descriptor_from_mapping(value):
    if not isinstance(value, dict):
        raise ValueError("unsupported segment descriptor")
    if value.get("kind") == "ordinary":
        if set(value) != _ORDINARY_FIELDS:
            raise ValueError("unsupported segment descriptor")
        return OrdinarySegmentDescriptor(
            value["object_id"], value["sha256"], value["byte_length"], value["record_count"],
            SegmentStatistics.from_mapping(value["statistics"]), value["codec_version"])
    if value.get("kind") == "coded":
        if set(value) != _CODED_FIELDS or not isinstance(value["shards"], list):
            raise ValueError("unsupported segment descriptor")
        original = descriptor_from_mapping(value["original"])
        if not isinstance(original, OrdinarySegmentDescriptor):
            raise ValueError("coded original must be an ordinary descriptor")
        shards = []
        for item in value["shards"]:
            if not isinstance(item, dict) or set(item) != _SHARD_FIELDS:
                raise ValueError("invalid coded shard mapping")
            shards.append(CodedShardDescriptor(item["index"], item["object_id"],
                                               item["sha256"], item["byte_length"],
                                               item["payload_length"]))
        return CodedSegmentDescriptor(original, value["layout"], tuple(shards))
    raise ValueError("unsupported segment descriptor")


def build_coded_descriptor(original, layout, payloads, object_store):
    """Write the five physical shards and return a coded descriptor.

    Returns ``(descriptor, confirmed)``.  The descriptor always records the
    intended identity, digest and lengths of all five shards (their physical
    bytes are deterministic), while ``confirmed`` counts only shards that were
    acknowledged with the expected identity and verified by reading the
    written object back.  The caller decides whether three successes suffice.
    """
    if not isinstance(original, OrdinarySegmentDescriptor):
        raise ValueError("coded original must be an ordinary segment")
    validate_coded_layout(layout)
    if layout["original_length"] != original.byte_length:
        raise ValueError("coded layout length must match the original segment")
    if layout["original_sha256"] != original.sha256:
        raise ValueError("coded layout digest must match the original segment")
    payloads = tuple(payloads)
    if len(payloads) != CODED_SHARD_COUNT:
        raise ValueError("exactly five shard payloads are required")
    entries = []
    confirmed = 0
    for index, payload in enumerate(payloads):
        if not isinstance(payload, bytes) or len(payload) != layout["payload_length"]:
            raise ValueError("invalid coded shard payload")
        data = pack_coded_shard(layout, index, payload)
        digest = hashlib.sha256(data).hexdigest()
        usable = False
        try:
            acknowledgement = object_store.put_immutable(digest, data)
            if (acknowledgement is not None and getattr(acknowledgement, "success", False)
                    and getattr(acknowledgement, "object_id", None) == digest):
                if object_store.read_bytes(digest) == data:
                    usable = True
        except OSError:
            usable = False
        confirmed += int(usable)
        entries.append(CodedShardDescriptor(index, digest, digest, len(data), len(payload)))
    return CodedSegmentDescriptor(original, layout, tuple(entries)), confirmed
