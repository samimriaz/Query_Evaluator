"""Per-operator execution counters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OperatorMetrics:
    """Actual work performed by one physical operator."""

    node_id: str
    label: str
    rows_in: int = 0
    rows_out: int = 0
    buffer_misses: int = 0
    page_writes: int = 0
    comparisons: int = 0
    hash_operations: int = 0
    predicate_evaluations: int = 0

    @property
    def actual_io(self) -> int:
        return self.buffer_misses + self.page_writes

