from dataclasses import dataclass
import re
from .record_model import is_integer
from .segment_stats import SegmentStatistics

_FIELDS = {"kind", "codec_version", "object_id", "sha256", "byte_length",
           "record_count", "statistics"}


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


def descriptor_to_mapping(descriptor):
    if not isinstance(descriptor, OrdinarySegmentDescriptor):
        raise ValueError("unsupported descriptor")
    return {"kind": "ordinary", "codec_version": descriptor.codec_version,
            "object_id": descriptor.object_id, "sha256": descriptor.sha256,
            "byte_length": descriptor.byte_length, "record_count": descriptor.record_count,
            "statistics": descriptor.statistics.to_mapping()}


def descriptor_from_mapping(value):
    if not isinstance(value, dict) or set(value) != _FIELDS or value["kind"] != "ordinary":
        raise ValueError("unsupported segment descriptor")
    return OrdinarySegmentDescriptor(
        value["object_id"], value["sha256"], value["byte_length"], value["record_count"],
        SegmentStatistics.from_mapping(value["statistics"]), value["codec_version"])
