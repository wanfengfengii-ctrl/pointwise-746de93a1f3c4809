import hashlib
import json

BATCH_DIGEST_VERSION = 1


def canonical_batch_bytes(records):
    value = [BATCH_DIGEST_VERSION, [record.as_list() for record in records]]
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def compute_batch_digest(records):
    return hashlib.sha256(canonical_batch_bytes(records)).hexdigest()
