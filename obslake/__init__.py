import sys

# Observation counters have no decimal digit limit in the public contract.
if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(0)

from .record_model import Record
from .catalog import Catalog
from .intake import IntakeService
from .snapshot_reader import SnapshotReader
from .integrity import Unavailable


class Lake:
    def __init__(self, root, object_store=None):
        from .product_service import ProductService
        self.catalog = Catalog.open(root, object_store)
        try:
            self.intake = IntakeService(self.catalog)
            self.reader = SnapshotReader(self.catalog)
            self.products = ProductService(self.catalog, self.reader)
        except BaseException:
            self.catalog.close()
            raise

    def accept_batch(self, batch_id, records):
        return self.intake.accept_batch(batch_id, records)

    def publish_snapshot(self, batch_ids):
        return self.catalog.publish_snapshot(batch_ids)

    def read(self, snapshot_id, station, start, end):
        return self.reader.read(snapshot_id, station, start, end)

    def summarize(self, snapshot_id, station, start, end):
        return self.products.summarize(snapshot_id, station, start, end)

    def export_product(self, snapshot_id, station, start, end):
        return self.products.export_product(snapshot_id, station, start, end)

    def get_product(self, product_id):
        return self.products.get_product(product_id)

    def close(self):
        self.catalog.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
