from .scan_request import validate_scan_request
from .segment_reader import SegmentReader
from .range_scan import scan_snapshot


class SnapshotReader:
    def __init__(self, catalog):
        self.catalog = catalog
        self.segment_reader = SegmentReader(catalog.object_store)

    def read(self, snapshot_id, station, start, end):
        request = validate_scan_request(station, start, end)
        snapshot = self.catalog.load_snapshot(snapshot_id)
        return scan_snapshot(snapshot, request, self.segment_reader)
