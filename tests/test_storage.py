import unittest

from querylab.storage.buffer_pool import BufferPool
from querylab.storage.table import Table


class StorageTests(unittest.TestCase):
    def test_table_uses_requested_page_count(self) -> None:
        rows = [{"id": row_id} for row_id in range(10)]
        table = Table("items", rows, target_page_count=5)

        self.assertEqual(table.page_count, 5)
        self.assertEqual(sum(len(page.rows) for page in table.pages), 10)

    def test_buffer_pool_uses_lru_eviction_and_counts_io(self) -> None:
        pool: BufferPool[str] = BufferPool(frame_count=2)

        pool.fetch("a", lambda: "page-a")
        pool.fetch("b", lambda: "page-b")
        pool.fetch("a", lambda: "unused")
        pool.fetch("c", lambda: "page-c")

        self.assertEqual(pool.metrics.buffer_misses, 3)
        self.assertEqual(pool.metrics.buffer_hits, 1)
        self.assertEqual(pool.cached_page_keys, ("a", "c"))

    def test_actual_io_counts_misses_and_writes(self) -> None:
        pool: BufferPool[str] = BufferPool(frame_count=1)
        pool.fetch("a", lambda: "page-a")
        pool.write_temporary_page()

        self.assertEqual(pool.metrics.actual_io, 2)


if __name__ == "__main__":
    unittest.main()

