"""Per-operator execution counters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OperatorMetrics:
    """Actual work performed by one physical operator."""

    # Identity fields connect these counters to the corresponding plan node.
    node_id: str
    label: str

    # Cardinality counters show how much data enters and leaves the operator.
    rows_in: int = 0
    rows_out: int = 0

    # Only misses and writes contribute to the actual I/O cost.
    buffer_misses: int = 0
    page_writes: int = 0

    # CPU-oriented counters are reported for diagnosis but do not participate
    # in QueryLab's I/O-only plan ranking.
    comparisons: int = 0
    hash_operations: int = 0
    predicate_evaluations: int = 0

    @property
    def actual_io(self) -> int:
        return self.buffer_misses + self.page_writes
