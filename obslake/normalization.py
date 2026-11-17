from .record_model import record_sort_key


def normalize_records(records):
    return tuple(sorted(records, key=record_sort_key))
