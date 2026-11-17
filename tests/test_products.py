import json
from tests.fixtures import LakeCase


class TestProducts(LakeCase):
    def test_exact_station_summary(self):
        snapshot = self.publish_sample()
        product = self.lake.summarize(snapshot, None, 10, 20)
        actual = [row.as_mapping() for row in product.summary]
        expected = [
            {"station": "桥站", "count": 2, "sum": 0, "first_time": 10, "last_time": 19},
            {"station": "芦苇站", "count": 3, "sum": 9007199254740993, "first_time": 10, "last_time": 15}]
        self.assertEqual(actual, expected)
        exported = self.lake.export_product(snapshot, None, 10, 20)
        lines = [json.loads(line) for line in exported.export_bytes.splitlines()]
        independent = {}
        for row in lines:
            group = independent.setdefault(row["station"], {"count": 0, "sum": 0, "times": []})
            group["count"] += 1
            group["sum"] += row["value"]
            group["times"].append(row["time"])
        downstream = [
            {"station": station, "count": group["count"], "sum": group["sum"],
             "first_time": min(group["times"]), "last_time": max(group["times"])}
            for station, group in sorted(independent.items())]
        self.assertEqual(downstream, expected)
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "v6.integer_precision",
            "input": self.sample()["batches"],
            "expected": expected, "actual": actual,
            "downstream_expected": expected, "downstream_actual": downstream,
            "code_paths": ["obslake/range_scan.py", "obslake/aggregation.py",
                           "obslake/product_store.py", "obslake/export.py"],
            "oracle": "hand-counted sample; independent JSON-lines integer recount"
        }, ensure_ascii=False, separators=(",", ":")))

    def test_fixed_product_reopen(self):
        snapshot = self.publish_sample()
        summary = self.lake.summarize(snapshot, None, 10, 20)
        exported = self.lake.export_product(snapshot, None, 10, 20)
        self.assertEqual(summary.request.snapshot_id, snapshot)
        self.lake.accept_batch("later", [("later", "桥站", 11, 999)])
        new_snapshot = self.lake.publish_snapshot(["later"])
        self.reopen()
        self.assertEqual(self.lake.catalog.current_snapshot_id(), new_snapshot)
        repeated = self.lake.export_product(snapshot, None, 10, 20)
        self.assertEqual(repeated, exported)
        for path in (self.root / "objects").rglob("*"):
            if path.is_file():
                path.unlink()
        before_reads = len(self.store.reads)
        self.assertEqual(self.lake.get_product(summary.product_id), summary)
        self.assertEqual(self.lake.get_product(exported.product_id), exported)
        self.assertEqual(len(self.store.reads), before_reads)
        with self.assertRaises(ValueError):
            self.lake.get_product("unknown")

    def test_export_bytes_and_empty_results(self):
        self.lake.accept_batch("escaped", [('r"\n', "站", -2, 7)])
        snapshot = self.lake.publish_snapshot(["escaped"])
        product = self.lake.export_product(snapshot, None, -3, 0)
        expected = b'{"record_id":"r\\"\\n","station":"' + "站".encode("utf-8") + b'","time":-2,"value":7}\n'
        self.assertEqual(product.export_bytes, expected)
        self.assertEqual(self.lake.export_product(snapshot, None, -3, 0), product)
        empty_summary = self.lake.summarize(snapshot, None, 0, 0)
        empty_export = self.lake.export_product(snapshot, None, 0, 0)
        self.assertEqual(empty_summary.summary, ())
        self.assertEqual(empty_export.export_bytes, b"")
        self.reopen()
        self.assertEqual(self.lake.get_product(empty_export.product_id), empty_export)

    def test_unbounded_integer_roundtrip(self):
        huge = 10**5000
        self.lake.accept_batch("huge", [("a", "S", huge, huge + 1), ("b", "S", huge + 1, -huge)])
        snapshot = self.lake.publish_snapshot(["huge"])
        product = self.lake.summarize(snapshot, None, huge, huge + 2)
        self.assertEqual(product.summary[0].sum, 1)
        self.assertIs(type(product.summary[0].sum), int)
        exported = self.lake.export_product(snapshot, None, huge, huge + 2)
        self.assertEqual([json.loads(line)["value"] for line in exported.export_bytes.splitlines()],
                         [huge + 1, -huge])
        self.reopen()
        self.assertEqual(self.lake.get_product(product.product_id), product)
