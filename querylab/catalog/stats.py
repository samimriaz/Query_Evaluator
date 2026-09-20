"""Table, column, and histogram statistics."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from querylab.model import Scalar


@dataclass(frozen=True)
class HistogramBucket:
    """One inclusive range in an equi-depth histogram."""

    lower: Scalar
    upper: Scalar
    row_count: int
    distinct_count: int


@dataclass(frozen=True)
class EquiDepthHistogram:
    """A compact histogram with approximately equal rows per bucket."""

    buckets: tuple[HistogramBucket, ...]
    total_rows: int

    @classmethod
    def build(
        cls,
        values: list[Scalar],
        bucket_count: int = 10,
    ) -> EquiDepthHistogram | None:
        if not values:
            return None

        sorted_values = sorted(values)
        bucket_size = max(1, ceil(len(sorted_values) / bucket_count))
        buckets: list[HistogramBucket] = []

        for start in range(0, len(sorted_values), bucket_size):
            bucket_values = sorted_values[start : start + bucket_size]
            buckets.append(
                HistogramBucket(
                    lower=bucket_values[0],
                    upper=bucket_values[-1],
                    row_count=len(bucket_values),
                    distinct_count=len(set(bucket_values)),
                )
            )

        return cls(tuple(buckets), len(values))

    def estimate_equality(self, value: Scalar) -> float:
        for bucket in self.buckets:
            if bucket.lower <= value <= bucket.upper:
                estimated_matches = bucket.row_count / max(1, bucket.distinct_count)
                return estimated_matches / self.total_rows
        return 0.0


@dataclass(frozen=True)
class ColumnStats:
    """Statistics for one table column."""

    distinct_count: int
    minimum: Scalar
    maximum: Scalar
    histogram: EquiDepthHistogram | None = None


@dataclass(frozen=True)
class TableStats:
    """Optimizer-visible statistics for one table."""

    row_count: int
    page_count: int
    columns: dict[str, ColumnStats]

