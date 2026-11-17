from collections.abc import Mapping
import hashlib
import json
import struct
from .integrity import Unavailable
from .validation import validate_records
from .normalization import normalize_records

ORDINARY_CODEC_VERSION = 1
_HEADER = b"OBSLAKE/1\n"

CODED_CODEC_VERSION = 1
CODED_ALGORITHM = "GF256-VANDERMONDE/1"
CODED_SHARD_COUNT = 5
CODED_DATA_SHARDS = 3
_SHARD_MAGIC = b"OBSC/1\n"
_SHARD_HEADER_FIELDS = {"codec_version", "format", "shard_index", "payload_length",
                        "original_length", "original_sha256", "layout_sha256"}


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


# ---------------------------------------------------------------------------
# Five-shard, three-of-five erasure code over GF(2^8).
#
# The original bytes are split into three equal, zero-padded data blocks and
# evaluated at five distinct points with a Vandermonde matrix.  Any three of
# the five rows form an invertible Vandermonde matrix, so any three payloads
# recover the data blocks exactly.  The zero padding is truncated using the
# original length carried in the layout and never becomes part of a record.
# ---------------------------------------------------------------------------

_LAYOUT_FIELDS = {"codec_version", "algorithm", "shard_count", "data_shards",
                  "original_length", "original_sha256", "payload_length",
                  "layout_sha256"}
_EXP = [0] * 512
_LOG = [0] * 256
_GENERATOR = 3  # primitive element of GF(2^8) under the 0x11B polynomial


def _multiply_plain(left, right):
    result = 0
    for _ in range(8):
        if right & 1:
            result ^= left
        high = left & 0x80
        left = (left << 1) & 0xFF
        if high:
            left ^= 0x1B
        right >>= 1
    return result


def _build_tables():
    value = 1
    for exponent in range(255):
        _EXP[exponent] = value
        _LOG[value] = exponent
        value = _multiply_plain(value, _GENERATOR)
    if value != 1 or len({_EXP[i] for i in range(255)}) != 255:
        raise RuntimeError("GF(256) generator is not primitive")
    for exponent in range(255, 512):
        _EXP[exponent] = _EXP[exponent - 255]


_build_tables()


def _multiply(left, right):
    if left == 0 or right == 0:
        return 0
    return _EXP[_LOG[left] + _LOG[right]]


def _inverse(value):
    if value == 0:
        raise ZeroDivisionError("zero has no multiplicative inverse")
    return _EXP[255 - _LOG[value]]


def _coefficient_row(index, width=CODED_DATA_SHARDS):
    point = index + 1
    return [_EXP[(_LOG[point] * power) % 255] if power else 1 for power in range(width)]


def _invert(matrix):
    size = len(matrix)
    work = [list(row) + [1 if i == j else 0 for j in range(size)]
            for i, row in enumerate(matrix)]
    for column in range(size):
        pivot = next(row for row in range(column, size) if work[row][column])
        if pivot != column:
            work[column], work[pivot] = work[pivot], work[column]
        factor = _inverse(work[column][column])
        work[column] = [_multiply(value, factor) for value in work[column]]
        for row in range(size):
            if row != column and work[row][column]:
                factor = work[row][column]
                work[row] = [left ^ _multiply(factor, right)
                             for left, right in zip(work[row], work[column])]
    return [row[size:] for row in work]


