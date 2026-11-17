from .record_model import record_sort_key
from .scan_request import contains_record
from .segment_stats import may_intersect


def scan_snapshot(snapshot, request, segment_reader):
    selected = []
    for ordinal, binding in enumerate(snapshot.members):
        if not may_intersect(binding.segment.statistics, request):
            continue
        records = segment_reader.read_segment(binding.segment)
        for record in records:
            if contains_record(request, record):
                selected.append((record_sort_key(record) + (ordinal,), record))
    selected.sort(key=lambda pair: pair[0])
    return [record for _, record in selected]
