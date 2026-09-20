"""Fixed-size logical data pages."""

from __future__ import annotations

from dataclasses import dataclass

from querylab.model import Row


@dataclass(frozen=True)
class Page:
    """A logical page containing rows from one table."""

    table_name: str
    page_number: int
    rows: tuple[Row, ...]

    @property
    def key(self) -> str:
        return f"table:{self.table_name}:{self.page_number}"

