"""A readable in-memory stand-in for a B+tree index."""

from __future__ import annotations

from collections import defaultdict
from math import ceil, log

from querylab.model import Scalar
from querylab.storage.buffer_pool import BufferPool
from querylab.storage.table import Table

RowLocation = tuple[int, int]


class BTreeIndex:
    """Map ordered keys to table row locations.

    The mapping keeps this project focused on optimization. Index traversal
    still fetches one simulated page per tree level, so its I/O is measurable.
    """

    def __init__(
        self,
        table: Table,
        column: str,
        clustered: bool = False,
        entries_per_page: int = 100,
    ) -> None:
        self.table = table
        self.column = column
        self.clustered = clustered
        self.name = f"{table.name}_{column}_idx"
        self._locations: dict[Scalar, list[RowLocation]] = defaultdict(list)

        for page in table.pages:
            for row_offset, row in enumerate(page.rows):
                self._locations[row[column]].append((page.page_number, row_offset))

        leaf_pages = max(1, ceil(table.row_count / entries_per_page))
        if leaf_pages == 1:
            self.height = 1
        else:
            self.height = 1 + ceil(log(leaf_pages, entries_per_page))

    def equality_search(
        self,
        value: Scalar,
        buffer_pool: BufferPool[object],
    ) -> list[RowLocation]:
        """Fetch the index path and return locations matching one key."""

        for level in range(self.height):
            page_key = f"index:{self.name}:level:{level}:key:{value}"
            buffer_pool.fetch(page_key, lambda: {"level": level, "value": value})

        return list(self._locations.get(value, []))

