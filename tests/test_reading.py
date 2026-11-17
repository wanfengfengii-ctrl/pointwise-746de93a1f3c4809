from tests.fixtures import LakeCase
from obslake import Record
from obslake.normalization import normalize_records
from obslake.segment_codec import encode_segment, decode_segment
from obslake.segment_stats import compute_segment_statistics
from obslake.segment_descriptor import descriptor_to_mapping, descriptor_from_mapping


class TestReading(LakeCase):
    def test_half_open_and_duplicates(self):
        snapshot = self.publish_sample()
        rows = self.lists(snapshot, start=10, end=20)
        expected = [
            ["b1", "桥站", 10, -5], ["b2", "桥站", 19, 5],
            ["a1", "芦苇站", 10, 9007199254740993],
            ["a1", "芦苇站", 10, 9007199254740993],
            ["a3", "芦苇站", 15, -9007199254740993]]
        self.assertEqual(rows, expected)
        self.assertEqual(self.lists(snapshot, start=10, end=10), [])
        self.assertEqual(self.lists(snapshot, station="unknown", start=10, end=20), [])
        self.assertEqual(self.lists(snapshot, station="桥站", start=19, end=20), [["b2", "桥站", 19, 5]])

    def test_member_order_and_fixed_history(self):
        self.lake.accept_batch("left", [("same", "S", 2, 11)])
        self.lake.accept_batch("right", [("same", "S", 2, 22)])
        a = self.lake.publish_snapshot(["left", "right"])
        b = self.lake.publish_snapshot(["right", "left"])
        self.assertEqual(self.lists(a), [["same", "S", 2, 11], ["same", "S", 2, 22]])
        self.assertEqual(self.lists(b), [["same", "S", 2, 22], ["same", "S", 2, 11]])
        self.reopen()
        self.assertEqual(self.lists(a), [["same", "S", 2, 11], ["same", "S", 2, 22]])

    def test_pruning_against_independent_filter(self):
        batches = [
            [("a", "A", -5, 1), ("b", "A", 0, 2), ("c", "B", 8, 3)],
            [("d", "A", 5, 4), ("e", "B", 10, 5)],
            [],
            [("huge", "A", 10**80, -(10**90))]]
        for i, rows in enumerate(batches):
            self.lake.accept_batch(str(i), rows)
        snapshot = self.lake.publish_snapshot([str(i) for i in range(len(batches))])
        for station in (None, "A", "B", "missing"):
            for start, end in ((-6, -5), (-5, 0), (0, 5), (5, 8), (8, 10),
                               (10, 11), (0, 0), (10**80, 10**80 + 1)):
                expected = []
                for ordinal, rows in enumerate(batches):
                    for row in rows:
                        if (station is None or row[1] == station) and start <= row[2] < end:
                            expected.append((row[1], row[2], row[0], ordinal, list(row)))
                expected.sort(key=lambda entry: entry[:4])
                self.assertEqual(self.lists(snapshot, station, start, end), [entry[4] for entry in expected])
        reads = len(self.store.reads)
        self.assertEqual(self.lists(snapshot, "missing", -10, 10), [])
        self.assertEqual(len(self.store.reads), reads)

    def test_codec_roundtrip_and_metadata(self):
        records = normalize_records([
            Record("r|\n\"", "站\n", -(10**70), 10**100),
            Record("z", "站\n", 0, -7)])
        data = encode_segment(records)
        self.assertEqual(decode_segment(data), records)
        self.assertEqual(decode_segment(encode_segment(())), ())
        binding = self.lake.accept_batch("codec", records)
        descriptor = binding.segment
        self.assertEqual(descriptor.byte_length, len(data))
        self.assertEqual(descriptor.record_count, 2)
        self.assertEqual(descriptor.statistics, compute_segment_statistics(records))
        self.assertEqual(descriptor_from_mapping(descriptor_to_mapping(descriptor)), descriptor)
        for bad in (b"", b"OBSLAKE/2\n[]\n", data + b"x", b"OBSLAKE/1\n[[true,\"S\",1,2]]\n",
                    b"OBSLAKE/1\n[[\"r\",\"S\",1,true]]\n", b"OBSLAKE/1\n{}\n"):
            with self.assertRaises(ValueError):
                decode_segment(bad)

    def test_invalid_request_and_empty_snapshot(self):
        self.publish_sample()
        empty = self.lake.publish_snapshot([])
        self.assertEqual(self.lists(empty), [])
        self.assertEqual(self.lake.catalog.load_snapshot(empty).members, ())
        for station, start, end in (("", 0, 1), (False, 0, 1), (None, True, 1),
                                    (None, 0, False), (None, 2, 1), (None, 0.0, 1)):
            with self.assertRaises(ValueError):
                self.lake.read(empty, station, start, end)
        with self.assertRaises(ValueError):
            self.lake.read("unknown", None, 0, 0)
