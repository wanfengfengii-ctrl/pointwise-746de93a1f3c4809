from collections.abc import Mapping
from .record_model import Record, require_text


def validate_batch_id(batch_id):
    return require_text(batch_id, "batch_id")


def validate_records(records):
    result = []
    identities = set()
    for raw in records:
        if isinstance(raw, Record):
            record = Record(*raw.as_list())
        elif isinstance(raw, Mapping):
            if set(raw) != {"record_id", "station", "time", "value"}:
                raise ValueError("record must have exactly four fields")
            record = Record(raw["record_id"], raw["station"], raw["time"], raw["value"])
        elif isinstance(raw, (tuple, list)) and len(raw) == 4:
            record = Record(*raw)
        else:
            raise ValueError("record must be a Record, mapping or four-item sequence")
        if record.record_id in identities:
            raise ValueError("duplicate record_id in batch")
        identities.add(record.record_id)
        result.append(record)
    return tuple(result)
