"""External merge sort with visible run generation and merge passes."""

from __future__ import annotations

import heapq
from collections.abc import Callable
from math import ceil

from querylab.model import Row, Scalar
from querylab.storage.buffer_pool import BufferPool


def _merge_runs(
    runs: list[list[Row]],
    key: Callable[[Row], Scalar],
) -> list[Row]:
    """Merge already sorted runs using one cursor per run."""

    merged: list[Row] = []
    heap: list[tuple[Scalar, int, int, Row]] = []

    # Seed the heap with the first row from each run. The run and row numbers
    # act as cursors and also provide deterministic tie-breaking.
    for run_number, run in enumerate(runs):
        if run:
            heapq.heappush(heap, (key(run[0]), run_number, 0, run[0]))

    while heap:
        # The smallest available key is the next row in global sorted order.
        _, run_number, row_number, row = heapq.heappop(heap)
        merged.append(row)

        # Replace that row with the next row from the same run.
        next_row_number = row_number + 1
        if next_row_number < len(runs[run_number]):
            next_row = runs[run_number][next_row_number]
            heapq.heappush(
                heap,
                (key(next_row), run_number, next_row_number, next_row),
            )

    return merged


def external_merge_sort(
    rows: list[Row],
    key: Callable[[Row], Scalar],
    buffer_pool: BufferPool[object],
    buffer_frames: int,
    tuples_per_page: int,
    operation_id: str,
) -> list[Row]:
    """Sort rows while metering temporary runs and merge passes."""

    rows_per_run = max(1, buffer_frames * tuples_per_page)
    if len(rows) <= rows_per_run:
        # No temporary pages are needed when the entire input fits in memory.
        return sorted(rows, key=key)

    runs: list[list[Row]] = []

    # Phase 1: sort memory-sized chunks and write each initial run.
    for start in range(0, len(rows), rows_per_run):
        run = sorted(rows[start : start + rows_per_run], key=key)
        runs.append(run)

        run_pages = ceil(len(run) / tuples_per_page)
        for _ in range(run_pages):
            buffer_pool.write_temporary_page()

    # Phase 2: merge at most M-1 runs at a time until one run remains.
    fan_in = max(2, buffer_frames - 1)
    merge_pass = 0

    while len(runs) > 1:
        next_runs: list[list[Row]] = []

        for group_number, start in enumerate(range(0, len(runs), fan_in)):
            group = runs[start : start + fan_in]

            # Read every temporary input page participating in this merge.
            for run_number, run in enumerate(group):
                page_count = ceil(len(run) / tuples_per_page)
                for page_number in range(page_count):
                    page_key = (
                        f"temp:{operation_id}:pass:{merge_pass}:"
                        f"group:{group_number}:run:{run_number}:page:{page_number}"
                    )
                    buffer_pool.fetch(page_key, lambda: page_key)

            merged = _merge_runs(group, key)
            next_runs.append(merged)

            # The merged run is written back unless it can be consumed after
            # this pass. QueryLab counts the write to keep each pass explicit.
            output_pages = ceil(len(merged) / tuples_per_page)
            for _ in range(output_pages):
                buffer_pool.write_temporary_page()

        runs = next_runs
        merge_pass += 1

    return runs[0]
