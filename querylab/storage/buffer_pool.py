"""A small least-recently-used buffer pool with explicit I/O metering."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass
class IOMetrics:
    """Physical page operations observed during execution."""

    page_requests: int = 0
    buffer_hits: int = 0
    buffer_misses: int = 0
    page_writes: int = 0

    @property
    def actual_io(self) -> int:
        return self.buffer_misses + self.page_writes

    def snapshot(self) -> tuple[int, int, int, int]:
        return (
            self.page_requests,
            self.buffer_hits,
            self.buffer_misses,
            self.page_writes,
        )


class BufferPool(Generic[T]):
    """Cache logical pages and evict the least recently used page."""

    def __init__(self, frame_count: int) -> None:
        if frame_count < 1:
            raise ValueError("frame_count must be at least 1")

        self.frame_count = frame_count
        self.metrics = IOMetrics()
        self._frames: OrderedDict[str, T] = OrderedDict()

    def fetch(self, page_key: str, loader: Callable[[], T]) -> T:
        """Return a page, loading it from simulated storage on a miss."""

        self.metrics.page_requests += 1

        if page_key in self._frames:
            self.metrics.buffer_hits += 1
            page = self._frames.pop(page_key)
            self._frames[page_key] = page
            return page

        self.metrics.buffer_misses += 1
        page = loader()

        if len(self._frames) >= self.frame_count:
            self._frames.popitem(last=False)

        self._frames[page_key] = page
        return page

    def write_temporary_page(self) -> None:
        """Record one page written to simulated temporary storage."""

        self.metrics.page_writes += 1

    def clear(self, reset_metrics: bool = True) -> None:
        """Remove cached pages and optionally reset all counters."""

        self._frames.clear()
        if reset_metrics:
            self.metrics = IOMetrics()

    @property
    def cached_page_keys(self) -> tuple[str, ...]:
        return tuple(self._frames.keys())

