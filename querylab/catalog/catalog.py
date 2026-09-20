"""Catalog analysis and intentionally stale statistics."""

from __future__ import annotations

import json
from pathlib import Path

from querylab.catalog.stats import (
    ColumnStats,
    EquiDepthHistogram,
    HistogramBucket,
    TableStats,
)
from querylab.database import Database
from querylab.model import Scalar


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

    def save(self, path: str | Path) -> None:
        """Persist the current statistics and index metadata as readable JSON."""

        if not self.table_stats:
            raise RuntimeError("catalog must be analyzed before it can be saved")

        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible representation of this catalog."""

        tables: dict[str, object] = {}

        for table_name, table_stats in sorted(self.table_stats.items()):
            columns: dict[str, object] = {}

            for column_name, column_stats in sorted(table_stats.columns.items()):
                histogram: dict[str, object] | None = None
                if column_stats.histogram is not None:
                    histogram = {
                        "total_rows": column_stats.histogram.total_rows,
                        "buckets": [
                            {
                                "lower": bucket.lower,
                                "upper": bucket.upper,
                                "row_count": bucket.row_count,
                                "distinct_count": bucket.distinct_count,
                            }
                            for bucket in column_stats.histogram.buckets
                        ],
                    }

                columns[column_name] = {
                    "distinct_count": column_stats.distinct_count,
                    "minimum": column_stats.minimum,
                    "maximum": column_stats.maximum,
                    "histogram": histogram,
                }

            indexes: dict[str, object] = {}
            for (indexed_table, column_name), index in sorted(
                self.database.indexes.items()
            ):
                if indexed_table != table_name:
                    continue
                indexes[column_name] = {
                    "name": index.name,
                    "height": index.height,
                    "clustered": index.clustered,
                }

            tables[table_name] = {
                "row_count": table_stats.row_count,
                "page_count": table_stats.page_count,
                "columns": columns,
                "indexes": indexes,
            }

        return {
            "schema_version": 1,
            "frozen": self.frozen,
            "tables": tables,
        }

    @classmethod
    def load(cls, database: Database, path: str | Path) -> Catalog:
        """Load a frozen catalog snapshot for repeatable or stale-stat runs."""

        input_path = Path(path)
        document = json.loads(input_path.read_text(encoding="utf-8"))

        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("unsupported catalog snapshot schema")

        stored_tables = document.get("tables")
        if not isinstance(stored_tables, dict):
            raise ValueError("catalog snapshot is missing tables")

        catalog = cls(database)
        loaded_stats: dict[str, TableStats] = {}

        for table_name, stored_table in stored_tables.items():
            if not isinstance(table_name, str) or not isinstance(stored_table, dict):
                raise ValueError("invalid table entry in catalog snapshot")

            stored_columns = stored_table.get("columns")
            if not isinstance(stored_columns, dict):
                raise ValueError(f"catalog table {table_name} is missing columns")

            columns: dict[str, ColumnStats] = {}
            for column_name, stored_column in stored_columns.items():
                if not isinstance(column_name, str) or not isinstance(
                    stored_column,
                    dict,
                ):
                    raise ValueError("invalid column entry in catalog snapshot")

                histogram = cls._load_histogram(stored_column.get("histogram"))
                columns[column_name] = ColumnStats(
                    distinct_count=cls._integer(
                        stored_column.get("distinct_count"),
                        "distinct_count",
                    ),
                    minimum=cls._scalar(stored_column.get("minimum"), "minimum"),
                    maximum=cls._scalar(stored_column.get("maximum"), "maximum"),
                    histogram=histogram,
                )

            loaded_stats[table_name] = TableStats(
                row_count=cls._integer(stored_table.get("row_count"), "row_count"),
                page_count=cls._integer(
                    stored_table.get("page_count"),
                    "page_count",
                ),
                columns=columns,
            )

        catalog.table_stats = loaded_stats
        catalog.freeze()
        return catalog

    @classmethod
    def _load_histogram(cls, value: object) -> EquiDepthHistogram | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("invalid histogram in catalog snapshot")

        stored_buckets = value.get("buckets")
        if not isinstance(stored_buckets, list):
            raise ValueError("histogram is missing buckets")

        buckets: list[HistogramBucket] = []
        for stored_bucket in stored_buckets:
            if not isinstance(stored_bucket, dict):
                raise ValueError("invalid histogram bucket")
            buckets.append(
                HistogramBucket(
                    lower=cls._scalar(stored_bucket.get("lower"), "bucket lower"),
                    upper=cls._scalar(stored_bucket.get("upper"), "bucket upper"),
                    row_count=cls._integer(
                        stored_bucket.get("row_count"),
                        "bucket row_count",
                    ),
                    distinct_count=cls._integer(
                        stored_bucket.get("distinct_count"),
                        "bucket distinct_count",
                    ),
                )
            )

        return EquiDepthHistogram(
            buckets=tuple(buckets),
            total_rows=cls._integer(value.get("total_rows"), "histogram total_rows"),
        )

    @staticmethod
    def _integer(value: object, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field_name} must be an integer")
        return value

    @staticmethod
    def _scalar(value: object, field_name: str) -> Scalar:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"{field_name} must be a scalar value")
        return value
