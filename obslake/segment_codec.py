import json
from .validation import validate_records
from .normalization import normalize_records

ORDINARY_CODEC_VERSION = 1
_HEADER = b"OBSLAKE/1\n"


def encode_segment(records):
    records = tuple(records)
    checked = validate_records(records)
    if checked != normalize_records(checked):
        raise ValueError("segment records must be normalized")
    payload = json.dumps([r.as_list() for r in checked], ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return _HEADER + payload + b"\n"


def decode_segment(data):
    if not isinstance(data, bytes) or not data.startswith(_HEADER):
        raise ValueError("unsupported ordinary segment header")
    value = json.loads(data[len(_HEADER):].decode("utf-8"))
    if not isinstance(value, list) or any(not isinstance(row, list) for row in value):
        raise ValueError("invalid segment records")
    records = validate_records(value)
    if records != normalize_records(records):
        raise ValueError("segment records out of order")
    if encode_segment(records) != data:
        raise ValueError("noncanonical or trailing segment bytes")
    return records
