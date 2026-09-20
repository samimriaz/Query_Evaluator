"""A table split across a configurable number of logical pages."""

from __future__ import annotations

from math import ceil

from querylab.model import Row
from querylab.storage.buffer_pool import BufferPool
from querylab.storage.page import Page


class Table:
    """Store rows in fixed logical pages.

    ``target_page_count`` supports controlled physical-layout experiments.
    Without it, page count is derived from ``tuples_per_page``.
    """

    def __init__(
        self,
        name: str,
        rows: list[Row],
        tuples_per_page: int = 100,
        target_page_count: int | None = None,
    ) -> None:
        if tuples_per_page < 1:
            raise ValueError("tuples_per_page must be at least 1")
        if target_page_count is not None and target_page_count < 1:
            raise ValueError("target_page_count must be at least 1")

        self.name = name
        self.columns = tuple(rows[0].keys()) if rows else ()
        self._rows = rows
        self._tuples_per_page = tuples_per_page
        self._target_page_count = target_page_count
        self.pages = self._make_pages(rows, tuples_per_page, target_page_count)

    def _make_pages(
        self,
        rows: list[Row],
        tuples_per_page: int,
        target_page_count: int | None,
    ) -> list[Page]:
        if target_page_count is None:
            target_page_count = max(1, ceil(len(rows) / tuples_per_page))

        rows_per_page = max(1, ceil(len(rows) / target_page_count))
        pages: list[Page] = []

        for page_number in range(target_page_count):
            start = page_number * rows_per_page
            end = start + rows_per_page
            page_rows = tuple(rows[start:end])
            pages.append(Page(self.name, page_number, page_rows))

        return pages

    @property
    def row_count(self) -> int:
        return len(self._rows)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def fetch_page(self, page_number: int, buffer_pool: BufferPool[object]) -> Page:
        page = self.pages[page_number]
        loaded = buffer_pool.fetch(page.key, lambda: page)
        if not isinstance(loaded, Page):
            raise TypeError(f"buffer key {page.key} did not contain a data page")
        return loaded

    def all_rows(self) -> list[Row]:
        """Return source rows for catalog analysis, outside query execution."""

        return list(self._rows)

    def insert_rows(self, rows: list[Row]) -> None:
        """Append rows and rebuild the educational page layout."""

        if not rows:
            return
        if self.columns and tuple(rows[0].keys()) != self.columns:
            raise ValueError("inserted rows must match the table columns")

        self._rows.extend(rows)
        self.pages = self._make_pages(
            self._rows,
            self._tuples_per_page,
            self._target_page_count,
        )
