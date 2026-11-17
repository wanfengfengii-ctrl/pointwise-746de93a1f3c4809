from tests.fixtures import LakeCase
from obslake.batch_digest import compute_batch_digest
from obslake.normalization import normalize_records
from obslake.validation import validate_records
from obslake.segment_reader import SegmentReader


class TestIntake(LakeCase):
    def test_normalize_and_replay(self):
        rows = [("z", "S", 2, 0), ("b", "S", 1, -3), ("a", "S", 1, 4), ("x", "A", -2, 8)]
        binding = self.lake.accept_batch("day", rows)
        writes = self.store.write_attempts
        self.assertEqual(self.lake.accept_batch("day", list(reversed(rows))), binding)
        self.assertEqual(self.store.write_attempts, writes)
        records = SegmentReader(self.store).read_segment(binding.segment)
        self.assertEqual([r.as_list() for r in records],
                         [["x", "A", -2, 8], ["a", "S", 1, 4], ["b", "S", 1, -3], ["z", "S", 2, 0]])
        self.assertEqual(binding.segment.record_count, 4)
        self.assertEqual(self.count("batches"), 1)

    def test_reject_whole_batch(self):
        original = self.lake.accept_batch("old", [("x", "S", 1, 7)])
        snapshot = self.lake.publish_snapshot(["old"])
        invalid = [
            [("x", "S", 1, 1), ("x", "T", 2, 2)],
            [("x", "S", 1, True)], [("x", "S", False, 1)],
            [("x", "S", 1.0, 1)], [("x", "S", 1, "1")],
            [("x", "S", None, 1)], [("", "S", 1, 1)], [("x", "", 1, 1)],
            [{"record_id": "x", "station": "S", "time": 1}],
            [{"record_id": "x", "station": "S", "time": 1, "value": 1, "extra": 0}],
            [("x", "S", 1)], [("x", "S", 1, None)]]
        writes = self.store.write_attempts
        for number, rows in enumerate(invalid):
            with self.subTest(number=number):
                with self.assertRaises(ValueError):
                    self.lake.accept_batch("bad-" + str(number), [("valid", "S", 0, 0)] + rows)
        for batch_id in ("", None, True, 42):
            with self.assertRaises(ValueError):
                self.lake.accept_batch(batch_id, [])
        with self.assertRaises(ValueError):
            self.lake.accept_batch("old", [("x", "S", 1, 8)])
        self.assertEqual(self.count("batches"), 1)
        self.assertEqual(self.store.write_attempts, writes)
        self.assertEqual(self.lake.catalog.registry.require("old"), original)
        self.assertEqual(self.lake.catalog.current_snapshot_id(), snapshot)

    def test_digest_boundaries(self):
        pairs = [
            ([("r", "a|b", 1, 2)], [("r|a", "b", 1, 2)]),
            ([("q\n\"", "芦苇", 1, 2)], [("q", "\n\"芦苇", 1, 2)]),
        ]
        for number, (left, right) in enumerate(pairs):
            left_digest = compute_batch_digest(normalize_records(validate_records(left)))
            right_digest = compute_batch_digest(normalize_records(validate_records(right)))
            self.assertNotEqual(left_digest, right_digest)
            batch_id = "edge-" + str(number)
            binding = self.lake.accept_batch(batch_id, left)
            with self.assertRaises(ValueError):
                self.lake.accept_batch(batch_id, right)
            self.assertEqual(self.lake.catalog.registry.require(batch_id), binding)

    def test_empty_batch_and_source_failure(self):
        binding = self.lake.accept_batch("empty", [])
        self.assertEqual(self.lake.accept_batch("empty", iter([])), binding)
        self.assertEqual(SegmentReader(self.store).read_segment(binding.segment), ())
        self.assertEqual(binding.segment.statistics.stations, ())
        self.assertGreater(binding.segment.byte_length, 0)
        writes = self.store.write_attempts

        def broken_source():
            yield ("one", "S", 1, 1)
            raise RuntimeError("upstream stopped")

        with self.assertRaisesRegex(RuntimeError, "upstream stopped"):
            self.lake.accept_batch("broken", broken_source())
        self.assertIsNone(self.lake.catalog.registry.lookup("broken"))
        self.assertEqual(self.store.write_attempts, writes)