def _layout_core_bytes(layout):
    core = {key: value for key, value in layout.items() if key != "layout_sha256"}
    return json.dumps(core, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _make_layout(raw):
    length = len(raw)
    layout = {"codec_version": CODED_CODEC_VERSION, "algorithm": CODED_ALGORITHM,
              "shard_count": CODED_SHARD_COUNT, "data_shards": CODED_DATA_SHARDS,
              "original_length": length,
              "original_sha256": hashlib.sha256(raw).hexdigest(),
              "payload_length": (length + CODED_DATA_SHARDS - 1) // CODED_DATA_SHARDS}
    layout["layout_sha256"] = hashlib.sha256(_layout_core_bytes(layout)).hexdigest()
    return layout


def validate_coded_layout(layout):
    if not isinstance(layout, dict) or set(layout) != _LAYOUT_FIELDS:
        raise ValueError("invalid coded layout fields")
    if layout["codec_version"] != CODED_CODEC_VERSION or layout["algorithm"] != CODED_ALGORITHM:
        raise ValueError("unsupported coded layout algorithm")
    if layout["shard_count"] != CODED_SHARD_COUNT or layout["data_shards"] != CODED_DATA_SHARDS:
        raise ValueError("unsupported coded shard geometry")
    length = layout["original_length"]
    if not isinstance(length, int) or isinstance(length, bool) or length < 0:
        raise ValueError("invalid coded original length")
    digest = layout["original_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid coded original digest")
    payload_length = layout["payload_length"]
    if payload_length != (length + CODED_DATA_SHARDS - 1) // CODED_DATA_SHARDS:
        raise ValueError("coded payload length does not match original length")
    if not isinstance(layout["layout_sha256"], str):
        raise ValueError("invalid coded layout digest")
    if layout["layout_sha256"] != hashlib.sha256(_layout_core_bytes(layout)).hexdigest():
        raise ValueError("coded layout digest mismatch")
    return layout


def encode_coded_bytes(raw):
    if not isinstance(raw, bytes):
        raise ValueError("coded input must be bytes")
    layout = _make_layout(raw)
    width = layout["payload_length"]
    blocks = []
    for index in range(CODED_DATA_SHARDS):
        block = raw[index * width:(index + 1) * width]
        blocks.append(block + b"\0" * (width - len(block)))
    payloads = []
    for index in range(CODED_SHARD_COUNT):
        row = _coefficient_row(index)
        payload = bytearray(width)
        for position in range(width):
            value = 0
            for block_index in range(CODED_DATA_SHARDS):
                value ^= _multiply(row[block_index], blocks[block_index][position])
            payload[position] = value
        payloads.append(bytes(payload))
    return layout, payloads


def _piece_payloads(layout, pieces):
    if not isinstance(pieces, Mapping):
        raise Unavailable("coded pieces must be a mapping")
    selected = {}
    for index, payload in pieces.items():
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < CODED_SHARD_COUNT:
            raise Unavailable("invalid coded piece index")
        if not isinstance(payload, bytes) or len(payload) != layout["payload_length"]:
            raise Unavailable("invalid coded piece payload")
        selected[index] = payload
    if len(selected) < CODED_DATA_SHARDS:
        raise Unavailable("fewer than three coded pieces")
    return selected


def decode_coded_bytes(layout, pieces):
    validate_coded_layout(layout)
    selected = _piece_payloads(layout, pieces)
    ids = sorted(selected)[:CODED_DATA_SHARDS]
    matrix = _invert([_coefficient_row(index) for index in ids])
    width = layout["payload_length"]
    blocks = [bytearray(width) for _ in range(CODED_DATA_SHARDS)]
    for position in range(width):
        column = [selected[index][position] for index in ids]
        for block_index in range(CODED_DATA_SHARDS):
            value = 0
            for row_index in range(CODED_DATA_SHARDS):
                value ^= _multiply(matrix[block_index][row_index], column[row_index])
            blocks[block_index][position] = value
    raw = b"".join(bytes(block) for block in blocks)[:layout["original_length"]]
    if len(raw) != layout["original_length"]:
        raise Unavailable("coded reconstruction length mismatch")
    if hashlib.sha256(raw).hexdigest() != layout["original_sha256"]:
        raise Unavailable("coded reconstruction digest mismatch")
    return raw


def pack_coded_shard(layout, index, payload):
    validate_coded_layout(layout)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < CODED_SHARD_COUNT:
        raise ValueError("invalid coded shard index")
    if not isinstance(payload, bytes) or len(payload) != layout["payload_length"]:
        raise ValueError("invalid coded shard payload")
    header = {"codec_version": CODED_CODEC_VERSION, "format": "OBSC/1",
              "shard_index": index, "payload_length": len(payload),
              "original_length": layout["original_length"],
              "original_sha256": layout["original_sha256"],
              "layout_sha256": layout["layout_sha256"]}
    encoded = json.dumps(header, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return _SHARD_MAGIC + encoded + b"\n" + payload


def unpack_coded_shard(layout, index, data):
    validate_coded_layout(layout)
    if not isinstance(data, bytes) or not data.startswith(_SHARD_MAGIC):
        raise ValueError("unsupported coded shard header")
    remainder = data[len(_SHARD_MAGIC):]
    newline = remainder.find(b"\n")
    if newline < 0:
        raise ValueError("truncated coded shard header")
    try:
        header = json.loads(remainder[:newline].decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("invalid coded shard header") from exc
    if not isinstance(header, dict) or set(header) != _SHARD_HEADER_FIELDS:
        raise ValueError("invalid coded shard header fields")
    if header["format"] != "OBSC/1" or header["codec_version"] != CODED_CODEC_VERSION:
        raise ValueError("unsupported coded shard format")
    if header["shard_index"] != index:
        raise ValueError("coded shard index mismatch")
    for name in ("payload_length", "original_length", "original_sha256", "layout_sha256"):
        if header[name] != layout[name]:
            raise ValueError("coded shard does not belong to this layout")
    payload = remainder[newline + 1:]
    if len(payload) != header["payload_length"]:
        raise ValueError("coded shard payload length mismatch")
    return payload
