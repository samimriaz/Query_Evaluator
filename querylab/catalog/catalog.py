"""Catalog analysis and intentionally stale statistics."""

from __future__ import annotations

from querylab.catalog.stats import ColumnStats, EquiDepthHistogram, TableStats
from querylab.database import Database


class Catalog:
    """Store statistics independently from live table contents."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.table_stats: dict[str, TableStats] = {}
        self.frozen = False

    def analyze(self, bucket_count: int = 10, force: bool = False) -> None:
        """Recompute all statistics unless the catalog is frozen."""

        if self.frozen and not force:
            raise RuntimeError("catalog statistics are frozen")

        analyzed: dict[str, TableStats] = {}

        for table_name, table in self.database.tables.items():
            rows = table.all_rows()
            columns: dict[str, ColumnStats] = {}

            for column_name in table.columns:
                values = [row[column_name] for row in rows]
                columns[column_name] = ColumnStats(
                    distinct_count=len(set(values)),
                    minimum=min(values),
                    maximum=max(values),
                    histogram=EquiDepthHistogram.build(values, bucket_count),
                )

            analyzed[table_name] = TableStats(
                row_count=table.row_count,
                page_count=table.page_count,
                columns=columns,
            )

        self.table_stats = analyzed

    def freeze(self) -> None:
        self.frozen = True

    def unfreeze(self) -> None:
        self.frozen = False

    def table(self, table_name: str) -> TableStats:
        if table_name not in self.table_stats:
            raise KeyError(f"no catalog statistics for table: {table_name}")
        return self.table_stats[table_name]

