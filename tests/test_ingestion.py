import unittest

from src.ingestion import DataStore
from src.models import SettlementRecord, Source, VisitsRecord


class DataStoreTest(unittest.TestCase):
    def test_ingest_assigns_sequential_batches(self):
        store = DataStore()
        first = store.ingest(Source.SETTLEMENT, [SettlementRecord("2026-01", "R1", "D01", 100, 5, 6)])
        second = store.ingest(Source.VISITS, [VisitsRecord("2026-01", "R1", 1000)])
        self.assertEqual((first.seq, second.seq), (1, 2))
        self.assertEqual(len(store.batches()), 2)

    def test_correction_keeps_history_and_latest_wins(self):
        store = DataStore()
        store.ingest(Source.SETTLEMENT, [SettlementRecord("2026-01", "R1", "D01", 100, 5, 6)])
        store.ingest(
            Source.SETTLEMENT,
            [SettlementRecord("2026-01", "R1", "D01", 130, 5, 6)],
            note="数据修订",
        )
        latest = store.latest(Source.SETTLEMENT)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0].amount_yuan, 130)
        seqs = [seq for seq, _ in store.latest_with_seq(Source.SETTLEMENT)]
        self.assertEqual(seqs, [2])

    def test_wrong_record_type_rejected(self):
        store = DataStore()
        with self.assertRaises(TypeError):
            store.ingest(Source.SETTLEMENT, [VisitsRecord("2026-01", "R1", 1000)])

    def test_ingest_marks_region_reported(self):
        store = DataStore()
        store.ingest(Source.SETTLEMENT, [SettlementRecord("2026-01", "R1", "D01", 100, 5, 6)])
        self.assertTrue(store.is_reported(Source.SETTLEMENT, "2026-01", "R1"))
        self.assertFalse(store.is_reported(Source.SETTLEMENT, "2026-02", "R1"))

    def test_mark_reported_distinguishes_zero_from_missing(self):
        store = DataStore()
        store.mark_reported(Source.SETTLEMENT, "2026-01", "R2")
        self.assertTrue(store.is_reported(Source.SETTLEMENT, "2026-01", "R2"))
        self.assertEqual(store.latest(Source.SETTLEMENT), [])
        self.assertEqual(store.reported_regions(Source.SETTLEMENT, "2026-01"), {"R2"})


if __name__ == "__main__":
    unittest.main()
