import json


def encode_export(records):
    pieces = []
    for record in records:
        text = json.dumps(record.as_mapping(), ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
        pieces.append(text.encode("utf-8") + b"\n")
    return b"".join(pieces)
