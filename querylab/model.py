"""Shared query and plan data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

Scalar: TypeAlias = int | float | str
Row: TypeAlias = dict[str, Scalar]


@dataclass(frozen=True)
class ColumnRef:
    """A column reference, optionally qualified by a table alias."""

    name: str
    table_alias: str | None = None

    @property
    def qualified_name(self) -> str:
        if self.table_alias:
            return f"{self.table_alias}.{self.name}"
        return self.name


@dataclass(frozen=True)
class Predicate:
    """A comparison between a column and a literal value."""

    column: ColumnRef
    operator: str
    value: Scalar


@dataclass(frozen=True)
class JoinCondition:
    """An equality condition connecting two tables."""

    left: ColumnRef
    right: ColumnRef


@dataclass(frozen=True)
class TableRef:
    """A table and the alias used by a query."""

    name: str
    alias: str


@dataclass(frozen=True)
class SelectItem:
    """A projected column or aggregate."""

    column: ColumnRef
    aggregate: str | None = None
    output_name: str | None = None


@dataclass
class Query:
    """The supported logical query representation."""

    tables: list[TableRef]
    select_items: list[SelectItem]
    predicates: list[Predicate] = field(default_factory=list)
    joins: list[JoinCondition] = field(default_factory=list)
    group_by: list[ColumnRef] = field(default_factory=list)

