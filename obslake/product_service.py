from .product_model import ProductRequest
from .product_store import ProductStore
from .aggregation import summarize_records
from .export import encode_export
from .snapshot_reader import SnapshotReader


class ProductService:
    def __init__(self, catalog, reader=None):
        self.reader = reader if reader is not None else SnapshotReader(catalog)
        self.store = ProductStore(catalog.connection)

    def summarize(self, snapshot_id, station, start, end):
        request = ProductRequest("summary", snapshot_id, station, start, end)
        records = self.reader.read(snapshot_id, station, start, end)
        rows = summarize_records(records)
        return self.store.save_summary(request, rows)

    def export_product(self, snapshot_id, station, start, end):
        request = ProductRequest("export", snapshot_id, station, start, end)
        records = self.reader.read(snapshot_id, station, start, end)
        data = encode_export(records)
        return self.store.save_export(request, data)

    def get_product(self, product_id):
        return self.store.get_product(product_id)


def demo():
    import json
    from pathlib import Path
    import tempfile
    from .catalog import Catalog
    from .intake import IntakeService

    sample = json.loads((Path(__file__).resolve().parent.parent / "samples.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="obslake-demo-") as root:
        with Catalog.open(root) as catalog:
            intake = IntakeService(catalog)
            products = ProductService(catalog)
            for batch_id, rows in sample["batches"].items():
                intake.accept_batch(batch_id, rows)
            snapshot = catalog.publish_snapshot(list(sample["batches"]))
            summary = products.summarize(snapshot, None, 10, 20)
            exported = products.export_product(snapshot, None, 10, 20)
            actual = [row.as_mapping() for row in summary.summary]
            expected = sample["expected_summary"]
            assert actual == expected, (actual, expected)
            exported_rows = [json.loads(line) for line in exported.export_bytes.splitlines()]
            assert exported_rows == sample["expected_records"]
            intake.accept_batch("B3", [("new", "桥站", 12, 100)])
            catalog.publish_snapshot(["B3"])
        with Catalog.open(root) as reopened:
            products = ProductService(reopened)
            assert products.get_product(summary.product_id) == summary
            assert products.get_product(exported.product_id) == exported
            repeated = products.export_product(snapshot, None, 10, 20)
            assert repeated.export_bytes == exported.export_bytes
            print("POINTWISE_SCENARIO " + json.dumps({
                "case_id": "v7.saved_binding",
                "input": {"batches": sample["batches"], "window": [10, 20]},
                "expected": expected, "actual": actual,
                "downstream_expected": sample["expected_records"],
                "downstream_actual": exported_rows,
                "code_paths": ["obslake/intake.py", "obslake/catalog.py",
                               "obslake/range_scan.py", "obslake/aggregation.py",
                               "obslake/product_store.py", "obslake/export.py"],
                "oracle": "hand-counted five observations; independent JSON-lines decoding; reopened saved-product equality"
            }, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    demo()
